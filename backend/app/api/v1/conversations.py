"""Conversation endpoints: CRUD, messages, search, streaming chat."""

from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import StreamingResponse

from app.api.deps import (
    DbDep,
    ManagerDep,
    PrincipalDep,
    RegistryDep,
    SettingsDep,
    ToolExecutorDep,
    get_database,
    rate_limit,
)
from app.db.enums import ConversationStatus
from app.schemas.chat import (
    ChatResponse,
    ConversationCreateIn,
    ConversationOut,
    ConversationUpdateIn,
    MessageCreateIn,
    MessageEditIn,
    MessageOut,
    SearchHit,
)
from app.services import conversations as conversation_service
from app.services.conversations import message_with_attachments

router = APIRouter(prefix="/conversations", tags=["conversations"])


def _conversation_out(row, message_count: int = 0) -> ConversationOut:
    return ConversationOut(
        id=row.id,
        title=row.title,
        status=row.status,
        mode=row.mode,
        system_prompt=row.system_prompt,
        model_config_id=row.model_config_id,
        message_count=message_count,
        last_message_at=row.last_message_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _conversation_with_count(db, row) -> ConversationOut:
    import sqlalchemy as sa

    from app.db.models import Message

    count = await db.scalar(
        sa.select(sa.func.count(Message.id)).where(
            Message.conversation_id == row.id, Message.is_active.is_(True)
        )
    )
    return _conversation_out(row, count or 0)


@router.post(
    "",
    response_model=ConversationOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a conversation",
)
async def create_conversation(
    payload: ConversationCreateIn, principal: PrincipalDep, db: DbDep
):
    row = await conversation_service.create_conversation(
        db, user_id=principal.user_id, payload=payload
    )
    return _conversation_out(row)


@router.get("", response_model=list[ConversationOut], summary="List conversations")
async def list_conversations(
    principal: PrincipalDep,
    db: DbDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status_filter: ConversationStatus | None = Query(default=None, alias="status"),
    q: str | None = Query(default=None, max_length=200),
):
    rows, total = await conversation_service.list_conversations(
        db,
        user_id=principal.user_id,
        limit=limit,
        offset=offset,
        status=status_filter,
        q=q,
    )
    return [_conversation_out(row) for row in rows]


@router.get("/{conversation_id}", response_model=ConversationOut, summary="Get a conversation")
async def get_conversation(
    conversation_id: UUID, principal: PrincipalDep, db: DbDep
):
    row = await conversation_service.get_conversation(
        db, user_id=principal.user_id, conversation_id=conversation_id
    )
    return await _conversation_with_count(db, row)


@router.patch("/{conversation_id}", response_model=ConversationOut, summary="Update a conversation")
async def update_conversation(
    conversation_id: UUID,
    payload: ConversationUpdateIn,
    principal: PrincipalDep,
    db: DbDep,
):
    row = await conversation_service.update_conversation(
        db, user_id=principal.user_id, conversation_id=conversation_id, payload=payload
    )
    return await _conversation_with_count(db, row)


@router.delete("/{conversation_id}", status_code=204, summary="Delete a conversation")
async def delete_conversation(conversation_id: UUID, principal: PrincipalDep, db: DbDep):
    await conversation_service.delete_conversation(
        db, user_id=principal.user_id, conversation_id=conversation_id
    )


@router.post("/{conversation_id}/archive", response_model=ConversationOut, summary="Archive a conversation")
async def archive_conversation(conversation_id: UUID, principal: PrincipalDep, db: DbDep):
    row = await conversation_service.set_archived(
        db, user_id=principal.user_id, conversation_id=conversation_id, archived=True
    )
    return await _conversation_with_count(db, row)


@router.post("/{conversation_id}/unarchive", response_model=ConversationOut, summary="Unarchive a conversation")
async def unarchive_conversation(conversation_id: UUID, principal: PrincipalDep, db: DbDep):
    row = await conversation_service.set_archived(
        db, user_id=principal.user_id, conversation_id=conversation_id, archived=False
    )
    return await _conversation_with_count(db, row)


# ------------------------------------------------------------------ messages

def _message_out(row, attachments: list[dict] | None = None) -> MessageOut:
    return MessageOut(
        id=row.id,
        conversation_id=row.conversation_id,
        seq=row.seq,
        role=row.role,
        content=row.content,
        parent_message_id=row.parent_message_id,
        is_active=row.is_active,
        token_count=row.token_count,
        tool_call_id=row.tool_call_id,
        tool_name=row.tool_name,
        meta=dict(row.meta or {}),
        created_at=row.created_at,
        edited_at=row.edited_at,
        attachments=attachments or [],
    )


@router.get(
    "/{conversation_id}/messages",
    response_model=list[MessageOut],
    summary="List messages (paginated, ascending)",
)
async def list_messages(
    conversation_id: UUID,
    principal: PrincipalDep,
    db: DbDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    include_inactive: bool = Query(default=False),
):
    rows, total = await conversation_service.list_messages(
        db,
        user_id=principal.user_id,
        conversation_id=conversation_id,
        limit=limit,
        offset=offset,
        include_inactive=include_inactive,
    )
    out = []
    for row in rows:
        detail = await message_with_attachments(db, row)
        out.append(_message_out(row, detail["attachments"]))
    return out


@router.post(
    "/{conversation_id}/messages",
    response_model=ChatResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("chat-send"))],
    summary="Send a message and get the assistant response (non-streaming)",
)
async def send_message(
    conversation_id: UUID,
    payload: MessageCreateIn,
    principal: PrincipalDep,
    db: DbDep,
    settings: SettingsDep,
    manager: ManagerDep,
    registry: RegistryDep,
    tool_executor: ToolExecutorDep,
):
    result = await conversation_service.send_message(
        db,
        settings,
        user_id=principal.user_id,
        conversation_id=conversation_id,
        payload=payload,
        manager=manager,
        registry=registry,
        tool_executor=tool_executor,
    )
    user_detail = await message_with_attachments(db, result["user_message"])
    return ChatResponse(
        user_message=_message_out(result["user_message"], user_detail["attachments"]),
        assistant_message=_message_out(result["assistant_message"]),
        tool_messages=[_message_out(m) for m in result["tool_messages"]],
    )


