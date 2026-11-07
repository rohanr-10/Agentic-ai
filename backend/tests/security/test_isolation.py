"""Security: cross-user isolation on every resource type."""

from __future__ import annotations

import pytest

from tests.conftest import PASSWORD, auth_headers, create_conversation, create_task


@pytest.fixture
async def two_users(client):
    alice = await auth_headers(client, "alice@example.com")
    bob = await auth_headers(client, "bob@example.com")
    return alice, bob


class TestConversationIsolation:
    async def test_bob_cannot_read_alices_conversation(self, client, two_users):
        alice, bob = two_users
        conversation = await create_conversation(client, alice, title="Alice's secret")
        response = await client.get(
            f"/api/v1/conversations/{conversation['id']}", headers=bob
        )
        assert response.status_code == 404

    async def test_bob_cannot_list_alices_conversations(self, client, two_users):
        alice, bob = two_users
        await create_conversation(client, alice, title="private stuff")
        listing = await client.get("/api/v1/conversations", headers=bob)
        assert listing.json() == []

    async def test_bob_cannot_write_messages(self, client, two_users):
        alice, bob = two_users
        conversation = await create_conversation(client, alice)
        response = await client.post(
            f"/api/v1/conversations/{conversation['id']}/messages",
            json={"content": "intrusion"},
            headers=bob,
        )
        assert response.status_code == 404

    async def test_bob_cannot_delete_or_archive(self, client, two_users):
        alice, bob = two_users
        conversation = await create_conversation(client, alice)
        assert (
            await client.delete(f"/api/v1/conversations/{conversation['id']}", headers=bob)
        ).status_code == 404
        assert (
            await client.post(
                f"/api/v1/conversations/{conversation['id']}/archive", headers=bob
            )
        ).status_code == 404


class TestFileIsolation:
    async def test_bob_cannot_download_alices_file(self, client, two_users):
        alice, bob = two_users
        upload = await client.post(
            "/api/v1/files",
            files={"file": ("secret.txt", b"alice secrets", "text/plain")},
            headers=alice,
        )
        file_id = upload.json()["id"]

        assert (await client.get(f"/api/v1/files/{file_id}", headers=bob)).status_code == 404
        download = await client.get(f"/api/v1/files/{file_id}/download", headers=bob)
        assert download.status_code == 404
        assert download.content != b"alice secrets"
        assert (
            await client.delete(f"/api/v1/files/{file_id}", headers=bob)
        ).status_code == 404

    async def test_bob_cannot_extract_via_tool(self, client, two_users, worker):
        alice, bob = two_users
        upload = await client.post(
            "/api/v1/files",
            files={"file": ("private.txt", b"alice private data", "text/plain")},
            headers=alice,
        )
        await worker.drain(max_wait_s=2.0)
        response = await client.post(
            "/api/v1/tools/document.extract/execute",
            json={"input": {"file_id": upload.json()["id"]}},
            headers=bob,
        )
        assert response.status_code in (400, 404)


class TestTaskIsolation:
    async def test_bob_cannot_control_alices_task(self, client, two_users):
        alice, bob = two_users
        task = await create_task(client, alice)
        task_id = task["id"]
        for action in ("pause", "resume", "cancel"):
            response = await client.post(
                f"/api/v1/tasks/{task_id}/{action}", headers=bob
            )
            assert response.status_code == 404
        assert (await client.get(f"/api/v1/tasks/{task_id}", headers=bob)).status_code == 404
        assert (
            await client.get(f"/api/v1/tasks/{task_id}/executions", headers=bob)
        ).status_code == 404

    async def test_bob_cannot_approve_alices_task(self, client, two_users, worker):
        alice, bob = two_users
        task = await create_task(client, alice)
        await worker.drain(max_wait_s=3.0)
        approvals = await client.get("/api/v1/approvals?status=pending", headers=alice)
        approval_id = approvals.json()["items"][0]["id"]
        response = await client.post(
            f"/api/v1/approvals/{approval_id}",
            json={"decision": "approve"},
            headers=bob,
        )
        assert response.status_code == 404
        # alice can still decide her own approval
        assert (
            await client.post(
                f"/api/v1/approvals/{approval_id}",
                json={"decision": "approve"},
                headers=alice,
            )
        ).status_code == 200


class TestMemoryIsolation:
    async def test_bob_cannot_read_or_verify_alices_memory(self, client, two_users):
        alice, bob = two_users
        created = await client.post(
            "/api/v1/memory",
            json={"category": "semantic", "content": "alice birthday jan 1"},
            headers=alice,
        )
        memory_id = created.json()["id"]
        assert (await client.get(f"/api/v1/memory/{memory_id}", headers=bob)).status_code == 404
        assert (
            await client.post(
                f"/api/v1/memory/{memory_id}/verify",
                json={"evidence": "guessed"},
                headers=bob,
            )
        ).status_code == 404
        # bob's search does not surface alice's memory
        search = await client.post(
            "/api/v1/memory/search", json={"query": "alice birthday"}, headers=bob
        )
        assert search.json() == []

    async def test_conversation_scoped_memory_not_leaked_cross_user(self, client, two_users):
        alice, bob = two_users
        conversation = await create_conversation(client, alice)
        await client.post(
            "/api/v1/memory",
            json={
                "category": "conversational",
                "scope": "conversation",
                "scope_id": conversation["id"],
                "content": "project alpha details",
            },
            headers=alice,
        )
        # even knowing the conversation id, bob's recall finds nothing
        search = await client.post(
            "/api/v1/memory/search",
            json={"query": "project alpha", "conversation_id": conversation["id"]},
            headers=bob,
        )
        assert search.json() == []


class TestNotificationIsolation:
    async def test_bob_sees_only_his_notifications(self, client, two_users):
        alice, bob = two_users
        await create_task(client, alice)  # generates approval notifications later
        alice_notifications = await client.get("/api/v1/notifications", headers=alice)
        bob_notifications = await client.get("/api/v1/notifications", headers=bob)
        # bob's list must not contain alice's items
        alice_ids = {n["id"] for n in alice_notifications.json()["items"]}
        bob_ids = {n["id"] for n in bob_notifications.json()["items"]}
        assert not alice_ids & bob_ids
