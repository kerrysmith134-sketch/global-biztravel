"""Trip-level risk aggregation and recovery state machine.

Source of truth stays in ProactiveSuggestion, VisitFollowUp, ExternalSignal and PlanVersion.
This service maintains a durable cross-source case projection and an append-only transition log.
It never sends customer messages, changes bookings, mutates visits or edits the active plan.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.errors import Conflict, NotFound, Unprocessable
from app.models.planning import PlanVersion
from app.models.proactive import ProactiveSuggestion
from app.models.records import VisitFollowUp
from app.models.trip import Trip
from app.models.trip_risks import TripRiskCase, TripRiskCaseEvent
from app.models.workflow import AgentRun
from app.services import notification_service


TERMINAL_STATUSES = frozenset({"RESOLVED", "DISMISSED"})
ACTIVE_SUGGESTION_STATUSES = frozenset({"PENDING", "APPROVED", "EXECUTED", "SUPPRESSED"})
ACTIVE_FOLLOW_UP_STATUSES = frozenset({"OPEN", "IN_PROGRESS"})

REVIEW_INTERVALS = {
    "CRITICAL": timedelta(minutes=10),
    "HIGH": timedelta(minutes=30),
    "MEDIUM": timedelta(hours=4),
    "LOW": timedelta(hours=24),
}


@dataclass
class RiskReconciliationReport:
    trip_id: str
    cases_created: int = 0
    cases_updated: int = 0
    cases_reopened: int = 0
    escalated: int = 0
    reviewed: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class RiskTransitionOutcome:
    case: TripRiskCase
    event: TripRiskCaseEvent


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(source_refs: list[dict[str, Any]], evidence_refs: list[dict[str, Any]]) -> str:
    raw = _canonical_json({"source_refs": source_refs, "evidence_refs": evidence_refs})
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _risk_type(suggestion_type: str | None, source_type: str | None = None) -> str:
    value = (suggestion_type or "").upper()
    if "FLIGHT" in value:
        return "FLIGHT"
    if "WEATHER" in value:
        return "WEATHER"
    if "VISA" in value:
        return "VISA"
    if "FX" in value or "CURRENCY" in value:
        return "FINANCIAL"
    if "PLAN" in value or "COORDINATE" in value:
        return "PLANNING"
    if source_type == "APPOINTMENT_RECOVERY":
        return "APPOINTMENT"
    return "VISIT_FOLLOW_UP"


def _severity(value: str | None) -> str:
    normalized = (value or "MEDIUM").upper()
    return normalized if normalized in REVIEW_INTERVALS else "MEDIUM"


def _next_review(now: datetime, severity: str) -> datetime:
    return now + REVIEW_INTERVALS[_severity(severity)]


def _parse_due_date(value: str | None, timezone_name: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value[:10])
    except (TypeError, ValueError):
        return None
    # Due dates are date-only business commitments; use trip timezone at 09:00.
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        zone = UTC
    return datetime.combine(parsed, time(hour=9), tzinfo=zone).astimezone(UTC)


def _append_event(
    session: Session,
    case: TripRiskCase,
    *,
    event_type: str,
    from_status: str | None,
    actor_type: str = "SYSTEM",
    actor_user_id: UUID | None = None,
    note: str | None = None,
    detail: dict[str, Any] | None = None,
) -> TripRiskCaseEvent:
    event = TripRiskCaseEvent(
        organization_id=case.organization_id,
        trip_id=case.trip_id,
        case_id=case.id,
        event_type=event_type,
        from_status=from_status,
        to_status=case.status,
        actor_type=actor_type,
        actor_user_id=actor_user_id,
        note=note,
        detail=detail or {},
    )
    session.add(event)
    session.flush()
    return event


def _source_ref(source_type: str, source_id: UUID, **fields: Any) -> dict[str, Any]:
    return {"type": source_type, "id": str(source_id), **fields}


def _build_groups(
    session: Session,
    organization_id: UUID,
    trip: Trip,
) -> dict[str, dict[str, Any]]:
    suggestions = list(
        session.scalars(
            select(ProactiveSuggestion)
            .where(
                ProactiveSuggestion.organization_id == organization_id,
                ProactiveSuggestion.trip_id == trip.id,
            )
            .order_by(ProactiveSuggestion.created_at, ProactiveSuggestion.id)
        ).all()
    )
    suggestions_by_id = {row.id: row for row in suggestions}
    recovery_run_ids = {
        row.source_agent_run_id
        for row in session.scalars(
            select(VisitFollowUp).where(
                VisitFollowUp.organization_id == organization_id,
                VisitFollowUp.trip_id == trip.id,
                VisitFollowUp.source_agent_run_id.is_not(None),
            )
        ).all()
        if row.source_agent_run_id is not None
    }
    runs = {}
    if recovery_run_ids:
        runs = {
            row.id: row
            for row in session.scalars(
                select(AgentRun).where(
                    AgentRun.organization_id == organization_id,
                    AgentRun.id.in_(recovery_run_ids),
                )
            ).all()
        }

    groups: dict[str, dict[str, Any]] = {}
    for suggestion in suggestions:
        if (
            suggestion.status not in ACTIVE_SUGGESTION_STATUSES
            and suggestion.status != "DISMISSED"
        ):
            continue
        key = f"suggestion:{suggestion.id}"
        action = suggestion.proposed_action if isinstance(suggestion.proposed_action, dict) else {}
        params = action.get("params") if isinstance(action.get("params"), dict) else {}
        groups[key] = {
            "case_key": key,
            "risk_type": _risk_type(suggestion.suggestion_type),
            "severity": _severity(suggestion.severity),
            "title": suggestion.title,
            "summary": suggestion.rationale,
            "source_refs": [
                _source_ref(
                    "PROACTIVE_SUGGESTION",
                    suggestion.id,
                    suggestion_type=suggestion.suggestion_type,
                    status=suggestion.status,
                    push_status=suggestion.push_status,
                    severity=suggestion.severity,
                    confidence=suggestion.confidence,
                    source_trigger_id=(
                        str(suggestion.source_trigger_id) if suggestion.source_trigger_id else None
                    ),
                )
            ],
            "evidence_refs": [
                dict(item) for item in (suggestion.evidence or []) if isinstance(item, dict)
            ],
            "context": {
                "suggestion_type": suggestion.suggestion_type,
                "suggestion_status": suggestion.status,
                "push_status": suggestion.push_status,
                "push_reason": suggestion.push_reason,
                "confidence": suggestion.confidence,
                "requires_approval": suggestion.requires_approval,
                "signal_id": params.get("signal_id"),
                "signal_type": params.get("signal_type"),
            },
            "suggestion": suggestion,
            "follow_ups": [],
            "should_create": suggestion.status in ACTIVE_SUGGESTION_STATUSES,
        }

    follow_ups = list(
        session.scalars(
            select(VisitFollowUp)
            .where(
                VisitFollowUp.organization_id == organization_id,
                VisitFollowUp.trip_id == trip.id,
            )
            .order_by(VisitFollowUp.created_at, VisitFollowUp.id)
        ).all()
    )
    for follow_up in follow_ups:
        source_suggestion_id = None
        run = runs.get(follow_up.source_agent_run_id) if follow_up.source_agent_run_id else None
        if run is not None and isinstance(run.input, dict):
            raw_suggestion_id = run.input.get("suggestion_id")
            if raw_suggestion_id:
                try:
                    source_suggestion_id = UUID(str(raw_suggestion_id))
                except (TypeError, ValueError):
                    source_suggestion_id = None

        group_key = (
            f"suggestion:{source_suggestion_id}"
            if source_suggestion_id in suggestions_by_id
            else f"followup:{follow_up.id}"
        )
        group = groups.get(group_key)
        if group is None:
            if follow_up.status not in ACTIVE_FOLLOW_UP_STATUSES:
                continue
            group = {
                "case_key": group_key,
                "risk_type": _risk_type(None, follow_up.source_type),
                "severity": "MEDIUM",
                "title": follow_up.title,
                "summary": "存在一项需要出差人处理或核验的拜访跟进事项。",
                "source_refs": [],
                "evidence_refs": [],
                "context": {"follow_up_only": True},
                "suggestion": None,
                "follow_ups": [],
                "should_create": True,
            }
            groups[group_key] = group

        group["follow_ups"].append(follow_up)
        if follow_up.status in ACTIVE_FOLLOW_UP_STATUSES:
            group["should_create"] = True
        group["source_refs"].append(
            _source_ref(
                "VISIT_FOLLOW_UP",
                follow_up.id,
                visit_id=str(follow_up.visit_id),
                source_type=follow_up.source_type,
                status=follow_up.status,
                source_agent_run_id=(
                    str(follow_up.source_agent_run_id) if follow_up.source_agent_run_id else None
                ),
                due_date=follow_up.due_date,
            )
        )
        if follow_up.source_agent_run_id:
            group["evidence_refs"].append(
                _source_ref("AGENT_RUN", follow_up.source_agent_run_id, agent_type=follow_up.source_type)
            )

    # Stable order and no duplicated exact source reference.
    for group in groups.values():
        deduped = {
            (item.get("type"), item.get("id")): item
            for item in group["source_refs"]
        }
        group["source_refs"] = sorted(
            deduped.values(), key=lambda item: (str(item.get("type")), str(item.get("id")))
        )
        group["evidence_refs"] = sorted(
            group["evidence_refs"],
            key=lambda item: (str(item.get("type")), str(item.get("id")), str(item.get("kind", ""))),
        )
        active_follow_ups = [
            row for row in group["follow_ups"] if row.status in ACTIVE_FOLLOW_UP_STATUSES
        ]
        group["context"]["active_follow_up_count"] = len(active_follow_ups)
        group["context"]["in_progress_follow_up_count"] = sum(
            row.status == "IN_PROGRESS" for row in active_follow_ups
        )
        group["context"]["completed_follow_up_count"] = sum(
            row.status == "COMPLETED" for row in group["follow_ups"]
        )
        if group["suggestion"] is None and group["follow_ups"]:
            group["title"] = group["follow_ups"][0].title
        due_dates = [
            parsed
            for row in active_follow_ups
            if (parsed := _parse_due_date(row.due_date, trip.home_timezone)) is not None
        ]
        if due_dates:
            group["context"]["earliest_follow_up_due_at"] = min(due_dates).isoformat()

    return groups


def _inferred_status(group: dict[str, Any], current_status: str) -> str:
    if current_status in TERMINAL_STATUSES or current_status in {"ESCALATED", "WAITING_EXTERNAL"}:
        return current_status
    follow_ups = group.get("follow_ups", [])
    if any(row.status == "IN_PROGRESS" for row in follow_ups):
        return "IN_PROGRESS"
    suggestion = group.get("suggestion")
    if suggestion is not None and suggestion.status in {"APPROVED", "EXECUTED"}:
        return "ACKNOWLEDGED"
    if any(row.status == "OPEN" for row in follow_ups) and current_status == "OPEN":
        return "ACKNOWLEDGED"
    return current_status


def _enqueue_escalation_notification(
    session: Session,
    trip: Trip,
    case: TripRiskCase,
) -> None:
    if case.status != "ESCALATED":
        return
    notification_service.enqueue_for_trip_risk_case(
        session,
        case,
        user_id=trip.owner_user_id,
    )


def _upsert_case(
    session: Session,
    organization_id: UUID,
    trip: Trip,
    group: dict[str, Any],
    now: datetime,
    report: RiskReconciliationReport,
) -> TripRiskCase | None:
    case = session.scalars(
        select(TripRiskCase)
        .where(
            TripRiskCase.organization_id == organization_id,
            TripRiskCase.trip_id == trip.id,
            TripRiskCase.case_key == group["case_key"],
        )
        .with_for_update()
    ).one_or_none()
    if case is None and not group.get("should_create", True):
        return None

    source_refs = group["source_refs"]
    evidence_refs = group["evidence_refs"]
    fingerprint = _fingerprint(source_refs, evidence_refs)
    next_review = _next_review(now, group["severity"])
    for ref in source_refs:
        if ref.get("type") == "VISIT_FOLLOW_UP" and ref.get("status") in ACTIVE_FOLLOW_UP_STATUSES:
            due_at = group["context"].get("earliest_follow_up_due_at")
            if due_at:
                try:
                    due_dt = datetime.fromisoformat(due_at)
                    if due_dt > now:
                        next_review = min(next_review, due_dt)
                except ValueError:
                    pass

    if case is None:
        case = TripRiskCase(
            organization_id=organization_id,
            trip_id=trip.id,
            case_key=group["case_key"],
            risk_type=group["risk_type"],
            status="OPEN",
            severity=group["severity"],
            title=group["title"][:500],
            summary=group["summary"][:8000],
            source_refs=source_refs,
            evidence_refs=evidence_refs,
            context={**group["context"], "source_fingerprint": fingerprint},
            first_seen_at=now,
            last_observed_at=now,
            next_review_at=next_review,
            version=1,
        )
        session.add(case)
        session.flush()
        _append_event(
            session,
            case,
            event_type="OPENED",
            from_status=None,
            detail={"source_fingerprint": fingerprint, "source_refs": source_refs},
        )
        report.cases_created += 1
        inferred = _inferred_status(group, case.status)
        if inferred != case.status:
            _transition_system(session, case, inferred, now=now, reason="来源已确认或执行中")
            report.cases_updated += 1
        return case

    previous_fingerprint = (
        case.context.get("source_fingerprint")
        if isinstance(case.context, dict)
        else None
    )
    source_changed = previous_fingerprint != fingerprint
    old_status = case.status
    active_follow_ups = [
        row for row in group.get("follow_ups", []) if row.status in ACTIVE_FOLLOW_UP_STATUSES
    ]
    source_suggestion = group.get("suggestion")
    if (
        source_suggestion is not None
        and source_suggestion.status == "DISMISSED"
        and not active_follow_ups
        and case.status != "DISMISSED"
    ):
        case.status = "DISMISSED"
        case.dismissed_at = now
        case.resolution_basis = "SOURCE_SUGGESTION_DISMISSED"
        case.resolution_note = "来源主动建议已被用户驳回"
        case.next_review_at = None
        case.version += 1
        case.risk_type = group["risk_type"]
        case.severity = group["severity"]
        case.title = group["title"][:500]
        case.summary = group["summary"][:8000]
        case.source_refs = source_refs
        case.evidence_refs = evidence_refs
        case.context = {**(case.context or {}), **group["context"], "source_fingerprint": fingerprint}
        case.last_observed_at = now
        _append_event(
            session,
            case,
            event_type="DISMISSED",
            from_status=old_status,
            detail={"reason": "SOURCE_SUGGESTION_DISMISSED", "source_fingerprint": fingerprint},
        )
        report.cases_updated += 1
        return case

    case.risk_type = group["risk_type"]
    case.severity = group["severity"]
    case.title = group["title"][:500]
    case.summary = group["summary"][:8000]
    case.source_refs = source_refs
    case.evidence_refs = evidence_refs
    case.context = {**(case.context or {}), **group["context"], "source_fingerprint": fingerprint}
    case.last_observed_at = now
    if case.status in TERMINAL_STATUSES and source_changed:
        case.status = "OPEN"
        case.resolved_at = None
        case.dismissed_at = None
        case.resolution_basis = None
        case.resolution_note = None
        case.acknowledged_at = None
        case.started_at = None
        case.waiting_since = None
        case.escalated_at = None
        case.next_review_at = next_review
        case.version += 1
        _append_event(
            session,
            case,
            event_type="REOPENED",
            from_status=old_status,
            detail={"source_fingerprint": fingerprint, "reason": "new source evidence observed"},
        )
        report.cases_reopened += 1
        inferred_after_reopen = _inferred_status(group, case.status)
        if inferred_after_reopen != case.status:
            _transition_system(
                session,
                case,
                inferred_after_reopen,
                now=now,
                reason="新证据到达后按来源生命周期恢复处理状态",
            )
            report.cases_updated += 1
        return case

    inferred = _inferred_status(group, case.status)
    if inferred != case.status:
        _transition_system(session, case, inferred, now=now, reason="来源状态变化")
        report.cases_updated += 1
    if source_changed:
        case.version += 1
        case.next_review_at = next_review if case.status != "WAITING_EXTERNAL" else case.next_review_at
        _append_event(
            session,
            case,
            event_type="SOURCE_LINKED",
            from_status=case.status,
            detail={"source_fingerprint": fingerprint, "source_refs": source_refs},
        )
        report.cases_updated += 1
    if case.status not in TERMINAL_STATUSES and case.next_review_at and case.next_review_at <= now:
        if case.severity in {"HIGH", "CRITICAL"} and case.status != "ESCALATED":
            _transition_system(session, case, "ESCALATED", now=now, reason="复核期限已到且风险未关闭")
            case.escalated_at = now
            case.next_review_at = now + timedelta(hours=1)
            _enqueue_escalation_notification(session, trip, case)
            report.escalated += 1
        else:
            case.last_observed_at = now
            case.next_review_at = now + (
                timedelta(hours=1) if case.status == "ESCALATED" else REVIEW_INTERVALS[case.severity]
            )
            case.version += 1
            _append_event(
                session,
                case,
                event_type="REVIEWED",
                from_status=case.status,
                detail={"result": "STILL_OPEN", "next_review_at": case.next_review_at.isoformat()},
            )
            report.reviewed += 1
    return case


def _transition_system(
    session: Session,
    case: TripRiskCase,
    status: str,
    *,
    now: datetime,
    reason: str,
) -> TripRiskCaseEvent:
    old_status = case.status
    case.status = status
    case.version += 1
    if status == "ACKNOWLEDGED":
        case.acknowledged_at = now
    elif status == "IN_PROGRESS":
        case.started_at = now
    elif status == "WAITING_EXTERNAL":
        case.waiting_since = now
    elif status == "ESCALATED":
        case.escalated_at = now
    return _append_event(
        session,
        case,
        event_type={
            "ACKNOWLEDGED": "ACKNOWLEDGED",
            "IN_PROGRESS": "STARTED",
            "WAITING_EXTERNAL": "WAITING_EXTERNAL",
            "ESCALATED": "ESCALATED",
        }[status],
        from_status=old_status,
        note=reason,
        detail={"reason": reason},
    )


def reconcile_trip(
    session: Session,
    organization_id: UUID | str,
    trip_id: UUID | str,
    *,
    now: datetime | None = None,
) -> RiskReconciliationReport:
    org = UUID(str(organization_id))
    trip_uuid = UUID(str(trip_id))
    current = now or session.scalar(select(func.clock_timestamp()))
    trip = session.scalars(
        select(Trip)
        .where(Trip.organization_id == org, Trip.id == trip_uuid)
        .with_for_update()
    ).one_or_none()
    if trip is None:
        raise NotFound("出行不存在")

    report = RiskReconciliationReport(trip_id=str(trip.id))
    groups = _build_groups(session, org, trip)
    for group in groups.values():
        try:
            with session.begin_nested():
                _upsert_case(session, org, trip, group, current, report)
        except Exception as exc:  # noqa: BLE001 — isolate one malformed source projection
            report.errors.append(
                f"{group['case_key']}: {type(exc).__name__}: {exc}"
            )
    # Due cases can still escalate even when their source disappeared from the current feed.
    due_cases = list(
        session.scalars(
            select(TripRiskCase)
            .where(
                TripRiskCase.organization_id == org,
                TripRiskCase.trip_id == trip.id,
                TripRiskCase.status.notin_(TERMINAL_STATUSES),
                TripRiskCase.next_review_at.is_not(None),
                TripRiskCase.next_review_at <= current,
            )
            .with_for_update()
        ).all()
    )
    grouped_ids = {group["case_key"] for group in groups.values()}
    for case in due_cases:
        if case.case_key in grouped_ids:
            continue
        if case.severity in {"HIGH", "CRITICAL"} and case.status != "ESCALATED":
            _transition_system(session, case, "ESCALATED", now=current, reason="复核期限已到且风险未关闭")
            case.escalated_at = current
            case.next_review_at = current + timedelta(hours=1)
            _enqueue_escalation_notification(session, trip, case)
            report.escalated += 1
        else:
            case.next_review_at = current + (
                timedelta(hours=1) if case.status == "ESCALATED" else REVIEW_INTERVALS[case.severity]
            )
            case.version += 1
            _append_event(
                session,
                case,
                event_type="REVIEWED",
                from_status=case.status,
                detail={"result": "SOURCE_NOT_SEEN", "next_review_at": case.next_review_at.isoformat()},
            )
            report.reviewed += 1
    session.flush()
    return report


def reconcile_organization(
    session: Session,
    organization_id: UUID | str,
    *,
    now: datetime | None = None,
) -> list[RiskReconciliationReport]:
    org = UUID(str(organization_id))
    current = now or session.scalar(select(func.clock_timestamp()))
    trip_ids = list(
        session.scalars(
            select(Trip.id)
            .where(
                Trip.organization_id == org,
                Trip.status.notin_(( "COMPLETED", "CANCELLED")),
            )
            .order_by(Trip.window_start)
        ).all()
    )
    reports = []
    for trip_id in trip_ids:
        try:
            with session.begin_nested():
                report = reconcile_trip(session, org, trip_id, now=current)
            reports.append(report)
        except Exception as exc:  # noqa: BLE001 — isolate one deleted/corrupt trip from the tenant scan
            reports.append(
                RiskReconciliationReport(
                    trip_id=str(trip_id),
                    errors=[f"{type(exc).__name__}: {exc}"],
                )
            )
    return reports


def _validate_resolution(
    session: Session,
    case: TripRiskCase,
    trip: Trip,
    *,
    basis: str | None,
) -> None:
    if basis == "PLAN_REVALIDATED":
        if trip.active_plan_version_id is None:
            raise Unprocessable("没有 ACTIVE 计划，不能以计划重新验证为依据关闭风险")
        plan = session.scalars(
            select(PlanVersion).where(
                PlanVersion.organization_id == case.organization_id,
                PlanVersion.trip_id == trip.id,
                PlanVersion.id == trip.active_plan_version_id,
                PlanVersion.status == "ACTIVE",
                PlanVersion.trip_version == trip.version,
            )
        ).one_or_none()
        if plan is None:
            raise Unprocessable("ACTIVE 计划尚未基于当前 Trip Version，不能关闭风险")
    elif basis in {"FOLLOW_UP_COMPLETED", "FOLLOW_UP_DISMISSED"}:
        follow_up_ids = [
            UUID(str(ref["id"]))
            for ref in (case.source_refs or [])
            if ref.get("type") == "VISIT_FOLLOW_UP" and ref.get("id")
        ]
        if not follow_up_ids:
            raise Unprocessable("该风险没有可核验的 VisitFollowUp 来源")
        rows = list(
            session.scalars(
                select(VisitFollowUp).where(
                    VisitFollowUp.organization_id == case.organization_id,
                    VisitFollowUp.trip_id == trip.id,
                    VisitFollowUp.id.in_(follow_up_ids),
                )
            ).all()
        )
        if len(rows) != len(follow_up_ids):
            raise Unprocessable("来源 FollowUp 记录不完整，不能关闭风险")
        allowed_statuses = (
            {"COMPLETED"} if basis == "FOLLOW_UP_COMPLETED" else {"DISMISSED"}
        )
        if any(row.status not in allowed_statuses for row in rows):
            raise Unprocessable("并非所有来源 FollowUp 都满足关闭依据")
        suggestion_ids = [
            UUID(str(ref["id"]))
            for ref in (case.source_refs or [])
            if ref.get("type") == "PROACTIVE_SUGGESTION" and ref.get("id")
        ]
        if suggestion_ids:
            suggestions = list(
                session.scalars(
                    select(ProactiveSuggestion).where(
                        ProactiveSuggestion.organization_id == case.organization_id,
                        ProactiveSuggestion.trip_id == trip.id,
                        ProactiveSuggestion.id.in_(suggestion_ids),
                    )
                ).all()
            )
            if len(suggestions) != len(suggestion_ids):
                raise Unprocessable("主动建议来源记录不完整，不能关闭风险")
            allowed_suggestion_statuses = (
                {"EXECUTED", "DISMISSED", "EXPIRED"}
                if basis == "FOLLOW_UP_COMPLETED"
                else {"DISMISSED", "EXPIRED"}
            )
            if any(row.status not in allowed_suggestion_statuses for row in suggestions):
                raise Unprocessable("主动建议来源仍未完成或驳回，不能仅凭 FollowUp 关闭风险")
    elif basis == "TRIP_CLOSED":
        if trip.status not in {"COMPLETED", "CANCELLED"}:
            raise Unprocessable("出行尚未完成或取消")
    elif basis in {"HUMAN_ATTESTATION", "FALSE_POSITIVE_CONFIRMED"}:
        if basis == "FALSE_POSITIVE_CONFIRMED":
            suggestion_ids = [
                UUID(str(ref["id"]))
                for ref in (case.source_refs or [])
                if ref.get("type") == "PROACTIVE_SUGGESTION" and ref.get("id")
            ]
            if not suggestion_ids:
                raise Unprocessable("该风险没有主动建议来源，不能以误报为依据驳回")
            dismissed = session.scalar(
                select(func.count())
                .select_from(ProactiveSuggestion)
                .where(
                    ProactiveSuggestion.organization_id == case.organization_id,
                    ProactiveSuggestion.trip_id == trip.id,
                    ProactiveSuggestion.id.in_(suggestion_ids),
                    ProactiveSuggestion.status == "DISMISSED",
                )
            )
            if dismissed != len(suggestion_ids):
                raise Unprocessable("必须先驳回全部关联主动建议，再关闭误报风险")
            follow_up_ids = [
                UUID(str(ref["id"]))
                for ref in (case.source_refs or [])
                if ref.get("type") == "VISIT_FOLLOW_UP" and ref.get("id")
            ]
            if follow_up_ids:
                follow_up_rows = list(
                    session.scalars(
                        select(VisitFollowUp).where(
                            VisitFollowUp.organization_id == case.organization_id,
                            VisitFollowUp.trip_id == trip.id,
                            VisitFollowUp.id.in_(follow_up_ids),
                        )
                    ).all()
                )
                if len(follow_up_rows) != len(follow_up_ids) or any(
                    row.status not in {"COMPLETED", "DISMISSED"} for row in follow_up_rows
                ):
                    raise Unprocessable("仍有未完成或未驳回的来源 FollowUp，不能关闭误报风险")
    else:
        raise Unprocessable("必须提供有效的风险关闭依据")


def transition_case(
    session: Session,
    organization_id: UUID | str,
    trip_id: UUID | str,
    case_id: UUID | str,
    *,
    expected_version: int,
    to_status: str,
    actor_user_id: UUID | str,
    note: str,
    next_review_at: datetime | None = None,
    resolution_basis: str | None = None,
) -> RiskTransitionOutcome:
    org = UUID(str(organization_id))
    trip_uuid = UUID(str(trip_id))
    case_uuid = UUID(str(case_id))
    actor = UUID(str(actor_user_id))
    trip = session.scalars(
        select(Trip)
        .where(Trip.organization_id == org, Trip.id == trip_uuid)
        .with_for_update()
    ).one_or_none()
    if trip is None:
        raise NotFound("出行不存在")
    case = session.scalars(
        select(TripRiskCase)
        .where(
            TripRiskCase.organization_id == org,
            TripRiskCase.trip_id == trip.id,
            TripRiskCase.id == case_uuid,
        )
        .with_for_update()
    ).one_or_none()
    if case is None:
        raise NotFound("Trip Risk Case 不存在")
    if case.version != expected_version:
        raise Conflict(
            "风险状态已被其他操作更新，请重新读取后再操作",
            details={"expected_version": expected_version, "current_version": case.version},
        )
    if not note or len(note.strip()) < 3:
        raise Unprocessable("风险状态变更必须提供至少 3 个字符的说明")

    allowed = {
        "OPEN": {"ACKNOWLEDGED", "IN_PROGRESS", "WAITING_EXTERNAL", "RESOLVED", "DISMISSED"},
        "ACKNOWLEDGED": {"OPEN", "IN_PROGRESS", "WAITING_EXTERNAL", "RESOLVED", "DISMISSED"},
        "IN_PROGRESS": {"ACKNOWLEDGED", "WAITING_EXTERNAL", "RESOLVED", "DISMISSED"},
        "WAITING_EXTERNAL": {"OPEN", "ACKNOWLEDGED", "IN_PROGRESS", "RESOLVED", "DISMISSED"},
        "ESCALATED": {"ACKNOWLEDGED", "IN_PROGRESS", "WAITING_EXTERNAL", "RESOLVED", "DISMISSED"},
        "RESOLVED": {"OPEN"},
        "DISMISSED": {"OPEN"},
    }
    if to_status not in allowed.get(case.status, set()):
        raise Conflict(
            "不允许该 Trip Risk Case 状态转换",
            details={"from_status": case.status, "to_status": to_status},
        )

    now = session.scalar(select(func.clock_timestamp()))
    if to_status == "WAITING_EXTERNAL":
        if next_review_at is None or next_review_at <= now:
            raise Unprocessable("等待外部确认时必须提供未来的复核时间")
        case.waiting_since = now
        case.next_review_at = next_review_at
    elif to_status == "RESOLVED":
        _validate_resolution(session, case, trip, basis=resolution_basis)
        case.resolution_basis = resolution_basis
        case.resolution_note = note.strip()
        case.resolved_at = now
        case.next_review_at = None
    elif to_status == "DISMISSED":
        if resolution_basis not in {"FALSE_POSITIVE_CONFIRMED", "FOLLOW_UP_DISMISSED"}:
            raise Unprocessable("驳回风险必须基于已驳回的建议或已驳回的来源跟进项")
        _validate_resolution(session, case, trip, basis=resolution_basis)
        case.dismissed_at = now
        case.resolution_basis = resolution_basis or "HUMAN_DISMISSED"
        case.resolution_note = note.strip()
        case.next_review_at = None
    elif to_status == "OPEN" and case.status in TERMINAL_STATUSES:
        case.resolved_at = None
        case.dismissed_at = None
        case.resolution_basis = None
        case.resolution_note = None
        case.next_review_at = _next_review(now, case.severity)
    elif to_status in {"ACKNOWLEDGED", "IN_PROGRESS"}:
        case.next_review_at = _next_review(now, case.severity)

    old_status = case.status
    case.status = to_status
    case.version += 1
    if to_status == "ACKNOWLEDGED":
        case.acknowledged_at = now
    elif to_status == "IN_PROGRESS":
        case.started_at = now
    event_type = {
        "ACKNOWLEDGED": "ACKNOWLEDGED",
        "IN_PROGRESS": "STARTED",
        "WAITING_EXTERNAL": "WAITING_EXTERNAL",
        "RESOLVED": "RESOLVED",
        "DISMISSED": "DISMISSED",
        "OPEN": "REOPENED",
    }[to_status]
    event = _append_event(
        session,
        case,
        event_type=event_type,
        from_status=old_status,
        actor_type="USER",
        actor_user_id=actor,
        note=note.strip(),
        detail={
            "resolution_basis": resolution_basis,
            "next_review_at": next_review_at.isoformat() if next_review_at else None,
        },
    )
    session.flush()
    return RiskTransitionOutcome(case=case, event=event)


def list_cases(
    session: Session,
    organization_id: UUID | str,
    trip_id: UUID | str,
    *,
    status: str | None = None,
    limit: int = 100,
) -> list[TripRiskCase]:
    stmt = select(TripRiskCase).where(
        TripRiskCase.organization_id == UUID(str(organization_id)),
        TripRiskCase.trip_id == UUID(str(trip_id)),
    )
    if status is not None:
        stmt = stmt.where(TripRiskCase.status == status)
    return list(
        session.scalars(
            stmt.order_by(
                TripRiskCase.next_review_at.asc().nullslast(),
                TripRiskCase.created_at.desc(),
            ).limit(max(1, min(limit, 200)))
        ).all()
    )


def list_case_events(
    session: Session,
    organization_id: UUID | str,
    trip_id: UUID | str,
    case_id: UUID | str,
    *,
    limit: int = 100,
) -> list[TripRiskCaseEvent]:
    org = UUID(str(organization_id))
    trip = UUID(str(trip_id))
    case = UUID(str(case_id))
    exists = session.scalars(
        select(TripRiskCase.id).where(
            TripRiskCase.organization_id == org,
            TripRiskCase.trip_id == trip,
            TripRiskCase.id == case,
        )
    ).one_or_none()
    if exists is None:
        raise NotFound("Trip Risk Case 不存在")
    return list(
        session.scalars(
            select(TripRiskCaseEvent)
            .where(
                TripRiskCaseEvent.organization_id == org,
                TripRiskCaseEvent.trip_id == trip,
                TripRiskCaseEvent.case_id == case,
            )
            .order_by(TripRiskCaseEvent.created_at.desc(), TripRiskCaseEvent.id.desc())
            .limit(max(1, min(limit, 500)))
        ).all()
    )


__all__ = [
    "RiskReconciliationReport",
    "RiskTransitionOutcome",
    "list_case_events",
    "list_cases",
    "reconcile_organization",
    "reconcile_trip",
    "transition_case",
]
