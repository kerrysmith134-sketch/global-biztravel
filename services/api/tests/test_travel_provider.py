"""AT-003：交通 Provider 不可用时降级，标记 stale，未知费用不当作 0。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.db import org_session
from app.integrations.travel import UnavailableTravelProvider
from app.main import app
from app.models.geo import Place
from app.models.trip import Trip
from app.services import planning_service
from fastapi.testclient import TestClient
from sqlalchemy import delete
from travel_planner import plan_trip

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
USER_A = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers() -> dict[str, str]:
    return {"X-Org-Id": ORG_A, "X-User-Id": USER_A}


def test_provider_failure_falls_back_with_stale_unknown_cost(client: TestClient) -> None:
    now = datetime.now(UTC)
    place = client.post(
        "/v1/places",
        headers=headers(),
        json={"name": f"AT-003 地点·{uuid.uuid4().hex[:6]}", "city": "Berlin", "country_code": "DE", "lat": 52.52, "lon": 13.405},
    )
    assert place.status_code == 201, place.text
    place_id = place.json()["id"]
    trip = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"AT-003·{uuid.uuid4().hex[:8]}",
            "origin_place_id": place_id,
            "window_start": (now + timedelta(days=5)).isoformat(),
            "window_end": (now + timedelta(days=8)).isoformat(),
            "countries": ["DE"],
        },
    )
    assert trip.status_code == 201, trip.text
    trip_id = trip.json()["id"]
    visit = client.post(
        f"/v1/trips/{trip_id}/visits",
        headers=headers(),
        json={"place_id": place_id, "title": "AT-003 拜访", "service_minutes": 60, "movement_mode": "AIR"},
    )
    assert visit.status_code == 201, visit.text
    try:
        with org_session(UUID(ORG_A)) as session:
            trip_row = session.get(Trip, UUID(trip_id))
            build = planning_service.build_problem(
                session,
                ORG_A,
                trip_row,
                travel_provider=UnavailableTravelProvider(),
            )
        assert build.travel_degraded is True
        assert build.travel_errors
        assert build.problem.travel_estimates
        assert all(item.stale for item in build.problem.travel_estimates)
        assert all(item.cost_amount is None for item in build.problem.travel_estimates)

        result = plan_trip(build.problem, solver="heuristic")
        assert result.validation.feasible is True
        assert result.legs
        assert all(leg.stale for leg in result.legs)
        assert all(leg.cost_amount is None for leg in result.legs)
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))
            session.execute(delete(Place).where(Place.id == UUID(place_id)))
