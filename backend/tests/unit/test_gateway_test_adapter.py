"""Unit tests: model gateway contracts with the deterministic test adapter."""

from __future__ import annotations

import pytest

from app.core.errors import ModelBadRequestError, ModelTimeoutError
from app.db.enums import ModelAdapterType
from app.gateway.adapters.test_adapter import TestAdapter
from app.gateway.base import (
    InferenceMessage,
    InferenceRequest,
    ToolSpec,
)


def _config(**parameters) -> object:
    from datetime import datetime

    from app.core.utils import new_uuid

    from app.db.models import ModelConfig

    return ModelConfig(
        id=new_uuid(),
        name="test-echo",
        display_name="Test",
        adapter_type=ModelAdapterType.TEST,
        context_window=512,
        max_output_tokens=256,
        capabilities={
            "streaming": True,
            "tool_calls": True,
            "structured_output": True,
        },
        parameters=parameters,
        is_default=True,
        is_enabled=True,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )


def _request(**overrides) -> InferenceRequest:
    payload = dict(
        messages=[InferenceMessage(role="user", content="what is the answer?")],
    )
    payload.update(overrides)
    return InferenceRequest(**payload)


class TestGenerate:
    async def test_default_response_is_labelled_echo(self):
        adapter = TestAdapter(_config())
        response = await adapter.generate(_request())
        assert response.content.startswith("[test-adapter]")
        assert "what is the answer?" in response.content
        assert response.finish_reason == "stop"
        assert response.usage is not None and response.usage.estimated

    async def test_scripted_content(self):
        adapter = TestAdapter(_config(script=[{"type": "content", "content": "planned reply"}]))
        response = await adapter.generate(_request())
        assert response.content == "planned reply"

    async def test_scripted_tool_calls(self):
        adapter = TestAdapter(
            _config(
                script=[
                    {
                        "type": "tool_call",
                        "content": "let me check",
                        "tool_calls": [
                            {"id": "c1", "name": "http.request", "arguments": {"url": "https://x"}}
                        ],
                    }
                ]
            )
        )
        response = await adapter.generate(_request())
        assert response.finish_reason == "tool_calls"
        assert response.tool_calls[0].name == "http.request"
        assert response.tool_calls[0].arguments == {"url": "https://x"}

    async def test_scripted_structured_output(self):
        adapter = TestAdapter(
            _config(script=[{"type": "structured", "content": {"answer": 42}}])
        )
        response = await adapter.generate(_request(response_schema={"type": "object"}))
        import json

        assert json.loads(response.content) == {"answer": 42}

    async def test_structured_without_script_is_honest_error(self):
        adapter = TestAdapter(_config())
        with pytest.raises(ModelBadRequestError):
            await adapter.generate(_request(response_schema={"type": "object"}))

    async def test_scripted_error(self):
        adapter = TestAdapter(_config(script=[{"type": "error", "code": "model_timeout"}]))
        with pytest.raises(ModelTimeoutError):
            await adapter.generate(_request())

    async def test_context_window_validation(self):
        adapter = TestAdapter(_config())
        big = "x" * 4000  # ~1000 estimated tokens > 512-token declared window
        with pytest.raises(ModelBadRequestError) as exc_info:
            await adapter.generate(
                _request(messages=[InferenceMessage(role="user", content=big)])
            )
        assert "context window" in exc_info.value.message

    async def test_script_items_consume_in_order(self):
        adapter = TestAdapter(
            _config(
                script=[
                    {"type": "content", "content": "first"},
                    {"type": "content", "content": "second"},
                ]
            )
        )
        assert (await adapter.generate(_request())).content == "first"
        assert (await adapter.generate(_request())).content == "second"
        # then falls through to deterministic echo
        assert (await adapter.generate(_request())).content.startswith("[test-adapter]")


class TestStream:
    async def test_stream_chunks_and_finishes(self):
        adapter = TestAdapter(_config())
        events = [event async for event in adapter.stream(_request())]
        deltas = [e for e in events if e.kind == "delta"]
        done = [e for e in events if e.kind == "done"]
        assert deltas and done
        assert done[-1].finish_reason == "stop"
        assert done[-1].usage is not None

    async def test_stream_scripted_tool_call(self):
        adapter = TestAdapter(
            _config(
                script=[
                    {
                        "type": "tool_call",
                        "content": "checking",
                        "tool_calls": [{"id": "c1", "name": "fs.read", "arguments": {"path": "a"}}],
                    }
                ]
            )
        )
        events = [event async for event in adapter.stream(_request())]
        tool_events = [e for e in events if e.kind == "tool_call"]
        assert tool_events and tool_events[0].tool_call.name == "fs.read"

    async def test_stream_error_event(self):
        adapter = TestAdapter(
            _config(script=[{"type": "stream_error", "message": "boom"}])
        )
        events = [event async for event in adapter.stream(_request())]
        assert any(e.kind == "error" and e.error == "boom" for e in events)


class TestHealth:
    async def test_test_adapter_reports_healthy_and_honest(self):
        adapter = TestAdapter(_config())
        report = await adapter.health()
        assert report.healthy
        assert "not a model runtime" in report.detail


class TestToolCapabilityDeclaration:
    async def test_tools_declared_when_adapter_lacks_support(self):
        config = _config()
        config.capabilities = {"streaming": True, "tool_calls": False, "structured_output": True}
        adapter = TestAdapter(config)
        request = _request(tools=[ToolSpec(name="t", description="d", input_schema={})])
        with pytest.raises(ModelBadRequestError) as exc_info:
            await adapter.generate(request)
        assert "does not declare tool-call support" in exc_info.value.message
