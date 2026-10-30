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
from app.api.schemas import ExpenseReportCreate
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
from app.models.proactive import ProactiveSuggestion
from app.models.trip import Trip
from app.services import (
    action_center_service,
    expense_report_service,
    job_service,
    proactive_service,
    trip_service,
)

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

        # 待决定建议：卡片携带 subject_id，客户端据此调会话内动作（批准/驳回）。
        pending = session.scalars(
            select(ProactiveSuggestion)
            .where(
                ProactiveSuggestion.organization_id == conversation.organization_id,
                ProactiveSuggestion.trip_id == trip.id,
                ProactiveSuggestion.status == "PENDING",
            )
            .order_by(ProactiveSuggestion.created_at.desc())
            .limit(5)
        ).all()
        for suggestion in pending:
            cards.append(
                {
                    "type": "PENDING_SUGGESTION",
                    "title": suggestion.title,
                    "detail": suggestion.rationale[:160],
                    "entity_id": str(suggestion.id),
                    "link": "suggestion",
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


def run_action(
    session: Session,
    organization_id: UUID | str,
    conversation_id: UUID | str,
    action: str,
    subject_id: str,
    *,
    actor_user_id: UUID | str,
    actor_role: str | None,
) -> AssistantMessage:
    """会话内动作（PRD 6.3 执行进度）：适配既有业务服务，人审门槛不变。

    v1 只开放 ``approve_suggestion`` / ``dismiss_suggestion``：直接复用
    ``proactive_service`` 的批准/驳回（角色、审批策略、阈值都在那边），助手只是入口。
    执行结果以 ASSISTANT 消息 + ACTION_RESULT 卡片落回对话，审计仍在 suggestion_audit。
    """
    conversation = get_conversation(session, organization_id, conversation_id)
    if action == "approve_suggestion":
        proactive_service.approve(
            session,
            organization_id,
            subject_id,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
        )
        suggestion = session.get(ProactiveSuggestion, UUID(str(subject_id)))
        status = suggestion.status if suggestion is not None else "UNKNOWN"
        detail = f"建议已批准，当前状态 {status}"
    elif action == "generate_plan":
        if conversation.trip_id is None:
            raise Unprocessable("对话未绑定行程，无法生成计划", details={"action": action})
        trip = session.get(Trip, conversation.trip_id)
        if trip is None or trip.status in ("CANCELLED", "COMPLETED"):
            raise Unprocessable(
                "该出行当前不可生成计划", details={"status": trip.status if trip else None}
            )
        dedupe_key = f"plan_trip:{trip.id}:{trip.version}"
        job = job_service.enqueue(
            session,
            organization_id,
            job_type=job_service.JOB_TYPE_PLAN_TRIP,
            payload={"trip_id": str(trip.id), "trip_version": trip.version},
            dedupe_key=dedupe_key,
        )
        if trip.status == "DRAFT":
            trip.status = "PLANNING"
        session.flush()
        detail = f"已入队生成计划任务（job {str(job.id)[:8]}）"
        card = {
            "type": "PLAN_JOB",
            "title": "生成计划",
            "detail": detail,
            "entity_id": str(job.id),
            "link": "trip",
        }
        row = AssistantMessage(
            organization_id=str(conversation.organization_id),
            conversation_id=conversation.id,
            role="ASSISTANT",
            content=detail,
            cards=[card],
        )
        session.add(row)
        session.flush()
        return row
    elif action == "create_expense_report":
        if conversation.trip_id is None:
            raise Unprocessable("对话未绑定行程，无法生成报销单", details={"action": action})
        detail_obj = expense_report_service.create_report(
            session,
            organization_id,
            conversation.trip_id,
            ExpenseReportCreate(),
            actor_user_id=actor_user_id,
        )
        report = detail_obj.report
        detail = (
            f"已从费用记录生成报销单 {str(report.id)[:8]}"
            f"（{len(detail_obj.items)} 项，状态 {report.status}）"
        )
        row = AssistantMessage(
            organization_id=str(conversation.organization_id),
            conversation_id=conversation.id,
            role="ASSISTANT",
            content=detail,
            cards=[
                {
                    "type": "EXPENSE_REPORT",
                    "title": "报销单已生成",
                    "detail": detail,
                    "entity_id": str(report.id),
                    "link": "trip",
                }
            ],
        )
        session.add(row)
        session.flush()
        return row
    elif action == "dismiss_suggestion":
        proactive_service.dismiss(
            session,
            organization_id,
            subject_id,
            actor_user_id=actor_user_id,
        )
        suggestion = session.get(ProactiveSuggestion, UUID(str(subject_id)))
        status = suggestion.status if suggestion is not None else "UNKNOWN"
        detail = f"建议已驳回，当前状态 {status}"
    else:
        raise Unprocessable("不支持的动作", details={"action": action})

    row = AssistantMessage(
        organization_id=str(conversation.organization_id),
        conversation_id=conversation.id,
        role="ASSISTANT",
        content=detail,
        cards=[
            {
                "type": "ACTION_RESULT",
                "title": action,
                "detail": detail,
                "entity_id": subject_id,
                "link": "action_center",
            }
        ],
    )
    session.add(row)
    session.flush()
    return row


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
    "run_action",
    "get_conversation",
    "delete_conversation",
    "list_conversations",
    "messages",
    "post_message",
]
