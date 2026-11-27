"""Human-approved visit follow-ups: provenance, tenant isolation, idempotency and lifecycle audit."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.records import VisitFollowUp, VisitFollowUpEvent
from app.models.trip import Trip
from app.models.workflow import AgentRun
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "33333333-3333-3333-3333-333333333333"
ADMIN_A = "55555555-5555-5555-5555-555555555555"
USER_B = "44444444-4444-4444-4444-444444444444"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers(*, org: str = ORG_A, user: str = ADMIN_A, key: str | None = None) -> dict[str, str]:
    value = {"X-Org-Id": org, "X-User-Id": user}
    if key is not None:
        value["Idempotency-Key"] = key
    return value


def make_visit(client: TestClient) -> tuple[str, str, str]:
    now = datetime.now(UTC)
    marker = uuid.uuid4().hex[:8]
    trip = client.post(
        "/v1/trips",
        headers=headers(key=str(uuid.uuid4())),
        json={
            "name": f"Follow-up lifecycle · {marker}",
            "window_start": (now + timedelta(days=5)).isoformat(),
            "window_end": (now + timedelta(days=8)).isoformat(),
            "countries": ["DE"],
        },
    )
    assert trip.status_code == 201, trip.text
    place = client.post(
        "/v1/places",
        headers=headers(key=str(uuid.uuid4())),
        json={
            "name": f"Follow-up place · {marker}",
            "city": "Berlin",
            "country_code": "DE",
            "lat": 52.52,
            "lon": 13.405,
        },
    )
    assert place.status_code == 201, place.text
    visit = client.post(
        f"/v1/trips/{trip.json()['id']}/visits",
        headers=headers(key=str(uuid.uuid4())),
        json={
            "place_id": place.json()["id"],
            "title": "Buyer commercial review",
            "service_minutes": 60,
        },
    )
    assert visit.status_code == 201, visit.text
    return trip.json()["id"], visit.json()["id"], place.json()["id"]


def test_record_follow_ups_require_human_decision_and_keep_audit(client: TestClient) -> None:
    trip_id, visit_id, place_id = make_visit(client)
    follow_up_ids: list[str] = []
    try:
        record = client.post(
            f"/v1/visits/{visit_id}/records",
            headers=headers(key=str(uuid.uuid4())),
            json={
                "client_record_id": str(uuid.uuid4()),
                "outcome": "MET",
                "notes": "Buyer asked for delivery and warranty evidence.",
                "client_updated_at": datetime.now(UTC).isoformat(),
                "photos": [],
                "follow_up_tasks": [
                    {"title": "Send factory capacity evidence", "due_date": "2026-12-01"},
                    {"title": "Confirm warranty extension terms", "due_date": "2026-12-03"},
                ],
            },
        )
        assert record.status_code == 201, record.text
        record_id = record.json()["id"]
        trip_before = client.get(f"/v1/trips/{trip_id}", headers=headers()).json()

        feed = client.get(f"/v1/visits/{visit_id}/follow-ups", headers=headers())
        assert feed.status_code == 200, feed.text
        assert feed.json()["items"] == []
        suggestions = feed.json()["suggestions"]
        assert [item["title"] for item in suggestions] == [
            "Send factory capacity evidence",
            "Confirm warranty extension terms",
        ]
        assert {item["source_type"] for item in suggestions} == {"RECORD"}

        accept_key = str(uuid.uuid4())
        accept_payload = {
            "source_type": "RECORD",
            "decision": "ACCEPT",
            "source_record_id": record_id,
            "source_index": 0,
        }
        accepted = client.post(
            f"/v1/visits/{visit_id}/follow-ups",
            headers=headers(key=accept_key),
            json=accept_payload,
        )
        assert accepted.status_code == 201, accepted.text
        accepted_body = accepted.json()
        follow_up_ids.append(accepted_body["id"])
        assert accepted_body["status"] == "OPEN"
        assert accepted_body["title"] == "Send factory capacity evidence"
        assert accepted_body["confirmed_by_user_id"] == ADMIN_A
        assert [event["event_type"] for event in accepted_body["events"]] == ["ACCEPTED"]

        replay = client.post(
            f"/v1/visits/{visit_id}/follow-ups",
            headers=headers(key=accept_key),
            json=accept_payload,
        )
        assert replay.status_code == 201, replay.text
        assert replay.headers.get("Idempotent-Replay") == "true"
        assert replay.json()["id"] == accepted_body["id"]

        dismissed = client.post(
            f"/v1/visits/{visit_id}/follow-ups",
            headers=headers(key=str(uuid.uuid4())),
            json={
                "source_type": "RECORD",
                "decision": "DISMISS",
                "source_record_id": record_id,
                "source_index": 1,
                "note": "This item was already handled in the call.",
            },
        )
        assert dismissed.status_code == 201, dismissed.text
        follow_up_ids.append(dismissed.json()["id"])
        assert dismissed.json()["status"] == "DISMISSED"

        after_decision = client.get(
            f"/v1/visits/{visit_id}/follow-ups", headers=headers()
        ).json()
        assert after_decision["suggestions"] == []
        assert len(after_decision["items"]) == 2

        follow_up_id = accepted_body["id"]
        started = client.patch(
            f"/v1/visit-follow-ups/{follow_up_id}",
            headers=headers(key=str(uuid.uuid4())),
            json={"status": "IN_PROGRESS"},
        )
        assert started.status_code == 200, started.text
        assert started.json()["status"] == "IN_PROGRESS"

        completed = client.patch(
            f"/v1/visit-follow-ups/{follow_up_id}",
            headers=headers(key=str(uuid.uuid4())),
            json={"status": "COMPLETED"},
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["completed_by_user_id"] == ADMIN_A

        invalid_reopen = client.patch(
            f"/v1/visit-follow-ups/{follow_up_id}",
            headers=headers(key=str(uuid.uuid4())),
            json={"status": "OPEN"},
        )
        assert invalid_reopen.status_code == 422

        reopened = client.patch(
            f"/v1/visit-follow-ups/{follow_up_id}",
            headers=headers(key=str(uuid.uuid4())),
            json={"status": "OPEN", "note": "Buyer requested a revised delivery schedule."},
        )
        assert reopened.status_code == 200, reopened.text
        assert reopened.json()["status"] == "OPEN"
        assert [event["event_type"] for event in reopened.json()["events"]] == [
            "ACCEPTED", "STARTED", "COMPLETED", "REOPENED"
        ]

        center = client.get(
            "/v1/action-center", headers=headers(), params={"trip_id": trip_id}
        )
        assert center.status_code == 200, center.text
        prep = next(item for section in center.json()["sections"] if section["key"] == "PREPARATION"
                    for item in section["items"] if item["entity_id"] == follow_up_id)
        assert prep["kind"] == "VISIT_FOLLOW_UP"
        assert prep["status"] == "OPEN"

        trip_after = client.get(f"/v1/trips/{trip_id}", headers=headers()).json()
        assert trip_after["version"] == trip_before["version"]
        # The decision and lifecycle never mutate the original visit state.
        current_visit = client.get(f"/v1/trips/{trip_id}/visits", headers=headers()).json()
        assert current_visit[0]["status"] == "DONE"

        stolen = client.get(
            f"/v1/visits/{visit_id}/follow-ups",
            headers=headers(org=ORG_B, user=USER_B),
        )
        assert stolen.status_code == 404

        with org_session(UUID(ORG_A)) as session:
            events = list(
                session.scalars(
                    select(VisitFollowUpEvent).where(
                        VisitFollowUpEvent.follow_up_id == UUID(follow_up_id)
                    )
                )
            )
            assert len(events) == 4
            assert {event.actor_user_id for event in events} == {UUID(ADMIN_A)}
    finally:
        with org_session(UUID(ORG_A)) as session:
            if follow_up_ids:
                session.execute(
                    delete(VisitFollowUp).where(
                        VisitFollowUp.id.in_([UUID(value) for value in follow_up_ids])
                    )
                )
            session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))
            from app.models.geo import Place

            session.execute(delete(Place).where(Place.id == UUID(place_id)))


def test_minutes_agent_run_follow_up_is_bound_to_exact_visit(client: TestClient) -> None:
    trip_id, visit_id, place_id = make_visit(client)
    run_id = uuid.uuid4()
    try:
        with org_session(UUID(ORG_A)) as session:
            session.add(
                AgentRun(
                    id=run_id,
                    organization_id=UUID(ORG_A),
                    agent_type="MEETING_MINUTES_DRAFT",
                    status="SUCCEEDED",
                    input={"visit_id": visit_id},
                    output={
                        "follow_up_tasks": [
                            {"title": "Prepare revised commercial offer", "due_date": "2026-12-05"}
                        ]
                    },
                )
            )

        feed = client.get(f"/v1/visits/{visit_id}/follow-ups", headers=headers())
        assert feed.status_code == 200, feed.text
        suggestion = feed.json()["suggestions"][0]
        assert suggestion["source_type"] == "MINUTES_DRAFT"
        assert suggestion["source_agent_run_id"] == str(run_id)

        other_visit = client.post(
            f"/v1/trips/{trip_id}/visits",
            headers=headers(key=str(uuid.uuid4())),
            json={"place_id": place_id, "title": "A different customer meeting", "service_minutes": 60},
        )
        assert other_visit.status_code == 201, other_visit.text
        rejected = client.post(
            f"/v1/visits/{other_visit.json()['id']}/follow-ups",
            headers=headers(key=str(uuid.uuid4())),
            json={
                "source_type": "MINUTES_DRAFT",
                "decision": "ACCEPT",
                "source_agent_run_id": str(run_id),
                "source_index": 0,
            },
        )
        assert rejected.status_code == 404, rejected.text

        accepted = client.post(
            f"/v1/visits/{visit_id}/follow-ups",
            headers=headers(key=str(uuid.uuid4())),
            json={
                "source_type": "MINUTES_DRAFT",
                "decision": "ACCEPT",
                "source_agent_run_id": str(run_id),
                "source_index": 0,
            },
        )
        assert accepted.status_code == 201, accepted.text
        follow_up_id = accepted.json()["id"]
        assert accepted.json()["source_agent_run_id"] == str(run_id)
        assert accepted.json()["visit_id"] == visit_id

        with org_session(UUID(ORG_A)) as session:
            session.execute(
                delete(VisitFollowUp).where(VisitFollowUp.id == UUID(follow_up_id))
            )
            session.execute(delete(AgentRun).where(AgentRun.id == run_id))
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))
            from app.models.geo import Place

            session.execute(delete(Place).where(Place.id == UUID(place_id)))

def test_manual_follow_up_has_origin_and_audited_edit(client: TestClient) -> None:
    trip_id, visit_id, place_id = make_visit(client)
    follow_up_id: UUID | None = None
    try:
        created = client.post(
            f"/v1/visits/{visit_id}/follow-ups",
            headers=headers(key=str(uuid.uuid4())),
            json={
                "source_type": "MANUAL",
                "decision": "CREATE",
                "title": "Prepare distributor pricing comparison",
                "due_date": "2026-12-08",
                "note": "Added after the customer discussion.",
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()
        follow_up_id = UUID(body["id"])
        assert body["source_type"] == "MANUAL"
        assert body["status"] == "OPEN"
        assert body["created_by_user_id"] == ADMIN_A
        assert body["confirmed_by_user_id"] is None
        assert [event["event_type"] for event in body["events"]] == ["CREATED_MANUAL"]

        updated = client.patch(
            f"/v1/visit-follow-ups/{follow_up_id}",
            headers=headers(key=str(uuid.uuid4())),
            json={
                "title": "Prepare revised distributor pricing comparison",
                "due_date": "2026-12-10",
                "note": "Updated after supplier feedback.",
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["title"] == "Prepare revised distributor pricing comparison"
        assert updated.json()["due_date"] == "2026-12-10"
        assert [event["event_type"] for event in updated.json()["events"]] == [
            "CREATED_MANUAL", "UPDATED"
        ]

        feed = client.get(f"/v1/visits/{visit_id}/follow-ups", headers=headers())
        assert feed.status_code == 200, feed.text
        assert len(feed.json()["items"]) == 1
        assert feed.json()["suggestions"] == []
    finally:
        with org_session(UUID(ORG_A)) as session:
            if follow_up_id is not None:
                session.execute(delete(VisitFollowUp).where(VisitFollowUp.id == follow_up_id))
            session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))
            from app.models.geo import Place

            session.execute(delete(Place).where(Place.id == UUID(place_id)))


def test_appointment_recovery_actions_enter_human_follow_up_lifecycle(
    client: TestClient,
) -> None:
    from app.models.proactive import ProactiveSuggestion
    from app.models.workflow import WorkflowInstance

    trip_id, visit_id, place_id = make_visit(client)
    second_visit = client.post(
        f"/v1/trips/{trip_id}/visits",
        headers=headers(key=str(uuid.uuid4())),
        json={"place_id": place_id, "title": "Secondary buyer meeting", "service_minutes": 45},
    )
    assert second_visit.status_code == 201, second_visit.text
    second_visit_id = second_visit.json()["id"]

    workflow_id, suggestion_id, run_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    now = datetime.now(UTC)
    workflow = WorkflowInstance(
        id=workflow_id,
        organization_id=UUID(ORG_A),
        trip_id=UUID(trip_id),
        workflow_type="APPOINTMENT_RECOVERY",
        status="COMPLETED",
        payload={
            "source_suggestion_id": str(suggestion_id),
            "agent_type": "APPOINTMENT_RECOVERY",
            "agent_run_id": str(run_id),
            "recovery_status": "REVIEW_REQUIRED",
        },
    )
    suggestion = ProactiveSuggestion(
        id=suggestion_id,
        organization_id=UUID(ORG_A),
        trip_id=UUID(trip_id),
        suggestion_type="FLIGHT_STATUS_ALERT",
        status="PENDING",
        title="Flight disruption requires appointment review",
        rationale="Provider event impacts the active itinerary.",
        evidence=[],
        proposed_action={"kind": "REVIEW_EXTERNAL_CHANGE", "params": {
            "recovery_workflow_id": str(workflow_id),
            "recovery_agent_run_id": str(run_id),
        }},
        confidence=0.95,
        requires_approval=True,
        severity="MEDIUM",
        dedupe_key=f"test-recovery-{uuid.uuid4()}",
        push_status="PUSHED",
    )
    output = {
        "agent_type": "APPOINTMENT_RECOVERY",
        "agent_version": "appointment-recovery-v1.1",
        "schema_version": "1.1",
        "status": "REVIEW_REQUIRED",
        "actions": [
            {
                "scope": "VISIT",
                "visit_id": visit_id,
                "action_code": "REVALIDATE_TRAVEL_CHAIN",
                "priority": "MEDIUM",
                "decision_status": "DATA_REVALIDATION_REQUIRED",
                "appointment_status": "UNASSESSED",
                "rationale": "A travel segment is missing and must be refreshed.",
                "required_inputs": ["FRESH_NON_STALE_TRAVEL_LEG"],
                "recommended_steps": ["Refresh travel segment evidence."],
            },
            {
                "scope": "VISIT",
                "visit_id": second_visit_id,
                "action_code": "CONFIRM_CUSTOMER_APPOINTMENT_WINDOW",
                "priority": "MEDIUM",
                "decision_status": "CUSTOMER_CONFIRMATION_REQUIRED",
                "appointment_status": "WINDOW_END_UNKNOWN",
                "rationale": "The appointment end time is not known.",
                "required_inputs": ["CUSTOMER_CONFIRMED_APPOINTMENT_WINDOW"],
                "recommended_steps": ["Confirm the customer time window."],
            },
            {
                "scope": "TRIP",
                "visit_id": None,
                "action_code": "REVALIDATE_FLIGHT_PLAN_IMPACT_ASSESSMENT",
                "priority": "MEDIUM",
                "decision_status": "DATA_REVALIDATION_REQUIRED",
                "appointment_status": "UNASSESSED",
                "required_inputs": ["CURRENT_ACTIVE_PLAN"],
                "recommended_steps": ["Repair the plan binding."],
            },
        ],
    }
    run = AgentRun(
        id=run_id,
        organization_id=UUID(ORG_A),
        workflow_id=workflow_id,
        agent_type="APPOINTMENT_RECOVERY",
        status="SUCCEEDED",
        input={"trip_id": trip_id, "suggestion_id": str(suggestion_id)},
        output=output,
        started_at=now,
        finished_at=now,
    )
    try:
        with org_session(UUID(ORG_A)) as session:
            session.add(workflow)
            session.add(suggestion)
            session.flush()
            session.add(run)
            session.flush()

        trip_before = client.get(f"/v1/trips/{trip_id}", headers=headers()).json()
        feed = client.get(f"/v1/visits/{visit_id}/follow-ups", headers=headers())
        assert feed.status_code == 200, feed.text
        candidates = [item for item in feed.json()["suggestions"]
                      if item["source_type"] == "APPOINTMENT_RECOVERY"]
        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate["source_agent_run_id"] == str(run_id)
        assert candidate["source_index"] == 0
        assert candidate["action_code"] == "REVALIDATE_TRAVEL_CHAIN"
        assert candidate["decision_status"] == "DATA_REVALIDATION_REQUIRED"
        assert candidate["required_inputs"] == ["FRESH_NON_STALE_TRAVEL_LEG"]

        cross_visit = client.post(
            f"/v1/visits/{second_visit_id}/follow-ups",
            headers=headers(key=str(uuid.uuid4())),
            json={"source_type":"APPOINTMENT_RECOVERY","decision":"ACCEPT",
                  "source_agent_run_id":str(run_id),"source_index":0},
        )
        assert cross_visit.status_code == 404, cross_visit.text

        accept_key = str(uuid.uuid4())
        accept_payload = {
            "source_type":"APPOINTMENT_RECOVERY","decision":"ACCEPT",
            "source_agent_run_id":str(run_id),"source_index":0,
            "note":"Traveler accepted the data revalidation action.",
        }
        accepted = client.post(
            f"/v1/visits/{visit_id}/follow-ups",
            headers=headers(key=accept_key),
            json=accept_payload,
        )
        assert accepted.status_code == 201, accepted.text
        body = accepted.json()
        assert body["status"] == "OPEN"
        assert body["source_type"] == "APPOINTMENT_RECOVERY"
        assert body["source_agent_run_id"] == str(run_id)
        assert body["source_index"] == 0
        assert body["source_action_context"]["integrity_status"] == "VERIFIED"
        assert body["source_action_context"]["action_code"] == "REVALIDATE_TRAVEL_CHAIN"
        follow_up_id = body["id"]

        replay = client.post(
            f"/v1/visits/{visit_id}/follow-ups",
            headers=headers(key=accept_key),
            json=accept_payload,
        )
        assert replay.status_code == 201
        assert replay.headers.get("Idempotent-Replay") == "true"
        assert replay.json()["id"] == follow_up_id

        center = client.get("/v1/action-center", headers=headers(), params={"trip_id":trip_id})
        assert center.status_code == 200, center.text
        center_item = next(
            item for section in center.json()["sections"] for item in section["items"]
            if item["entity_id"] == follow_up_id
        )
        assert center_item["kind"] == "VISIT_FOLLOW_UP"
        assert center_item["status"] == "OPEN"

        dismissed = client.post(
            f"/v1/visits/{second_visit_id}/follow-ups",
            headers=headers(key=str(uuid.uuid4())),
            json={"source_type":"APPOINTMENT_RECOVERY","decision":"DISMISS",
                  "source_agent_run_id":str(run_id),"source_index":1,
                  "note":"Customer appointment window already confirmed separately."},
        )
        assert dismissed.status_code == 201, dismissed.text
        assert dismissed.json()["status"] == "DISMISSED"

        trip_after = client.get(f"/v1/trips/{trip_id}", headers=headers()).json()
        assert trip_after["version"] == trip_before["version"]
        with org_session(UUID(ORG_A)) as session:
            events = list(session.scalars(
                select(VisitFollowUpEvent).where(
                    VisitFollowUpEvent.follow_up_id == UUID(follow_up_id)
                ).order_by(VisitFollowUpEvent.created_at.asc())
            ))
            assert [event.event_type for event in events] == ["ACCEPTED"]
            assert all(event.actor_user_id == UUID(ADMIN_A) for event in events)
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(delete(VisitFollowUp).where(VisitFollowUp.source_agent_run_id == run_id))
            session.execute(delete(AgentRun).where(AgentRun.id == run_id))
            session.execute(delete(ProactiveSuggestion).where(ProactiveSuggestion.id == suggestion_id))
            session.execute(delete(WorkflowInstance).where(WorkflowInstance.id == workflow_id))
            session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))
            from app.models.geo import Place
            session.execute(delete(Place).where(Place.id == UUID(place_id)))