@router.post(
    "/{conversation_id}/messages/stream",
    summary="Send a message and stream the assistant response (SSE)",
    dependencies=[Depends(rate_limit("chat-stream"))],
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def stream_message(
    conversation_id: UUID,
    payload: MessageCreateIn,
    request: Request,
    principal: PrincipalDep,
    settings: SettingsDep,
    manager: ManagerDep,
    registry: RegistryDep,
    tool_executor: ToolExecutorDep,
):
    database = get_database(request)
    bus = request.app.state.bus

    async def event_stream():
        async for event in conversation_service.stream_chat(
            database,
            settings,
            bus,
            user_id=principal.user_id,
            conversation_id=conversation_id,
            payload=payload,
            manager=manager,
            registry=registry,
            tool_executor=tool_executor,
        ):
            yield f"event: {event['type']}\ndata: {json.dumps(event['data'], default=str)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.patch(
    "/{conversation_id}/messages/{message_id}",
    response_model=MessageOut,
    summary="Edit a user message (provenance preserved)",
)
async def edit_message(
    conversation_id: UUID,
    message_id: UUID,
    payload: MessageEditIn,
    principal: PrincipalDep,
    db: DbDep,
):
    row = await conversation_service.edit_message(
        db,
        user_id=principal.user_id,
        conversation_id=conversation_id,
        message_id=message_id,
        content=payload.content,
    )
    return _message_out(row)


@router.post(
    "/{conversation_id}/messages/{message_id}/regenerate",
    response_model=ChatResponse,
    summary="Regenerate an assistant response (old version kept inactive)",
)
async def regenerate(
    conversation_id: UUID,
    message_id: UUID,
    principal: PrincipalDep,
    db: DbDep,
    settings: SettingsDep,
    manager: ManagerDep,
    registry: RegistryDep,
    tool_executor: ToolExecutorDep,
):
    result = await conversation_service.regenerate_response(
        db,
        settings,
        user_id=principal.user_id,
        conversation_id=conversation_id,
        message_id=message_id,
        manager=manager,
        registry=registry,
        tool_executor=tool_executor,
    )
    return ChatResponse(
        user_message=_message_out(result["user_message"]) if result["user_message"] else None,  # type: ignore[arg-type]
        assistant_message=_message_out(result["assistant_message"]),
        tool_messages=[],
    )


@router.get(
    "/{conversation_id}/search",
    response_model=list[SearchHit],
    summary="Search messages within a conversation",
)
async def search(
    conversation_id: UUID,
    principal: PrincipalDep,
    db: DbDep,
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    rows, total = await conversation_service.search_messages(
        db,
        user_id=principal.user_id,
        conversation_id=conversation_id,
        query=q,
        limit=limit,
        offset=offset,
    )
    return [
        SearchHit(
            message_id=row.id,
            seq=row.seq,
            role=row.role,
            content_snippet=_snippet(row.content, q),
            created_at=row.created_at,
        )
        for row in rows
    ]


def _snippet(content: str, query: str, width: int = 160) -> str:
    lower = content.lower()
    position = lower.find(query.lower())
    if position < 0:
        return content[:width]
    start = max(0, position - width // 2)
    return ("…" if start > 0 else "") + content[start : start + width]
