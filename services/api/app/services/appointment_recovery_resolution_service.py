"""Evidence-backed reassessment for accepted appointment recovery follow-ups."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import Conflict, NotFound, Unprocessable
from app.api.schemas import AppointmentRecoveryReassessmentCreate
from app.models.flight_operations import FlightOperationalUpdate
from app.models.flights import TripFlightSegment
from app.models.integrations import ExternalSignal
from app.models.planning import PlanVersion
from app.models.records import VisitFollowUp, VisitFollowUpEvent
from app.models.trip import Trip, Visit
from app.models.workflow import AgentRun, Evidence, WorkflowInstance
from app.services import external_impact_service, visit_follow_up_service

AGENT_TYPE = "APPOINTMENT_RECOVERY_REASSESSMENT"
AGENT_VERSION = "appointment-recovery-reassessment-v1.0"
SCHEMA_VERSION = "1.0"
_REQUIRED_WINDOW_ACTIONS = {
    "REQUEST_CUSTOMER_RESCHEDULE_WINDOW",
    "CONFIRM_CUSTOMER_APPOINTMENT_WINDOW",
}
_ASSESSABLE_STATUSES = {"OPERATIONAL_DELAY_ASSESSED", "NO_OPERATIONAL_DELAY"}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return sha256(_json(value).encode("utf-8")).hexdigest()


def _date(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None
    return None


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


def _point(
    status: str | None,
    arrival: datetime | None,
    delay: Any,
    start: datetime | None,
    end: datetime | None,
    before_start: bool | None,
) -> dict[str, Any]:
    minutes = (
        delay if isinstance(delay, int) and not isinstance(delay, bool) and delay >= 0 else None
    )
    if isinstance(delay, float) and delay >= 0 and delay.is_integer():
        minutes = int(delay)
    return {
        "appointment_status": status,
        "projected_arrival_at": _iso(arrival),
        "delay_minutes": minutes,
        "time_window_start": _iso(start),
        "time_window_end": _iso(end),
        "arrival_before_window_start": before_start,
    }


def _evidence(
    session: Session,
    organization_id: UUID,
    follow_up_id: UUID,
    kind: str,
    ref: str,
    payload: dict[str, Any],
    status: str,
    quality: dict[str, Any],
    now: datetime,
    observation: str | None = None,
) -> Evidence:
    row = Evidence(
        organization_id=organization_id,
        subject_type="visit_follow_up",
        subject_id=follow_up_id,
        kind=kind,
        ref=ref,
        source=AGENT_TYPE,
        payload=payload,
        retrieved_at=now,
        canonical_ref=ref,
        content_hash=_digest(payload),
        observation=observation,
        verification_status=status,
        quality=quality,
    )
    session.add(row)
    return row


def _signal_for_update(
    session: Session,
    organization_id: UUID,
    trip_id: UUID,
    update: FlightOperationalUpdate,
) -> ExternalSignal:
    signal = session.scalars(
        select(ExternalSignal).where(
            ExternalSignal.organization_id == organization_id,
            ExternalSignal.provider == update.provider,
            ExternalSignal.signal_type == "FLIGHT_STATUS",
            ExternalSignal.subject_type == "TRIP",
            ExternalSignal.subject_ref == str(trip_id),
            ExternalSignal.dedupe_key == f"flight_ops:{update.provider_event_id}",
        )
    ).one_or_none()
    if signal is None:
        raise NotFound("航班运营事件缺少对应的外部信号封套")
    return signal


def _replay(
    session: Session, organization_id: UUID, trip_id: UUID, run: AgentRun
) -> dict[str, Any] | None:
    if run.workflow_id is None:
        return None
    workflow = session.scalars(
        select(WorkflowInstance).where(
            WorkflowInstance.organization_id == organization_id,
            WorkflowInstance.id == run.workflow_id,
            WorkflowInstance.trip_id == trip_id,
            WorkflowInstance.workflow_type == AGENT_TYPE,
            WorkflowInstance.status == "COMPLETED",
        )
    ).one_or_none()
    payload = workflow.payload if workflow is not None and isinstance(workflow.payload, dict) else {}
    if workflow is None or payload.get("agent_run_id") != str(run.id):
        return None
    output = run.output if isinstance(run.output, dict) else {}
    input_data = run.input if isinstance(run.input, dict) else {}
    return {
        "follow_up_id": input_data.get("follow_up_id"),
        "workflow_id": str(workflow.id),
        "agent_run_id": str(run.id),
        **output,
        "created_at": _iso(run.finished_at or run.created_at),
    }


def reassess_follow_up(
    session: Session,
    organization_id: UUID | str,
    follow_up_id: UUID | str,
    payload: AppointmentRecoveryReassessmentCreate,
    *,
    actor_user_id: UUID | str,
) -> dict[str, Any]:
    org, actor = UUID(str(organization_id)), UUID(str(actor_user_id))
    now = datetime.now(UTC)

    follow_up = session.scalars(
        select(VisitFollowUp)
        .where(
            VisitFollowUp.organization_id == org,
            VisitFollowUp.id == UUID(str(follow_up_id)),
        )
        .with_for_update()
    ).one_or_none()
    if follow_up is None:
        raise NotFound("拜访跟进项不存在")
    if follow_up.source_type != "APPOINTMENT_RECOVERY":
        raise Unprocessable("只有航班预约恢复来源可以重新评估")
    if follow_up.status not in {"OPEN", "IN_PROGRESS"}:
        raise Conflict("只有待处理或进行中的恢复跟进项可以重新评估")

    trip = session.scalars(
        select(Trip).where(
            Trip.organization_id == org,
            Trip.id == follow_up.trip_id,
        ).with_for_update()
    ).one_or_none()
    if trip is None:
        raise NotFound("出行不存在")
    visit = session.scalars(
        select(Visit).where(
            Visit.organization_id == org,
            Visit.trip_id == trip.id,
            Visit.id == follow_up.visit_id,
        ).with_for_update()
    ).one_or_none()
    if visit is None:
        raise NotFound("恢复来源拜访不存在")

    source_run, source_action = visit_follow_up_service.load_recovery_action(
        session, org, follow_up, lock_run=True
    )
    source_input = source_run.input if isinstance(source_run.input, dict) else {}
    try:
        source_update_id = UUID(str(source_input.get("operational_update_id")))
        source_signal_id = UUID(str(source_input.get("signal_id")))
    except (TypeError, ValueError):
        raise Conflict("原恢复运行缺少有效的航班事件引用") from None
    source_update = session.scalars(
        select(FlightOperationalUpdate).where(
            FlightOperationalUpdate.organization_id == org,
            FlightOperationalUpdate.trip_id == trip.id,
            FlightOperationalUpdate.id == source_update_id,
        )
    ).one_or_none()
    if source_update is None or source_update.content_sha256 != source_input.get("content_sha256"):
        raise Conflict("原始航班运营事件已缺失或摘要不一致")
    source_signal = session.scalars(
        select(ExternalSignal).where(
            ExternalSignal.organization_id == org,
            ExternalSignal.id == source_signal_id,
            ExternalSignal.signal_type == "FLIGHT_STATUS",
            ExternalSignal.subject_type == "TRIP",
            ExternalSignal.subject_ref == str(trip.id),
        )
    ).one_or_none()
    if source_signal is None:
        raise Conflict("原始航班事件信号封套不存在")
    original = external_impact_service.assess_flight_signal_impact(
        session, org, trip, source_signal
    )
    if (
        original.get("operational_update_id") != str(source_update.id)
        or original.get("content_sha256") != source_update.content_sha256
    ):
        raise Conflict("原始航班事件无法通过完整性复核")

    action_code = str(source_action.get("action_code") or "")
    if action_code == "VERIFY_REPLACEMENT_FLIGHT_AND_RECONFIRM":
        if payload.replacement_flight_segment_id is None:
            raise Unprocessable("替代航班复核必须引用不同的已录入机票段")
        if payload.replacement_flight_segment_id == source_update.flight_segment_id:
            raise Unprocessable("替代航班必须与原航班不同")
        target_segment_id = payload.replacement_flight_segment_id
    else:
        if payload.replacement_flight_segment_id is not None:
            raise Unprocessable("只有替代航班复核动作可以指定替代机票段")
        target_segment_id = source_update.flight_segment_id

    segment = session.scalars(
        select(TripFlightSegment).where(
            TripFlightSegment.organization_id == org,
            TripFlightSegment.trip_id == trip.id,
            TripFlightSegment.id == target_segment_id,
        )
    ).one_or_none()
    if segment is None:
        raise NotFound("机票段不属于当前出行")
    if segment.booking_status == "CANCELLED":
        raise Unprocessable("不能使用已取消的机票段进行预约重评")
    if action_code == "VERIFY_REPLACEMENT_FLIGHT_AND_RECONFIRM" and segment.booking_status not in {
        "BOOKED", "TICKETED"
    }:
        raise Unprocessable("替代航班必须是 BOOKED 或 TICKETED")

    latest = session.scalars(
        select(FlightOperationalUpdate)
        .where(
            FlightOperationalUpdate.organization_id == org,
            FlightOperationalUpdate.trip_id == trip.id,
            FlightOperationalUpdate.flight_segment_id == segment.id,
            FlightOperationalUpdate.observed_at > source_update.observed_at,
            FlightOperationalUpdate.id != source_update.id,
        )
        .order_by(FlightOperationalUpdate.observed_at.desc(), FlightOperationalUpdate.id.desc())
        .limit(1)
    ).one_or_none()
    if latest is None:
        raise Conflict(
            "缺少晚于原恢复事件的新航班运营证据",
            details={"required_evidence": "NEWER_VERIFIED_FLIGHT_OPERATIONAL_UPDATE"},
        )
    signal = _signal_for_update(session, org, trip.id, latest)
    if signal.observed_at <= source_update.observed_at:
        raise Conflict("最新航班事件观察时间没有晚于原始事件")
    if signal.valid_until is None or signal.valid_until <= now:
        raise Conflict("最新航班运营事件已过期", details={"signal_id": str(signal.id)})
    if signal.observed_at > now + timedelta(minutes=5):
        raise Conflict("航班事件观察时间超出当前时间容差")

    assessment = external_impact_service.assess_flight_signal_impact(session, org, trip, signal)
    if (
        assessment.get("operational_update_id") != str(latest.id)
        or assessment.get("content_sha256") != latest.content_sha256
    ):
        raise Conflict("最新航班运营事件无法通过完整性复核")

    active_plan = None
    if trip.active_plan_version_id is not None:
        active_plan = session.scalars(
            select(PlanVersion).where(
                PlanVersion.organization_id == org,
                PlanVersion.trip_id == trip.id,
                PlanVersion.id == trip.active_plan_version_id,
                PlanVersion.status == "ACTIVE",
            )
        ).one_or_none()
    impacts = assessment.get("stop_impacts")
    stop_impacts = impacts if isinstance(impacts, list) else []
    target = next(
        (
            item for item in stop_impacts
            if isinstance(item, dict) and str(item.get("visit_id") or "") == str(visit.id)
        ),
        None,
    )

    gaps: list[str] = []
    impact_status = str(assessment.get("status") or "UNKNOWN")
    if active_plan is None:
        gaps.append("CURRENT_ACTIVE_PLAN_MISSING")
    elif assessment.get("plan_version_id") != str(active_plan.id):
        gaps.append("IMPACT_PLAN_VERSION_NOT_CURRENT")
    if impact_status not in _ASSESSABLE_STATUSES:
        gaps.append(f"FLIGHT_IMPACT_NOT_ASSESSABLE:{impact_status}")
    if assessment.get("propagation_complete") is not True:
        gaps.append("TRAVEL_CHAIN_PROPAGATION_INCOMPLETE")
    linked_leg = assessment.get("linked_air_leg")
    if not isinstance(linked_leg, dict) or linked_leg.get("stale") is not False:
        gaps.append("ACTIVE_FLIGHT_PLAN_BINDING_NOT_VERIFIED")
    if target is None:
        gaps.append("TARGET_VISIT_NOT_PRESENT_IN_IMPACT_SNAPSHOT")
    arrival = _date(target.get("projected_arrival_at")) if target is not None else None
    if arrival is None:
        gaps.append("PROJECTED_VISIT_ARRIVAL_UNAVAILABLE")

    window = payload.customer_window
    if action_code in _REQUIRED_WINDOW_ACTIONS and window is None:
        raise Unprocessable("此恢复动作必须提交出差人确认的客户预约窗口")
    if action_code == "VERIFY_REPLACEMENT_FLIGHT_AND_RECONFIRM" and window is None:
        raise Unprocessable("替代航班复核必须同时提交客户预约窗口")
    attestation = window.model_dump(mode="json") if window is not None else None
    window_start = window.start_at if window is not None else visit.time_window_start
    window_end = window.end_at if window is not None else visit.time_window_end
    before_start = arrival < window_start if arrival is not None and window_start is not None else None

    if gaps:
        appointment_status = (
            str(target.get("appointment_status") or "UNASSESSED") if target is not None else "UNASSESSED"
        )
        if arrival is None:
            appointment_status = "UNASSESSED"
    elif window_end is None:
        appointment_status = "WINDOW_END_UNKNOWN"
        gaps.append("CUSTOMER_APPOINTMENT_WINDOW_END_REQUIRED")
    elif arrival is not None and arrival > window_end:
        appointment_status = "MISSED_WINDOW"
    else:
        appointment_status = "WITHIN_WINDOW"

    recommendation = None
    decision = "REVIEW_REQUIRED"
    if not gaps:
        if appointment_status == "MISSED_WINDOW":
            decision = "CUSTOMER_WINDOW_STILL_INFEASIBLE"
        elif appointment_status == "WINDOW_END_UNKNOWN":
            decision = "CUSTOMER_CONFIRMATION_REQUIRED"
        elif (
            active_plan is not None
            and window is not None
            and (
                visit.time_window_start != window.start_at
                or visit.time_window_end != window.end_at
            )
        ):
            decision = "PLAN_CHANGE_RECOMMENDATION_READY"
            recommendation = {
                "operation_type": "CHANGE_VISIT_TIME",
                "visit_id": str(visit.id),
                "base_plan_version_id": str(active_plan.id),
                "base_trip_version": trip.version,
                "new_window_start": _iso(window.start_at),
                "new_window_end": _iso(window.end_at),
                "reason": "客户预约窗口由出差人提供确认依据，且新航班事件在当前计划下预计可满足该窗口。",
                "requires_human_submission": True,
            }
        else:
            decision = "REASSESSMENT_CLEARED"

    before = {
        "appointment_status": source_action.get("appointment_status"),
        "projected_arrival_at": source_action.get("projected_arrival_at"),
        "delay_minutes": source_action.get("delay_minutes"),
        "time_window_start": None,
        "time_window_end": _iso(_date(source_action.get("time_window_end"))),
        "arrival_before_window_start": None,
    }
    after = _point(
        appointment_status,
        arrival,
        target.get("delay_minutes") if target is not None else None,
        window_start,
        window_end,
        before_start,
    )
    changed = [
        key for key in ("appointment_status", "projected_arrival_at", "delay_minutes", "time_window_end")
        if before.get(key) != after.get(key)
    ]
    outcome = (
        "REVIEW_REQUIRED"
        if decision in {"REVIEW_REQUIRED", "CUSTOMER_WINDOW_STILL_INFEASIBLE", "CUSTOMER_CONFIRMATION_REQUIRED"}
        else "REASSESSED"
    )
    verification = "PARTIALLY_VERIFIED" if attestation is not None else "NOT_PROVIDED"
    input_snapshot = {
        "follow_up_id": str(follow_up.id),
        "trip_id": str(trip.id),
        "visit_id": str(visit.id),
        "source_recovery_agent_run_id": str(source_run.id),
        "source_recovery_action_index": follow_up.source_index,
        "source_operational_update_id": str(source_update.id),
        "source_operational_update_sha256": source_update.content_sha256,
        "source_signal_id": str(source_signal.id),
        "latest_operational_update_id": str(latest.id),
        "latest_operational_update_sha256": latest.content_sha256,
        "latest_signal_id": str(signal.id),
        "flight_segment_id": str(segment.id),
        "current_plan_version_id": str(active_plan.id) if active_plan is not None else None,
        "current_plan_version_number": active_plan.version_number if active_plan is not None else None,
        "base_trip_version": trip.version,
        "customer_window_attestation": attestation,
    }
    input_sha256 = _digest(input_snapshot)
    existing = session.scalars(
        select(AgentRun).where(
            AgentRun.organization_id == org,
            AgentRun.agent_type == AGENT_TYPE,
            AgentRun.status == "SUCCEEDED",
            AgentRun.input["follow_up_id"].as_string() == str(follow_up.id),
            AgentRun.input["input_sha256"].as_string() == input_sha256,
        ).order_by(AgentRun.created_at.desc(), AgentRun.id.desc()).limit(1)
    ).one_or_none()
    if existing is not None:
        replay = _replay(session, org, trip.id, existing)
        if replay is not None:
            return replay

    event_payload = {
        "flight_segment_id": str(latest.flight_segment_id),
        "provider": latest.provider,
        "provider_event_id": latest.provider_event_id,
        "update_type": latest.update_type,
        "flight_designator": latest.flight_designator,
        "departure_airport_iata": latest.departure_airport_iata,
        "arrival_airport_iata": latest.arrival_airport_iata,
        "estimated_departure_at": _iso(latest.estimated_departure_at),
        "estimated_arrival_at": _iso(latest.estimated_arrival_at),
        "observed_at": _iso(latest.observed_at),
        "valid_until": _iso(latest.valid_until),
        "content_sha256": latest.content_sha256,
        "signal_id": str(signal.id),
        "signal_confidence": signal.confidence,
        "signal_valid_until": _iso(signal.valid_until),
    }
    guardrails = {
        "plan_mutated": False,
        "visit_mutated": False,
        "trip_mutated": False,
        "booking_changed": False,
        "customer_contacted": False,
        "customer_message_sent": False,
        "follow_up_completed": False,
        "customer_window_independently_verified": False,
    }
    workflow = WorkflowInstance(
        organization_id=org,
        trip_id=trip.id,
        workflow_type=AGENT_TYPE,
        status="COMPLETED",
        created_by_user_id=actor,
        payload={
            "follow_up_id": str(follow_up.id),
            "source_recovery_agent_run_id": str(source_run.id),
            "source_suggestion_id": str(source_input.get("suggestion_id") or ""),
            "agent_type": AGENT_TYPE,
            "agent_version": AGENT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "agent_run_id": None,
            "outcome": outcome,
            "decision": decision,
            "source_signal_id": str(signal.id),
            "operational_update_id": str(latest.id),
            "plan_version_id": str(active_plan.id) if active_plan is not None else None,
            "input_sha256": input_sha256,
        },
    )
    session.add(workflow)
    session.flush()
    output: dict[str, Any] = {
        "agent_type": AGENT_TYPE,
        "agent_version": AGENT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "outcome": outcome,
        "decision": decision,
        "source_signal_id": str(signal.id),
        "flight_operational_update_id": str(latest.id),
        "flight_segment_id": str(segment.id),
        "current_plan_version_id": str(active_plan.id) if active_plan is not None else None,
        "input_sha256": input_sha256,
        "input_snapshot": input_snapshot,
        "before": before,
        "after": after,
        "changed_fields": changed,
        "data_gaps": gaps,
        "customer_window_verification_status": verification,
        "customer_window_attestation": attestation,
        "flight_operational_event": event_payload,
        "impact_assessment": assessment,
        "plan_change_recommendation": recommendation,
        "evidence_ids": [],
        "guardrails": guardrails,
    }
    run = AgentRun(
        organization_id=org,
        workflow_id=workflow.id,
        agent_type=AGENT_TYPE,
        status="SUCCEEDED",
        input={
            "trip_id": str(trip.id),
            "follow_up_id": str(follow_up.id),
            "visit_id": str(visit.id),
            "source_recovery_agent_run_id": str(source_run.id),
            "source_recovery_action_index": follow_up.source_index,
            "source_operational_update_id": str(source_update.id),
            "operational_update_id": str(latest.id),
            "signal_id": str(signal.id),
            "plan_version_id": str(active_plan.id) if active_plan is not None else None,
            "input_sha256": input_sha256,
            "agent_version": AGENT_VERSION,
            "schema_version": SCHEMA_VERSION,
        },
        output=output,
        started_at=now,
        finished_at=now,
    )
    session.add(run)
    session.flush()
    workflow_payload = dict(workflow.payload or {})
    workflow_payload["agent_run_id"] = str(run.id)
    workflow.payload = workflow_payload

    rows = [
        _evidence(
            session, org, follow_up.id, "FLIGHT_OPERATIONAL_UPDATE",
            f"flight-operational-update:{latest.id}", event_payload, "VERIFIED",
            {
                "verification_method": "IMMUTABLE_PROVIDER_EVENT_AND_ENVELOPE_REVALIDATION",
                "content_sha256": latest.content_sha256,
                "signal_id": str(signal.id),
                "valid_at_retrieval": True,
            },
            now, latest.summary,
        ),
        _evidence(
            session, org, follow_up.id, "APPOINTMENT_RECOVERY_IMPACT_SNAPSHOT",
            f"reassessment-impact:{run.id}",
            {
                "signal_id": str(signal.id),
                "operational_update_id": str(latest.id),
                "content_sha256": latest.content_sha256,
                "plan_version_id": str(active_plan.id) if active_plan is not None else None,
                "assessment": assessment,
                "target_visit_id": str(visit.id),
                "target_visit_projection": after,
            },
            "VERIFIED" if not gaps else "PARTIALLY_VERIFIED",
            {
                "verification_method": "DETERMINISTIC_CURRENT_PLAN_PROPAGATION",
                "propagation_complete": assessment.get("propagation_complete") is True,
                "data_gaps": gaps,
                "plan_version_matches_active": (
                    active_plan is not None and assessment.get("plan_version_id") == str(active_plan.id)
                ),
            },
            now, "航班事件已针对当前 ACTIVE 计划传播；此快照不修改计划。",
        ),
    ]
    if attestation is not None:
        rows.append(
            _evidence(
                session, org, follow_up.id, "APPOINTMENT_WINDOW_ATTESTATION",
                f"customer-window-attestation:{run.id}",
                {
                    "customer_window": attestation,
                    "asserted_by_user_id": str(actor),
                    "source_recovery_agent_run_id": str(source_run.id),
                },
                "PARTIALLY_VERIFIED",
                {
                    "verification_method": "TRAVELER_ATTESTATION",
                    "externally_verified": False,
                    "asserted_by_user_id": str(actor),
                    "confirmation_method": attestation.get("confirmation_method"),
                    "confirmation_reference_provided": bool(attestation.get("confirmation_reference")),
                },
                now, "客户预约窗口由出差人陈述，尚未由系统独立读取或核实。",
            )
        )
    rows.append(
        _evidence(
            session, org, follow_up.id, "APPOINTMENT_RECOVERY_REASSESSMENT",
            f"appointment-reassessment:{run.id}",
            {
                "workflow_id": str(workflow.id),
                "agent_run_id": str(run.id),
                "follow_up_id": str(follow_up.id),
                "outcome": outcome,
                "decision": decision,
                "input_sha256": input_sha256,
                "before": before,
                "after": after,
                "changed_fields": changed,
                "data_gaps": gaps,
                "plan_change_recommendation": recommendation,
                "guardrails": guardrails,
            },
            "PARTIALLY_VERIFIED" if attestation is not None or gaps else "VERIFIED",
            {
                "verification_method": "DETERMINISTIC_REASSESSMENT",
                "flight_evidence_verified": True,
                "customer_window_verification_status": verification,
                "input_sha256": input_sha256,
            },
            now, f"预约恢复重新评估：{decision}",
        )
    )
    session.flush()
    evidence_ids = [str(row.id) for row in rows]
    output["evidence_ids"] = evidence_ids
    run.output = output
    session.add(
        VisitFollowUpEvent(
            organization_id=org,
            follow_up_id=follow_up.id,
            event_type="REASSESSED",
            from_status=follow_up.status,
            to_status=follow_up.status,
            actor_user_id=actor,
            note=f"AgentRun {run.id} · {decision}",
        )
    )
    session.flush()
    return {
        "follow_up_id": str(follow_up.id),
        "workflow_id": str(workflow.id),
        "agent_run_id": str(run.id),
        **output,
        "created_at": _iso(now),
    }


__all__ = ["AGENT_TYPE", "AGENT_VERSION", "SCHEMA_VERSION", "reassess_follow_up"]
