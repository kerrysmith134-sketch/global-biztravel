"""出差结果分析与客户机会复盘（PRD P1）。

把一次出行的**计划 vs 实际**与**客户机会**汇总成一个只读结果：

- 拜访：计划/完成/未会面/取消 的计数（来自 ``visits.status``）与现场记录的结果分布
  （``visit_records.outcome``）。
- 费用：生效计划的估算（``plan_versions.cost_*``）与实际费用（``trip_expenses``）按币种分列，
  不做汇率折算（与预算汇总同一口径）。
- 客户机会复盘：纪要分类洞察（``agent_runs.output.insights``）按类别计数，
  商机信号/交易条件/商业承诺/阶段变化的原文附上——这些正是 PRD 要求"标记为待确认"的内容，
  只读呈现，**不写回客户档案或 CRM**。
- 复盘记忆：该出行的 ``trip_memory_summaries``（TRIP_REVIEW）摘要引用。

只读聚合，不写库、不触发任务；任何"机会"都标注为待确认。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.expenses import TripExpense
from app.models.memory import TripMemorySummary
from app.models.planning import PlanVersion
from app.models.records import VisitRecord
from app.models.trip import Trip, Visit
from app.models.workflow import AgentRun
from app.services import trip_service

INSIGHT_KIND_LABELS = {
    "SUMMARY": "会议摘要",
    "CUSTOMER_NEED": "客户需求",
    "OPPORTUNITY_SIGNAL": "商机信号",
    "DEAL_TERM": "交易条件",
    "COMMITMENT": "商业承诺",
    "STAGE_CHANGE": "商机阶段变化",
}

OPPORTUNITY_KINDS = frozenset(
    {"OPPORTUNITY_SIGNAL", "DEAL_TERM", "COMMITMENT", "STAGE_CHANGE"}
)

VISIT_STATUS_LABELS = {
    "PLANNED": "待排期",
    "SCHEDULED": "已排期",
    "DONE": "已完成",
    "MISSED": "未会面",
    "CANCELLED": "已取消",
}

RECORD_OUTCOME_LABELS = {
    "MET": "已会面",
    "NOT_MET": "未会面",
    "RESCHEDULED": "改期",
    "CANCELLED": "取消",
}

SIGNAL_LIMIT = 10


def build_outcome(
    session: Session, organization_id: UUID | str, trip_id: UUID | str
) -> dict[str, Any]:
    trip: Trip = trip_service.require_trip(session, organization_id, trip_id)
    org = str(organization_id)

    visits_by_status = dict(
        session.execute(
            select(Visit.status, func.count())
            .where(Visit.organization_id == org, Visit.trip_id == trip.id)
            .group_by(Visit.status)
        ).all()
    )
    visits = {
        status: int(count)
        for status, count in sorted(visits_by_status.items())
    }

    record_outcomes = dict(
        session.execute(
            select(VisitRecord.outcome, func.count())
            .join(Visit, Visit.id == VisitRecord.visit_id)
            .where(
                VisitRecord.organization_id == org,
                Visit.trip_id == trip.id,
            )
            .group_by(VisitRecord.outcome)
        ).all()
    )

    expenses = session.execute(
        select(
            TripExpense.currency,
            func.coalesce(func.sum(TripExpense.amount), 0),
            func.count(),
        )
        .where(TripExpense.organization_id == org, TripExpense.trip_id == trip.id)
        .group_by(TripExpense.currency)
    ).all()
    actual_cost = [
        {"currency": row[0], "amount": float(row[1]), "count": int(row[2])}
        for row in expenses
    ]

    plan = session.scalars(
        select(PlanVersion).where(
            PlanVersion.organization_id == org,
            PlanVersion.trip_id == trip.id,
            PlanVersion.status == "ACTIVE",
        )
    ).one_or_none()

    # 纪要分类洞察：纪要草稿的输出在 agent_runs.output 里，经 visit_id 关联到本行程。
    visit_ids = [
        str(vid)
        for vid in session.scalars(
            select(Visit.id).where(
                Visit.organization_id == org, Visit.trip_id == trip.id
            )
        )
    ]
    insights_by_kind: dict[str, int] = {}
    opportunity_signals: list[dict[str, Any]] = []
    if visit_ids:
        runs = session.scalars(
            select(AgentRun)
            .where(
                AgentRun.organization_id == org,
                AgentRun.agent_type == "MEETING_MINUTES_DRAFT",
                AgentRun.status == "SUCCEEDED",
                AgentRun.output.isnot(None),
            )
            .order_by(AgentRun.finished_at.desc())
            .limit(100)
        ).all()
        visit_id_set = set(visit_ids)
        for run in runs:
            visit_id = str((run.input or {}).get("visit_id") or "")
            if visit_id not in visit_id_set:
                continue
            for insight in run.output.get("insights") or []:
                if not isinstance(insight, dict):
                    continue
                kind = str(insight.get("kind") or "")
                text = str(insight.get("text") or "").strip()
                if not kind or not text:
                    continue
                insights_by_kind[kind] = insights_by_kind.get(kind, 0) + 1
                if kind in OPPORTUNITY_KINDS and len(opportunity_signals) < SIGNAL_LIMIT:
                    opportunity_signals.append(
                        {
                            "kind": kind,
                            "kind_label": INSIGHT_KIND_LABELS.get(kind, kind),
                            "text": text,
                            "visit_id": visit_id,
                            "requires_confirmation": True,
                        }
                    )

    reviews = session.scalars(
        select(TripMemorySummary)
        .where(
            TripMemorySummary.organization_id == org,
            TripMemorySummary.trip_id == trip.id,
            TripMemorySummary.valid_until.is_(None),
        )
        .order_by(TripMemorySummary.valid_from.desc())
        .limit(5)
    ).all()

    return {
        "trip_id": trip.id,
        "status": trip.status,
        "window": {
            "start": trip.window_start.isoformat(),
            "end": trip.window_end.isoformat(),
        },
        "visits": visits,
        "visit_labels": VISIT_STATUS_LABELS,
        "record_outcomes": {
            outcome: int(count) for outcome, count in sorted(record_outcomes.items())
        },
        "record_outcome_labels": RECORD_OUTCOME_LABELS,
        "plan_cost": (
            {
                "total": float(plan.cost_total) if plan.cost_total is not None else None,
                "currency": plan.cost_currency,
                "unknown_count": plan.cost_unknown_count,
            }
            if plan is not None
            else None
        ),
        "actual_cost": actual_cost,
        "insights_by_kind": insights_by_kind,
        "insight_kind_labels": INSIGHT_KIND_LABELS,
        "opportunity_signals": opportunity_signals,
        "memory_summaries": [
            {"kind": summary.kind, "summary": summary.summary}
            for summary in reviews
        ],
    }


__all__ = ["INSIGHT_KIND_LABELS", "OPPORTUNITY_KINDS", "build_outcome"]
