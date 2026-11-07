"""OpenAI-compatible chat-completions adapter — the Saluki 27B boundary.

This adapter speaks the de-facto standard ``/chat/completions`` protocol
(non-streaming and SSE streaming), which most self-hosted runtimes expose
(vLLM, TGI, llama.cpp server, Ollama's compat layer, ...).

IMPORTANT — honest status:
    The platform has NOT been verified against a real Saluki 27B endpoint.
    This adapter implements the documented OpenAI wire format and is
    contract-tested against a local mock server (tests/unit/test_openai_adapter.py).
    Connecting the real runtime is a configuration + verification exercise
    documented in docs/SALUKI_INTEGRATION.md.  If Saluki's actual API
    differs, a new adapter module is added — no core code changes.

Error normalisation (never leaks upstream bodies to clients):
    401/403 -> authentication failure (config problem)
    404    -> bad request (wrong endpoint/model id)
    429    -> temporarily unavailable (retry)
    5xx    -> temporarily unavailable
    timeouts / connection errors -> timeout / unavailable
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import structlog

from app.core.errors import (
    ModelBadRequestError,
    ModelGatewayError,
    ModelTimeoutError,
    ModelUnavailableError,
)
from app.core.utils import utcnow
from app.db.enums import ModelAdapterType
from app.gateway.base import (
    HealthReport,
    InferenceMessage,
    InferenceRequest,
    InferenceResponse,
    ModelAdapter,
    StreamEvent,
    ToolCall,
    UsageInfo,
    register_adapter,
)

logger = structlog.stdlib.get_logger("app.gateway.openai")

_SSE_DATA_PREFIX = "data:"


@register_adapter(ModelAdapterType.OPENAI_COMPATIBLE)
class OpenAICompatibleAdapter(ModelAdapter):
    def __init__(self, config) -> None:
        super().__init__(config)
        if not config.endpoint:
            raise ModelBadRequestError(
                f"model '{config.name}' has no endpoint configured"
            )
        self._endpoint = str(config.endpoint).rstrip("/")
        self._model_id = config.model_id or config.name
        self._headers: dict[str, str] = {"Content-Type": "application/json"}
        if config.api_key:
            self._headers["Authorization"] = f"Bearer {config.api_key}"
        self._parameters = dict(config.parameters or {})
        self._timeout = httpx.Timeout(float(self._parameters.get("timeout_s", 120.0)))
        self._include_stream_usage = bool(self._parameters.get("include_stream_usage", False))

    # -- request mapping -----------------------------------------------------
    def _payload(self, request: InferenceRequest, *, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model_id,
            "messages": [self._encode_message(m) for m in request.messages],
            "stream": stream,
        }
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.stop:
            payload["stop"] = request.stop
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
                for t in request.tools
            ]
        if request.response_schema is not None:
            # Standard "json_object" mode; the schema itself is conveyed in
            # the prompt (documented limitation, see SALUKI_INTEGRATION.md).
            payload["response_format"] = {"type": "json_object"}
        if stream and self._include_stream_usage:
            payload["stream_options"] = {"include_usage": True}
        return payload

    @staticmethod
    def _encode_message(message: InferenceMessage) -> dict[str, Any]:
        encoded: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.tool_calls:
            encoded["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments),
                    },
                }
                for call in message.tool_calls
            ]
        if message.tool_call_id is not None:
            encoded["tool_call_id"] = message.tool_call_id
        if message.name is not None:
            encoded["name"] = message.name
        return encoded

    # -- error normalisation ---------------------------------------------------
    def _normalize_error(self, exc: Exception) -> ModelGatewayError:
        if isinstance(exc, httpx.TimeoutException):
            return ModelTimeoutError("model runtime timed out")
        if isinstance(exc, httpx.ConnectError):
            return ModelUnavailableError("cannot reach model runtime")
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            detail = ""
            try:
                body = exc.response.json()
                detail = str(body.get("error", {}).get("message", ""))[:200]
            except Exception:
                detail = (exc.response.text or "")[:200]
            if status in (401, 403):
                return ModelGatewayError(
                    "model runtime rejected our credentials",
                    details={"status": status},
                )
            if status == 404:
                return ModelBadRequestError(
                    "model runtime returned 404 — check endpoint and model id",
                    details={"status": status},
                )
            if status == 429:
                return ModelUnavailableError(
                    "model runtime rate-limited the request",
                    details={"status": status, "retry_after": exc.response.headers.get("retry-after")},
                )
            return ModelUnavailableError(
                "model runtime error",
                details={"status": status, "detail": detail},
            )
        return ModelGatewayError(f"unexpected model transport error: {type(exc).__name__}")

    # -- contract implementation -------------------------------------------------
    async def generate(self, request: InferenceRequest) -> InferenceResponse:
        self.validate_context(request)
        self.supports("tools", request)
        if request.response_schema is not None:
            self.supports("structured", request)

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._endpoint}/chat/completions",
                    headers=self._headers,
                    json=self._build_final_payload(request, stream=False),
                )
                response.raise_for_status()
                data = response.json()
        except ModelGatewayError:
            raise
        except Exception as exc:
            raise self._normalize_error(exc) from exc

        return self._decode_response(data)

    def _build_final_payload(self, request: InferenceRequest, *, stream: bool) -> dict[str, Any]:
        payload = self._payload(request, stream=stream)
        # Structured outputs: append the JSON schema into the system prompt so
        # json_object mode has a target shape (documented limitation).
        if request.response_schema is not None:
            schema_instruction = (
                "Respond with a single JSON object that strictly validates "
                "against this JSON schema:\n"
                + json.dumps(request.response_schema)
            )
            if payload["messages"] and payload["messages"][0]["role"] == "system":
                payload["messages"][0]["content"] += "\n\n" + schema_instruction
            else:
                payload["messages"].insert(0, {"role": "system", "content": schema_instruction})
        return payload

    def _decode_response(self, data: dict[str, Any]) -> InferenceResponse:
        choices = data.get("choices") or []
        if not choices:
            raise ModelGatewayError("model runtime returned no choices")
        message = choices[0].get("message") or {}
        tool_calls: list[ToolCall] = []
        for i, call in enumerate(message.get("tool_calls") or []):
            function = call.get("function") or {}
            arguments: dict[str, Any] = {}
            raw_args = function.get("arguments")
            if raw_args:
                try:
                    arguments = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except json.JSONDecodeError:
                    raise ModelGatewayError("model returned malformed tool arguments")
            tool_calls.append(
                ToolCall(
                    id=str(call.get("id") or f"call-{i}"),
                    name=str(function.get("name") or ""),
                    arguments=arguments,
                )
            )
        usage = data.get("usage") or {}
        finish = choices[0].get("finish_reason") or "stop"
        return InferenceResponse(
            content=str(message.get("content") or ""),
            finish_reason="tool_calls" if tool_calls else (finish if finish in ("stop", "length") else "stop"),
            tool_calls=tool_calls,
            usage=UsageInfo(
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
            )
            if usage
            else None,
            model=str(data.get("model") or self._model_id),
            adapter="openai_compatible",
        )

    def stream(self, request: InferenceRequest) -> AsyncIterator[StreamEvent]:
        return self._stream_events(request)

    async def _stream_events(self, request: InferenceRequest) -> AsyncIterator[StreamEvent]:
        self.validate_context(request)
        self.supports("tools", request)
        # accumulated tool calls (OpenAI streams them incrementally)
        pending_calls: dict[int, dict[str, Any]] = {}
        usage: UsageInfo | None = None
        finish_reason = "stop"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self._endpoint}/chat/completions",
                    headers=self._headers,
                    json=self._build_final_payload(request, stream=True),
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        event = self._parse_sse_line(
                            line, pending_calls, self._include_stream_usage
                        )
                        if event is None:
                            continue
                        if event.kind == "done":
                            finish_reason = event.finish_reason or finish_reason
                            usage = event.usage or usage
                            continue
                        if event.kind == "delta":
                            yield event
                        elif event.kind == "tool_call":
                            yield event
                        elif event.kind == "error":
                            yield event
                            return
        except ModelGatewayError as exc:
            yield StreamEvent(kind="error", error=exc.message)
            return
        except Exception as exc:
            yield StreamEvent(kind="error", error=self._normalize_error(exc).message)
            return

        # flush accumulated tool calls once the stream ends
        for index in sorted(pending_calls):
            call = pending_calls[index]
            arguments: dict[str, Any] = {}
            raw = call.get("arguments", "")
            if raw:
                try:
                    arguments = json.loads(raw)
                except json.JSONDecodeError:
                    yield StreamEvent(kind="error", error="model returned malformed tool arguments")
                    return
            yield StreamEvent(
                kind="tool_call",
                tool_call=ToolCall(
                    id=str(call.get("id") or f"call-{index}"),
                    name=str(call.get("name") or ""),
                    arguments=arguments,
                ),
            )
            finish_reason = "tool_calls"
        yield StreamEvent(kind="done", finish_reason=finish_reason, usage=usage)

    @staticmethod
    def _parse_sse_line(
        line: str,
        pending_calls: dict[int, dict[str, Any]],
        include_usage: bool,
    ) -> StreamEvent | None:
        line = line.strip()
        if not line.startswith(_SSE_DATA_PREFIX):
            return None
        data = line[len(_SSE_DATA_PREFIX) :].strip()
        if data == "[DONE]":
            return StreamEvent(kind="done")
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            return None
        if include_usage and chunk.get("usage"):
            u = chunk["usage"]
            usage = UsageInfo(
                prompt_tokens=u.get("prompt_tokens"),
                completion_tokens=u.get("completion_tokens"),
                total_tokens=u.get("total_tokens"),
            )
            return StreamEvent(kind="done", usage=usage)
        choices = chunk.get("choices") or []
        if not choices:
            return None
        choice = choices[0]
        delta = choice.get("delta") or {}
        if delta.get("content"):
            return StreamEvent(kind="delta", text=str(delta["content"]))
        for fragment in delta.get("tool_calls") or []:
            index = int(fragment.get("index", 0))
            slot = pending_calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
            if fragment.get("id"):
                slot["id"] = fragment["id"]
            function = fragment.get("function") or {}
            if function.get("name"):
                slot["name"] = slot["name"] + function["name"]
            if function.get("arguments"):
                slot["arguments"] = slot["arguments"] + function["arguments"]
        if choice.get("finish_reason"):
            return StreamEvent(kind="done", finish_reason=choice["finish_reason"])
        return None

    async def health(self) -> HealthReport:
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(10.0)
            ) as client:
                response = await client.get(f"{self._endpoint}/models", headers=self._headers)
            if response.status_code == 200:
                return HealthReport(
                    healthy=True,
                    detail="model runtime reachable (/models responded)",
                    checked_at=utcnow().isoformat(),
                )
            return HealthReport(
                healthy=False,
                detail=f"model runtime returned HTTP {response.status_code}",
                checked_at=utcnow().isoformat(),
            )
        except Exception as exc:
            return HealthReport(
                healthy=False,
                detail=f"model runtime unreachable: {type(exc).__name__}",
                checked_at=utcnow().isoformat(),
            )
