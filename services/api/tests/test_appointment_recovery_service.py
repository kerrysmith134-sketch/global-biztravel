from datetime import UTC, datetime

from app.services.appointment_recovery_service import build_recovery_brief

GENERATED_AT = datetime(2026, 11, 27, 9, 0, tzinfo=UTC)


def test_missing_active_plan_fails_closed_to_trip_review() -> None:
    brief = build_recovery_brief(
        {"status": "NO_ACTIVE_PLAN", "stop_impacts": []},
        generated_at=GENERATED_AT,
    )

    assert brief["status"] == "REVIEW_REQUIRED"
    assert brief["propagation_complete"] is False
    assert brief["impact_snapshot_complete"] is False
    assert brief["guardrails"]["human_decision_required"] is True
    assert brief["actions"][0]["scope"] == "TRIP"
    assert brief["actions"][0]["action_code"] == "REVALIDATE_FLIGHT_PLAN_IMPACT_ASSESSMENT"


def test_unassessed_visit_without_stop_row_gets_explicit_action() -> None:
    brief = build_recovery_brief(
        {
            "status": "ARRIVAL_ESTIMATE_MISSING",
            "propagation_complete": False,
            "unassessed_visit_ids": ["visit-1"],
            "stop_impacts": [],
        },
        generated_at=GENERATED_AT,
    )

    assert brief["status"] == "REVIEW_REQUIRED"
    assert brief["unassessed_visit_ids"] == ["visit-1"]
    assert brief["actions"][0]["visit_id"] == "visit-1"
    assert brief["actions"][0]["action_code"] == "REVALIDATE_TRAVEL_CHAIN"
    assert "STOP_IMPACT_ROW_MISSING" in brief["data_gaps"]


def test_unknown_appointment_status_is_not_silently_ignored() -> None:
    brief = build_recovery_brief(
        {
            "status": "OPERATIONAL_DELAY_ASSESSED",
            "propagation_complete": True,
            "stop_impacts": [
                {
                    "visit_id": "visit-2",
                    "appointment_status": "NEW_PROVIDER_STATUS",
                    "delay_minutes": 15,
                }
            ],
        },
        generated_at=GENERATED_AT,
    )

    assert brief["status"] == "REVIEW_REQUIRED"
    assert brief["actions"][0]["appointment_status"] == "UNASSESSED"
    assert brief["actions"][0]["action_code"] == "REVALIDATE_TRAVEL_CHAIN"
    assert "UNRECOGNIZED_APPOINTMENT_STATUS" in brief["data_gaps"]


def test_conflicting_rows_for_same_visit_are_unassessed() -> None:
    brief = build_recovery_brief(
        {
            "status": "OPERATIONAL_DELAY_ASSESSED",
            "propagation_complete": True,
            "stop_impacts": [
                {"visit_id": "visit-3", "appointment_status": "WITHIN_WINDOW", "delay_minutes": 0},
                {"visit_id": "visit-3", "appointment_status": "MISSED_WINDOW", "delay_minutes": 20},
            ],
        },
        generated_at=GENERATED_AT,
    )

    assert brief["status"] == "REVIEW_REQUIRED"
    assert brief["actions"][0]["appointment_status"] == "UNASSESSED"
    assert brief["actions"][0]["action_code"] == "REVALIDATE_TRAVEL_CHAIN"
    assert brief["unassessed_visit_ids"] == ["visit-3"]


def test_string_true_is_not_accepted_as_complete_propagation() -> None:
    brief = build_recovery_brief(
        {
            "status": "OPERATIONAL_DELAY_ASSESSED",
            "propagation_complete": "true",
            "stop_impacts": [],
        },
        generated_at=GENERATED_AT,
    )

    assert brief["status"] == "REVIEW_REQUIRED"
    assert brief["propagation_complete"] is False
    assert brief["actions"][0]["scope"] == "TRIP"


def test_complete_zero_delay_assessment_may_have_no_recovery_action() -> None:
    brief = build_recovery_brief(
        {
            "status": "NO_OPERATIONAL_DELAY",
            "propagation_complete": True,
            "stop_impacts": [
                {
                    "visit_id": "visit-4",
                    "appointment_status": "WITHIN_WINDOW",
                    "delay_minutes": 0,
                }
            ],
        },
        generated_at=GENERATED_AT,
    )

    assert brief["status"] == "NO_RECOVERY_ACTION_REQUIRED"
    assert brief["actions"] == []
    assert brief["impact_snapshot_complete"] is True
    assert brief["guardrails"]["human_decision_required"] is False

def test_malformed_stop_rows_cannot_be_a_complete_assessment() -> None:
    brief = build_recovery_brief(
        {
            "status": "OPERATIONAL_DELAY_ASSESSED",
            "propagation_complete": True,
            "stop_impacts": [{"appointment_status": "WITHIN_WINDOW", "delay_minutes": 0}],
        },
        generated_at=GENERATED_AT,
    )

    assert brief["status"] == "REVIEW_REQUIRED"
    assert brief["propagation_complete"] is False
    assert brief["actions"][0]["scope"] == "TRIP"
    assert "STOP_IMPACT_VISIT_ID_MISSING" in brief["data_gaps"]
