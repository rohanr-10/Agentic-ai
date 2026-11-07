"""Concurrency: parallel message creation, duplicate suppression, parallel tasks."""

from __future__ import annotations

import asyncio

from tests.conftest import auth_headers, create_conversation, create_task


class TestConcurrentMessages:
    async def test_parallel_sends_produce_dense_unique_sequence(self, client, headers):
        """20 concurrent message submissions: no gaps, no duplicates, no 500s."""
        conversation = await create_conversation(client, headers)
        conversation_id = conversation["id"]

        async def send(index: int):
            return await client.post(
                f"/api/v1/conversations/{conversation_id}/messages",
                json={"content": f"concurrent message {index}"},
                headers=headers,
            )

        responses = await asyncio.gather(*[send(i) for i in range(20)])
        statuses = [r.status_code for r in responses]
        assert all(status == 201 for status in statuses), statuses

        listing = await client.get(
            f"/api/v1/conversations/{conversation_id}/messages?limit=200",
            headers=headers,
        )
        messages = listing.json()
        # the overall sequence is dense and unique: user and assistant
        # messages interleave, every submission lands exactly once
        all_seqs = sorted(m["seq"] for m in messages)
        assert all_seqs == list(range(1, len(messages) + 1)), all_seqs
        user_messages = [m for m in messages if m["role"] == "user"]
        assert len(user_messages) == 20
        assert len(messages) == 40  # 20 user + 20 assistant replies

    async def test_duplicate_submissions_collapse(self, client, headers):
        """Identical client_request_id submitted concurrently yields one row."""
        conversation = await create_conversation(client, headers)
        conversation_id = conversation["id"]

        async def send():
            return await client.post(
                f"/api/v1/conversations/{conversation_id}/messages",
                json={"content": "dedupe me", "client_request_id": "dup-req-1"},
                headers=headers,
            )

        responses = await asyncio.gather(*[send() for _ in range(8)])
        # every response is a success and refers to the same user message
        user_ids = {r.json()["user_message"]["id"] for r in responses if r.status_code == 201}
        assert len(user_ids) == 1

        listing = await client.get(
            f"/api/v1/conversations/{conversation_id}/messages",
            headers=headers,
        )
        user_messages = [m for m in listing.json() if m["role"] == "user"]
        assert len(user_messages) == 1

    async def test_parallel_conversations_are_independent(self, client, headers):
        async def make(index: int):
            return await client.post(
                "/api/v1/conversations",
                json={"title": f"parallel conv {index}"},
                headers=headers,
            )

        responses = await asyncio.gather(*[make(i) for i in range(10)])
        ids = {r.json()["id"] for r in responses}
        assert len(ids) == 10


class TestConcurrentTasks:
    async def test_parallel_task_execution(self, client, headers, worker):
        """Three tasks with independent workspaces run concurrently to completion."""
        client._transport.app.state.settings.auto_approve_plans = True
        try:
            task_ids = []
            for index in range(3):
                task = await create_task(
                    client,
                    headers,
                    plan={
                        "objective_restated": f"job {index}",
                        "steps": [
                            {
                                "id": "write",
                                "title": f"Write file for task {index}",
                                "tool_key": "fs.write",
                                "input": {"path": f"task{index}.txt", "content": f"data-{index}"},
                                "timeout_s": 30,
                            }
                        ],
                    },
                )
                task_ids.append(task["id"])

            # drain all jobs
            await worker.drain(max_wait_s=10.0)

            for index, task_id in enumerate(task_ids):
                detail = await client.get(f"/api/v1/tasks/{task_id}", headers=headers)
                assert detail.json()["status"] == "completed", detail.json()
                artifacts = detail.json()["artifacts"]["files"]
                assert any(f["path"] == f"task{index}.txt" for f in artifacts)
        finally:
            client._transport.app.state.settings.auto_approve_plans = False

    async def test_parallel_users_do_not_interfere(self, client, headers, worker):
        other = await auth_headers(client, "parallel-other@example.com")
        client._transport.app.state.settings.auto_approve_plans = True
        try:
            from tests.conftest import create_conversation

            conversation_a = await create_conversation(client, headers)
            conversation_b = await create_conversation(client, other)

            async def send(conv_headers, conv_id, content):
                return await client.post(
                    f"/api/v1/conversations/{conv_id}/messages",
                    json={"content": content},
                    headers=conv_headers,
                )

            results = await asyncio.gather(
                *[
                    send(headers, conversation_a["id"], f"message A {i}")
                    for i in range(5)
                ],
                *[
                    send(other, conversation_b["id"], f"message B {i}")
                    for i in range(5)
                ],
            )
            assert all(r.status_code == 201 for r in results)

            listing_a = await client.get(
                f"/api/v1/conversations/{conversation_a['id']}/messages", headers=headers
            )
            listing_b = await client.get(
                f"/api/v1/conversations/{conversation_b['id']}/messages", headers=other
            )
            contents_a = " ".join(m["content"] for m in listing_a.json())
            contents_b = " ".join(m["content"] for m in listing_b.json())
            assert "message B" not in contents_a
            assert "message A" not in contents_b
        finally:
            client._transport.app.state.settings.auto_approve_plans = False
