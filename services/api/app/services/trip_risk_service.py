"""Trip-level risk aggregation and recovery state machine.

Source of truth stays in ProactiveSuggestion, VisitFollowUp, ExternalSignal and PlanVersion.
This service maintains a durable cross-source case projection and an append-only transition log.
It never sends customer messages, changes bookings, mutates visits or edits the active plan.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.errors import Conflict, NotFound, Unprocessable
from app.models.integrations import ExternalSignal
from app.models.planning import PlanVersion
from app.models.proactive import ProactiveSuggestion
from app.models.records import VisitFollowUp, VisitFollowUpEvent
from app.models.trip import Trip
from app.models.trip_risks import TripRiskCase, TripRiskCaseEvent
from app.models.workflow import AgentRun, Evidence, WorkflowInstance
from app.services import external_impact_service, notification_service
from app.services.signal_clock_policy import MAX_FUTURE_OBSERVED_AT_SKEW

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
    zone: tzinfo = UTC
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        pass
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
        case_version=case.version,
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






def _follow_up_reassessment_readiness(
    follow_up: VisitFollowUp,
    source_run: AgentRun | None,
    workflows: list[WorkflowInstance],
    runs: dict[UUID, AgentRun],
    events: list[VisitFollowUpEvent],
    evidence_rows: list[Evidence],
    signal_assessments: list[dict[str, Any]],
) -> dict[str, Any]:
    """Verify one persisted, evidence-backed reassessment without treating attestation as proof."""
    if not workflows:
        if any(event.event_type == "REASSESSED" for event in events):
            return {
                "status": "INTEGRITY_FAILURE",
                "integrity_status": "FAILED",
                "reasons": ["REASSESSMENT_EVENT_WITHOUT_WORKFLOW"],
            }
        return {"status": "NOT_REASSESSED", "integrity_status": "NOT_APPLICABLE", "reasons": []}

    workflow = max(workflows, key=lambda row: (row.created_at, str(row.id)))
    payload = workflow.payload if isinstance(workflow.payload, dict) else {}
    run_id = str(payload.get("agent_run_id") or "").strip()
    try:
        run_uuid = UUID(run_id) if run_id else None
    except (TypeError, ValueError):
        run_uuid = None
    run = runs.get(run_uuid) if run_uuid is not None else None
    failures: list[str] = []
    stale_reasons: list[str] = []
    output = run.output if run is not None and isinstance(run.output, dict) else {}
    run_input = run.input if run is not None and isinstance(run.input, dict) else {}
    input_snapshot = output.get("input_snapshot")
    snapshot = input_snapshot if isinstance(input_snapshot, dict) else {}

    if workflow.workflow_type != "APPOINTMENT_RECOVERY_REASSESSMENT":
        failures.append("REASSESSMENT_WORKFLOW_TYPE_MISMATCH")
    if (
        workflow.organization_id != follow_up.organization_id
        or workflow.trip_id != follow_up.trip_id
    ):
        failures.append("REASSESSMENT_WORKFLOW_SCOPE_MISMATCH")
    if workflow.status != "COMPLETED":
        failures.append("REASSESSMENT_WORKFLOW_NOT_COMPLETED")
    if payload.get("follow_up_id") != str(follow_up.id):
        failures.append("REASSESSMENT_WORKFLOW_FOLLOW_UP_MISMATCH")
    if payload.get("agent_type") != "APPOINTMENT_RECOVERY_REASSESSMENT":
        failures.append("REASSESSMENT_WORKFLOW_AGENT_TYPE_MISMATCH")
    if payload.get("agent_run_id") != run_id or run is None:
        failures.append("REASSESSMENT_AGENT_RUN_MISSING_OR_MISMATCH")
    if source_run is None:
        failures.append("SOURCE_RECOVERY_AGENT_RUN_MISSING")
    if run is not None:
        if run.organization_id != follow_up.organization_id:
            failures.append("REASSESSMENT_AGENT_RUN_ORG_MISMATCH")
        if run.workflow_id != workflow.id:
            failures.append("REASSESSMENT_AGENT_RUN_WORKFLOW_MISMATCH")
        if (
            run.agent_type != "APPOINTMENT_RECOVERY_REASSESSMENT"
            or run.status != "SUCCEEDED"
        ):
            failures.append("REASSESSMENT_AGENT_RUN_NOT_SUCCEEDED")
        if run.finished_at is None:
            failures.append("REASSESSMENT_AGENT_RUN_FINISH_TIME_MISSING")
        if run_input.get("trip_id") != str(follow_up.trip_id):
            failures.append("REASSESSMENT_AGENT_RUN_TRIP_MISMATCH")
        if run_input.get("follow_up_id") != str(follow_up.id):
            failures.append("REASSESSMENT_AGENT_RUN_FOLLOW_UP_MISMATCH")
        if run_input.get("visit_id") != str(follow_up.visit_id):
            failures.append("REASSESSMENT_AGENT_RUN_VISIT_MISMATCH")
        if source_run is not None:
            if run_input.get("source_recovery_agent_run_id") != str(source_run.id):
                failures.append("REASSESSMENT_SOURCE_RECOVERY_RUN_MISMATCH")
            if snapshot.get("source_recovery_agent_run_id") != str(source_run.id):
                failures.append("REASSESSMENT_SNAPSHOT_SOURCE_RUN_MISMATCH")
            if snapshot.get("source_recovery_action_index") != follow_up.source_index:
                failures.append("REASSESSMENT_SOURCE_ACTION_INDEX_MISMATCH")
            source_input = source_run.input if isinstance(source_run.input, dict) else {}
            if (
                snapshot.get("source_operational_update_id")
                != source_input.get("operational_update_id")
            ):
                failures.append("REASSESSMENT_SOURCE_FLIGHT_EVENT_MISMATCH")
            if snapshot.get("source_signal_id") != source_input.get("signal_id"):
                failures.append("REASSESSMENT_SOURCE_SIGNAL_MISMATCH")
        if run_input.get("operational_update_id") != output.get("flight_operational_update_id"):
            failures.append("REASSESSMENT_LATEST_FLIGHT_EVENT_MISMATCH")
        if run_input.get("signal_id") != output.get("source_signal_id"):
            failures.append("REASSESSMENT_LATEST_SIGNAL_MISMATCH")
        if run_input.get("plan_version_id") != output.get("current_plan_version_id"):
            failures.append("REASSESSMENT_PLAN_VERSION_MISMATCH")
        input_sha256 = output.get("input_sha256")
        if (
            not isinstance(input_sha256, str)
            or _canonical_json(run_input.get("input_sha256")) != _canonical_json(input_sha256)
            or payload.get("input_sha256") != input_sha256
            or _canonical_json(snapshot) == _canonical_json({})
            or hashlib.sha256(_canonical_json(snapshot).encode("utf-8")).hexdigest() != input_sha256
        ):
            failures.append("REASSESSMENT_INPUT_FINGERPRINT_MISMATCH")
        if (
            payload.get("decision") != output.get("decision")
            or payload.get("outcome") != output.get("outcome")
        ):
            failures.append("REASSESSMENT_WORKFLOW_OUTPUT_MISMATCH")
        if payload.get("source_recovery_agent_run_id") != run_input.get(
            "source_recovery_agent_run_id"
        ):
            failures.append("REASSESSMENT_WORKFLOW_SOURCE_RUN_MISMATCH")
        if payload.get("source_signal_id") != output.get("source_signal_id"):
            failures.append("REASSESSMENT_WORKFLOW_SIGNAL_MISMATCH")
        if payload.get("operational_update_id") != output.get(
            "flight_operational_update_id"
        ):
            failures.append("REASSESSMENT_WORKFLOW_FLIGHT_EVENT_MISMATCH")
        if payload.get("plan_version_id") != output.get("current_plan_version_id"):
            failures.append("REASSESSMENT_WORKFLOW_PLAN_VERSION_MISMATCH")
        if payload.get("input_sha256") != output.get("input_sha256"):
            failures.append("REASSESSMENT_WORKFLOW_INPUT_HASH_MISMATCH")
        if payload.get("agent_version") != output.get("agent_version"):
            failures.append("REASSESSMENT_WORKFLOW_AGENT_VERSION_MISMATCH")
        if payload.get("schema_version") != output.get("schema_version"):
            failures.append("REASSESSMENT_WORKFLOW_SCHEMA_VERSION_MISMATCH")

    latest_signal_id = str(output.get("source_signal_id") or "")
    current_signal_rows = [
        item
        for item in signal_assessments
        if isinstance(item, dict)
        and str(item.get("signal_id") or "") == latest_signal_id
    ]
    current_signal_impact = (
        current_signal_rows[-1].get("impact_assessment")
        if current_signal_rows
        and isinstance(current_signal_rows[-1].get("impact_assessment"), dict)
        else None
    )
    if latest_signal_id and current_signal_impact is None:
        stale_reasons.append("REASSESSMENT_SIGNAL_NOT_CURRENT_OR_ACTIVE")
    elif current_signal_impact is not None:
        reassessment_source_fields = {
            "operational_update_id": output.get("flight_operational_update_id"),
            "content_sha256": (
                output.get("flight_operational_event", {}).get("content_sha256")
                if isinstance(output.get("flight_operational_event"), dict)
                else None
            ),
            "plan_version_id": output.get("current_plan_version_id"),
            "trip_version": (
                output.get("input_snapshot", {}).get("base_trip_version")
                if isinstance(output.get("input_snapshot"), dict)
                else None
            ),
            "plan_trip_version": (
                output.get("impact_assessment", {}).get("plan_trip_version")
                if isinstance(output.get("impact_assessment"), dict)
                else None
            ),
            "plan_input_current": (
                output.get("impact_assessment", {}).get("plan_input_current")
                if isinstance(output.get("impact_assessment"), dict)
                else None
            ),
            "flight_segment_id": output.get("flight_segment_id"),
        }
        for field_name, recorded_value in reassessment_source_fields.items():
            if current_signal_impact.get(field_name) != recorded_value:
                stale_reasons.append(
                    f"REASSESSMENT_CURRENT_{field_name.upper()}_CHANGED"
                )

    expected_event_note_prefix = f"AgentRun {run_id} · "
    if run is not None and not any(
        event.event_type == "REASSESSED"
        and event.note is not None
        and event.note.startswith(expected_event_note_prefix)
        for event in events
    ):
        failures.append("REASSESSMENT_AUDIT_EVENT_MISSING")

    evidence_by_ref = {
        (row.kind, row.ref): row
        for row in evidence_rows
        if row.subject_type == "visit_follow_up" and row.subject_id == follow_up.id
    }
    expected_evidence = {
        "FLIGHT_OPERATIONAL_UPDATE": (
            f"flight-operational-update:{output.get('flight_operational_update_id')}"
        ),
        "APPOINTMENT_RECOVERY_IMPACT_SNAPSHOT": f"reassessment-impact:{run_id}",
        "APPOINTMENT_RECOVERY_REASSESSMENT": f"appointment-reassessment:{run_id}",
    }
    evidence_ids: set[str] = set()
    flight_event_value = output.get("flight_operational_event")
    flight_event = flight_event_value if isinstance(flight_event_value, dict) else {}
    impact_value = output.get("impact_assessment")
    impact = impact_value if isinstance(impact_value, dict) else {}
    for kind, ref in expected_evidence.items():
        row = evidence_by_ref.get((kind, ref))
        if row is None:
            failures.append(f"REASSESSMENT_EVIDENCE_MISSING:{kind}")
            continue
        evidence_ids.add(str(row.id))
        payload_hash = hashlib.sha256(
            _canonical_json(row.payload).encode("utf-8")
        ).hexdigest()
        if payload_hash != row.content_hash:
            failures.append(f"REASSESSMENT_EVIDENCE_HASH_MISMATCH:{kind}")
        if row.verification_status in {"DISPUTED", "REJECTED", "UNVERIFIED"}:
            failures.append(f"REASSESSMENT_EVIDENCE_NOT_ACCEPTABLE:{kind}")

        evidence_payload = row.payload if isinstance(row.payload, dict) else {}
        if kind == "FLIGHT_OPERATIONAL_UPDATE":
            if _canonical_json(evidence_payload) != _canonical_json(flight_event):
                failures.append("REASSESSMENT_FLIGHT_EVIDENCE_CONTENT_MISMATCH")
        elif kind == "APPOINTMENT_RECOVERY_IMPACT_SNAPSHOT":
            if (
                evidence_payload.get("signal_id") != output.get("source_signal_id")
                or evidence_payload.get("operational_update_id")
                != output.get("flight_operational_update_id")
                or evidence_payload.get("content_sha256")
                != flight_event.get("content_sha256")
                or evidence_payload.get("plan_version_id")
                != output.get("current_plan_version_id")
                or evidence_payload.get("target_visit_id") != str(follow_up.visit_id)
                or _canonical_json(evidence_payload.get("assessment"))
                != _canonical_json(impact)
                or _canonical_json(evidence_payload.get("target_visit_projection"))
                != _canonical_json(output.get("after"))
            ):
                failures.append("REASSESSMENT_IMPACT_EVIDENCE_CONTENT_MISMATCH")
        elif kind == "APPOINTMENT_RECOVERY_REASSESSMENT":
            expected_reassessment = {
                "workflow_id": str(workflow.id),
                "agent_run_id": run_id,
                "follow_up_id": str(follow_up.id),
                "outcome": output.get("outcome"),
                "decision": output.get("decision"),
                "input_sha256": output.get("input_sha256"),
                "before": output.get("before"),
                "after": output.get("after"),
                "changed_fields": output.get("changed_fields"),
                "data_gaps": output.get("data_gaps"),
                "plan_change_recommendation": output.get("plan_change_recommendation"),
                "guardrails": output.get("guardrails"),
            }
            if _canonical_json(evidence_payload) != _canonical_json(expected_reassessment):
                failures.append("REASSESSMENT_RESULT_EVIDENCE_CONTENT_MISMATCH")
    attestation = output.get("customer_window_attestation")
    attestation_ref = f"customer-window-attestation:{run_id}"
    attestation_row = evidence_by_ref.get(
        ("APPOINTMENT_WINDOW_ATTESTATION", attestation_ref)
    )
    if isinstance(attestation, dict):
        if attestation_row is None:
            failures.append("REASSESSMENT_WINDOW_ATTESTATION_EVIDENCE_MISSING")
        else:
            attestation_payload = (
                attestation_row.payload
                if isinstance(attestation_row.payload, dict)
                else {}
            )
            attestation_quality = (
                attestation_row.quality
                if isinstance(attestation_row.quality, dict)
                else {}
            )
            attestation_hash = hashlib.sha256(
                _canonical_json(attestation_payload).encode("utf-8")
            ).hexdigest()
            evidence_ids.add(str(attestation_row.id))
            if attestation_hash != attestation_row.content_hash:
                failures.append("REASSESSMENT_WINDOW_ATTESTATION_HASH_MISMATCH")
            if attestation_payload.get("customer_window") != attestation:
                failures.append("REASSESSMENT_WINDOW_ATTESTATION_CONTENT_MISMATCH")
            if not attestation_payload.get("asserted_by_user_id"):
                failures.append("REASSESSMENT_WINDOW_ATTESTATION_ACTOR_MISSING")
            if attestation_quality.get("externally_verified") is not False:
                failures.append("REASSESSMENT_WINDOW_ATTESTATION_OVERCLAIMED")
            if attestation_row.verification_status != "PARTIALLY_VERIFIED":
                failures.append("REASSESSMENT_WINDOW_ATTESTATION_STATUS_MISMATCH")
    elif attestation_row is not None:
        failures.append("REASSESSMENT_UNEXPECTED_WINDOW_ATTESTATION")

    snapshot_window = snapshot.get("customer_window_attestation")
    if _canonical_json(snapshot_window) != _canonical_json(attestation):
        failures.append("REASSESSMENT_WINDOW_ATTESTATION_INPUT_MISMATCH")

    output_evidence_ids = output.get("evidence_ids")
    if (
        not isinstance(output_evidence_ids, list)
        or not evidence_ids.issubset({str(item) for item in output_evidence_ids})
    ):
        failures.append("REASSESSMENT_EVIDENCE_MANIFEST_MISMATCH")

    guardrails_value = output.get("guardrails")
    guardrails = guardrails_value if isinstance(guardrails_value, dict) else {}
    for key in (
        "plan_mutated",
        "visit_mutated",
        "trip_mutated",
        "booking_changed",
        "customer_contacted",
        "customer_message_sent",
        "follow_up_completed",
        "customer_window_independently_verified",
    ):
        if guardrails.get(key) is not False:
            failures.append(f"REASSESSMENT_GUARDRAIL_FAILED:{key}")

    assessment_value = output.get("impact_assessment")
    assessment = assessment_value if isinstance(assessment_value, dict) else {}
    data_gaps = output.get("data_gaps")
    has_data_gaps = not isinstance(data_gaps, list) or bool(data_gaps)
    if assessment.get("propagation_complete") is not True:
        has_data_gaps = True
    if failures:
        status = "INTEGRITY_FAILURE"
        integrity_status = "FAILED"
    elif stale_reasons:
        status = "REASSESSMENT_STALE"
        integrity_status = "VERIFIED"
    elif has_data_gaps:
        status = "REASSESSMENT_REVIEW_REQUIRED"
        integrity_status = "VERIFIED"
    elif output.get("decision") == "PLAN_CHANGE_RECOMMENDATION_READY":
        status = "PLAN_CHANGE_HUMAN_SUBMISSION_REQUIRED"
        integrity_status = "VERIFIED"
    elif output.get("decision") == "REASSESSMENT_CLEARED":
        # The customer-window value may be a traveler attestation; it is not independently
        # verified and cannot close the Trip Risk Case by itself.
        status = "REASSESSMENT_CLEARED_PENDING_HUMAN_RESOLUTION"
        integrity_status = "VERIFIED"
    else:
        status = "REASSESSMENT_REVIEW_REQUIRED"
        integrity_status = "VERIFIED"

    return {
        "status": status,
        "integrity_status": integrity_status,
        "workflow_id": str(workflow.id),
        "agent_run_id": str(run.id) if run is not None else None,
        "decision": output.get("decision"),
        "outcome": output.get("outcome"),
        "latest_flight_operational_update_id": output.get("flight_operational_update_id"),
        "latest_signal_id": output.get("source_signal_id"),
        "plan_version_id": output.get("current_plan_version_id"),
        "customer_window_verification_status": output.get("customer_window_verification_status"),
        "evidence_ids": sorted(evidence_ids),
        "reasons": sorted(set(failures + stale_reasons)),
    }

def _recovery_readiness(
    trip: Trip,
    suggestion: ProactiveSuggestion,
    workflows: list[WorkflowInstance],
    runs: dict[UUID, AgentRun],
    follow_ups: list[VisitFollowUp],
    signal_assessments: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Verify the persisted recovery chain; a generated brief is not a completed recovery."""
    action = suggestion.proposed_action if isinstance(suggestion.proposed_action, dict) else {}
    raw_params = action.get("params")
    params: dict[str, Any] = raw_params if isinstance(raw_params, dict) else {}
    brief = params.get("recovery_brief")
    if not isinstance(brief, dict):
        return (
            {
                "status": "NOT_AVAILABLE",
                "integrity_status": "NOT_APPLICABLE",
                "expected_action_count": 0,
                "accepted_action_count": 0,
                "active_action_count": 0,
                "completed_action_count": 0,
                "dismissed_action_count": 0,
                "missing_action_indexes": [],
                "actions": [],
                "reasons": ["RECOVERY_BRIEF_NOT_PRESENT"],
            },
            [],
        )

    actions_value = brief.get("actions")
    actions = actions_value if isinstance(actions_value, list) else []
    if any(not isinstance(item, dict) for item in actions):
        actions = []
        actions_invalid = True
    else:
        actions_invalid = False


    if not actions and brief.get("status") == "NO_RECOVERY_ACTION_REQUIRED":
        source_value = brief.get("source")
        source = source_value if isinstance(source_value, dict) else {}
        guardrails_value = brief.get("guardrails")
        guardrails = guardrails_value if isinstance(guardrails_value, dict) else {}
        no_action_failures: list[str] = []
        no_action_stale_reasons: list[str] = []
        signal_id_value = params.get("signal_id")
        signal_id = str(signal_id_value).strip() if signal_id_value else ""
        if source.get("signal_id") != (signal_id or None):
            no_action_failures.append("NO_ACTION_SIGNAL_REFERENCE_MISMATCH")
        if brief.get("execution_mode") != "DETERMINISTIC":
            no_action_failures.append("NO_ACTION_EXECUTION_MODE_INVALID")
        if not isinstance(brief.get("agent_version"), str) or not brief.get("agent_version"):
            no_action_failures.append("NO_ACTION_AGENT_VERSION_MISSING")
        if not isinstance(brief.get("schema_version"), str) or not brief.get("schema_version"):
            no_action_failures.append("NO_ACTION_SCHEMA_VERSION_MISSING")
        if not isinstance(actions_value, list) or actions_invalid:
            no_action_failures.append("NO_ACTION_LIST_INVALID")
        if not isinstance(guardrails_value, dict):
            no_action_failures.append("NO_ACTION_GUARDRAILS_MISSING")
        else:
            if guardrails.get("human_decision_required") is not False:
                no_action_failures.append("NO_ACTION_HUMAN_DECISION_GUARDRAIL_MISMATCH")
            for key in (
                "plan_mutated",
                "booking_changed",
                "customer_contacted",
                "customer_message_sent",
                "candidate_windows_inferred",
            ):
                if guardrails.get(key) is not False:
                    no_action_failures.append(f"NO_ACTION_SIDE_EFFECT_GUARDRAIL_FAILED:{key}")
            if guardrails.get("candidate_windows") != []:
                no_action_failures.append("NO_ACTION_CANDIDATE_WINDOWS_NOT_EMPTY")
        if brief.get("propagation_complete") is not True:
            no_action_failures.append("NO_ACTION_PROPAGATION_INCOMPLETE")
        if brief.get("impact_snapshot_complete") is not True:
            no_action_failures.append("NO_ACTION_IMPACT_SNAPSHOT_INCOMPLETE")
        if brief.get("data_gaps") != []:
            no_action_failures.append("NO_ACTION_DATA_GAPS_PRESENT")
        assessments = [
            item
            for item in signal_assessments
            if isinstance(item, dict)
            and str(item.get("signal_id") or "") == signal_id
        ]
        current_impact = (
            assessments[-1].get("impact_assessment")
            if assessments and isinstance(assessments[-1].get("impact_assessment"), dict)
            else None
        )
        if signal_id and current_impact is None:
            no_action_stale_reasons.append("CURRENT_SIGNAL_IMPACT_NOT_AVAILABLE")
        elif current_impact is None:
            no_action_failures.append("NO_ACTION_CURRENT_IMPACT_MISSING")
        else:
            source_fields = {
                "operational_update_id": current_impact.get("operational_update_id"),
                "content_sha256": current_impact.get("content_sha256"),
                "plan_version_id": current_impact.get("plan_version_id"),
                "trip_version": current_impact.get("trip_version"),
                "plan_trip_version": current_impact.get("plan_trip_version"),
                "plan_input_current": current_impact.get("plan_input_current"),
                "binding_id": current_impact.get("binding_id"),
                "flight_segment_id": current_impact.get("flight_segment_id"),
            }
            for field_name, current_value in source_fields.items():
                if source.get(field_name) != current_value:
                    no_action_stale_reasons.append(
                        f"NO_ACTION_CURRENT_{field_name.upper()}_CHANGED"
                    )
            if current_impact.get("propagation_complete") is not True:
                no_action_stale_reasons.append("NO_ACTION_CURRENT_PROPAGATION_INCOMPLETE")
            if current_impact.get("status") not in {
                "OPERATIONAL_DELAY_ASSESSED",
                "NO_OPERATIONAL_DELAY",
            }:
                no_action_stale_reasons.append("NO_ACTION_CURRENT_IMPACT_NOT_ASSESSABLE")
            for field_name in (
                "affected_visit_ids",
                "missed_visit_ids",
                "unassessed_visit_ids",
            ):
                brief_ids = brief.get(field_name)
                current_ids = current_impact.get(field_name)
                if (
                    not isinstance(brief_ids, list)
                    or not isinstance(current_ids, list)
                    or any(not isinstance(value, str) for value in brief_ids)
                    or any(not isinstance(value, str) for value in current_ids)
                ):
                    no_action_failures.append(
                        f"NO_ACTION_{field_name.upper()}_INVALID"
                    )
                elif set(brief_ids) != set(current_ids):
                    no_action_stale_reasons.append(
                        f"NO_ACTION_CURRENT_{field_name.upper()}_CHANGED"
                    )

        if no_action_failures:
            no_action_status = "INTEGRITY_FAILURE"
            no_action_integrity = "FAILED"
        elif no_action_stale_reasons:
            no_action_status = "STALE_BRIEF"
            no_action_integrity = "VERIFIED"
        else:
            no_action_status = "NO_ACTION_REQUIRED_VERIFIED"
            no_action_integrity = "VERIFIED"
        no_action_hash = hashlib.sha256(
            _canonical_json(brief).encode("utf-8")
        ).hexdigest()
        no_action_readiness = {
            "status": no_action_status,
            "integrity_status": no_action_integrity,
            "workflow_id": None,
            "agent_run_id": None,
            "brief_status": brief.get("status"),
            "brief_sha256": no_action_hash,
            "expected_action_count": 0,
            "accepted_action_count": 0,
            "active_action_count": 0,
            "completed_action_count": 0,
            "dismissed_action_count": 0,
            "missing_action_indexes": [],
            "actions": [],
            "reasons": sorted(set(no_action_failures + no_action_stale_reasons)),
        }
        no_action_evidence = [
            {
                "type": "APPOINTMENT_RECOVERY_READINESS",
                "id": str(suggestion.id),
                "status": no_action_status,
                "integrity_status": no_action_integrity,
                "sha256": no_action_hash,
            }
        ]
        return no_action_readiness, no_action_evidence

    expected_source_suggestion_id = str(suggestion.id)
    workflow_by_id: dict[str, WorkflowInstance] = {str(row.id): row for row in workflows}
    workflows_for_suggestion = [
        row
        for row in workflows
        if isinstance(row.payload, dict)
        and row.payload.get("source_suggestion_id") == expected_source_suggestion_id
    ]
    raw_workflow_id = params.get("recovery_workflow_id")
    workflow_id = str(raw_workflow_id).strip() if raw_workflow_id else ""
    workflow: WorkflowInstance | None = workflow_by_id.get(workflow_id) if workflow_id else None
    integrity_failures: list[str] = []
    stale_reasons: list[str] = []

    if workflow is None and workflow_id:
        integrity_failures.append("RECOVERY_WORKFLOW_POINTER_NOT_FOUND")
    if workflow is None and not workflow_id:
        if len(workflows_for_suggestion) == 1:
            workflow = workflows_for_suggestion[0]
        elif len(workflows_for_suggestion) > 1:
            integrity_failures.append("MULTIPLE_RECOVERY_WORKFLOWS_FOR_SUGGESTION")
        else:
            integrity_failures.append("RECOVERY_WORKFLOW_MISSING")
    if workflow is not None and workflow_id and str(workflow.id) != workflow_id:
        integrity_failures.append("RECOVERY_WORKFLOW_POINTER_MISMATCH")
    if workflow is not None:
        if workflow.workflow_type != "APPOINTMENT_RECOVERY":
            integrity_failures.append("RECOVERY_WORKFLOW_TYPE_MISMATCH")
        if workflow.organization_id != suggestion.organization_id or workflow.trip_id != trip.id:
            integrity_failures.append("RECOVERY_WORKFLOW_SCOPE_MISMATCH")
        workflow_payload = workflow.payload if isinstance(workflow.payload, dict) else {}
        if workflow_payload.get("source_suggestion_id") != expected_source_suggestion_id:
            integrity_failures.append("RECOVERY_WORKFLOW_SUGGESTION_MISMATCH")
        if workflow_payload.get("source_trigger_id") != (
            str(suggestion.source_trigger_id) if suggestion.source_trigger_id else None
        ):
            integrity_failures.append("RECOVERY_WORKFLOW_TRIGGER_MISMATCH")
        if workflow.status != "COMPLETED":
            integrity_failures.append("RECOVERY_WORKFLOW_NOT_COMPLETED")

    raw_run_id = params.get("recovery_agent_run_id")
    run_id = str(raw_run_id).strip() if raw_run_id else ""
    if workflow is not None:
        payload_run_id = str((workflow.payload or {}).get("agent_run_id") or "").strip()
        if run_id and payload_run_id and run_id != payload_run_id:
            integrity_failures.append("RECOVERY_AGENT_RUN_REFERENCE_MISMATCH")
        if not run_id:
            run_id = payload_run_id

    run_uuid: UUID | None = None
    try:
        run_uuid = UUID(run_id) if run_id else None
    except (TypeError, ValueError):
        integrity_failures.append("RECOVERY_AGENT_RUN_ID_INVALID")
    run = runs.get(run_uuid) if run_uuid is not None else None
    if run is None:
        integrity_failures.append("RECOVERY_AGENT_RUN_MISSING")

    source_value = brief.get("source")
    source = source_value if isinstance(source_value, dict) else {}
    guardrails_value = brief.get("guardrails")
    guardrails = guardrails_value if isinstance(guardrails_value, dict) else {}
    required_guardrails = (
        "plan_mutated",
        "booking_changed",
        "customer_contacted",
        "customer_message_sent",
        "candidate_windows_inferred",
    )
    if not isinstance(guardrails_value, dict):
        integrity_failures.append("RECOVERY_GUARDRAILS_MISSING")
    elif any(guardrails.get(key) is not False for key in required_guardrails):
        integrity_failures.append("RECOVERY_SIDE_EFFECT_GUARDRAIL_FAILED")
    if guardrails.get("candidate_windows") != []:
        integrity_failures.append("RECOVERY_CANDIDATE_WINDOWS_MUST_BE_EMPTY")
    if actions_invalid:
        integrity_failures.append("RECOVERY_ACTION_LIST_INVALID")
    for action_index, action_item in enumerate(actions):
        if not isinstance(action_item, dict):
            continue
        if action_item.get("candidate_windows") != []:
            integrity_failures.append(
                f"RECOVERY_ACTION_CANDIDATE_WINDOWS_NOT_EMPTY:{action_index}"
            )
        customer_message = action_item.get("customer_message")
        if (
            not isinstance(customer_message, dict)
            or customer_message.get("status") != "NOT_DRAFTED"
        ):
            integrity_failures.append(
                f"RECOVERY_ACTION_CUSTOMER_MESSAGE_NOT_GUARDED:{action_index}"
            )
    if not isinstance(brief.get("agent_version"), str) or not brief.get("agent_version"):
        integrity_failures.append("RECOVERY_AGENT_VERSION_MISSING")
    if not isinstance(brief.get("schema_version"), str) or not brief.get("schema_version"):
        integrity_failures.append("RECOVERY_SCHEMA_VERSION_MISSING")

    signal_id_value = params.get("signal_id")
    signal_id = str(signal_id_value).strip() if signal_id_value else ""
    if source.get("signal_id") != (signal_id or None):
        integrity_failures.append("RECOVERY_BRIEF_SIGNAL_MISMATCH")
    matching_assessments = [
        item
        for item in signal_assessments
        if isinstance(item, dict) and str(item.get("signal_id") or "") == signal_id
    ]
    current_assessment = (
        matching_assessments[-1].get("impact_assessment")
        if matching_assessments
        and isinstance(matching_assessments[-1].get("impact_assessment"), dict)
        else None
    )
    if current_assessment is not None:
        if current_assessment.get("plan_input_current") is not True:
            stale_reasons.append("CURRENT_PLAN_INPUT_STALE")
        source_flight_segment_id = source.get("flight_segment_id")
        source_operational_update_id = source.get("operational_update_id")
        if source_flight_segment_id and source_operational_update_id:
            related_flight_assessments = [
                item
                for item in signal_assessments
                if isinstance(item, dict)
                and item.get("signal_type") == "FLIGHT_STATUS"
                and isinstance(item.get("impact_assessment"), dict)
                and item["impact_assessment"].get("flight_segment_id")
                == source_flight_segment_id
                and item["impact_assessment"].get("operational_update_id")
            ]
            if related_flight_assessments:
                latest_flight_assessment = related_flight_assessments[-1]["impact_assessment"]
                if (
                    latest_flight_assessment.get("operational_update_id")
                    != source_operational_update_id
                ):
                    stale_reasons.append("NEWER_FLIGHT_OPERATIONAL_EVENT_AVAILABLE")
        current_source_fields = {
            "operational_update_id": current_assessment.get("operational_update_id"),
            "content_sha256": current_assessment.get("content_sha256"),
            "plan_version_id": current_assessment.get("plan_version_id"),
            "trip_version": current_assessment.get("trip_version"),
            "plan_trip_version": current_assessment.get("plan_trip_version"),
            "plan_input_current": current_assessment.get("plan_input_current"),
            "binding_id": current_assessment.get("binding_id"),
            "flight_segment_id": current_assessment.get("flight_segment_id"),
        }
        for field_name, current_value in current_source_fields.items():
            if source.get(field_name) != current_value:
                stale_reasons.append(f"CURRENT_IMPACT_{field_name.upper()}_CHANGED")
    elif signal_id:
        stale_reasons.append("CURRENT_SIGNAL_IMPACT_NOT_AVAILABLE")

    if workflow is not None and isinstance(workflow.payload, dict):
        payload = workflow.payload
        if payload.get("source_signal_id") != (signal_id or ""):
            integrity_failures.append("RECOVERY_WORKFLOW_SIGNAL_MISMATCH")
        if payload.get("agent_type") != brief.get("agent_type"):
            integrity_failures.append("RECOVERY_WORKFLOW_AGENT_TYPE_MISMATCH")
        if payload.get("agent_version") != brief.get("agent_version"):
            integrity_failures.append("RECOVERY_WORKFLOW_AGENT_VERSION_MISMATCH")
        if payload.get("recovery_status") != brief.get("status"):
            integrity_failures.append("RECOVERY_WORKFLOW_STATUS_MISMATCH")
        if payload.get("plan_version_id") != source.get("plan_version_id"):
            integrity_failures.append("RECOVERY_WORKFLOW_PLAN_VERSION_MISMATCH")

    if run is not None:
        run_input = run.input if isinstance(run.input, dict) else {}
        if run.organization_id != suggestion.organization_id:
            integrity_failures.append("RECOVERY_AGENT_RUN_ORG_MISMATCH")
        if run.workflow_id != (workflow.id if workflow is not None else None):
            integrity_failures.append("RECOVERY_AGENT_RUN_WORKFLOW_MISMATCH")
        if run.agent_type != brief.get("agent_type") or run.agent_type != "APPOINTMENT_RECOVERY":
            integrity_failures.append("RECOVERY_AGENT_RUN_TYPE_MISMATCH")
        if run.status != "SUCCEEDED" or run.finished_at is None:
            integrity_failures.append("RECOVERY_AGENT_RUN_NOT_SUCCEEDED")
        if run_input.get("trip_id") != str(trip.id):
            integrity_failures.append("RECOVERY_AGENT_RUN_TRIP_MISMATCH")
        if run_input.get("suggestion_id") != expected_source_suggestion_id:
            integrity_failures.append("RECOVERY_AGENT_RUN_SUGGESTION_MISMATCH")
        if run_input.get("trigger_id") != (
            str(suggestion.source_trigger_id) if suggestion.source_trigger_id else None
        ):
            integrity_failures.append("RECOVERY_AGENT_RUN_TRIGGER_MISMATCH")
        if run_input.get("signal_id") != (signal_id or ""):
            integrity_failures.append("RECOVERY_AGENT_RUN_SIGNAL_MISMATCH")
        for input_key, source_key in (
            ("operational_update_id", "operational_update_id"),
            ("content_sha256", "content_sha256"),
            ("plan_version_id", "plan_version_id"),
            ("trip_version", "trip_version"),
            ("plan_trip_version", "plan_trip_version"),
            ("plan_input_current", "plan_input_current"),
        ):
            if run_input.get(input_key) != source.get(source_key):
                integrity_failures.append(f"RECOVERY_AGENT_RUN_{input_key.upper()}_MISMATCH")
        if workflow is not None and isinstance(workflow.payload, dict):
            if workflow.payload.get("trip_version") != run_input.get("trip_version"):
                integrity_failures.append("RECOVERY_WORKFLOW_TRIP_VERSION_MISMATCH")
            if workflow.payload.get("plan_trip_version") != run_input.get("plan_trip_version"):
                integrity_failures.append("RECOVERY_WORKFLOW_PLAN_TRIP_VERSION_MISMATCH")
            if workflow.payload.get("plan_input_current") != run_input.get("plan_input_current"):
                integrity_failures.append("RECOVERY_WORKFLOW_PLAN_FRESHNESS_MISMATCH")
        if run_input.get("agent_version") != brief.get("agent_version"):
            integrity_failures.append("RECOVERY_AGENT_RUN_VERSION_MISMATCH")
        if run_input.get("schema_version") != brief.get("schema_version"):
            integrity_failures.append("RECOVERY_AGENT_RUN_SCHEMA_MISMATCH")
        if _canonical_json(run.output) != _canonical_json(brief):
            integrity_failures.append("RECOVERY_AGENT_RUN_OUTPUT_MISMATCH")

    if integrity_failures:
        integrity_status = "FAILED"
        readiness_status = "INTEGRITY_FAILURE"
    elif stale_reasons:
        integrity_status = "VERIFIED"
        readiness_status = "STALE_BRIEF"
    else:
        integrity_status = "VERIFIED"
        brief_status = brief.get("status")
        if not actions:
            if (
                brief_status == "NO_RECOVERY_ACTION_REQUIRED"
                and brief.get("propagation_complete") is True
                and brief.get("impact_snapshot_complete") is True
                and not brief.get("data_gaps")
            ):
                readiness_status = "NO_ACTION_REQUIRED_VERIFIED"
            else:
                readiness_status = "INTEGRITY_FAILURE"
                integrity_status = "FAILED"
                integrity_failures.append(
                    "EMPTY_RECOVERY_ACTIONS_WITHOUT_COMPLETE_NO_ACTION_ASSESSMENT"
                )
        else:
            if guardrails.get("human_decision_required") is not True:
                integrity_failures.append("RECOVERY_HUMAN_DECISION_GUARDRAIL_MISMATCH")
                integrity_status = "FAILED"
                readiness_status = "INTEGRITY_FAILURE"
            follow_up_by_index: dict[int, VisitFollowUp] = {}
            duplicate_indexes: set[int] = set()
            for row in follow_ups:
                if run is None or row.source_agent_run_id != run.id:
                    continue
                if row.source_type != "APPOINTMENT_RECOVERY":
                    integrity_failures.append("RECOVERY_FOLLOW_UP_SOURCE_TYPE_MISMATCH")
                    continue
                index = row.source_index
                if index is None or index < 0 or index >= len(actions):
                    integrity_failures.append("RECOVERY_FOLLOW_UP_SOURCE_INDEX_INVALID")
                    continue
                if row.source_key != f"APPOINTMENT_RECOVERY:{run.id}:{index}":
                    integrity_failures.append("RECOVERY_FOLLOW_UP_SOURCE_KEY_MISMATCH")
                expected_visit_id = actions[index].get("visit_id")
                if expected_visit_id and str(row.visit_id) != str(expected_visit_id):
                    integrity_failures.append("RECOVERY_FOLLOW_UP_VISIT_MISMATCH")
                if row.confirmed_at is None or row.confirmed_by_user_id is None:
                    integrity_failures.append("RECOVERY_FOLLOW_UP_HUMAN_CONFIRMATION_MISSING")
                if index in follow_up_by_index:
                    duplicate_indexes.add(index)
                else:
                    follow_up_by_index[index] = row
            if duplicate_indexes:
                integrity_failures.append("DUPLICATE_RECOVERY_FOLLOW_UP_SOURCE_INDEX")
            if integrity_failures:
                integrity_status = "FAILED"
                readiness_status = "INTEGRITY_FAILURE"
            else:
                missing_indexes = sorted(set(range(len(actions))) - set(follow_up_by_index))
                statuses = [row.status for row in follow_up_by_index.values()]
                if any(
                    status not in {"OPEN", "IN_PROGRESS", "COMPLETED", "DISMISSED"}
                    for status in statuses
                ):
                    integrity_status = "FAILED"
                    readiness_status = "INTEGRITY_FAILURE"
                    integrity_failures.append("RECOVERY_FOLLOW_UP_STATUS_INVALID")
                elif not follow_up_by_index:
                    readiness_status = "AWAITING_HUMAN_DECISION"
                elif any(status == "IN_PROGRESS" for status in statuses):
                    readiness_status = "IN_PROGRESS"
                elif any(status == "OPEN" for status in statuses) and not missing_indexes:
                    readiness_status = "AWAITING_HUMAN_ACTION"
                elif all(status == "COMPLETED" for status in statuses) and not missing_indexes:
                    readiness_status = "REASSESSMENT_REQUIRED"
                elif all(status == "DISMISSED" for status in statuses) and not missing_indexes:
                    readiness_status = "HUMAN_REVIEW_REQUIRED"
                elif (
                    all(status in {"COMPLETED", "DISMISSED"} for status in statuses)
                    and not missing_indexes
                ):
                    readiness_status = "REASSESSMENT_REQUIRED"
                elif follow_up_by_index:
                    readiness_status = "PARTIALLY_ACCEPTED"
                else:
                    readiness_status = "AWAITING_HUMAN_DECISION"

    actions_read: list[dict[str, Any]] = []
    follow_up_by_index_for_read: dict[int, VisitFollowUp] = {}
    if run is not None:
        for row in follow_ups:
            if row.source_agent_run_id == run.id and row.source_type == "APPOINTMENT_RECOVERY":
                if row.source_index is not None:
                    follow_up_by_index_for_read[row.source_index] = row
    for index, action_item in enumerate(actions):
        if not isinstance(action_item, dict):
            continue
        follow_up = follow_up_by_index_for_read.get(index)
        actions_read.append(
            {
                "source_index": index,
                "action_code": action_item.get("action_code"),
                "visit_id": action_item.get("visit_id"),
                "decision_status": action_item.get("decision_status"),
                "follow_up_id": str(follow_up.id) if follow_up is not None else None,
                "follow_up_status": (
                    follow_up.status if follow_up is not None else "AWAITING_HUMAN_DECISION"
                ),
            }
        )

    accepted_count = len(follow_up_by_index_for_read)
    active_count = sum(
        row.status in ACTIVE_FOLLOW_UP_STATUSES for row in follow_up_by_index_for_read.values()
    )
    completed_count = sum(
        row.status == "COMPLETED" for row in follow_up_by_index_for_read.values()
    )
    dismissed_count = sum(
        row.status == "DISMISSED" for row in follow_up_by_index_for_read.values()
    )
    missing_action_indexes = sorted(set(range(len(actions))) - set(follow_up_by_index_for_read))
    readiness: dict[str, Any] = {
        "status": readiness_status,
        "integrity_status": integrity_status,
        "workflow_id": str(workflow.id) if workflow is not None else None,
        "agent_run_id": str(run.id) if run is not None else None,
        "brief_status": brief.get("status"),
        "brief_sha256": hashlib.sha256(_canonical_json(brief).encode("utf-8")).hexdigest(),
        "expected_action_count": len(actions),
        "accepted_action_count": accepted_count,
        "active_action_count": active_count,
        "completed_action_count": completed_count,
        "dismissed_action_count": dismissed_count,
        "missing_action_indexes": missing_action_indexes,
        "actions": actions_read,
        "reasons": sorted(set(integrity_failures + stale_reasons)),
    }
    evidence = [
        {
            "type": "APPOINTMENT_RECOVERY_READINESS",
            "id": str(run.id) if run is not None else expected_source_suggestion_id,
            "status": readiness_status,
            "integrity_status": integrity_status,
            "sha256": readiness["brief_sha256"],
        }
    ]
    if workflow is not None:
        evidence.append({"type": "APPOINTMENT_RECOVERY_WORKFLOW", "id": str(workflow.id)})
    if run is not None:
        evidence.append({"type": "APPOINTMENT_RECOVERY_AGENT_RUN", "id": str(run.id)})
    return readiness, evidence

