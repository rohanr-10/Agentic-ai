"""Deterministic test adapter (contract testing / local development).

This adapter is a *test double*, not a model.  It:

* produces clearly labelled deterministic output derived from the prompt
  (never pretends to be a capable model);
* optionally replays a scripted sequence of responses (content, tool calls,
  structured payloads, errors) configured on the model config row —
  ``parameters.script`` — so tool-call loops, planners and error paths can
  be integration-tested end-to-end without a real runtime;
* supports streaming by chunking its deterministic content;
* reports itself healthy (it is in-process).

Script item shapes (consumed in order, one per request):

    {"type": "content",   "content": "..."}
    {"type": "tool_call", "content": "...", "tool_calls": [
        {"id": "call-1", "name": "http.request", "arguments": {...}}]}
    {"type": "structured","content": {...}}          # dict -> JSON string
    {"type": "error",    "code": "model_timeout"}    # raises a gateway error
    {"type": "stream_error", "message": "..."}       # error mid-stream
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import structlog

from app.core.errors import ModelBadRequestError, ModelTimeoutError, ModelUnavailableError
from app.core.utils import estimate_tokens, truncate_text, utcnow
from app.db.enums import ModelAdapterType
from app.gateway.base import (
    HealthReport,
    InferenceRequest,
    InferenceResponse,
    ModelAdapter,
    StreamEvent,
    ToolCall,
    UsageInfo,
    register_adapter,
)

logger = structlog.stdlib.get_logger("app.gateway.test")

_TEST_PREFIX = "[test-adapter]"


@register_adapter(ModelAdapterType.TEST)
class TestAdapter(ModelAdapter):
    __test__ = False  # not a pytest test class

    def __init__(self, config) -> None:
        super().__init__(config)
        script = (config.parameters or {}).get("script") or []
        if not isinstance(script, list):
            raise ModelBadRequestError("test adapter script must be a list")
        self._script: list[dict[str, Any]] = [dict(item) for item in script]
        self._lock = asyncio.Lock()

    # -- helpers -------------------------------------------------------------
    async def _pop_script_item(self) -> dict[str, Any] | None:
        async with self._lock:
            return self._script.pop(0) if self._script else None

    def _default_content(self, request: InferenceRequest) -> str:
        user_text = request.user_text() or "(no user message)"
        tool_names = [t.name for t in request.tools]
        parts = [
            f"{_TEST_PREFIX} deterministic echo response from adapter '{self.name}'.",
            f"prompt: {truncate_text(user_text, 400)}",
        ]
        if tool_names:
            parts.append("available tools: " + ", ".join(tool_names))
        return "\n".join(parts)

    def _usage(self, request: InferenceRequest, content: str) -> UsageInfo:
        return UsageInfo(
            prompt_tokens=estimate_tokens(" ".join(m.content for m in request.messages)),
            completion_tokens=estimate_tokens(content),
            estimated=True,
        )

    @staticmethod
    def _script_error(code: str) -> Exception:
        if code == "model_timeout":
            return ModelTimeoutError("test adapter: simulated timeout")
        if code == "model_unavailable":
            return ModelUnavailableError("test adapter: simulated unavailability")
        return ModelBadRequestError(f"test adapter: simulated error '{code}'")

    # -- contract implementation ----------------------------------------------
    async def generate(self, request: InferenceRequest) -> InferenceResponse:
        self.validate_context(request)
        self.supports("tools", request)
        if request.response_schema is not None:
            self.supports("structured", request)

        item = await self._pop_script_item()
        if item is None:
            if request.response_schema is not None:
                raise ModelBadRequestError(
                    "test adapter produces structured output only via a configured script"
                )
            content = self._default_content(request)
            return InferenceResponse(
                content=content,
                finish_reason="stop",
                usage=self._usage(request, content),
                model=self.name,
                adapter="test",
            )

        kind = item.get("type", "content")
        if kind == "error":
            raise self._script_error(item.get("code", "model_bad_request"))

        if kind == "tool_call":
            tool_calls = [
                ToolCall(
                    id=str(tc.get("id") or f"test-call-{i}"),
                    name=str(tc["name"]),
                    arguments=dict(tc.get("arguments") or {}),
                )
                for i, tc in enumerate(item.get("tool_calls") or [])
            ]
            content = item.get("content", "")
            return InferenceResponse(
                content=content,
                finish_reason="tool_calls",
                tool_calls=tool_calls,
                usage=self._usage(request, content),
                model=self.name,
                adapter="test",
            )

        if kind == "structured":
            content = json.dumps(item.get("content", {}))
            return InferenceResponse(
                content=content,
                finish_reason="stop",
                usage=self._usage(request, content),
                model=self.name,
                adapter="test",
            )

        content = str(item.get("content", ""))
        return InferenceResponse(
            content=content,
            finish_reason="stop",
            usage=self._usage(request, content),
            model=self.name,
            adapter="test",
        )

    def stream(self, request: InferenceRequest) -> AsyncIterator[StreamEvent]:
        """Chunked deterministic stream."""
        return self._stream_events(request)

    async def _stream_events(self, request: InferenceRequest) -> AsyncIterator[StreamEvent]:
        self.validate_context(request)
        self.supports("tools", request)
        item = await self._pop_script_item()

        if item is not None and item.get("type") in ("stream_error", "error"):
            message = str(item.get("message") or f"scripted error: {item.get('code', 'unknown')}")
            yield StreamEvent(kind="error", error=message)
            return

        if item is not None and item.get("type") == "tool_call":
            tool_calls = [
                ToolCall(
                    id=str(tc.get("id") or f"test-call-{i}"),
                    name=str(tc["name"]),
                    arguments=dict(tc.get("arguments") or {}),
                )
                for i, tc in enumerate(item.get("tool_calls") or [])
            ]
            for call in tool_calls:
                yield StreamEvent(kind="tool_call", tool_call=call)
            content = str(item.get("content", ""))
            if content:
                for chunk in _chunk(content):
                    yield StreamEvent(kind="delta", text=chunk)
            yield StreamEvent(kind="done", finish_reason="tool_calls")
            return

        content = (
            str(item.get("content", "")) if item is not None else self._default_content(request)
        )
        for chunk in _chunk(content):
            yield StreamEvent(kind="delta", text=chunk)
        yield StreamEvent(
            kind="done",
            finish_reason="stop",
            usage=UsageInfo(
                prompt_tokens=estimate_tokens(" ".join(m.content for m in request.messages)),
                completion_tokens=estimate_tokens(content),
                estimated=True,
            ),
        )

    async def health(self) -> HealthReport:
        return HealthReport(
            healthy=True,
            detail="in-process deterministic test adapter (not a model runtime)",
            checked_at=utcnow().isoformat(),
        )


def _chunk(text: str, size: int = 24) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]
