"""AI 助手对话接口（PRD 页面 05）。"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import (
    ROLE_TRAVELER,
    ROLE_VIEWER,
    ContextDep,
    IdempotencyKeyDep,
    SessionDep,
    require_role,
)
from app.api.idempotent import IdempotentWrite
from app.api.schemas import (
    AssistantActionCreate,
    AssistantConversationCreate,
    AssistantConversationDetailRead,
    AssistantConversationRead,
    AssistantMessageCreate,
    AssistantMessageRead,
    AssistantToolExecuteCreate,
)
from app.services import assistant_service

router = APIRouter(tags=["assistant"])
ASSISTANT_TOOL_EXECUTE_ENDPOINT = "POST /v1/assistant/conversations/{conversation_id}/tools:execute"


def _conversation_read(row) -> AssistantConversationRead:
    return AssistantConversationRead.model_validate(row)


def _message_read(row) -> AssistantMessageRead:
    return AssistantMessageRead.model_validate(row)


@router.post(
    "/assistant/conversations",
    response_model=AssistantConversationRead,
    status_code=status.HTTP_201_CREATED,
    summary="新建对话",
)
def create_conversation(
    payload: AssistantConversationCreate, context: ContextDep, session: SessionDep
):
    require_role(context, ROLE_TRAVELER)
    row = assistant_service.create_conversation(
        session,
        context.organization_id,
        user_id=context.user_id,
        trip_id=payload.trip_id,
        title=payload.title or "",
    )
    return _conversation_read(row)


@router.get(
    "/assistant/conversations",
    response_model=list[AssistantConversationRead],
    summary="我的对话列表",
)
def list_conversations(context: ContextDep, session: SessionDep):
    require_role(context, ROLE_VIEWER)
    return [
        _conversation_read(row)
        for row in assistant_service.list_conversations(
            session, context.organization_id, context.user_id
        )
    ]


@router.get(
    "/assistant/conversations/{conversation_id}",
    response_model=AssistantConversationDetailRead,
    summary="对话详情（含消息）",
)
def get_conversation(conversation_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_VIEWER)
    rows = assistant_service.messages(session, context.organization_id, conversation_id)
    conversation = assistant_service.get_conversation(
        session, context.organization_id, conversation_id
    )
    return AssistantConversationDetailRead(
        conversation=_conversation_read(conversation),
        messages=[_message_read(row) for row in rows],
    )


@router.post(
    "/assistant/conversations/{conversation_id}/messages",
    response_model=AssistantMessageRead,
    summary="发送消息：获取基于事实卡片的回复",
)
def post_message(
    conversation_id: UUID,
    payload: AssistantMessageCreate,
    context: ContextDep,
    session: SessionDep,
):
    require_role(context, ROLE_TRAVELER)
    _, bot = assistant_service.post_message(
        session,
        context.organization_id,
        conversation_id,
        payload.content,
        user_id=context.user_id,
    )
    return _message_read(bot)


@router.post(
    "/assistant/conversations/{conversation_id}/actions",
    response_model=AssistantMessageRead,
    summary="会话内执行（复用业务服务的人审门槛）",
)
def run_action(
    conversation_id: UUID,
    payload: AssistantActionCreate,
    context: ContextDep,
    session: SessionDep,
):
    require_role(context, ROLE_TRAVELER)
    row = assistant_service.run_action(
        session,
        context.organization_id,
        conversation_id,
        payload.action,
        str(payload.subject_id),
        actor_user_id=context.user_id,
        actor_role=context.role,
    )
    return _message_read(row)


@router.post(
    "/assistant/conversations/{conversation_id}/tools:execute",
    response_model=AssistantMessageRead,
    summary="执行一个已登记、已授权的 Agent 工具",
)
def execute_tool(
    conversation_id: UUID,
    payload: AssistantToolExecuteCreate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_VIEWER)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=ASSISTANT_TOOL_EXECUTE_ENDPOINT,
        key=idempotency_key,
        payload={
            "conversation_id": str(conversation_id),
            "tool_name": payload.tool_name,
            "arguments": payload.arguments,
        },
    )
    if (replay := guard.begin()) is not None:
        return replay

    row = assistant_service.run_tool(
        session,
        context.organization_id,
        conversation_id,
        payload.tool_name,
        payload.arguments,
        actor_role=context.role,
    )
    return guard.finish(status_code=status.HTTP_200_OK, body=_message_read(row))


@router.delete(
    "/assistant/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除对话（只删聊天，不影响任务与审计）",
)
def delete_conversation(conversation_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_TRAVELER)
    assistant_service.delete_conversation(
        session, context.organization_id, conversation_id
    )