def _build_groups(
    session: Session,
    organization_id: UUID,
    trip: Trip,
    *,
    now: datetime | None = None,
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
    linked_signal_ids: set[UUID] = set()
    for suggestion in suggestions:
        linked_action = (
            suggestion.proposed_action if isinstance(suggestion.proposed_action, dict) else {}
        )
        linked_params_raw = linked_action.get("params")
        linked_params: dict[str, Any] = (
            linked_params_raw if isinstance(linked_params_raw, dict) else {}
        )
        raw_linked_signal_id = linked_params.get("signal_id")
        if raw_linked_signal_id:
            try:
                linked_signal_ids.add(UUID(str(raw_linked_signal_id)))
            except (TypeError, ValueError):
                continue

    non_operational_flight_signal_ids: set[str] = set()
    if linked_signal_ids:
        linked_signals = session.scalars(
            select(ExternalSignal).where(
                ExternalSignal.organization_id == organization_id,
                ExternalSignal.id.in_(linked_signal_ids),
            )
        ).all()
        operational_markers = {
            "operational_update_id",
            "operational_update_content_sha256",
            "operational_update_type",
            "flight_operational_update",
        }
        non_operational_flight_signal_ids = {
            str(signal.id)
            for signal in linked_signals
            if signal.signal_type == "FLIGHT_STATUS"
            and not operational_markers.intersection(
                signal.payload if isinstance(signal.payload, dict) else {}
            )
        }

    reassessment_run_ids: set[UUID] = set()
    reassessment_workflows = list(
        session.scalars(
            select(WorkflowInstance)
            .where(
                WorkflowInstance.organization_id == organization_id,
                WorkflowInstance.trip_id == trip.id,
                WorkflowInstance.workflow_type == "APPOINTMENT_RECOVERY_REASSESSMENT",
            )
            .order_by(WorkflowInstance.created_at, WorkflowInstance.id)
        ).all()
    )
    reassessment_workflows_by_follow_up: dict[str, list[WorkflowInstance]] = {}
    for reassessment_workflow in reassessment_workflows:
        workflow_payload = (
            reassessment_workflow.payload
            if isinstance(reassessment_workflow.payload, dict)
            else {}
        )
        follow_up_ref = workflow_payload.get("follow_up_id")
        if isinstance(follow_up_ref, str):
            reassessment_workflows_by_follow_up.setdefault(follow_up_ref, []).append(
                reassessment_workflow
            )
        reassessment_run_id = workflow_payload.get("agent_run_id")
        if reassessment_run_id:
            try:
                reassessment_run_ids.add(UUID(str(reassessment_run_id)))
            except (TypeError, ValueError):
                continue

    recovery_workflows = list(
        session.scalars(
            select(WorkflowInstance)
            .where(
                WorkflowInstance.organization_id == organization_id,
                WorkflowInstance.trip_id == trip.id,
                WorkflowInstance.workflow_type == "APPOINTMENT_RECOVERY",
            )
            .order_by(WorkflowInstance.created_at, WorkflowInstance.id)
        ).all()
    )
    recovery_workflows_by_suggestion: dict[str, list[WorkflowInstance]] = {}
    recovery_run_ids: set[UUID] = set(reassessment_run_ids)
    for recovery_workflow in recovery_workflows:
        workflow_payload = (
            recovery_workflow.payload if isinstance(recovery_workflow.payload, dict) else {}
        )
        source_suggestion_id = workflow_payload.get("source_suggestion_id")
        if isinstance(source_suggestion_id, str):
            recovery_workflows_by_suggestion.setdefault(source_suggestion_id, []).append(
                recovery_workflow
            )
        raw_run_id = workflow_payload.get("agent_run_id")
        if raw_run_id:
            try:
                recovery_run_ids.add(UUID(str(raw_run_id)))
            except (TypeError, ValueError):
                continue
    for suggestion in suggestions:
        action_for_run = (
            suggestion.proposed_action if isinstance(suggestion.proposed_action, dict) else {}
        )
        params_for_run = action_for_run.get("params")
        if isinstance(params_for_run, dict) and params_for_run.get("recovery_agent_run_id"):
            try:
                recovery_run_ids.add(UUID(str(params_for_run["recovery_agent_run_id"])))
            except (TypeError, ValueError):
                continue
    recovery_run_ids.update({
        row.source_agent_run_id
        for row in session.scalars(
            select(VisitFollowUp).where(
                VisitFollowUp.organization_id == organization_id,
                VisitFollowUp.trip_id == trip.id,
                VisitFollowUp.source_agent_run_id.is_not(None),
            )
        ).all()
        if row.source_agent_run_id is not None
    })
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

    # A reassessment may be based on a newer provider event than the original
    # Recovery Brief. Route that event into the same source-backed Case by
    # following Reassessment Workflow -> source Recovery AgentRun -> original Signal.
    reassessment_signal_case_keys: dict[str, str] = {}
    for reassessment_workflow in reassessment_workflows:
        workflow_payload = (
            reassessment_workflow.payload
            if isinstance(reassessment_workflow.payload, dict)
            else {}
        )
        latest_signal_id = workflow_payload.get("source_signal_id")
        raw_source_run_id = workflow_payload.get("source_recovery_agent_run_id")
        if not latest_signal_id or not raw_source_run_id:
            continue
        try:
            source_run_id = UUID(str(raw_source_run_id))
        except (TypeError, ValueError):
            continue
        source_run = runs.get(source_run_id)
        source_input = source_run.input if source_run is not None and isinstance(
            source_run.input, dict
        ) else {}
        original_signal_id = source_input.get("signal_id")
        if original_signal_id:
            reassessment_signal_case_keys[str(latest_signal_id)] = (
                f"external_signal:{str(original_signal_id)}"
            )

    groups: dict[str, dict[str, Any]] = {}
    signal_group_keys: dict[str, str] = {}
    for suggestion in suggestions:
        if (
            suggestion.status not in ACTIVE_SUGGESTION_STATUSES
            and suggestion.status != "DISMISSED"
        ):
            continue
        key = f"suggestion:{suggestion.id}"
        action: dict[str, Any] = (
            suggestion.proposed_action if isinstance(suggestion.proposed_action, dict) else {}
        )
        raw_params = action.get("params")
        params: dict[str, Any] = raw_params if isinstance(raw_params, dict) else {}
        raw_signal_id = params.get("signal_id")
        normalized_signal_id = str(raw_signal_id).strip() if raw_signal_id else ""
        if normalized_signal_id in non_operational_flight_signal_ids:
            # Keep the suggestion in its own domain feed for human review, but do not
            # promote aircraft telemetry into a verified Trip Risk & Recovery Case.
            continue
        if normalized_signal_id:
            signal_group_keys[normalized_signal_id] = key
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
            should_create = follow_up.status in ACTIVE_FOLLOW_UP_STATUSES
            if not should_create:
                existing_case = session.scalars(
                    select(TripRiskCase.id).where(
                        TripRiskCase.organization_id == organization_id,
                        TripRiskCase.trip_id == trip.id,
                        TripRiskCase.case_key == group_key,
                    )
                ).one_or_none()
                if existing_case is None:
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
                "should_create": should_create,
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
                follow_up_source_type=follow_up.source_type,
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

    # ExternalSignal is the canonical source record for provider observations. Merge it
    # into the matching suggestion Case when possible; otherwise keep a signal-native Case.
    # OpenSky telemetry deliberately stays out of the risk queue unless a separately
    # validated FlightOperationalUpdate envelope is present.
    evaluation_now = now or session.scalar(select(func.clock_timestamp()))
    observed_at_cutoff = evaluation_now + MAX_FUTURE_OBSERVED_AT_SKEW
    subject_refs: list[tuple[str, str]] = [("TRIP", str(trip.id))]
    subject_refs.extend(
        ("COUNTRY", str(country).upper())
        for country in (trip.countries or [])
        if str(country).strip()
    )
    signal_subject_filter = or_(
        *[
            (ExternalSignal.subject_type == subject_type)
            & (ExternalSignal.subject_ref == subject_ref)
            for subject_type, subject_ref in subject_refs
        ]
    )
    existing_case_keys = set(
        session.scalars(
            select(TripRiskCase.case_key).where(
                TripRiskCase.organization_id == organization_id,
                TripRiskCase.trip_id == trip.id,
            )
        ).all()
    )
    external_signals = list(
        session.scalars(
            select(ExternalSignal)
            .where(
                ExternalSignal.organization_id == organization_id,
                signal_subject_filter,
                ExternalSignal.observed_at <= observed_at_cutoff,
                ExternalSignal.valid_from <= observed_at_cutoff,
                or_(
                    ExternalSignal.valid_until.is_(None),
                    ExternalSignal.valid_until > evaluation_now,
                ),
            )
            .order_by(ExternalSignal.observed_at, ExternalSignal.id)
        ).all()
    )
    for signal in external_signals:
        payload = signal.payload if isinstance(signal.payload, dict) else {}
        if signal.signal_type == "FLIGHT_STATUS":
            operational_markers = {
                "operational_update_id",
                "operational_update_content_sha256",
                "operational_update_type",
                "flight_operational_update",
            }
            if not operational_markers.intersection(payload):
                # Aircraft position/velocity snapshots are not airline operational facts.
                continue
            impact = external_impact_service.assess_flight_signal_impact(
                session, organization_id, trip, signal
            )
        elif signal.signal_type == "WEATHER_DISRUPTION":
            impact = external_impact_service.assess_weather_signal_impact(
                session, organization_id, trip, signal
            )
        elif signal.signal_type in {"VISA_POLICY_CHANGE", "FX_VOLATILITY"}:
            impact = {
                "signal_id": str(signal.id),
                "signal_type": signal.signal_type,
                "status": "SOURCE_ONLY_REQUIRES_HUMAN_REVIEW",
                "assessment_method": "SOURCE_ONLY_MANUAL_REVIEW",
                "plan_version_id": None,
                "explanation": (
                    "该类外部信号目前没有经过确定性验证的行程影响适配器；"
                    "仅保留 Provider 来源和有效时间，必须人工核验，不推断签证结论或汇率损失。"
                ),
            }
        else:
            continue

        signal_id = str(signal.id)
        signal_case_key = f"external_signal:{signal_id}"
        suggestion_key = signal_group_keys.get(signal_id)
        reassessment_case_key = reassessment_signal_case_keys.get(signal_id)
        if reassessment_case_key and (
            reassessment_case_key in existing_case_keys
            or reassessment_case_key in groups
        ):
            case_key = reassessment_case_key
        elif signal_case_key in existing_case_keys:
            case_key = signal_case_key
        elif suggestion_key and suggestion_key in existing_case_keys:
            # Preserve a suggestion-key Case created by the earlier projection version.
            case_key = suggestion_key
        else:
            # New signal-backed episodes use the stable source identity, not a transient
            # suggestion ID. A suggestion can be regenerated as signal content changes.
            case_key = signal_case_key
        if case_key == signal_case_key and suggestion_key and suggestion_key in groups:
            linked_group = groups.pop(suggestion_key)
            linked_group["case_key"] = signal_case_key
            groups[signal_case_key] = linked_group
        group = groups.get(case_key)
        assessment_hash = hashlib.sha256(
            _canonical_json(impact).encode("utf-8")
        ).hexdigest()
        signal_snapshot = {
            "provider": signal.provider,
            "signal_type": signal.signal_type,
            "subject_type": signal.subject_type,
            "subject_ref": signal.subject_ref,
            "payload": payload,
            "confidence": signal.confidence,
            "observed_at": signal.observed_at.isoformat(),
            "valid_from": signal.valid_from.isoformat(),
            "valid_until": signal.valid_until.isoformat() if signal.valid_until else None,
            "dedupe_key": signal.dedupe_key,
        }
        signal_snapshot_hash = hashlib.sha256(
            _canonical_json(signal_snapshot).encode("utf-8")
        ).hexdigest()

        if signal.signal_type == "FLIGHT_STATUS":
            update_type = str(impact.get("update_type") or "")
            flight_labels = {
                "SCHEDULE_CHANGE": "航班计划变化",
                "DEPARTURE_DELAY": "航班起飞延误",
                "ARRIVAL_DELAY": "航班到达延误",
                "CANCELLED": "航班取消",
                "DIVERTED": "航班备降",
                "GATE_CHANGE": "航班登机口变化",
            }
            default_title = (
                f"{impact.get('flight_designator') or '航班'} "
                f"{flight_labels.get(update_type, '运营状态待核验')}"
            )
            missed_visits = impact.get("missed_visit_ids") or []
            affected_visits = impact.get("affected_visit_ids") or []
            if update_type in {"CANCELLED", "DIVERTED"} or missed_visits:
                signal_severity = "HIGH"
            elif impact.get("status") == "NO_SCHEDULE_IMPACT_REPORTED" or (
                impact.get("status") == "OPERATIONAL_DELAY_ASSESSED" and not affected_visits
            ):
                signal_severity = "LOW"
            else:
                signal_severity = "MEDIUM"
        elif signal.signal_type == "WEATHER_DISRUPTION":
            default_title = "恶劣天气暴露待核验"
            if impact.get("status") == "PLACE_AND_TIME_OVERLAP":
                signal_severity = "HIGH"
            elif impact.get("status") in {
                "PLACE_MATCH_NO_TIME_OVERLAP",
                "NO_PLANNED_STOP_AT_SIGNAL_PLACE",
            }:
                signal_severity = "LOW"
            else:
                signal_severity = "MEDIUM"
        elif signal.signal_type == "VISA_POLICY_CHANGE":
            default_title = "签证政策变化待核验"
            signal_severity = "MEDIUM"
        else:
            default_title = "汇率波动信号待核验"
            signal_severity = "MEDIUM"

        source_ref = _source_ref(
            "EXTERNAL_SIGNAL",
            signal.id,
            provider=signal.provider,
            signal_type=signal.signal_type,
            subject_type=signal.subject_type,
            subject_ref=signal.subject_ref,
            confidence=signal.confidence,
            observed_at=signal.observed_at.isoformat(),
            valid_from=signal.valid_from.isoformat(),
            valid_until=signal.valid_until.isoformat() if signal.valid_until else None,
            dedupe_key=signal.dedupe_key,
            content_sha256=signal_snapshot_hash,
        )
        evidence = [
            {
                "type": "EXTERNAL_SIGNAL_PAYLOAD",
                "id": signal_id,
                "sha256": signal_snapshot_hash,
            },
            {
                "type": "IMPACT_ASSESSMENT",
                "id": signal_id,
                "sha256": assessment_hash,
                "status": impact.get("status"),
                "assessment_method": impact.get("assessment_method"),
            },
        ]
        if impact.get("plan_version_id"):
            evidence.append(
                {
                    "type": "PLAN_VERSION",
                    "id": str(impact["plan_version_id"]),
                    "version_number": impact.get("plan_version_number"),
                }
            )
        if impact.get("operational_update_id"):
            evidence.append(
                {
                    "type": "FLIGHT_OPERATIONAL_UPDATE",
                    "id": str(impact["operational_update_id"]),
                    "provider": impact.get("provider"),
                    "provider_event_id": impact.get("provider_event_id"),
                    "update_type": impact.get("update_type"),
                    "content_sha256": impact.get("content_sha256"),
                }
            )
        if impact.get("flight_segment_id"):
            evidence.append(
                {
                    "type": "FLIGHT_SEGMENT",
                    "id": str(impact["flight_segment_id"]),
                    "flight_designator": impact.get("flight_designator"),
                }
            )
        if impact.get("binding_id"):
            evidence.append(
                {
                    "type": "FLIGHT_SEGMENT_PLAN_BINDING",
                    "id": str(impact["binding_id"]),
                    "plan_version_id": impact.get("plan_version_id"),
                }
            )
        for stop in impact.get("matched_stops", []):
            evidence.append(
                {
                    "type": "PLAN_STOP",
                    "id": str(stop["plan_stop_id"]),
                    "visit_id": str(stop["visit_id"]),
                    "time_window_overlap": stop.get("time_window_overlap"),
                }
            )
        for stop in impact.get("stop_impacts", []):
            evidence.append(
                {
                    "type": "PLAN_STOP_IMPACT",
                    "id": str(stop["plan_stop_id"]),
                    "visit_id": str(stop["visit_id"]),
                    "appointment_status": stop.get("appointment_status"),
                    "delay_minutes": stop.get("delay_minutes"),
                    "reason": stop.get("reason"),
                }
            )
        for leg in impact.get("related_travel_legs", []):
            leg_id = leg.get("plan_travel_leg_id")
            if leg_id:
                evidence.append(
                    {
                        "type": "PLAN_TRAVEL_LEG",
                        "id": str(leg_id),
                        "mode": leg.get("mode"),
                        "relation": leg.get("relation"),
                        "stale": leg.get("stale"),
                        "schedule_slack_minutes": leg.get("estimated_schedule_slack_minutes"),
                    }
                )
        linked_leg = impact.get("linked_air_leg")
        if isinstance(linked_leg, dict) and linked_leg.get("plan_travel_leg_id"):
            evidence.append(
                {
                    "type": "PLAN_TRAVEL_LEG",
                    "id": str(linked_leg["plan_travel_leg_id"]),
                    "mode": linked_leg.get("mode"),
                    "stale": linked_leg.get("stale"),
                }
            )

        if group is None:
            provider_summary = str(payload.get("summary") or "").strip()
            explanation = str(impact.get("explanation") or "").strip()
            summary_parts = [
                f"外部源 {signal.provider} 报告：{provider_summary}"
                if provider_summary
                else f"外部源 {signal.provider} 报告了一条相关信号。",
                explanation,
            ]
            group = {
                "case_key": case_key,
                "risk_type": {
                    "VISA_POLICY_CHANGE": "VISA",
                    "WEATHER_DISRUPTION": "WEATHER",
                    "FX_VOLATILITY": "FINANCIAL",
                    "FLIGHT_STATUS": "FLIGHT",
                }[signal.signal_type],
                "severity": signal_severity,
                "title": default_title,
                "summary": " ".join(part for part in summary_parts if part)[:8000],
                "source_refs": [],
                "evidence_refs": [],
                "context": {},
                "suggestion": None,
                "follow_ups": [],
                "should_create": True,
            }
            groups[case_key] = group
        else:
            severity_rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
            if severity_rank.get(signal_severity, 1) > severity_rank.get(group["severity"], 1):
                group["severity"] = signal_severity

        group["source_refs"].append(source_ref)
        group["evidence_refs"].extend(evidence)
        assessments = group["context"].get("external_signal_assessments")
        assessments = assessments if isinstance(assessments, list) else []
        assessments = [
            item for item in assessments
            if not isinstance(item, dict) or item.get("signal_id") != signal_id
        ]
        assessments.append(
            {
                "signal_id": signal_id,
                "signal_type": signal.signal_type,
                "provider": signal.provider,
                "observed_at": signal.observed_at.isoformat(),
                "content_sha256": signal_snapshot_hash,
                "impact_assessment_sha256": assessment_hash,
                "impact_assessment": impact,
            }
        )
        group["context"]["external_signal_assessments"] = sorted(
            assessments, key=lambda item: (str(item.get("observed_at")), str(item.get("signal_id")))
        )
        group["context"]["external_signal_count"] = len(assessments)
        existing_case_keys.add(case_key)

    recovery_follow_up_ids = [
        row.id
        for row in follow_ups
        if row.source_type == "APPOINTMENT_RECOVERY"
        and row.source_agent_run_id is not None
    ]
    reassessment_events_by_follow_up: dict[UUID, list[VisitFollowUpEvent]] = {}
    reassessment_evidence_by_follow_up: dict[UUID, list[Evidence]] = {}
    if recovery_follow_up_ids:
        for event in session.scalars(
            select(VisitFollowUpEvent).where(
                VisitFollowUpEvent.organization_id == organization_id,
                VisitFollowUpEvent.follow_up_id.in_(recovery_follow_up_ids),
                VisitFollowUpEvent.event_type == "REASSESSED",
            )
        ).all():
            reassessment_events_by_follow_up.setdefault(event.follow_up_id, []).append(event)
        for evidence_row in session.scalars(
            select(Evidence).where(
                Evidence.organization_id == organization_id,
                Evidence.subject_type == "visit_follow_up",
                Evidence.subject_id.in_(recovery_follow_up_ids),
                Evidence.kind.in_(
                    [
                        "FLIGHT_OPERATIONAL_UPDATE",
                        "APPOINTMENT_RECOVERY_IMPACT_SNAPSHOT",
                        "APPOINTMENT_RECOVERY_REASSESSMENT",
                        "APPOINTMENT_WINDOW_ATTESTATION",
                    ]
                ),
            )
        ).all():
            reassessment_evidence_by_follow_up.setdefault(
                evidence_row.subject_id, []
            ).append(evidence_row)

    # Recovery readiness is a projection over the persisted AgentRun, Workflow and
    # human-confirmed FollowUps. It does not execute actions or close a risk itself.
    for group in groups.values():
        source_suggestion = group.get("suggestion")
        if source_suggestion is not None and (
            source_suggestion.suggestion_type == "FLIGHT_STATUS_ALERT"
            or isinstance(source_suggestion.proposed_action, dict)
            and isinstance(source_suggestion.proposed_action.get("params"), dict)
            and source_suggestion.proposed_action["params"].get("recovery_brief") is not None
        ):
            recovery_readiness, recovery_evidence = _recovery_readiness(
                trip,
                source_suggestion,
                recovery_workflows_by_suggestion.get(str(source_suggestion.id), []),
                runs,
                group["follow_ups"],
                group["context"].get("external_signal_assessments", []),
            )
            action_readiness = {
                str(item.get("follow_up_id")): item
                for item in recovery_readiness.get("actions", [])
                if item.get("follow_up_id")
            }
            for follow_up in group["follow_ups"]:
                if follow_up.source_type != "APPOINTMENT_RECOVERY":
                    continue
                action_item = action_readiness.get(str(follow_up.id))
                if action_item is None:
                    continue
                source_run = runs.get(follow_up.source_agent_run_id)
                reassessment = _follow_up_reassessment_readiness(
                    follow_up,
                    source_run,
                    reassessment_workflows_by_follow_up.get(str(follow_up.id), []),
                    runs,
                    reassessment_events_by_follow_up.get(follow_up.id, []),
                    reassessment_evidence_by_follow_up.get(follow_up.id, []),
                    group["context"].get("external_signal_assessments", []),
                )
                action_item["reassessment"] = reassessment
                if reassessment.get("status") != "NOT_REASSESSED":
                    group["evidence_refs"].append(
                        {
                            "type": "APPOINTMENT_RECOVERY_REASSESSMENT_READINESS",
                            "id": str(reassessment.get("agent_run_id") or follow_up.id),
                            "status": reassessment.get("status"),
                            "integrity_status": reassessment.get("integrity_status"),
                            "evidence_ids": reassessment.get("evidence_ids", []),
                        }
                    )
            actions = recovery_readiness.get("actions", [])
            follow_up_statuses = [
                item.get("follow_up_status")
                for item in actions
                if item.get("follow_up_id")
            ]
            if recovery_readiness.get("integrity_status") == "VERIFIED" and follow_up_statuses:
                reassessments = [
                    item.get("reassessment")
                    for item in actions
                    if item.get("follow_up_id")
                ]
                if (
                    all(status == "COMPLETED" for status in follow_up_statuses)
                    and all(
                        isinstance(item, dict)
                        and item.get("status") == "REASSESSMENT_CLEARED_PENDING_HUMAN_RESOLUTION"
                        for item in reassessments
                    )
                ):
                    recovery_readiness["status"] = "REASSESSMENT_CLEARED_PENDING_HUMAN_RESOLUTION"
                elif any(
                    isinstance(item, dict)
                    and item.get("status") == "PLAN_CHANGE_HUMAN_SUBMISSION_REQUIRED"
                    for item in reassessments
                ):
                    recovery_readiness["status"] = "PLAN_CHANGE_HUMAN_SUBMISSION_REQUIRED"
                elif any(
                    isinstance(item, dict)
                    and item.get("status") in {
                        "REASSESSMENT_REVIEW_REQUIRED",
                        "REASSESSMENT_STALE",
                        "INTEGRITY_FAILURE",
                    }
                    for item in reassessments
                ):
                    recovery_readiness["status"] = "REASSESSMENT_REVIEW_REQUIRED"
            group["context"]["recovery_readiness"] = recovery_readiness
            group["evidence_refs"].extend(recovery_evidence)

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

    # A FollowUp-only Case follows the terminal state of its source task. Do not leave
    # a zombie OPEN risk behind after the task is completed or dismissed.
    linked_follow_ups = group.get("follow_ups", [])
    if group.get("suggestion") is None and linked_follow_ups:
        if all(row.status == "COMPLETED" for row in linked_follow_ups):
            target_status = "RESOLVED"
            resolution_basis = "FOLLOW_UP_COMPLETED"
            resolution_note = "全部来源跟进项已完成"
        elif all(row.status == "DISMISSED" for row in linked_follow_ups):
            target_status = "DISMISSED"
            resolution_basis = "FOLLOW_UP_DISMISSED"
            resolution_note = "全部来源跟进项已驳回"
        else:
            target_status = None
            resolution_basis = None
            resolution_note = None

        if target_status is not None:
            if case.status != target_status:
                previous_status = case.status
                case.status = target_status
                case.resolved_at = now if target_status == "RESOLVED" else None
                case.dismissed_at = now if target_status == "DISMISSED" else None
                case.resolution_basis = resolution_basis
                case.resolution_note = resolution_note
                case.next_review_at = None
                case.version += 1
                _append_event(
                    session,
                    case,
                    event_type=target_status,
                    from_status=previous_status,
                    note=resolution_note,
                    detail={
                        "resolution_basis": resolution_basis,
                        "source_fingerprint": fingerprint,
                    },
                )
                report.cases_updated += 1
            elif source_changed:
                case.version += 1
                _append_event(
                    session,
                    case,
                    event_type="SOURCE_LINKED",
                    from_status=case.status,
                    detail={"source_fingerprint": fingerprint, "source_refs": source_refs},
                )
                report.cases_updated += 1
            return case

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
    groups = _build_groups(session, org, trip, now=current)
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
        "RESOLVED": set(),
        "DISMISSED": set(),
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
        if resolution_basis not in {
            "PLAN_REVALIDATED",
            "FOLLOW_UP_COMPLETED",
            "TRIP_CLOSED",
            "HUMAN_ATTESTATION",
        }:
            raise Unprocessable("RESOLVED 必须使用已完成、已验证或人工确认类关闭依据")
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
            .order_by(TripRiskCaseEvent.case_version.desc())
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
