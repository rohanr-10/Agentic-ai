"""WebSocket endpoint for real-time task/conversation events.

Authentication: access token passed as a query parameter (browsers cannot
set Authorization headers on WebSocket connections); tokens are short-lived
so exposure in URLs is bounded — see docs/SECURITY.md.

Protocol (JSON):
    -> {"action": "subscribe",   "topics": ["task:<uuid>", ...]}
    -> {"action": "unsubscribe", "topics": [...]}
    <- {"topic": ..., "type": ..., "data": ..., "timestamp": ...}

Ownership is enforced per subscription: a topic may only be subscribed if
the underlying resource belongs to the authenticated user.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.api.deps import principal_from_token
from app.core.errors import AuthenticationError, NotFoundError

router = APIRouter(prefix="/ws", tags=["realtime"])


@router.websocket("")
async def websocket_endpoint(websocket: WebSocket, token: str = Query(default="")):
    await _authorize(websocket, token)


async def _authorize(websocket: WebSocket, token: str) -> None:
    if not token:
        await websocket.close(code=4401, reason="missing token")
        return
    try:
        principal = await principal_from_token(websocket.app, token)
    except AuthenticationError:
        await websocket.close(code=4401, reason="invalid token")
        return

    await websocket.accept()
    subscriptions: set[str] = set()

    try:
        bus = websocket.app.state.bus
        async with bus.subscribe([]) as subscription:
            receiver = asyncio.create_task(
                _receive(websocket, principal, subscriptions, subscription)
            )
            try:
                while True:
                    if receiver.done():
                        # client disconnected (or receiver crashed)
                        receiver.result() if not receiver.cancelled() else None
                        return
                    try:
                        event = await asyncio.wait_for(
                            subscription.queue.get(), timeout=0.2
                        )
                    except asyncio.TimeoutError:
                        continue
                    if event.topic in subscriptions:
                        await websocket.send_json(event.ws_encode())
            finally:
                receiver.cancel()
                try:
                    await receiver
                except (asyncio.CancelledError, WebSocketDisconnect):
                    pass
    except WebSocketDisconnect:
        return
    except Exception:
        try:
            await websocket.close(code=1011, reason="internal error")
        except Exception:  # pragma: no cover
            pass


async def _receive(websocket: WebSocket, principal, subscriptions: set[str], subscription) -> None:
    """Handle inbound control frames (subscribe/unsubscribe)."""
    while True:
        try:
            message = await websocket.receive_json()
        except WebSocketDisconnect:
            return
        action = message.get("action")
        topics = message.get("topics") or []
        if action == "subscribe":
            allowed: set[str] = set()
            for topic in topics:
                if await _may_subscribe(websocket.app, principal, topic):
                    allowed.add(topic)
                    subscription.topics.add(topic)  # live subscription on the bus
                else:
                    await websocket.send_json(
                        {"type": "error", "data": {"message": f"cannot subscribe to {topic}"}}
                    )
            subscriptions |= allowed
            await websocket.send_json(
                {"type": "subscribed", "data": {"topics": sorted(allowed)}}
            )
        elif action == "unsubscribe":
            subscriptions -= set(topics)
            for topic in topics:
                subscription.topics.discard(topic)
            await websocket.send_json(
                {"type": "unsubscribed", "data": {"topics": topics}}
            )


async def _may_subscribe(app, principal, topic: str) -> bool:
    """Ownership check for a topic."""
    from app.services.conversations import get_conversation
    from app.services.tasks import get_task

    kind, _, raw_id = topic.partition(":")
    try:
        resource_id = __import__("uuid").UUID(raw_id)
    except ValueError:
        return False
    try:
        async with app.state.database.session() as session:
            if kind == "task":
                await get_task(session, user_id=principal.user_id, task_id=resource_id)
            elif kind == "conversation":
                await get_conversation(
                    session, user_id=principal.user_id, conversation_id=resource_id
                )
            elif kind == "user":
                return resource_id == principal.user_id
            else:
                return False
        return True
    except NotFoundError:
        return False
