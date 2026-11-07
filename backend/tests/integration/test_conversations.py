"""Integration: conversations, messages, search, regenerate."""

from __future__ import annotations

import httpx
import pytest

from tests.conftest import auth_headers, create_conversation


async def _send(client, headers, conversation_id, content, **overrides):
    payload = {"content": content}
    payload.update(overrides)
    return await client.post(
        f"/api/v1/conversations/{conversation_id}/messages", json=payload, headers=headers
    )


class TestConversationCrud:
    async def test_create_list_get_update_delete(self, client, headers):
        created = await create_conversation(client, headers, title="Lifecycle")
        assert created["status"] == "active"
        assert created["message_count"] == 0

        listing = await client.get("/api/v1/conversations", headers=headers)
        assert listing.status_code == 200
        assert any(c["id"] == created["id"] for c in listing.json())

        detail = await client.get(f"/api/v1/conversations/{created['id']}", headers=headers)
        assert detail.status_code == 200

        renamed = await client.patch(
            f"/api/v1/conversations/{created['id']}",
            json={"title": "Renamed"},
            headers=headers,
        )
        assert renamed.json()["title"] == "Renamed"

        deleted = await client.delete(
            f"/api/v1/conversations/{created['id']}", headers=headers
        )
        assert deleted.status_code == 204
        gone = await client.get(f"/api/v1/conversations/{created['id']}", headers=headers)
        assert gone.status_code == 404

    async def test_pagination(self, client, headers):
        for i in range(7):
            await create_conversation(client, headers, title=f"Page {i}")
        page1 = await client.get(
            "/api/v1/conversations?limit=3&offset=0", headers=headers
        )
        page2 = await client.get(
            "/api/v1/conversations?limit=3&offset=3", headers=headers
        )
        assert len(page1.json()) == 3
        assert len(page2.json()) == 3
        ids1 = {c["id"] for c in page1.json()}
        ids2 = {c["id"] for c in page2.json()}
        assert not ids1 & ids2

    async def test_title_search(self, client, headers):
        await create_conversation(client, headers, title="Quarterly report")
        await create_conversation(client, headers, title="Grocery list")
        response = await client.get(
            "/api/v1/conversations?q=quarterly", headers=headers
        )
        assert response.status_code == 200
        results = response.json()
        assert len(results) == 1
        assert results[0]["title"] == "Quarterly report"

    async def test_archive_and_unarchive(self, client, headers):
        conversation = await create_conversation(client, headers)
        archived = await client.post(
            f"/api/v1/conversations/{conversation['id']}/archive", headers=headers
        )
        assert archived.json()["status"] == "archived"

        # archived conversations reject new messages
        blocked = await _send(client, headers, conversation["id"], "hello?")
        assert blocked.status_code == 400

        restored = await client.post(
            f"/api/v1/conversations/{conversation['id']}/unarchive", headers=headers
        )
        assert restored.json()["status"] == "active"
        ok = await _send(client, headers, conversation["id"], "hello again")
        assert ok.status_code == 201

    async def test_archived_filter(self, client, headers):
        conversation = await create_conversation(client, headers)
        await client.post(
            f"/api/v1/conversations/{conversation['id']}/archive", headers=headers
        )
        active = await client.get(
            "/api/v1/conversations?status=active", headers=headers
        )
        archived = await client.get(
            "/api/v1/conversations?status=archived", headers=headers
        )
        assert all(c["id"] != conversation["id"] for c in active.json())
        assert any(c["id"] == conversation["id"] for c in archived.json())


