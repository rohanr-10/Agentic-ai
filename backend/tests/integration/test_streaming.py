"""Integration: SSE streaming chat with the test adapter."""

from __future__ import annotations

import json

from tests.conftest import create_conversation


async def _stream(client, headers, conversation_id, content):
    request = client.build_request(
        "POST",
        f"/api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": content},
        headers=headers,
    )
    response = await client.send(request, stream=True)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = []
    event_type = None
    async for line in response.aiter_lines():
        if line.startswith("event: "):
            event_type = line[len("event: ") :].strip()
        elif line.startswith("data: ") and event_type:
            events.append((event_type, json.loads(line[len("data: ") :])))
            event_type = None
    await response.aclose()
    return events


class TestStreamingChat:
    async def test_stream_lifecycle_events(self, client, headers):
        conversation = await create_conversation(client, headers)
        events = await _stream(client, headers, conversation["id"], "stream this please")

        types = [t for t, _ in events]
        assert types[0] == "message.created"
        assert "delta" in types
        assert "message.completed" in types
        assert types[-1] == "done"

        created = events[0][1]
        assert created["role"] == "user"
        completed = next(data for t, data in events if t == "message.completed")
        assert completed["role"] == "assistant"

        deltas = [data["text"] for t, data in events if t == "delta"]
        text = "".join(deltas)
        assert text.startswith("[test-adapter]")

    async def test_streamed_message_is_persisted(self, client, headers):
        conversation = await create_conversation(client, headers)
        events = await _stream(client, headers, conversation["id"], "persist me")
        completed = next(data for t, data in events if t == "message.completed")
        message_id = completed["message_id"]

        listing = await client.get(
            f"/api/v1/conversations/{conversation['id']}/messages", headers=headers
        )
        messages = listing.json()
        assert any(m["id"] == message_id and m["role"] == "assistant" for m in messages)
        persisted = next(m for m in messages if m["id"] == message_id)
        assert persisted["meta"]["streamed"] is True

    async def test_stream_error_event_on_adapter_error(self, client, headers):
        """A scripted adapter error surfaces as an SSE error event."""
        app = client._transport.app
        # script the default model to fail once
        import sqlalchemy as sa

        from app.db.models import ModelConfig

        async with app.state.database.session() as session:
            config = await session.scalar(
                sa.select(ModelConfig).where(ModelConfig.name == "test-echo")
            )
            config.parameters = {
                "script": [{"type": "stream_error", "message": "simulated outage"}]
            }
            await session.commit()
        app.state.model_manager.invalidate(config.id)

        conversation = await create_conversation(client, headers)
        events = await _stream(client, headers, conversation["id"], "will fail")
        types = [t for t, _ in events]
        assert "error" in types

    async def test_tool_call_round_trip(self, client, headers):
        """Scripted tool call executes the real tool and feeds the result back."""
        import sqlalchemy as sa

        from app.db.models import ModelConfig

        app = client._transport.app
        async with app.state.database.session() as session:
            config = await session.scalar(
                sa.select(ModelConfig).where(ModelConfig.name == "test-echo")
            )
            # upload a file first, then have the model call document.extract on it
            upload = await client.post(
                "/api/v1/files",
                files={"file": ("data.txt", b"extractable payload", "text/plain")},
                headers=headers,
            )
            file_id = upload.json()["id"]

            # process extraction now
            from app.workers.runner import Worker

            worker = Worker(app.state.database, app.state.settings, app.state.bus)
            await worker.drain(max_wait_s=2.0)

            config.parameters = {
                "script": [
                    {
                        "type": "tool_call",
                        "content": "reading the file",
                        "tool_calls": [
                            {
                                "id": "c1",
                                "name": "document.extract",
                                "arguments": {"file_id": file_id, "max_chars": 1000},
                            }
                        ],
                    },
                    {"type": "content", "content": "final answer with file contents"},
                ]
            }
            await session.commit()
        app.state.model_manager.invalidate(config.id)

        conversation = await create_conversation(client, headers)
        events = await _stream(client, headers, conversation["id"], "read the file")
        types = [t for t, _ in events]
        assert "tool_call" in types
        assert "tool_result" in types

        # tool result persisted as a tool-role message
        listing = await client.get(
            f"/api/v1/conversations/{conversation['id']}/messages", headers=headers
        )
        tool_messages = [m for m in listing.json() if m["role"] == "tool"]
        assert len(tool_messages) == 1
        assert tool_messages[0]["tool_name"] == "document.extract"
        assert "extractable payload" in tool_messages[0]["content"]

        # final answer includes the scripted content
        assistant = [m for m in listing.json() if m["role"] == "assistant"][-1]
        assert "final answer" in assistant["content"]

        # tool executions audited
        from app.db.models import ToolExecution

        async with app.state.database.session() as session:
            rows = (
                (await session.execute(sa.select(ToolExecution))).scalars().all()
            )
        assert any(r.tool_key == "document.extract" and r.status.value == "succeeded" for r in rows)
