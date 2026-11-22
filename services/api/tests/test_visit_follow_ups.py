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

