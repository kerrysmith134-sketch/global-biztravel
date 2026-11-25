"""按 Trip 的审计回放（设计文档第 15 章）。

一次出行上发生过的事实散落在多张表：建议生命周期（``suggestion_audit``）、Outbox 事件、
现场记录、规划尝试、审批。本服务把它们摊平成一条时间线，回答"这次出行为什么变成了现在
这个样子"——不是统计，而是可逐条点开的证据。

只读：不修改任何状态。排序按时间倒序，``limit`` 限制返回条数。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.expense_reports import ExpenseReport
from app.models.memory import PlanAttemptLog
from app.models.proactive import Approval, ProactiveSuggestion, SuggestionAudit
from app.models.records import VisitRecord
from app.models.reliability import OutboxEvent
from app.models.trip import Visit


@dataclass
class AuditEvent:
    at: datetime
    kind: str
    action: str
    actor: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


def trip_timeline(
    session: Session, organization_id: UUID | str, trip_id: UUID | str, *, limit: int = 200
) -> list[AuditEvent]:
    org = UUID(str(organization_id))
    trip_uuid = UUID(str(trip_id))
    events: list[AuditEvent] = []

    suggestion_ids = list(
        session.scalars(
            select(ProactiveSuggestion.id).where(
                ProactiveSuggestion.organization_id == org,
                ProactiveSuggestion.trip_id == trip_uuid,
            )
        )
    )

    for suggestion in session.scalars(
        select(ProactiveSuggestion).where(
            ProactiveSuggestion.organization_id == org,
            ProactiveSuggestion.trip_id == trip_uuid,
        )
    ):
        events.append(
            AuditEvent(
                at=suggestion.created_at,
                kind="suggestion",
                action=suggestion.status,
                detail={
                    "suggestion_id": str(suggestion.id),
                    "type": suggestion.suggestion_type,
                    "title": suggestion.title,
                    "confidence": suggestion.confidence,
                    "push_status": suggestion.push_status,
                },
            )
        )

    if suggestion_ids:
        for row in session.scalars(
            select(SuggestionAudit).where(
                SuggestionAudit.organization_id == org,
                SuggestionAudit.suggestion_id.in_(suggestion_ids),
            )
        ):
            events.append(
                AuditEvent(
                    at=row.created_at,
                    kind="suggestion_audit",
                    action=row.action,
                    actor=(
                        str(row.actor_user_id)
                        if row.actor_user_id is not None
                        else row.actor_type
                    ),
                    detail={"suggestion_id": str(row.suggestion_id), **dict(row.detail or {})},
                )
            )
        for approval in session.scalars(
            select(Approval).where(
                Approval.organization_id == org,
                Approval.subject_type == "proactive_suggestion",
                Approval.subject_id.in_(suggestion_ids),
            )
        ):
            events.append(
                AuditEvent(
                    at=approval.decided_at,
                    kind="approval",
                    action=approval.decision,
                    actor=(
                        str(approval.decided_by_user_id)
                        if approval.decided_by_user_id is not None
                        else None
                    ),
                    detail={"suggestion_id": str(approval.subject_id), "note": approval.note},
                )
            )

    for event in session.scalars(
        select(OutboxEvent).where(
            OutboxEvent.organization_id == org,
            OutboxEvent.aggregate_id == trip_uuid,
        )
    ):
        events.append(
            AuditEvent(
                at=event.created_at,
                kind="outbox",
                action=event.event_type,
                detail={
                    "event_id": str(event.id),
                    "status": event.status,
                    "attempts": event.attempts,
                    "last_error": event.last_error,
                },
            )
        )

    visit_ids = list(
        session.scalars(
            select(Visit.id).where(Visit.organization_id == org, Visit.trip_id == trip_uuid)
        )
    )
    if visit_ids:
        for record in session.scalars(
            select(VisitRecord).where(
                VisitRecord.organization_id == org,
                VisitRecord.visit_id.in_(visit_ids),
            )
        ):
            events.append(
                AuditEvent(
                    at=record.created_at,
                    kind="visit_record",
                    action=record.outcome,
                    actor=(
                        str(record.created_by_user_id)
                        if record.created_by_user_id is not None
                        else None
                    ),
                    detail={
                        "record_id": str(record.id),
                        "visit_id": str(record.visit_id),
                        "applied": record.applied,
                        "notes": record.notes,
                    },
                )
            )

    for attempt in session.scalars(
        select(PlanAttemptLog).where(
            PlanAttemptLog.organization_id == org,
            PlanAttemptLog.trip_id == trip_uuid,
        )
    ):
        events.append(
            AuditEvent(
                at=attempt.created_at,
                kind="plan_attempt",
                action=f"{attempt.attempt_type}:{attempt.outcome}",
                detail={
                    "plan_version_id": (
                        str(attempt.plan_version_id) if attempt.plan_version_id else None
                    ),
                    "reason": attempt.reason,
                    **dict(attempt.details or {}),
                },
            )
        )

    for report in session.scalars(
        select(ExpenseReport).where(
            ExpenseReport.organization_id == org,
            ExpenseReport.trip_id == trip_uuid,
            ExpenseReport.cancelled_at.is_not(None),
        )
    ):
        cancelled_at = report.cancelled_at
        if cancelled_at is None:
            continue
        events.append(
            AuditEvent(
                at=cancelled_at,
                kind="expense_report",
                action="CANCELLED",
                actor=(
                    str(report.cancelled_by_user_id)
                    if report.cancelled_by_user_id is not None
                    else None
                ),
                detail={
                    "report_id": str(report.id),
                    "status": report.status,
                    "reason": report.cancellation_reason,
                },
            )
        )

    events.sort(key=lambda item: item.at, reverse=True)
    return events[:limit]


__all__ = ["AuditEvent", "trip_timeline"]
