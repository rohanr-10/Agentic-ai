"""Conversation & message management plus the chat pipeline.

Transaction discipline: every public entry point owns its transaction and
commits explicitly (message writes are transactional — user message,
assistant message and tool messages all commit together or not at all).

Concurrency discipline:
* message ordering uses ``seq`` with a UNIQUE(conversation_id, seq)
  constraint; concurrent writers retry allocation on conflict;
* duplicate submissions are deduplicated through
  UNIQUE(conversation_id, client_request_id) — the original message is
  returned instead of a second copy.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError, RequestValidationError
from app.core.utils import new_uuid, utcnow
from app.db.enums import ConversationStatus, MessageRole, OperatingMode
from app.db.models import Attachment, Conversation, FileRecord, Message
from app.events.bus import EventBus
from app.gateway.base import InferenceMessage, ToolSpec
from app.services import audit

logger = structlog.stdlib.get_logger("app.conversations")

# Concurrent-writer retry budget: under heavy contention (many parallel
# submissions on a loaded machine) a request can lose several seq races in
# a row; the budget plus randomized yield keeps the invariant "dense, gapless
# ordering" instead of surfacing conflicts to clients.
_SEQ_RETRIES = 32


# ---------------------------------------------------------------------------
# Conversation CRUD
# ---------------------------------------------------------------------------

async def get_conversation(
    session: AsyncSession, *, user_id: UUID, conversation_id: UUID
) -> Conversation:
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user_id:
        raise NotFoundError("conversation not found")
    return conversation


async def create_conversation(
    session: AsyncSession, *, user_id: UUID, payload
) -> Conversation:
    conversation = Conversation(
        id=new_uuid(),
        user_id=user_id,
        title=payload.title.strip() or "New conversation",
        mode=payload.mode,
        system_prompt=payload.system_prompt,
        model_config_id=payload.model_config_id,
    )
    session.add(conversation)
    await audit.record(
        session,
        event_type=audit.CONVERSATION_CREATED,
        user_id=user_id,
        resource_type="conversation",
        resource_id=conversation.id,
    )
    await session.commit()
    await session.refresh(conversation)
    return conversation


async def update_conversation(
    session: AsyncSession, *, user_id: UUID, conversation_id: UUID, payload
) -> Conversation:
    conversation = await get_conversation(session, user_id=user_id, conversation_id=conversation_id)
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(conversation, field, value)
    conversation.updated_at = utcnow()
    await audit.record(
        session,
        event_type=audit.CONVERSATION_UPDATED,
        user_id=user_id,
        resource_type="conversation",
        resource_id=conversation_id,
        details={"fields": sorted(changes.keys())},
    )
    await session.commit()
    await session.refresh(conversation)
    return conversation


async def set_archived(
    session: AsyncSession, *, user_id: UUID, conversation_id: UUID, archived: bool
) -> Conversation:
    conversation = await get_conversation(session, user_id=user_id, conversation_id=conversation_id)
    conversation.status = (
        ConversationStatus.ARCHIVED if archived else ConversationStatus.ACTIVE
    )
    conversation.updated_at = utcnow()
    await session.commit()
    await session.refresh(conversation)
    return conversation


async def delete_conversation(
    session: AsyncSession, *, user_id: UUID, conversation_id: UUID
) -> None:
    conversation = await get_conversation(session, user_id=user_id, conversation_id=conversation_id)
    await session.delete(conversation)  # cascades to messages/attachments
    await audit.record(
        session,
        event_type=audit.CONVERSATION_DELETED,
        user_id=user_id,
        resource_type="conversation",
        resource_id=conversation_id,
    )
    await session.commit()


async def list_conversations(
    session: AsyncSession,
    *,
    user_id: UUID,
    limit: int = 50,
    offset: int = 0,
    status: ConversationStatus | None = None,
    q: str | None = None,
) -> tuple[list[Conversation], int]:
    query = sa.select(Conversation).where(Conversation.user_id == user_id)
    count_query = sa.select(sa.func.count()).select_from(Conversation).where(
        Conversation.user_id == user_id
    )
    if status is not None:
        query = query.where(Conversation.status == status)
        count_query = count_query.where(Conversation.status == status)
    if q:
        like = f"%{_escape_like(q)}%"
        query = query.where(Conversation.title.ilike(like))
        count_query = count_query.where(Conversation.title.ilike(like))
    total = (await session.execute(count_query)).scalar_one()
    rows = (
        (
            await session.execute(
                query.order_by(Conversation.updated_at.desc()).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return list(rows), total


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

async def _owned_files(
    session: AsyncSession, *, user_id: UUID, file_ids: list[UUID]
) -> list[FileRecord]:
    records: list[FileRecord] = []
    for file_id in file_ids:
        record = await session.get(FileRecord, file_id)
        if record is None or record.user_id != user_id:
            raise NotFoundError(f"file not found: {file_id}")
        records.append(record)
    return records


async def allocate_seq(session: AsyncSession, conversation_id: UUID) -> int:
    return (await session.scalar(
        sa.select(sa.func.max(Message.seq)).where(Message.conversation_id == conversation_id)
    ) or 0) + 1


async def create_message(
    session: AsyncSession,
    *,
    conversation: Conversation,
    role: MessageRole,
    content: str,
    client_request_id: str | None = None,
    parent_message_id: UUID | None = None,
    model_config_id: UUID | None = None,
    token_count: int | None = None,
    tool_call_id: str | None = None,
    tool_name: str | None = None,
    meta: dict | None = None,
    attachments: list[FileRecord] | None = None,
) -> Message:
    """Insert a message with idempotency + ordering under concurrency.

    All identifiers are snapshotted up-front: the seq-conflict retry path
    rolls the session back, which expires ORM instances — expired-attribute
    access would then attempt sync IO and fail.  Only plain values are used
    after any rollback.
    """
    conversation_id = conversation.id
    attachment_file_ids = [record.id for record in (attachments or [])]

    if client_request_id:
        existing = await session.scalar(
            sa.select(Message).where(
                Message.conversation_id == conversation_id,
                Message.client_request_id == client_request_id,
            )
        )
        if existing is not None:
            return existing  # idempotent duplicate submission

    for attempt in range(_SEQ_RETRIES):
        seq = await allocate_seq(session, conversation_id)
        message = Message(
            id=new_uuid(),
            conversation_id=conversation_id,
            seq=seq,
            role=role,
            content=content,
            client_request_id=client_request_id if role == MessageRole.USER else None,
            parent_message_id=parent_message_id,
            is_active=True,
            model_config_id=model_config_id,
            token_count=token_count,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            meta=meta or {},
        )
        # Savepoint isolation: a seq/client_request_id conflict rolls back
        # only this insert.  A full session rollback would expire every ORM
        # instance in the session (breaking later attribute access) and
        # discard the caller's pending transactional work.
        try:
            async with session.begin_nested():
                session.add(message)
        except sa.exc.IntegrityError:
            # End the read transaction: under SQLite-WAL the retrying session
            # must see the winner's committed rows (a stale snapshot would
            # reallocate the same sequence number forever).
            await session.rollback()
            if client_request_id:
                existing = await session.scalar(
                    sa.select(Message).where(
                        Message.conversation_id == conversation_id,
                        Message.client_request_id == client_request_id,
                    )
                )
                if existing is not None:
                    return existing
            if attempt == _SEQ_RETRIES - 1:
                raise ConflictError("message sequence conflict")
            # randomized yield: racing writers back off instead of livelocking
            await asyncio.sleep(random.uniform(0.001, 0.01))
            continue
        for file_id in attachment_file_ids:
            session.add(Attachment(message_id=message.id, file_id=file_id))
        await session.execute(
            sa.update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(last_message_at=utcnow(), updated_at=utcnow())
        )
        return message
    raise ConflictError("message sequence conflict")


async def list_messages(
    session: AsyncSession,
    *,
    user_id: UUID,
    conversation_id: UUID,
    limit: int = 50,
    offset: int = 0,
    include_inactive: bool = False,
) -> tuple[list[Message], int]:
    await get_conversation(session, user_id=user_id, conversation_id=conversation_id)
    query = sa.select(Message).where(Message.conversation_id == conversation_id)
    count_query = (
        sa.select(sa.func.count()).select_from(Message).where(Message.conversation_id == conversation_id)
    )
    if not include_inactive:
        query = query.where(Message.is_active.is_(True))
        count_query = count_query.where(Message.is_active.is_(True))
    total = (await session.execute(count_query)).scalar_one()
    rows = (
        (await session.execute(query.order_by(Message.seq).limit(limit).offset(offset)))
        .scalars()
        .all()
    )
    return list(rows), total


async def get_message(
    session: AsyncSession, *, user_id: UUID, conversation_id: UUID, message_id: UUID
) -> Message:
    await get_conversation(session, user_id=user_id, conversation_id=conversation_id)
    message = await session.get(Message, message_id)
    if message is None or message.conversation_id != conversation_id:
        raise NotFoundError("message not found")
    return message


async def edit_message(
    session: AsyncSession,
    *,
    user_id: UUID,
    conversation_id: UUID,
    message_id: UUID,
    content: str,
) -> Message:
    message = await get_message(
        session, user_id=user_id, conversation_id=conversation_id, message_id=message_id
    )
    if message.role != MessageRole.USER:
        raise RequestValidationError("only user messages can be edited")
    message.content = content
    message.edited_at = utcnow()
    meta = dict(message.meta or {})
    meta["edited"] = True
    message.meta = meta
    await session.commit()
    await session.refresh(message)
    return message


async def search_messages(
    session: AsyncSession,
    *,
    user_id: UUID,
    conversation_id: UUID,
    query: str,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Message], int]:
    await get_conversation(session, user_id=user_id, conversation_id=conversation_id)
    like = f"%{_escape_like(query)}%"
    conditions = [
        Message.conversation_id == conversation_id,
        Message.is_active.is_(True),
        Message.content.ilike(like),
    ]
    rows = (
        (
            await session.execute(
                sa.select(Message)
                .where(*conditions)
                .order_by(Message.seq.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    total = (
        await session.execute(
            sa.select(sa.func.count()).select_from(Message).where(*conditions)
        )
    ).scalar_one()
    return list(rows), total


# ---------------------------------------------------------------------------
# Chat pipeline
# ---------------------------------------------------------------------------

def _chat_tool_specs(registry) -> list[ToolSpec]:
    return [
        ToolSpec(
            name=tool.key,
            description=tool.description,
            input_schema=tool.input_json_schema(),
        )
        for tool in registry.chat_tools()
    ]


async def _execute_chat_tool_call(
    tool_executor, call, user_id: UUID
) -> dict[str, Any]:
    from app.tools.base import ToolContext

    ctx = ToolContext(user_id=user_id, workspace=None, origin="chat")
    outcome = await tool_executor.execute(
        tool_key=call.name, input_data=call.arguments, ctx=ctx
    )
    return outcome.output


async def send_message(
    session: AsyncSession,
    settings: Settings,
    *,
    user_id: UUID,
    conversation_id: UUID,
    payload,
    manager,
    registry,
    tool_executor,
) -> dict[str, Message]:
    """Non-streaming chat pipeline with a bounded tool-call loop."""
    conversation = await get_conversation(session, user_id=user_id, conversation_id=conversation_id)
    if conversation.status == ConversationStatus.ARCHIVED:
        raise RequestValidationError("conversation is archived")

    files = await _owned_files(session, user_id=user_id, file_ids=payload.attachments)
    user_message = await create_message(
        session,
        conversation=conversation,
        role=MessageRole.USER,
        content=payload.content,
        client_request_id=payload.client_request_id,
        attachments=files,
    )
    # commit BEFORE any tool execution: the tool executor writes its audit
    # rows on a separate connection, and SQLite allows one writer at a time
    await session.commit()
    # create_message may have rolled back (seq race) which expires ORM
    # instances — snapshot plain ids and reload the conversation
    user_message_id = user_message.id
    await session.refresh(conversation)

    from app.services.context import (
        assemble_context,
        conversation_attachment_summary,
    )

    assembled = await assemble_context(
        session, settings, conversation, memory_query=payload.content
    )
    extra = conversation_attachment_summary(files)
    if extra:
        assembled.messages.append(InferenceMessage(role="user", content=extra))

    tool_messages: list[Message] = []
    rounds = 0
    request_messages = list(assembled.messages)
    tools = _chat_tool_specs(registry)

    while True:
        from app.gateway.base import InferenceRequest

        request = InferenceRequest(
            messages=request_messages,
            tools=tools,
            max_tokens=None,
        )
        response = await manager.generate(
            session, request, model_config_id=payload.model_config_id or conversation.model_config_id
        )

        if response.has_tool_calls and rounds < settings.chat_max_tool_rounds:
            rounds += 1
            # persist the assistant's tool-call turn for provenance
            await create_message(
                session,
                conversation=conversation,
                role=MessageRole.ASSISTANT,
                content=response.content,
                model_config_id=payload.model_config_id or conversation.model_config_id,
                meta={
                    "tool_calls": [
                        {"id": c.id, "name": c.name, "arguments": c.arguments}
                        for c in response.tool_calls
                    ]
                },
            )
            # release the write lock before tools run (see comment above)
            await session.commit()
            for call in response.tool_calls:
                try:
                    output = await _execute_chat_tool_call(tool_executor, call, user_id)
                    error = None
                except Exception as exc:
                    output = {"error": f"{type(exc).__name__}: {exc}"}
                    error = str(exc)
                content = json.dumps(output, default=str)[:20_000]
                tool_message = await create_message(
                    session,
                    conversation=conversation,
                    role=MessageRole.TOOL,
                    content=content,
                    tool_call_id=call.id,
                    tool_name=call.name,
                    meta={"tool_error": True} if error else {},
                )
                tool_messages.append(tool_message)
                request_messages.append(
                    InferenceMessage(
                        role="assistant",
                        content=response.content,
                        tool_calls=response.tool_calls,
                    )
                )
                request_messages.append(
                    InferenceMessage(
                        role="tool",
                        content=content,
                        tool_call_id=call.id,
                        name=call.name,
                    )
                )
            continue

        if response.has_tool_calls:
            # budget exhausted: record refusal instead of looping forever
            response_content = response.content + "\n[tool budget reached; no further tool calls executed]"
            response_tool_calls = []
        else:
            response_content = response.content
            response_tool_calls = []

        assistant_message = await create_message(
            session,
            conversation=conversation,
            role=MessageRole.ASSISTANT,
            content=response_content,
            parent_message_id=user_message_id,
            model_config_id=payload.model_config_id or conversation.model_config_id,
            token_count=(
                response.usage.completion_tokens if response.usage else None
            ),
            meta={
                "model": response.model,
                "adapter": response.adapter,
                "finish_reason": response.finish_reason,
                "tool_rounds": rounds,
                "usage": (
                    {
                        "prompt_tokens": response.usage.prompt_tokens,
                        "completion_tokens": response.usage.completion_tokens,
                        "estimated": response.usage.estimated,
                    }
                    if response.usage
                    else None
                ),
            },
        )
        await session.commit()
        # a rollback inside a later create_message may have expired the
        # earlier instances; reload so API serializers can read attributes
        await session.refresh(user_message)
        return {
            "user_message": user_message,
            "assistant_message": assistant_message,
            "tool_messages": tool_messages,
        }


async def stream_chat(
    database,
    settings: Settings,
    bus: EventBus,
    *,
    user_id: UUID,
    conversation_id: UUID,
    payload,
    manager,
    registry,
    tool_executor,
) -> AsyncIterator[dict]:
    """Streaming chat pipeline (SSE-shaped dict events).

    Events: message.created, delta, tool_call, tool_result, message.completed,
    done, error.  The assistant message is persisted when the stream ends
    (including on client disconnect — marked ``interrupted``).
    """
    from app.gateway.base import InferenceRequest
    from app.services.context import (
        assemble_context,
        conversation_attachment_summary,
    )

    assistant_message = None
    accumulated: list[str] = []
    rounds = 0

    async with database.session() as session:
        conversation = await get_conversation(
            session, user_id=user_id, conversation_id=conversation_id
        )
        if conversation.status == ConversationStatus.ARCHIVED:
            yield {"type": "error", "data": {"message": "conversation is archived"}}
            return
        files = await _owned_files(session, user_id=user_id, file_ids=payload.attachments)
        user_message = await create_message(
            session,
            conversation=conversation,
            role=MessageRole.USER,
            content=payload.content,
            client_request_id=payload.client_request_id,
            attachments=files,
        )
        await session.commit()
        user_message_id = user_message.id
        yield {
            "type": "message.created",
            "data": {"message_id": str(user_message_id), "role": "user", "seq": user_message.seq},
        }
        await session.refresh(conversation)  # create_message may have rolled back
        await bus.publish(
            f"conversation:{conversation_id}",
            "message.created",
            {"message_id": str(user_message.id), "role": "user"},
        )

        assembled = await assemble_context(
            session, settings, conversation, memory_query=payload.content
        )
        extra = conversation_attachment_summary(files)
        if extra:
            assembled.messages.append(InferenceMessage(role="user", content=extra))
        request_messages = list(assembled.messages)
        tools = _chat_tool_specs(registry)
        model_config_id = payload.model_config_id or conversation.model_config_id
        error_event: dict | None = None

        try:
            while True:
                request = InferenceRequest(messages=request_messages, tools=tools)
                finish_reason = "stop"
                usage = None
                round_content: list[str] = []
                round_tool_calls: list = []

                async for event in manager.stream(
                    session, request, model_config_id=model_config_id
                ):
                    if event.kind == "delta":
                        accumulated.append(event.text or "")
                        round_content.append(event.text or "")
                        yield {"type": "delta", "data": {"text": event.text or ""}}
                    elif event.kind == "tool_call" and event.tool_call is not None:
                        round_tool_calls.append(event.tool_call)
                    elif event.kind == "done":
                        finish_reason = event.finish_reason or "stop"
                        usage = event.usage
                    elif event.kind == "error":
                        error_event = {"type": "error", "data": {"message": event.error}}
                        break
                if error_event is not None:
                    break

                if round_tool_calls and rounds < settings.chat_max_tool_rounds:
                    rounds += 1
                    await create_message(
                        session,
                        conversation=conversation,
                        role=MessageRole.ASSISTANT,
                        content="".join(round_content),
                        model_config_id=model_config_id,
                        meta={
                            "tool_calls": [
                                {"id": c.id, "name": c.name, "arguments": c.arguments}
                                for c in round_tool_calls
                            ]
                        },
                    )
                    # release the SQLite write lock before tools run
                    await session.commit()
                    request_messages.append(
                        InferenceMessage(
                            role="assistant",
                            content="".join(round_content),
                            tool_calls=round_tool_calls,
                        )
                    )
                    for call in round_tool_calls:
                        yield {
                            "type": "tool_call",
                            "data": {"id": call.id, "name": call.name},
                        }
                        try:
                            output = await _execute_chat_tool_call(
                                tool_executor, call, user_id
                            )
                        except Exception as exc:
                            output = {"error": f"{type(exc).__name__}: {exc}"}
                        content = json.dumps(output, default=str)[:20_000]
                        tool_message = await create_message(
                            session,
                            conversation=conversation,
                            role=MessageRole.TOOL,
                            content=content,
                            tool_call_id=call.id,
                            tool_name=call.name,
                        )
                        await session.commit()
                        yield {
                            "type": "tool_result",
                            "data": {
                                "id": call.id,
                                "name": call.name,
                                "message_id": str(tool_message.id),
                            },
                        }
                        request_messages.append(
                            InferenceMessage(
                                role="tool",
                                content=content,
                                tool_call_id=call.id,
                                name=call.name,
                            )
                        )
                    continue

                # final assistant message
                interrupted = error_event is not None
                assistant_message = await create_message(
                    session,
                    conversation=conversation,
                    role=MessageRole.ASSISTANT,
                    content="".join(accumulated) or ("" if interrupted else "(empty response)"),
                    parent_message_id=user_message_id,
                    model_config_id=model_config_id,
                    token_count=usage.completion_tokens if usage else None,
                    meta={
                        "streamed": True,
                        "interrupted": interrupted,
                        "finish_reason": finish_reason,
                        "tool_rounds": rounds,
                        "usage": (
                            {
                                "prompt_tokens": usage.prompt_tokens,
                                "completion_tokens": usage.completion_tokens,
                                "estimated": usage.estimated,
                            }
                            if usage
                            else None
                        ),
                    },
                )
                await session.commit()
                break

        except (asyncio.CancelledError, GeneratorExit):
            # client disconnected mid-stream: persist what was generated,
            # clearly marked as interrupted (history is never silently lost)
            try:
                if accumulated and assistant_message is None:
                    async with database.session() as rescue:
                        conversation = await get_conversation(
                            rescue, user_id=user_id, conversation_id=conversation_id
                        )
                        await create_message(
                            rescue,
                            conversation=conversation,
                            role=MessageRole.ASSISTANT,
                            content="".join(accumulated),
                            parent_message_id=(
                                user_message_id if user_message is not None else None
                            ),
                            meta={"streamed": True, "interrupted": True},
                        )
                        await rescue.commit()
                        logger.info(
                            "chat_stream_interrupted_partial_persisted",
                            conversation_id=str(conversation_id),
                        )
            except Exception:  # pragma: no cover - best effort on disconnect
                logger.exception(
                    "chat_stream_interrupted_persist_failed",
                    conversation_id=str(conversation_id),
                )
            raise
        except Exception as exc:
            logger.exception("chat_stream_failed", conversation_id=str(conversation_id))
            from app.core.errors import AppError

            message = exc.message if isinstance(exc, AppError) else "internal error during generation"
            yield {"type": "error", "data": {"message": message}}
            return

        if assistant_message is not None:
            yield {
                "type": "message.completed",
                "data": {
                    "message_id": str(assistant_message.id),
                    "seq": assistant_message.seq,
                    "role": "assistant",
                },
            }
            await bus.publish(
                f"conversation:{conversation_id}",
                "message.completed",
                {"message_id": str(assistant_message.id)},
            )
        if error_event is not None:
            yield error_event
        yield {"type": "done", "data": {}}


async def regenerate_response(
    session: AsyncSession,
    settings: Settings,
    *,
    user_id: UUID,
    conversation_id: UUID,
    message_id: UUID,
    manager,
    registry,
    tool_executor,
) -> dict[str, Message]:
    """Regenerate an assistant response (history is preserved)."""
    message = await get_message(
        session, user_id=user_id, conversation_id=conversation_id, message_id=message_id
    )
    if message.role != MessageRole.ASSISTANT or not message.is_active:
        raise RequestValidationError("only the active assistant message can be regenerated")

    conversation = await get_conversation(
        session, user_id=user_id, conversation_id=conversation_id
    )
    message.is_active = False
    message.meta = {**(message.meta or {}), "superseded_by_regeneration": True}

    from app.services.context import assemble_context

    parent = (
        await session.get(Message, message.parent_message_id)
        if message.parent_message_id
        else None
    )
    assembled = await assemble_context(
        session,
        settings,
        conversation,
        before_seq=message.seq,
        memory_query=parent.content if parent else None,
    )
    from app.gateway.base import InferenceRequest

    request = InferenceRequest(messages=assembled.messages, tools=_chat_tool_specs(registry))
    response = await manager.generate(
        session, request, model_config_id=conversation.model_config_id
    )
    new_message = await create_message(
        session,
        conversation=conversation,
        role=MessageRole.ASSISTANT,
        content=response.content,
        parent_message_id=message.parent_message_id or None,
        model_config_id=conversation.model_config_id,
        token_count=response.usage.completion_tokens if response.usage else None,
        meta={
            "model": response.model,
            "regenerated_from": str(message.id),
            "finish_reason": response.finish_reason,
        },
    )
    await session.commit()
    return {"user_message": parent, "assistant_message": new_message, "tool_messages": []}


async def message_with_attachments(session: AsyncSession, message: Message) -> dict:
    attachments = (
        (
            await session.execute(
                sa.select(FileRecord)
                .join(Attachment, Attachment.file_id == FileRecord.id)
                .where(Attachment.message_id == message.id)
            )
        )
        .scalars()
        .all()
    )
    return {
        **{c.name: getattr(message, c.name) for c in message.__table__.columns},
        "attachments": [
            {
                "file_id": str(a.id),
                "filename": a.original_filename,
                "mime_type": a.mime_type,
                "size_bytes": a.size_bytes,
            }
            for a in attachments
        ],
    }
