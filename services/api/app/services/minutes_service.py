"""会议纪要草稿服务：现场记录 → LLM 草稿 → 可审计的调用记录。

设计文档 v2.0 第 13 章与 AT-012：现场记录之后要能生成"会议纪要及跟进任务草稿"。这里的边界
与权限边界一致：

- 只生成**草稿**，不发送、不写回客户档案、不创建正式任务；``requires_approval`` 的工具语义
  同样适用于此（本端点由 TRAVELER 主动触发，人已在场）。
- 每条草稿都能回溯到 ``llm_invocations``（``llm_invocation_id`` 随响应返回）。
- LLM 不可用时返回模板化草稿并标 ``degraded=true``，不让"模型挂了"阻断现场收尾。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm.contracts import LLMRequest
from app.llm.gateway import get_gateway
from app.llm.prompts import (
    INSIGHT_KINDS,
    MEETING_MINUTES_TEMPLATE_ID,
    MEETING_MINUTES_V2_PROMPT_VERSION,
    MEETING_MINUTES_V2_SCHEMA,
    MEETING_MINUTES_V2_SYSTEM,
    MUST_CONFIRM_KINDS,
)
from app.models.geo import Customer, Place
from app.models.records import VisitRecord
from app.models.trip import Trip, Visit
from app.models.workflow import AgentRun
from app.services import record_service, visit_service
from app.services.agent_runtime_service import fail_agent_run, start_agent_run, succeed_agent_run


@dataclass
class MinutesInsight:
    """一条被识别的信息（PRD 页面 07 §8.2）。

    ``requires_confirmation`` 由**服务端**按类别强制：除 SUMMARY 外一律 True，
    模型说 false 也改回来——"必须确认"是业务规则，不交给模型决定。
    """

    kind: str
    text: str
    requires_confirmation: bool


@dataclass
class MinutesDraft:
    invocation_id: UUID
    title: str
    summary: str
    agent_run_id: UUID | None = None
    decisions: list[str] = field(default_factory=list)
    follow_up_tasks: list[dict[str, Any]] = field(default_factory=list)
    insights: list[MinutesInsight] = field(default_factory=list)
    degraded: bool = False


def _latest_record(session: Session, organization_id: UUID | str, visit_id: UUID) -> VisitRecord | None:
    records = record_service.list_records(session, organization_id, visit_id)
    applied = [item for item in records if item.applied]
    if not applied:
        return None
    return max(applied, key=lambda item: (item.client_updated_at, item.created_at))


def _fallback(title_seed: str, customer_name: str, notes: str, tasks: list[dict[str, Any]]) -> dict[str, Any]:
    follow_ups = [task for task in tasks if str(task.get("title") or "").strip()]
    if not follow_ups:
        follow_ups = [{"title": "整理会议纪要与跟进事项并发送给客户", "due_date": None}]
    summary = f"与 {customer_name} 就「{title_seed}」进行了会面。"
    if notes:
        summary += f"现场记录：{notes}"
    else:
        summary += "现场未留下文字记录，请补充关键结论后再发送。"
    return {
        "title": f"{customer_name} · {title_seed} 会议纪要草稿",
        "summary": summary,
        "decisions": [],
        "follow_up_tasks": follow_ups,
        # 模板兜底：只回摘要类洞察，不猜客户需求/交易条件。
        "insights": (
            [{"kind": "SUMMARY", "text": notes.strip()[:200], "requires_confirmation": False}]
            if notes.strip()
            else []
        ),
    }


def _normalise_insights(value: Any) -> list[MinutesInsight]:
    """校验并归一化模型返回的洞察；类别非法或文本为空的直接丢弃。"""
    insights: list[MinutesInsight] = []
    if not isinstance(value, list):
        return insights
    for item in value:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip().upper()
        if kind not in INSIGHT_KINDS:
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        # 业务规则：非 SUMMARY 类别必须人工确认，模型的 false 一律覆盖为 True。
        requires = kind in MUST_CONFIRM_KINDS
        insights.append(
            MinutesInsight(kind=kind, text=text[:500], requires_confirmation=requires)
        )
    return insights


def _normalise_tasks(value: Any) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    if not isinstance(value, list):
        return tasks
    for item in value:
        if isinstance(item, dict):
            title = str(item.get("title") or "").strip()
            if title:
                tasks.append({"title": title, "due_date": item.get("due_date")})
        elif isinstance(item, str) and item.strip():
            tasks.append({"title": item.strip(), "due_date": None})
    return tasks


def draft_minutes(
    session: Session,
    organization_id: UUID | str,
    visit_id: UUID | str,
    *,
    tone: str = "PROFESSIONAL",
    agent_run: AgentRun | None = None,
) -> MinutesDraft:
    visit = visit_service.require_visit(session, organization_id, UUID(str(visit_id)))
    trip = session.get(Trip, visit.trip_id)
    place = session.get(Place, visit.place_id)
    customer = session.get(Customer, visit.customer_id) if visit.customer_id else None
    record = _latest_record(session, organization_id, visit.id)

    customer_name = (customer.name if customer is not None else None) or (place.name if place is not None else "客户")
    title_seed = visit.title or "商务拜访"
    notes = record.notes if record is not None else ""
    tasks = list(record.follow_up_tasks or []) if record is not None else []
    fallback = _fallback(title_seed, customer_name, notes, tasks)

    variables = {
        "visit_id": str(visit.id),
        "trip_name": trip.name if trip is not None else None,
        "visit_title": title_seed,
        "customer_name": customer_name,
        "place_name": place.name if place is not None else None,
        "outcome": record.outcome if record is not None else None,
        "notes": notes,
        "follow_up_tasks": tasks,
        "tone": tone,
    }
    request = LLMRequest(
        task="draft_meeting_minutes",
        prompt_template_id=MEETING_MINUTES_TEMPLATE_ID,
        prompt_version=MEETING_MINUTES_V2_PROMPT_VERSION,
        variables=variables,
        schema=MEETING_MINUTES_V2_SCHEMA,
        fallback={**fallback, "insights": []},
        system=MEETING_MINUTES_V2_SYSTEM,
    )
    owns_agent_run = agent_run is None
    run = agent_run
    if run is None:
        run = start_agent_run(
            session,
            organization_id,
            workflow_id=None,
            agent_type="MEETING_MINUTES_DRAFT",
            input_snapshot={
                "visit_id": str(visit.id),
                "trip_id": str(visit.trip_id),
                "source_record_id": str(record.id) if record is not None else None,
                "source_record_version": record.server_version if record is not None else None,
                "source_record_updated_at": (
                    record.client_updated_at.isoformat() if record is not None else None
                ),
                "notes_character_count": len(notes),
                "follow_up_task_count": len(tasks),
                "tone": tone,
                "prompt_template_id": MEETING_MINUTES_TEMPLATE_ID,
                "prompt_version": MEETING_MINUTES_V2_PROMPT_VERSION,
            },
        )
    try:
        response = get_gateway().complete(session, organization_id, request)
    except Exception as exc:
        if owns_agent_run:
            fail_agent_run(
                session,
                run,
                error=f"{type(exc).__name__}: {exc}",
                output_snapshot={"status": "FAILED"},
            )
        raise
    output = response.output or fallback
    if owns_agent_run:
        succeed_agent_run(session, run, output_snapshot=output)

    title = str(output.get("title") or fallback["title"])
    summary = str(output.get("summary") or fallback["summary"])
    decisions = [str(item) for item in (output.get("decisions") or []) if str(item).strip()]
    follow_ups = _normalise_tasks(output.get("follow_up_tasks"))
    if not follow_ups:
        follow_ups = _normalise_tasks(fallback["follow_up_tasks"])

    return MinutesDraft(
        invocation_id=response.invocation_id,
        title=title,
        agent_run_id=run.id,
        summary=summary,
        decisions=decisions,
        follow_up_tasks=follow_ups,
        insights=_normalise_insights(output.get("insights")),
        degraded=response.degraded,
    )


def list_draftable_visits(
    session: Session, organization_id: UUID | str, trip_id: UUID | str
) -> list[Visit]:
    """Return only visits with an applied field record eligible for minute drafting."""
    visits = list(
        session.scalars(
            select(Visit)
            .where(
                Visit.organization_id == str(organization_id),
                Visit.trip_id == str(trip_id),
            )
            .order_by(Visit.created_at.asc(), Visit.id.asc())
        )
    )
    if not visits:
        return []
    visit_ids = [visit.id for visit in visits]
    recorded_visit_ids = set(
        session.scalars(
            select(VisitRecord.visit_id)
            .where(
                VisitRecord.organization_id == str(organization_id),
                VisitRecord.visit_id.in_(visit_ids),
                VisitRecord.applied.is_(True),
            )
            .distinct()
        )
    )
    return [visit for visit in visits if visit.id in recorded_visit_ids]


__all__ = ["MinutesDraft", "MinutesInsight", "draft_minutes", "list_draftable_visits"]
