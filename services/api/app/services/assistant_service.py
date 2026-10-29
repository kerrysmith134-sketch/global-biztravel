"""AI 助手对话（PRD 页面 05，v1：只读上下文 + 结构化卡片，不代替执行）。

分层（PRD 6.1「对话记录与工作流状态分离」）：

- **对话**（本服务）：`assistant_conversations` / `assistant_messages` 只记录聊了什么；
  删除对话只删这两张表，任务/计划/审批不受影响，审计也不受影响。
- **事实卡片**：行程状态与行动中心计数由服务端**确定性**计算后随消息落库——卡片是事实快照，
  不由模型生成，避免"模型说有 3 条待办"与真实状态漂移。
- **执行边界**：助手只给入口与工具提案（`proposed_tools`，来自工具目录，带 requires_approval），
  执行仍走业务接口与人审（不变量 I3），不声称"已经办完"。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import NotFound, Unprocessable
from app.llm.contracts import LLMRequest
from app.llm.gateway import get_gateway
from app.llm.prompts import (
    ASSISTANT_PROMPT_VERSION,
    ASSISTANT_SCHEMA,
    ASSISTANT_SYSTEM,
    ASSISTANT_TEMPLATE_ID,
)
from app.llm.tools import list_tools
from app.models.assistant import AssistantConversation, AssistantMessage
from app.models.trip import Trip
from app.services import action_center_service, trip_service

HISTORY_LIMIT = 6
MAX_CONTENT = 4000


def get_conversation(
    session: Session, organization_id: UUID | str, conversation_id: UUID | str
) -> AssistantConversation:
    row = session.scalars(
        select(AssistantConversation).where(
            AssistantConversation.organization_id == str(organization_id),
            AssistantConversation.id == str(conversation_id),
        )
    ).one_or_none()
    if row is None:
        raise NotFound("对话不存在", details={"conversation_id": str(conversation_id)})
    return row


def create_conversation(
    session: Session,
    organization_id: UUID | str,
    *,
    user_id: UUID | str | None,
    trip_id: UUID | str | None = None,
    title: str = "",
) -> AssistantConversation:
    if trip_id is not None:
        trip_service.require_trip(session, organization_id, trip_id)
    row = AssistantConversation(
        organization_id=str(organization_id),
        user_id=None if user_id is None else str(user_id),
        trip_id=None if trip_id is None else str(trip_id),
        title=title.strip()[:200],
    )
    session.add(row)
    session.flush()
    return row


def list_conversations(
    session: Session, organization_id: UUID | str, user_id: UUID | str | None
) -> list[AssistantConversation]:
    stmt = select(AssistantConversation).where(
        AssistantConversation.organization_id == str(organization_id)
    )
    if user_id is not None:
        stmt = stmt.where(AssistantConversation.user_id == str(user_id))
    return list(
        session.scalars(stmt.order_by(AssistantConversation.updated_at.desc()).limit(20))
    )


def messages(
    session: Session, organization_id: UUID | str, conversation_id: UUID | str
) -> list[AssistantMessage]:
    conversation = get_conversation(session, organization_id, conversation_id)
    return list(
        session.scalars(
            select(AssistantMessage)
            .where(AssistantMessage.conversation_id == conversation.id)
            .order_by(AssistantMessage.created_at)
        )
    )


def delete_conversation(
    session: Session, organization_id: UUID | str, conversation_id: UUID | str
) -> None:
    conversation = get_conversation(session, organization_id, conversation_id)
    session.delete(conversation)
    session.flush()


def _facts(session: Session, conversation: AssistantConversation, message: str) -> dict:
    trip = (
        session.get(Trip, conversation.trip_id) if conversation.trip_id is not None else None
    )
    counts: dict[str, int] = {}
    cards: list[dict] = []
    if trip is not None:
        center = action_center_service.build_action_center(
            session, conversation.organization_id, trip_id=trip.id
        )
        counts = center["counts"]
        cards.append(
            {
                "type": "TRIP_STATUS",
                "title": f"{trip.name}",
                "detail": (
                    f"状态 {trip.status} · 窗口 {trip.window_start.date()} 至 "
                    f"{trip.window_end.date()} · 待处理 {sum(counts.values())} 项"
                ),
                "entity_id": str(trip.id),
                "link": "trip",
            }
        )
        cards.append(
            {
                "type": "ACTION_CENTER",
                "title": "行动中心快照",
                "detail": " · ".join(f"{k} {v}" for k, v in counts.items() if v) or "暂无待处理",
                "entity_id": str(trip.id),
                "link": "action_center",
            }
        )

    text = message.lower()
    proposals: list[dict] = []
    keywords = {
        "计划": "trip.get_plan",
        "plan": "trip.get_plan",
        "纪要": "visit.draft_minutes",
        "签证": "visa.prepare_checklist",
        "简报": "trip.get_plan",
    }
    matched = {name for kw, name in keywords.items() if kw in text}
    for tool in list_tools():
        if tool["name"] in matched:
            proposals.append(
                {
                    "name": tool["name"],
                    "description": tool["description"],
                    "requires_approval": tool["requires_approval"],
                    "scope": tool["scope"],
                }
            )
    return {"cards": cards, "counts": counts, "proposals": proposals}


def post_message(
    session: Session,
    organization_id: UUID | str,
    conversation_id: UUID | str,
    content: str,
    *,
    user_id: UUID | str | None = None,
) -> tuple[AssistantMessage, AssistantMessage]:
    conversation = get_conversation(session, organization_id, conversation_id)
    text = content.strip()
    if not text:
        raise Unprocessable("消息内容不能为空")
    if len(text) > MAX_CONTENT:
        raise Unprocessable("消息过长", details={"limit": MAX_CONTENT})

    facts = _facts(session, conversation, text)
    history = list(
        session.scalars(
            select(AssistantMessage)
            .where(AssistantMessage.conversation_id == conversation.id)
            .order_by(AssistantMessage.created_at.desc())
            .limit(HISTORY_LIMIT)
        )
    )
    variables = {
        "message": text,
        "trip_name": (
            session.get(Trip, conversation.trip_id).name
            if conversation.trip_id is not None
            else None
        ),
        "action_counts": facts["counts"],
        "history": [
            {"role": item.role, "content": item.content[:200]} for item in reversed(history)
        ],
    }
    request = LLMRequest(
        task="assistant_reply",
        prompt_template_id=ASSISTANT_TEMPLATE_ID,
        prompt_version=ASSISTANT_PROMPT_VERSION,
        variables=variables,
        schema=ASSISTANT_SCHEMA,
        fallback={"reply": "助手暂不可用；请到行动中心或行程页查看权威状态。"},
        system=ASSISTANT_SYSTEM,
    )
    response = get_gateway().complete(session, conversation.organization_id, request)
    reply = str(response.output.get("reply") or "").strip() or "（无回复）"

    cards = facts["cards"]
    if facts["proposals"]:
        cards = cards + [
            {
                "type": "TOOL_PROPOSAL",
                "title": proposal["name"],
                "detail": f"{proposal['description']}（需人审：{proposal['requires_approval']}）",
                "entity_id": proposal["name"],
                "link": "tool",
            }
            for proposal in facts["proposals"]
        ]

    user_row = AssistantMessage(
        organization_id=str(conversation.organization_id),
        conversation_id=conversation.id,
        role="USER",
        content=text,
        cards=[],
    )
    bot_row = AssistantMessage(
        organization_id=str(conversation.organization_id),
        conversation_id=conversation.id,
        role="ASSISTANT",
        content=reply,
        cards=cards,
        llm_invocation_id=response.invocation_id,
    )
    session.add_all([user_row, bot_row])
    if not conversation.title:
        conversation.title = text[:60]
    session.flush()
    return user_row, bot_row


__all__ = [
    "create_conversation",
    "get_conversation",
    "delete_conversation",
    "list_conversations",
    "messages",
    "post_message",
]
