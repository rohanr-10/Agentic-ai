"""Contract tests for the OpenAI-compatible adapter (the Saluki boundary).

Runs a REAL local HTTP server speaking the OpenAI wire format and asserts
the adapter maps requests/responses/stream chunks/errors correctly.  This
verifies the adapter against the documented protocol — it does NOT claim
verification against a real Saluki runtime (see docs/SALUKI_INTEGRATION.md).
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.core.errors import (
    ModelBadRequestError,
    ModelGatewayError,
    ModelUnavailableError,
)
from app.db.enums import ModelAdapterType
from app.gateway.adapters.openai_compatible import OpenAICompatibleAdapter
from app.gateway.base import InferenceMessage, InferenceRequest

from tests.unit.test_gateway_test_adapter import _config  # reuse config factory


class _Handler(BaseHTTPRequestHandler):
    behaviour: dict = {}

    def log_message(self, *args):  # silence
        pass

    def _send(self, status: int, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/models":
            if _Handler.behaviour.get("models_status") == 404:
                self._send(404, {"error": {"message": "not found"}})
            else:
                self._send(200, {"data": [{"id": "saluki-27b"}]})
        else:
            self._send(404, {})

    def do_POST(self):
        assert self.path == "/chat/completions"
        length = int(self.headers.get("Content-Length", 0))
        request_body = json.loads(self.rfile.read(length) or b"{}")
        _Handler.behaviour["last_request"] = request_body

        mode = _Handler.behaviour.get("mode", "ok")

        if mode == "auth_error":
            self._send(401, {"error": {"message": "bad key"}})
            return
        if mode == "rate_limited":
            self.send_response(429)
            self.send_header("Retry-After", "3")
            body = json.dumps({"error": {"message": "slow down"}}).encode()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if request_body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunks = _Handler.behaviour.get(
                "stream_chunks",
                [
                    {"choices": [{"delta": {"content": "Hel"}}]},
                    {"choices": [{"delta": {"content": "lo"}}]},
                    {"choices": [{"delta": {}, "finish_reason": "stop"}]},
                ],
            )
            for chunk in chunks:
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            return

        if mode == "tool_calls":
            self._send(
                200,
                {
                    "model": "saluki-27b",
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "role": "assistant",
                                "content": "I will check that.",
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "http.request",
                                            "arguments": json.dumps(
                                                {"url": "https://example.com"}
                                            ),
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17},
                },
            )
            return

        self._send(
            200,
            {
                "model": "saluki-27b",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "Hello there"},
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
            },
        )


@pytest.fixture
def mock_server():
    _Handler.behaviour = {}
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _adapter(endpoint: str) -> OpenAICompatibleAdapter:
    config = _config()
    config.name = "saluki-27b"
    config.adapter_type = ModelAdapterType.OPENAI_COMPATIBLE
    config.endpoint = endpoint
    config.model_id = "saluki-27b"
    config.parameters = {"timeout_s": 10}
    return OpenAICompatibleAdapter(config)


def _request(**overrides) -> InferenceRequest:
    payload = dict(messages=[InferenceMessage(role="user", content="hi")])
    payload.update(overrides)
    return InferenceRequest(**payload)


class TestGenerate:
    async def test_basic_mapping(self, mock_server):
        adapter = _adapter(mock_server)
        response = await adapter.generate(_request(temperature=0.2))
        assert response.content == "Hello there"
        assert response.finish_reason == "stop"
        assert response.usage.total_tokens == 13
        assert response.model == "saluki-27b"

        sent = _Handler.behaviour["last_request"]
        assert sent["model"] == "saluki-27b"
        assert sent["messages"][0]["role"] == "user"
        assert sent["temperature"] == 0.2

    async def test_tool_call_mapping(self, mock_server):
        _Handler.behaviour["mode"] = "tool_calls"
        adapter = _adapter(mock_server)
        response = await adapter.generate(_request())
        assert response.finish_reason == "tool_calls"
        assert response.tool_calls[0].id == "call_1"
        assert response.tool_calls[0].name == "http.request"
        assert response.tool_calls[0].arguments == {"url": "https://example.com"}

    async def test_structured_output_injects_schema_into_prompt(self, mock_server):
        adapter = _adapter(mock_server)
        schema = {"type": "object", "properties": {"answer": {"type": "number"}}}
        await adapter.generate(_request(response_schema=schema))
        sent = _Handler.behaviour["last_request"]
        assert sent["response_format"] == {"type": "json_object"}
        system_messages = [m for m in sent["messages"] if m["role"] == "system"]
        assert system_messages and "JSON schema" in system_messages[0]["content"]

    async def test_auth_error_normalized(self, mock_server):
        _Handler.behaviour["mode"] = "auth_error"
        adapter = _adapter(mock_server)
        with pytest.raises(ModelGatewayError) as exc_info:
            await adapter.generate(_request())
        assert not isinstance(exc_info.value, ModelUnavailableError)
        assert "credentials" in exc_info.value.message

    async def test_rate_limit_normalized(self, mock_server):
        _Handler.behaviour["mode"] = "rate_limited"
        adapter = _adapter(mock_server)
        with pytest.raises(ModelUnavailableError) as exc_info:
            await adapter.generate(_request())
        assert exc_info.value.details["status"] == 429

    async def test_unreachable_endpoint_normalized(self):
        adapter = _adapter("http://127.0.0.1:1")  # nothing listens here
        with pytest.raises(ModelUnavailableError):
            await adapter.generate(_request())

    async def test_missing_endpoint_rejected(self):
        config = _config()
        config.adapter_type = ModelAdapterType.OPENAI_COMPATIBLE
        config.endpoint = None
        with pytest.raises(ModelBadRequestError):
            OpenAICompatibleAdapter(config)


class TestStream:
    async def test_stream_deltas_and_done(self, mock_server):
        adapter = _adapter(mock_server)
        events = [e async for e in adapter.stream(_request())]
        deltas = "".join(e.text or "" for e in events if e.kind == "delta")
        assert deltas == "Hello"
        done = [e for e in events if e.kind == "done"]
        assert done and done[-1].finish_reason == "stop"

    async def test_stream_tool_calls_accumulated(self, mock_server):
        _Handler.behaviour["stream_chunks"] = [
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_9",
                                    "function": {"name": "fs.", "arguments": '{"pa'},
                                }
                            ]
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "function": {"name": "read", "arguments": 'th": "x"}'}}
                            ]
                        }
                    }
                ]
            },
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]
        adapter = _adapter(mock_server)
        events = [e async for e in adapter.stream(_request())]
        tool_events = [e for e in events if e.kind == "tool_call"]
        assert len(tool_events) == 1
        assert tool_events[0].tool_call.id == "call_9"
        assert tool_events[0].tool_call.name == "fs.read"
        assert tool_events[0].tool_call.arguments == {"path": "x"}
        assert events[-1].finish_reason == "tool_calls"


class TestHealth:
    async def test_healthy(self, mock_server):
        adapter = _adapter(mock_server)
        report = await adapter.health()
        assert report.healthy

    async def test_unhealthy_on_404_models(self, mock_server):
        _Handler.behaviour["models_status"] = 404
        adapter = _adapter(mock_server)
        report = await adapter.health()
        assert not report.healthy
        assert "404" in report.detail