class TestMessages:
    async def test_send_and_list_messages(self, client, headers):
        conversation = await create_conversation(client, headers)
        first = await _send(client, headers, conversation["id"], "first message")
        assert first.status_code == 201
        body = first.json()
        assert body["user_message"]["seq"] == 1
        assert body["user_message"]["role"] == "user"
        assert body["assistant_message"]["role"] == "assistant"
        assert body["assistant_message"]["seq"] == 2
        assert body["assistant_message"]["content"].startswith("[test-adapter]")

        second = await _send(client, headers, conversation["id"], "second message")
        assert second.json()["user_message"]["seq"] == 3

        listing = await client.get(
            f"/api/v1/conversations/{conversation['id']}/messages", headers=headers
        )
        messages = listing.json()
        assert [m["seq"] for m in messages] == [1, 2, 3, 4]
        # timestamps preserved and ordered
        assert messages[0]["created_at"] <= messages[-1]["created_at"]

    async def test_message_search(self, client, headers):
        conversation = await create_conversation(client, headers)
        await _send(client, headers, conversation["id"], "the quick brown fox")
        await _send(client, headers, conversation["id"], "lazy dogs sleep all day")
        response = await client.get(
            f"/api/v1/conversations/{conversation['id']}/search?q=brown+fox",
            headers=headers,
        )
        assert response.status_code == 200
        hits = response.json()
        # the user message matches; the test adapter echoes the prompt so the
        # assistant reply may match too
        user_hits = [h for h in hits if h["role"] == "user"]
        assert len(user_hits) == 1
        assert "brown fox" in user_hits[0]["content_snippet"]
        # no hits for the other conversation content
        none = await client.get(
            f"/api/v1/conversations/{conversation['id']}/search?q=zebra",
            headers=headers,
        )
        assert none.json() == []

    async def test_edit_user_message(self, client, headers):
        conversation = await create_conversation(client, headers)
        sent = await _send(client, headers, conversation["id"], "typo mesage")
        message_id = sent.json()["user_message"]["id"]
        edited = await client.patch(
            f"/api/v1/conversations/{conversation['id']}/messages/{message_id}",
            json={"content": "fixed message"},
            headers=headers,
        )
        assert edited.status_code == 200
        assert edited.json()["content"] == "fixed message"
        assert edited.json()["edited_at"] is not None
        assert edited.json()["meta"]["edited"] is True

    async def test_edit_assistant_message_forbidden(self, client, headers):
        conversation = await create_conversation(client, headers)
        sent = await _send(client, headers, conversation["id"], "hi")
        message_id = sent.json()["assistant_message"]["id"]
        edited = await client.patch(
            f"/api/v1/conversations/{conversation['id']}/messages/{message_id}",
            json={"content": "rewrite history"},
            headers=headers,
        )
        assert edited.status_code == 400

    async def test_regenerate_preserves_history(self, client, headers):
        conversation = await create_conversation(client, headers)
        sent = await _send(client, headers, conversation["id"], "give me an answer")
        original = sent.json()["assistant_message"]

        regenerated = await client.post(
            f"/api/v1/conversations/{conversation['id']}/messages/{original['id']}/regenerate",
            headers=headers,
        )
        assert regenerated.status_code == 200
        new_message = regenerated.json()["assistant_message"]
        assert new_message["id"] != original["id"]
        assert new_message["parent_message_id"] == original["parent_message_id"]

        # old version kept, flagged inactive
        full = await client.get(
            f"/api/v1/conversations/{conversation['id']}/messages?include_inactive=true",
            headers=headers,
        )
        by_id = {m["id"]: m for m in full.json()}
        assert by_id[original["id"]]["is_active"] is False
        assert by_id[new_message["id"]]["is_active"] is True

        # default listing hides the inactive one
        active_only = await client.get(
            f"/api/v1/conversations/{conversation['id']}/messages", headers=headers
        )
        assert original["id"] not in {m["id"] for m in active_only.json()}

    async def test_duplicate_client_request_id_is_idempotent(self, client, headers):
        conversation = await create_conversation(client, headers)
        payload = {"content": "only once", "client_request_id": "req-123"}
        first = await _send(client, headers, conversation["id"], **payload)
        second = await _send(client, headers, conversation["id"], **payload)
        assert first.status_code == second.status_code == 201
        # same user message returned; no duplicate row
        assert first.json()["user_message"]["id"] == second.json()["user_message"]["id"]
        listing = await client.get(
            f"/api/v1/conversations/{conversation['id']}/messages", headers=headers
        )
        user_messages = [m for m in listing.json() if m["role"] == "user"]
        assert len(user_messages) == 1

    async def test_attachment_association(self, client, headers):
        upload = await client.post(
            "/api/v1/files",
            files={"file": ("notes.txt", b"attached content", "text/plain")},
            headers=headers,
        )
        file_id = upload.json()["id"]

        conversation = await create_conversation(client, headers)
        sent = await _send(
            client, headers, conversation["id"], "summarize this", attachments=[file_id]
        )
        assert sent.status_code == 201
        attachments = sent.json()["user_message"]["attachments"]
        assert len(attachments) == 1
        assert attachments[0]["file_id"] == file_id
        assert attachments[0]["filename"] == "notes.txt"

    async def test_attachment_must_be_owned(self, client, headers):
        other_headers = await auth_headers(client, "other-attacher@example.com")
        upload = await client.post(
            "/api/v1/files",
            files={"file": ("other.txt", b"other user data", "text/plain")},
            headers=other_headers,
        )
        other_file_id = upload.json()["id"]

        conversation = await create_conversation(client, headers)
        response = await _send(
            client, headers, conversation["id"], "steal this", attachments=[other_file_id]
        )
        assert response.status_code == 404
