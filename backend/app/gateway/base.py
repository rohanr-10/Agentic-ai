"""Model gateway contracts.

The gateway is the ONLY place where model runtimes are contacted.  Everything
upstream (conversation management, orchestration, memory, task execution)
programs against these interfaces and never against a concrete model.

The primary production model (Underdog Saluki 27B) is integrated by
implementing ``ModelAdapter`` — see docs/SALUKI_INTEGRATION.md.  The
``TestAdapter`` exists for contract testing and local development and is
always clearly labelled in its output; it never fabricates model quality.
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

from app.core.errors import ModelBadRequestError
from app.core.utils import estimate_tokens
from app.db.enums import ModelAdapterType
from app.db.models import ModelConfig

Role = Literal["system", "user", "assistant", "tool"]
FinishReason = Literal["stop", "tool_calls", "length"]


@dataclass(frozen=True)
class ModelCapabilities:
    streaming: bool
    tool_calls: bool
    structured_output: bool
    context_window: int


@dataclass
class ToolSpec:
    """A tool advertised to the model during inference."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class InferenceMessage:
    role: Role
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None


@dataclass
class UsageInfo:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    estimated: bool = False


@dataclass
class InferenceRequest:
    messages: list[InferenceMessage]
    tools: list[ToolSpec] = field(default_factory=list)
    max_tokens: int | None = None
    temperature: float | None = None
    stop: list[str] = field(default_factory=list)
    """Structured output request: a JSON schema the response must satisfy."""
    response_schema: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def user_text(self) -> str:
        parts = [m.content for m in self.messages if m.role == "user"]
        return parts[-1] if parts else ""

    def estimated_prompt_tokens(self) -> int:
        return sum(estimate_tokens(m.content) for m in self.messages)


@dataclass
class InferenceResponse:
    content: str
    finish_reason: FinishReason
    usage: UsageInfo | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""
    adapter: str = ""

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


@dataclass(frozen=True)
class StreamEvent:
    """Incremental streaming event.

    kind:
      * ``delta``     — a text chunk (``text``)
      * ``tool_call`` — the model requests a tool (``tool_call``)
      * ``done``      — terminal; carries finish_reason + usage
      * ``error``     — terminal; carries error message
    """

    kind: Literal["delta", "tool_call", "done", "error"]
    text: str | None = None
    tool_call: ToolCall | None = None
    finish_reason: FinishReason | None = None
    usage: UsageInfo | None = None
    error: str | None = None


@dataclass(frozen=True)
class HealthReport:
    healthy: bool
    detail: str
    checked_at: str


class ModelAdapter(abc.ABC):
    """Contract every model runtime integration must satisfy."""

    def __init__(self, config: ModelConfig) -> None:
        # Snapshot plain values: the ORM instance may be detached from its
        # session by the time the adapter is used (manager-level caching).
        self._config_name = config.name
        self._context_window = config.context_window
        self._capabilities = dict(config.capabilities or {})

    @property
    def config_name(self) -> str:
        return self._config_name

    @property
    def name(self) -> str:
        return self._config_name

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            streaming=bool(self._capabilities.get("streaming", False)),
            tool_calls=bool(self._capabilities.get("tool_calls", False)),
            structured_output=bool(self._capabilities.get("structured_output", False)),
            context_window=self._context_window,
        )

    @abc.abstractmethod
    async def generate(self, request: InferenceRequest) -> InferenceResponse:
        """Single-shot inference."""

    @abc.abstractmethod
    def stream(self, request: InferenceRequest) -> AsyncIterator[StreamEvent]:
        """Streaming inference."""

    @abc.abstractmethod
    async def health(self) -> HealthReport:
        """Live health probe of the backing runtime."""

    def validate_context(self, request: InferenceRequest) -> None:
        """Reject requests that cannot fit the declared context window.

        Uses a conservative chars/4 estimate; runtimes that report exact
        prompt usage surface overflows as normalized errors anyway.
        """
        estimate = request.estimated_prompt_tokens()
        budget = self.capabilities.context_window - (request.max_tokens or 0)
        if estimate > max(1, budget):
            raise ModelBadRequestError(
                "prompt exceeds the model context window",
                details={
                    "estimated_prompt_tokens": estimate,
                    "context_window": self.capabilities.context_window,
                },
            )

    def supports(self, capability: str, request: InferenceRequest) -> None:
        if capability == "tools" and request.tools and not self.capabilities.tool_calls:
            raise ModelBadRequestError(
                f"model '{self.name}' does not declare tool-call support"
            )
        if (
            capability == "structured"
            and request.response_schema is not None
            and not self.capabilities.structured_output
        ):
            raise ModelBadRequestError(
                f"model '{self.name}' does not declare structured-output support"
            )


ADAPTER_REGISTRY: dict[str, type[ModelAdapter]] = {}


def register_adapter(adapter_type: ModelAdapterType | str) -> type:
    """Register an adapter class for a ``ModelAdapterType`` value."""

    def decorator(cls: type) -> type:
        ADAPTER_REGISTRY[adapter_type.value if hasattr(adapter_type, "value") else adapter_type] = cls
        return cls

    return decorator


def adapter_class_for(adapter_type: str) -> type[ModelAdapter]:
    try:
        return ADAPTER_REGISTRY[adapter_type]
    except KeyError as exc:
        raise ModelBadRequestError(
            f"unknown adapter type '{adapter_type}'",
            details={"known": sorted(ADAPTER_REGISTRY)},
        ) from exc
