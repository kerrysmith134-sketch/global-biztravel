"""Trip Risk & Recovery aggregate: cross-source linkage, lifecycle, escalation and concurrency."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.geo import Place
from app.models.notifications import Notification
from app.models.proactive import ProactiveSuggestion
from app.models.records import VisitFollowUp
from app.models.trip import Trip
from app.models.trip_risks import TripRiskCase, TripRiskCaseEvent
from app.models.workflow import AgentRun, WorkflowInstance
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "33333333-3333-3333-3333-333333333333"
USER_A = "22222222-2222-2222-2222-222222222222"
USER_B = "44444444-4444-4444-4444-444444444444"
ADMIN_A = "55555555-5555-5555-5555-555555555555"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers(*, org: str = ORG_A, user: str = USER_A, key: str | None = None) -> dict[str, str]:
    result = {"X-Org-Id": org, "X-User-Id": user}
    if key is not None:
        result["Idempotency-Key"] = key
    return result


def make_trip(client: TestClient) -> tuple[str, str, str]:
    marker = uuid.uuid4().hex[:8]
    now = datetime.now(UTC)
    trip = client.post(
        "/v1/trips",
        headers=headers(key=str(uuid.uuid4())),
        json={
            "name": f"Trip Risk integration · {marker}",
            "window_start": (now + timedelta(days=2)).isoformat(),
            "window_end": (now + timedelta(days=6)).isoformat(),
            "countries": ["DE"],
            "currency": "EUR",
        },
    )
    assert trip.status_code == 201, trip.text
    trip_id = trip.json()["id"]

    place = client.post(
        "/v1/places",
        headers=headers(key=str(uuid.uuid4())),
        json={
            "name": f"Trip Risk location · {marker}",
            "city": "Berlin",
            "country_code": "DE",
            "lat": 52.52,
            "lon": 13.405,
        },
    )
    assert place.status_code == 201, place.text
    place_id = place.json()["id"]

    visit = client.post(
        f"/v1/trips/{trip_id}/visits",
        headers=headers(key=str(uuid.uuid4())),
        json={
            "place_id": place_id,
            "title": "Customer appointment",
            "service_minutes": 60,
        },
    )
    assert visit.status_code == 201, visit.text
    return trip_id, place_id, visit.json()["id"]


def seed_flight_recovery_sources(trip_id: str, visit_id: str) -> tuple[str, str, str]:
    """Create one flight risk suggestion and one linked accepted recovery FollowUp."""
    trip_uuid = UUID(trip_id)
    visit_uuid = UUID(visit_id)
    suggestion_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    run_id = uuid.uuid4()
    follow_up_id = uuid.uuid4()
    now = datetime.now(UTC)

    with org_session(UUID(ORG_A)) as session:
        session.add(
            ProactiveSuggestion(
                id=suggestion_id,
                organization_id=UUID(ORG_A),
                trip_id=trip_uuid,
                suggestion_type="FLIGHT_STATUS_ALERT",
                status="PENDING",
                title="LH123 arrival delay may affect customer appointment",
                rationale="The provider reported a material arrival delay; the appointment needs review.",
                evidence=[
                    {
                        "kind": "FLIGHT_OPERATIONAL_UPDATE",
                        "ref": "flight-operations:provider-event-123",
                        "retrieved_at": now.isoformat(),
                    }
                ],
                proposed_action={
                    "kind": "PREPARE_REVIEW",
                    "tool": "trip.prepare_external_review",
                    "params": {"signal_type": "FLIGHT_STATUS"},
                },
                confidence=0.96,
                requires_approval=True,
                severity="HIGH",
                dedupe_key=f"test-flight-risk:{uuid.uuid4()}",
                push_status="PUSHED",
                notified_at=now,
            )
        )
        session.add(
            WorkflowInstance(
                id=workflow_id,
                organization_id=UUID(ORG_A),
                trip_id=trip_uuid,
                workflow_type="APPOINTMENT_RECOVERY",
                status="COMPLETED",
                payload={
                    "agent_run_id": str(run_id),
                    "source_suggestion_id": str(suggestion_id),
                },
            )
        )
        session.flush()
        session.add(
            AgentRun(
                id=run_id,
                organization_id=UUID(ORG_A),
                workflow_id=workflow_id,
                agent_type="APPOINTMENT_RECOVERY",
                status="SUCCEEDED",
                input={
                    "trip_id": str(trip_uuid),
                    "suggestion_id": str(suggestion_id),
                },
                output={"actions": [{"visit_id": str(visit_uuid)}]},
                started_at=now,
                finished_at=now,
            )
        )
        session.add(
            VisitFollowUp(
                id=follow_up_id,
                organization_id=UUID(ORG_A),
                trip_id=trip_uuid,
                visit_id=visit_uuid,
                source_type="APPOINTMENT_RECOVERY",
                source_key=f"APPOINTMENT_RECOVERY:{run_id}:0",
                source_agent_run_id=run_id,
                source_index=0,
                title="Confirm a revised customer appointment window",
                due_date=(now + timedelta(days=1)).date().isoformat(),
                status="OPEN",
                created_by_user_id=UUID(ADMIN_A),
                confirmed_by_user_id=UUID(ADMIN_A),
                confirmed_at=now,
            )
        )
    return str(suggestion_id), str(run_id), str(follow_up_id)


def seed_manual_follow_up(trip_id: str, visit_id: str) -> str:
    follow_up_id = uuid.uuid4()
    now = datetime.now(UTC)
    with org_session(UUID(ORG_A)) as session:
        session.add(
            VisitFollowUp(
                id=follow_up_id,
                organization_id=UUID(ORG_A),
                trip_id=UUID(trip_id),
                visit_id=UUID(visit_id),
                source_type="MANUAL",
                source_key=f"MANUAL:{uuid.uuid4()}",
                source_index=None,
                title="Submit visit outcome and confirm next steps",
                due_date=(now + timedelta(days=1)).date().isoformat(),
                status="OPEN",
                created_by_user_id=UUID(ADMIN_A),
                confirmed_by_user_id=UUID(ADMIN_A),
                confirmed_at=now,
            )
        )
    return str(follow_up_id)


def cleanup(trip_id: str, place_id: str) -> None:
    with org_session(UUID(ORG_A)) as session:
        session.execute(delete(Trip).where(Trip.organization_id == UUID(ORG_A), Trip.id == UUID(trip_id)))
        session.execute(delete(Place).where(Place.organization_id == UUID(ORG_A), Place.id == UUID(place_id)))


def fetch_case(client: TestClient, trip_id: str) -> dict:
    response = client.get(f"/v1/trips/{trip_id}/risk-cases", headers=headers())
    assert response.status_code == 200, response.text
    return response.json()["items"][0]


def test_trip_risk_reconciles_suggestion_and_follow_up_then_escalates_and_reopens(
    client: TestClient,
) -> None:
    trip_id, place_id, visit_id = make_trip(client)
    try:
        suggestion_id, run_id, follow_up_id = seed_flight_recovery_sources(trip_id, visit_id)

        reconcile_key = str(uuid.uuid4())
        reconciled = client.post(
            f"/v1/trips/{trip_id}/risk-cases:reconcile",
            headers=headers(key=reconcile_key),
        )
        assert reconciled.status_code == 200, reconciled.text
        assert reconciled.json()["cases_created"] == 1

        replay = client.post(
            f"/v1/trips/{trip_id}/risk-cases:reconcile",
            headers=headers(key=reconcile_key),
        )
        assert replay.status_code == 200, replay.text
        assert replay.headers.get("Idempotent-Replay") == "true"
        assert replay.json() == reconciled.json()

        second_scan = client.post(
            f"/v1/trips/{trip_id}/risk-cases:reconcile",
            headers=headers(key=str(uuid.uuid4())),
        )
        assert second_scan.status_code == 200, second_scan.text
        assert second_scan.json()["cases_created"] == 0

        feed = client.get(f"/v1/trips/{trip_id}/risk-cases", headers=headers())
        assert feed.status_code == 200, feed.text
        assert len(feed.json()["items"]) == 1
        case = feed.json()["items"][0]
        assert case["risk_type"] == "FLIGHT"
        assert case["status"] == "ACKNOWLEDGED"
        assert case["severity"] == "HIGH"
        assert case["version"] >= 2
        source_types = {item["type"] for item in case["source_refs"]}
        assert source_types == {"PROACTIVE_SUGGESTION", "VISIT_FOLLOW_UP"}
        assert {item["id"] for item in case["source_refs"]} >= {suggestion_id, follow_up_id}

        events_response = client.get(
            f"/v1/trips/{trip_id}/risk-cases/{case['id']}/events",
            headers=headers(),
        )
        assert events_response.status_code == 200, events_response.text
        event_types = {item["event_type"] for item in events_response.json()}
        assert {"OPENED", "ACKNOWLEDGED"} <= event_types
        event_versions = {
            item["event_type"]: item["case_version"]
            for item in events_response.json()
        }
        assert event_versions["OPENED"] == 1
        assert event_versions["ACKNOWLEDGED"] == 2

        with pytest.raises(IntegrityError):
            with org_session(UUID(ORG_A)) as session:
                immutable_event = session.get(
                    TripRiskCaseEvent,
                    UUID(events_response.json()[0]["id"]),
                )
                assert immutable_event is not None
                immutable_event.note = "Attempted audit mutation"
                session.flush()

        wait_until = datetime.now(UTC) + timedelta(minutes=20)
        waiting = client.post(
            f"/v1/trips/{trip_id}/risk-cases/{case['id']}:transition",
            headers=headers(key=str(uuid.uuid4())),
            json={
                "expected_version": case["version"],
                "to_status": "WAITING_EXTERNAL",
                "note": "Waiting for the customer to confirm a revised appointment window.",
                "next_review_at": wait_until.isoformat(),
            },
        )
        assert waiting.status_code == 200, waiting.text
        waiting_case = waiting.json()["case"]
        assert waiting_case["status"] == "WAITING_EXTERNAL"

        with org_session(UUID(ORG_A)) as session:
            persisted = session.get(TripRiskCase, UUID(case["id"]))
            assert persisted is not None
            persisted.next_review_at = datetime.now(UTC) - timedelta(seconds=1)

        due_reconcile = client.post(
            f"/v1/trips/{trip_id}/risk-cases:reconcile",
            headers=headers(key=str(uuid.uuid4())),
        )
        assert due_reconcile.status_code == 200, due_reconcile.text
        assert due_reconcile.json()["escalated"] == 1
        escalated = fetch_case(client, trip_id)
        assert escalated["status"] == "ESCALATED"
        assert escalated["escalated_at"] is not None
        with org_session(UUID(ORG_A)) as session:
            risk_notifications = list(
                session.scalars(
                    select(Notification).where(
                        Notification.organization_id == UUID(ORG_A),
                        Notification.user_id == UUID(USER_A),
                        Notification.kind == "TRIP_RISK_ESCALATION",
                        Notification.issue_key.like(f"trip-risk:{case['id']}:escalated:%"),
                    )
                )
            )
            assert len(risk_notifications) == 1
            assert risk_notifications[0].priority == "P0"
            assert risk_notifications[0].status == "PENDING"

        stale_transition = client.post(
            f"/v1/trips/{trip_id}/risk-cases/{case['id']}:transition",
            headers=headers(key=str(uuid.uuid4())),
            json={
                "expected_version": waiting_case["version"],
                "to_status": "ACKNOWLEDGED",
                "note": "This action uses a stale version.",
            },
        )
        assert stale_transition.status_code == 409, stale_transition.text

        blocked_resolution = client.post(
            f"/v1/trips/{trip_id}/risk-cases/{case['id']}:transition",
            headers=headers(key=str(uuid.uuid4())),
            json={
                "expected_version": escalated["version"],
                "to_status": "RESOLVED",
                "note": "Trying to resolve without revalidated plan evidence.",
                "resolution_basis": "PLAN_REVALIDATED",
            },
        )
        assert blocked_resolution.status_code == 422, blocked_resolution.text

        resolved = client.post(
            f"/v1/trips/{trip_id}/risk-cases/{case['id']}:transition",
            headers=headers(key=str(uuid.uuid4())),
            json={
                "expected_version": escalated["version"],
                "to_status": "RESOLVED",
                "note": "The responsible traveler confirms the current recovery outcome.",
                "resolution_basis": "HUMAN_ATTESTATION",
            },
        )
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["case"]["status"] == "RESOLVED"

        manual_reopen = client.post(
            f"/v1/trips/{trip_id}/risk-cases/{case['id']}:transition",
            headers=headers(key=str(uuid.uuid4())),
            json={
                "expected_version": resolved.json()["case"]["version"],
                "to_status": "OPEN",
                "note": "Manual reopen without changed source evidence.",
            },
        )
        assert manual_reopen.status_code == 422, manual_reopen.text

        with org_session(UUID(ORG_A)) as session:
            source = session.get(ProactiveSuggestion, UUID(suggestion_id))
            assert source is not None
            source.evidence = [
                *source.evidence,
                {"kind": "CUSTOMER_CONFIRMATION", "ref": "new-evidence-456"},
            ]

        reopened = client.post(
            f"/v1/trips/{trip_id}/risk-cases:reconcile",
            headers=headers(key=str(uuid.uuid4())),
        )
        assert reopened.status_code == 200, reopened.text
        assert reopened.json()["cases_reopened"] == 1
        assert fetch_case(client, trip_id)["status"] == "ACKNOWLEDGED"

        with org_session(UUID(ORG_A)) as session:
            case_events = list(
                session.scalars(
                    select(TripRiskCaseEvent)
                    .where(
                        TripRiskCaseEvent.organization_id == UUID(ORG_A),
                        TripRiskCaseEvent.trip_id == UUID(trip_id),
                        TripRiskCaseEvent.case_id == UUID(case["id"]),
                    )
                    .order_by(TripRiskCaseEvent.created_at, TripRiskCaseEvent.id)
                )
            )
            assert [row.event_type for row in case_events][0] == "OPENED"
            assert "ESCALATED" in {row.event_type for row in case_events}
            assert "RESOLVED" in {row.event_type for row in case_events}
            assert "REOPENED" in {row.event_type for row in case_events}
            persisted_run = session.get(AgentRun, UUID(run_id))
            assert persisted_run is not None
    finally:
        cleanup(trip_id, place_id)


def test_follow_up_only_case_resolves_when_manual_follow_up_completes(
    client: TestClient,
) -> None:
    trip_id, place_id, visit_id = make_trip(client)
    try:
        follow_up_id = seed_manual_follow_up(trip_id, visit_id)
        reconciled = client.post(
            f"/v1/trips/{trip_id}/risk-cases:reconcile",
            headers=headers(key=str(uuid.uuid4())),
        )
        assert reconciled.status_code == 200, reconciled.text
        assert reconciled.json()["cases_created"] == 1

        cases = client.get(f"/v1/trips/{trip_id}/risk-cases", headers=headers())
        assert cases.status_code == 200, cases.text
        case = cases.json()["items"][0]
        assert case["risk_type"] == "VISIT_FOLLOW_UP"
        assert case["status"] == "ACKNOWLEDGED"
        assert {item["id"] for item in case["source_refs"]} == {follow_up_id}

        wrong_close_basis = client.post(
            f"/v1/trips/{trip_id}/risk-cases/{case['id']}:transition",
            headers=headers(key=str(uuid.uuid4())),
            json={
                "expected_version": case["version"],
                "to_status": "RESOLVED",
                "note": "A dismissed follow-up cannot be used as a resolved basis.",
                "resolution_basis": "FOLLOW_UP_DISMISSED",
            },
        )
        assert wrong_close_basis.status_code == 422, wrong_close_basis.text

        completed = client.patch(
            f"/v1/visit-follow-ups/{follow_up_id}",
            headers=headers(key=str(uuid.uuid4())),
            json={"status": "COMPLETED"},
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "COMPLETED"

        after = client.get(f"/v1/trips/{trip_id}/risk-cases", headers=headers())
        assert after.status_code == 200, after.text
        completed_case = after.json()["items"][0]
        assert completed_case["status"] == "RESOLVED"
        assert completed_case["resolution_basis"] == "FOLLOW_UP_COMPLETED"

        timeline = client.get(
            f"/v1/trips/{trip_id}/risk-cases/{case['id']}/events",
            headers=headers(),
        )
        assert timeline.status_code == 200, timeline.text
        assert "RESOLVED" in {item["event_type"] for item in timeline.json()}
    finally:
        cleanup(trip_id, place_id)


def test_trip_risk_dismissal_requires_a_dismissed_source_and_is_tenant_scoped(
    client: TestClient,
) -> None:
    trip_id, place_id, visit_id = make_trip(client)
    try:
        suggestion_id, _, _ = seed_flight_recovery_sources(trip_id, visit_id)
        reconciled = client.post(
            f"/v1/trips/{trip_id}/risk-cases:reconcile",
            headers=headers(key=str(uuid.uuid4())),
        )
        assert reconciled.status_code == 200, reconciled.text
        case = fetch_case(client, trip_id)

        stolen = client.get(
            f"/v1/trips/{trip_id}/risk-cases",
            headers=headers(org=ORG_B, user=USER_B),
        )
        assert stolen.status_code == 404

        missing_basis = client.post(
            f"/v1/trips/{trip_id}/risk-cases/{case['id']}:transition",
            headers=headers(key=str(uuid.uuid4())),
            json={
                "expected_version": case["version"],
                "to_status": "DISMISSED",
                "note": "Dismiss without a source decision.",
            },
        )
        assert missing_basis.status_code == 422, missing_basis.text

        unresolved_follow_up_basis = client.post(
            f"/v1/trips/{trip_id}/risk-cases/{case['id']}:transition",
            headers=headers(key=str(uuid.uuid4())),
            json={
                "expected_version": case["version"],
                "to_status": "DISMISSED",
                "note": "Trying to dismiss while the source follow-up is still open.",
                "resolution_basis": "FOLLOW_UP_DISMISSED",
            },
        )
        assert unresolved_follow_up_basis.status_code == 422, unresolved_follow_up_basis.text

        with org_session(UUID(ORG_A)) as session:
            source = session.get(ProactiveSuggestion, UUID(suggestion_id))
            follow_up = session.scalars(
                select(VisitFollowUp).where(
                    VisitFollowUp.organization_id == UUID(ORG_A),
                    VisitFollowUp.trip_id == UUID(trip_id),
                    VisitFollowUp.visit_id == UUID(visit_id),
                )
            ).one()
            assert source is not None
            source.status = "PENDING"
            follow_up.status = "DISMISSED"

        active_suggestion_basis = client.post(
            f"/v1/trips/{trip_id}/risk-cases/{case['id']}:transition",
            headers=headers(key=str(uuid.uuid4())),
            json={
                "expected_version": case["version"],
                "to_status": "DISMISSED",
                "note": "A linked source suggestion is still active.",
                "resolution_basis": "FOLLOW_UP_DISMISSED",
            },
        )
        assert active_suggestion_basis.status_code == 422, active_suggestion_basis.text

        with org_session(UUID(ORG_A)) as session:
            source = session.get(ProactiveSuggestion, UUID(suggestion_id))
            assert source is not None
            source.status = "DISMISSED"

        refreshed = client.post(
            f"/v1/trips/{trip_id}/risk-cases:reconcile",
            headers=headers(key=str(uuid.uuid4())),
        )
        assert refreshed.status_code == 200, refreshed.text
        dismissed_case = fetch_case(client, trip_id)
        assert dismissed_case["status"] == "DISMISSED"
        assert dismissed_case["resolution_basis"] == "SOURCE_SUGGESTION_DISMISSED"

    finally:
        cleanup(trip_id, place_id)
