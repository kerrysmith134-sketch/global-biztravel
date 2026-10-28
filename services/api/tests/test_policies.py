"""差旅政策：预算/计划费用/提前预订/目的国，以及严格政策的激活阻断。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.planning import PlanVersion
from app.models.trip import Trip
from fastapi.testclient import TestClient
from sqlalchemy import delete

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
USER_A = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers() -> dict[str, str]:
    return {"X-Org-Id": ORG_A, "X-User-Id": USER_A}


def create_trip(client: TestClient) -> str:
    now = datetime.now(UTC)
    resp = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"政策测试·{uuid.uuid4().hex[:8]}",
            "window_start": (now + timedelta(days=5)).isoformat(),
            "window_end": (now + timedelta(days=8)).isoformat(),
            "countries": ["DE"],
            "currency": "EUR",
            "budget_amount": 1000,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def cleanup(trip_id: str, policy_id: str | None) -> None:
    with org_session(UUID(ORG_A)) as session:
        if policy_id:
            from app.models.policies import TravelPolicy

            session.execute(delete(TravelPolicy).where(TravelPolicy.id == UUID(policy_id)))
        session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))


def test_policy_crud_and_check(client: TestClient) -> None:
    trip_id = create_trip(client)
    policy_id: str | None = None
    try:
        created = client.post(
            "/v1/travel-policies",
            headers=headers(),
            json={
                "name": f"欧洲政策·{uuid.uuid4().hex[:6]}",
                "currency": "EUR",
                "max_trip_budget": 500,
                "max_plan_cost": 100,
                "min_advance_booking_days": 30,
                "allowed_countries": ["FR"],
                "strict": True,
                "enabled": True,
            },
        )
        assert created.status_code == 201, created.text
        policy_id = created.json()["id"]

        listed = client.get("/v1/travel-policies", headers=headers())
        assert any(item["id"] == policy_id for item in listed.json())

        check = client.get(f"/v1/trips/{trip_id}/policy-check", headers=headers())
        assert check.status_code == 200, check.text
        codes = {item["code"] for item in check.json()["violations"]}
        assert {"BUDGET_EXCEEDED", "ADVANCE_BOOKING_TOO_SHORT", "COUNTRY_NOT_ALLOWED", "PLAN_COST_MISSING"} <= codes
        assert check.json()["strict"] is True

        updated = client.patch(
            f"/v1/travel-policies/{policy_id}",
            headers=headers(),
            json={"max_trip_budget": 2000, "allowed_countries": ["DE"]},
        )
        assert updated.status_code == 200
        check2 = client.get(f"/v1/trips/{trip_id}/policy-check", headers=headers())
        codes2 = {item["code"] for item in check2.json()["violations"]}
        assert "BUDGET_EXCEEDED" not in codes2
        assert "COUNTRY_NOT_ALLOWED" not in codes2
    finally:
        cleanup(trip_id, policy_id)


def test_strict_policy_blocks_activation(client: TestClient) -> None:
    trip_id = create_trip(client)
    policy_id: str | None = None
    try:
        policy = client.post(
            "/v1/travel-policies",
            headers=headers(),
            json={
                "name": f"严格费用政策·{uuid.uuid4().hex[:6]}",
                "currency": "EUR",
                "max_plan_cost": 100,
                "strict": True,
                "enabled": True,
            },
        )
        assert policy.status_code == 201, policy.text
        policy_id = policy.json()["id"]

        with org_session(UUID(ORG_A)) as session:
            trip_row = session.get(Trip, UUID(trip_id))
            version = PlanVersion(
                organization_id=UUID(ORG_A),
                trip_id=UUID(trip_id),
                version_number=1,
                status="DRAFT",
                problem_hash="sha256:policy",
                solver="heuristic",
                objective_value=1.0,
                solution_status="FEASIBLE",
                cost_total=300.0,
                cost_currency="EUR",
                cost_unknown_count=0,
                trip_version=trip_row.version,
                validation={"feasible": True},
                unplaced=[],
                policy_violations=[],
            )
            session.add(version)
            session.flush()
            plan_id = version.id

        activated = client.post(
            f"/v1/trips/{trip_id}/plans/{plan_id}:activate",
            headers={**headers(), "If-Match": str(trip_row.version)},
        )
        assert activated.status_code == 409, activated.text
        assert activated.json()["error"]["code"] == "POLICY_VIOLATION"
    finally:
        cleanup(trip_id, policy_id)
