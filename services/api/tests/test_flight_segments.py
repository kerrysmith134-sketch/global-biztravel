"""Flight booking segments bind only through explicit human confirmation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.geo import Place
from app.models.planning import PlanStop, PlanTravelLeg, PlanVersion
from app.models.trip import Trip
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
USER_A = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers(**extra: str) -> dict[str, str]:
    return {"X-Org-Id": ORG_A, "X-User-Id": USER_A, **extra}


def create_trip_and_place(client: TestClient) -> tuple[str, str, str]:
    now = datetime.now(UTC)
    trip = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"Flight binding {uuid.uuid4().hex[:8]}",
            "window_start": (now + timedelta(days=1)).isoformat(),
            "window_end": (now + timedelta(days=4)).isoformat(),
            "countries": [],
        },
    )
    assert trip.status_code == 201, trip.text
    trip_id = trip.json()["id"]

    place = client.post(
        "/v1/places",
        headers=headers(),
        json={
            "name": f"Flight destination {uuid.uuid4().hex[:8]}",
            "city": "Frankfurt",
            "country_code": "DE",
            "lat": 50.1109,
            "lon": 8.6821,
        },
    )
    assert place.status_code == 201, place.text
    place_id = place.json()["id"]

    visit = client.post(
        f"/v1/trips/{trip_id}/visits",
        headers=headers(),
        json={"place_id": place_id, "title": "Flight-linked customer visit"},
    )
    assert visit.status_code == 201, visit.text
    return trip_id, place_id, visit.json()["id"]


def create_plan(
    trip_id: str,
    visit_id: str,
    *,
    version_number: int,
    mode: str = "AIR",
) -> tuple[str, str]:
    now = datetime.now(UTC)
    with org_session(UUID(ORG_A)) as session:
        trip = session.get(Trip, UUID(trip_id))
        assert trip is not None
        if version_number > 1:
            previous = session.scalars(
                select(PlanVersion).where(
                    PlanVersion.organization_id == UUID(ORG_A),
                    PlanVersion.trip_id == UUID(trip_id),
                    PlanVersion.status == "ACTIVE",
                )
            ).one()
            previous.status = "SUPERSEDED"
            previous.superseded_at = now
            session.flush()

        plan = PlanVersion(
            organization_id=UUID(ORG_A),
            trip_id=trip.id,
            version_number=version_number,
            status="ACTIVE",
            problem_hash=f"flight-binding:{uuid.uuid4().hex}",
            solver="heuristic",
            objective_value=0.0,
            solution_status="FEASIBLE",
            trip_version=trip.version,
            validation={"feasible": True},
            unplaced=[],
            activated_at=now,
        )
        session.add(plan)
        session.flush()
        trip.active_plan_version_id = plan.id
        trip.status = "ACTIVE"

        arrive_at = now + timedelta(days=2, hours=5)
        stop = PlanStop(
            organization_id=UUID(ORG_A),
            plan_version_id=plan.id,
            visit_id=UUID(visit_id),
            day_index=0,
            sequence=1,
            day_date=arrive_at.date(),
            arrive_at=arrive_at,
            depart_at=arrive_at + timedelta(hours=1),
            travel_minutes=0,
            service_minutes=60,
            travel_source="test-fixture",
        )
        session.add(stop)
        session.flush()
        leg = PlanTravelLeg(
            organization_id=UUID(ORG_A),
            plan_version_id=plan.id,
            from_stop_id=None,
            to_stop_id=stop.id,
            day_index=0,
            mode=mode,
            travel_minutes=600,
            distance_km=900,
            source="test-flight-estimate",
            stale=True,
        )
        session.add(leg)
        session.flush()
        return str(plan.id), str(leg.id)


def flight_payload() -> dict:
    departure = datetime.now(UTC) + timedelta(days=2)
    return {
        "flight_designator": "lh123",
        "departure_airport_iata": "jfk",
        "arrival_airport_iata": "fra",
        "scheduled_departure_at": departure.isoformat(),
        "scheduled_arrival_at": (departure + timedelta(hours=7)).isoformat(),
        "departure_timezone": "America/New_York",
        "arrival_timezone": "Europe/Berlin",
        "booking_status": "TICKETED",
        "tracking_provider": "OpenSky",
        "tracking_reference": "DLH123",
    }


def cleanup(trip_id: str, place_id: str) -> None:
    with org_session(UUID(ORG_A)) as session:
        session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))
        session.execute(delete(Place).where(Place.id == UUID(place_id)))


def test_flight_segment_binding_is_explicit_versioned_and_auditable(
    client: TestClient,
) -> None:
    trip_id, place_id, visit_id = create_trip_and_place(client)
    try:
        plan_one_id, leg_one_id = create_plan(
            trip_id, visit_id, version_number=1, mode="AIR"
        )
        created = client.post(
            f"/v1/trips/{trip_id}/flight-segments",
            headers=headers(**{"Idempotency-Key": str(uuid.uuid4())}),
            json=flight_payload(),
        )
        assert created.status_code == 201, created.text
        segment = created.json()
        assert segment["flight_designator"] == "LH123"
        assert segment["departure_airport_iata"] == "JFK"
        assert segment["arrival_airport_iata"] == "FRA"
        assert segment["tracking_provider"] == "opensky"
        assert segment["binding_state"] == "UNLINKED"
        segment_id = segment["id"]

        linked = client.post(
            f"/v1/trips/{trip_id}/flight-segments/{segment_id}:link",
            headers=headers(**{"Idempotency-Key": str(uuid.uuid4())}),
            json={"plan_travel_leg_id": leg_one_id},
        )
        assert linked.status_code == 200, linked.text
        body = linked.json()
        assert body["binding_state"] == "CURRENT_PLAN"
        assert len(body["bindings"]) == 1
        assert body["bindings"][0]["plan_version_id"] == plan_one_id
        assert body["bindings"][0]["plan_travel_leg_id"] == leg_one_id
        assert body["bindings"][0]["status"] == "ACTIVE"
        assert body["bindings"][0]["linked_by_user_id"] == USER_A

        plan_two_id, leg_two_id = create_plan(
            trip_id, visit_id, version_number=2, mode="AIR"
        )
        listed = client.get(
            f"/v1/trips/{trip_id}/flight-segments", headers=headers()
        )
        assert listed.status_code == 200, listed.text
        assert listed.json()[0]["binding_state"] == "STALE_PLAN_BINDING"

        rebound = client.post(
            f"/v1/trips/{trip_id}/flight-segments/{segment_id}:link",
            headers=headers(**{"Idempotency-Key": str(uuid.uuid4())}),
            json={"plan_travel_leg_id": leg_two_id},
        )
        assert rebound.status_code == 200, rebound.text
        rebound_body = rebound.json()
        assert rebound_body["binding_state"] == "CURRENT_PLAN"
        assert len(rebound_body["bindings"]) == 2
        assert rebound_body["bindings"][0]["status"] == "RELEASED"
        assert rebound_body["bindings"][0]["release_reason"] == "RELINKED"
        assert rebound_body["bindings"][0]["plan_version_id"] == plan_one_id
        assert rebound_body["bindings"][1]["status"] == "ACTIVE"
        assert rebound_body["bindings"][1]["plan_version_id"] == plan_two_id
        assert rebound_body["bindings"][1]["plan_travel_leg_id"] == leg_two_id

        unlinked = client.post(
            f"/v1/trips/{trip_id}/flight-segments/{segment_id}:unlink",
            headers=headers(**{"Idempotency-Key": str(uuid.uuid4())}),
        )
        assert unlinked.status_code == 200, unlinked.text
        assert unlinked.json()["binding_state"] == "UNLINKED"
        assert [item["status"] for item in unlinked.json()["bindings"]] == [
            "RELEASED",
            "RELEASED",
        ]
        assert unlinked.json()["bindings"][1]["release_reason"] == "USER_UNLINKED"
    finally:
        cleanup(trip_id, place_id)


def test_flight_segment_cannot_bind_to_ground_leg(client: TestClient) -> None:
    trip_id, place_id, visit_id = create_trip_and_place(client)
    try:
        _plan_id, ground_leg_id = create_plan(
            trip_id, visit_id, version_number=1, mode="GROUND"
        )
        created = client.post(
            f"/v1/trips/{trip_id}/flight-segments",
            headers=headers(**{"Idempotency-Key": str(uuid.uuid4())}),
            json=flight_payload(),
        )
        assert created.status_code == 201, created.text
        segment_id = created.json()["id"]

        invalid = client.post(
            f"/v1/trips/{trip_id}/flight-segments/{segment_id}:link",
            headers=headers(**{"Idempotency-Key": str(uuid.uuid4())}),
            json={"plan_travel_leg_id": ground_leg_id},
        )
        assert invalid.status_code == 422, invalid.text
        assert invalid.json()["error"]["code"] == "UNPROCESSABLE_ENTITY"

        listed = client.get(
            f"/v1/trips/{trip_id}/flight-segments", headers=headers()
        )
        assert listed.status_code == 200, listed.text
        assert listed.json()[0]["binding_state"] == "UNLINKED"
        assert listed.json()[0]["bindings"] == []
    finally:
        cleanup(trip_id, place_id)
