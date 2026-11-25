"""差旅费用与预算汇总（PRD 方向六）验收。"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.planning import PlanVersion
from app.models.reliability import IdempotencyRecord
from app.models.trip import Trip
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "33333333-3333-3333-3333-333333333333"
USER_A = "22222222-2222-2222-2222-222222222222"
USER_B = "44444444-4444-4444-4444-444444444444"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers(org: str = ORG_A, user: str = USER_A) -> dict[str, str]:
    return {"X-Org-Id": org, "X-User-Id": user}


def test_expenses_and_budget_summary(client: TestClient) -> None:
    now = datetime.now(UTC)
    trip = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"费用闭环·{uuid.uuid4().hex[:8]}",
            "window_start": (now + timedelta(days=5)).isoformat(),
            "window_end": (now + timedelta(days=8)).isoformat(),
            "countries": ["DE"],
            "currency": "EUR",
            "budget_amount": 1000,
        },
    )
    assert trip.status_code == 201, trip.text
    trip_id = trip.json()["id"]
    try:
        with org_session(UUID(ORG_A)) as session:
            session.add(
                PlanVersion(
                    organization_id=UUID(ORG_A),
                    trip_id=UUID(trip_id),
                    version_number=1,
                    status="ACTIVE",
                    problem_hash="sha256:expense",
                    solver="heuristic",
                    objective_value=1.0,
                    solution_status="FEASIBLE",
                    cost_total=300.0,
                    cost_currency="EUR",
                    cost_unknown_count=1,
                    trip_version=1,
                    validation={"feasible": True},
                    unplaced=[],
                )
            )

        def add(amount: str, currency: str) -> dict:
            resp = client.post(
                f"/v1/trips/{trip_id}/expenses",
                headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
                json={
                    "category": "MEALS",
                    "amount": amount,
                    "currency": currency,
                    "occurred_on": date.today().isoformat(),
                    "note": "test",
                },
            )
            assert resp.status_code == 201, resp.text
            return resp.json()

        first = add("200.00", "eur")
        add("100.00", "USD")
        add("50.00", "EUR")

        listed = client.get(f"/v1/trips/{trip_id}/expenses", headers=headers())
        assert listed.status_code == 200
        assert len(listed.json()) == 3

        budget = client.get(f"/v1/trips/{trip_id}/budget", headers=headers())
        assert budget.status_code == 200, budget.text
        body = budget.json()
        assert Decimal(str(body["budget_amount"])) == Decimal("1000.00")
        assert body["budget_currency"] == "EUR"
        assert body["planned_cost_total"] == 300.0
        assert body["planned_cost_unknown_count"] == 1
        by_currency = {item["currency"]: item for item in body["actual_by_currency"]}
        assert by_currency["EUR"]["amount"] == 250.0
        assert by_currency["EUR"]["count"] == 2
        assert by_currency["USD"]["amount"] == 100.0
        assert Decimal(str(body["actual_total_in_budget_currency"])) == Decimal("250.00")
        assert Decimal(str(body["variance_vs_budget"])) == Decimal("750.00")
        assert body["variance_vs_plan"] == 700.0

        # 跨租户读不到费用。
        stolen = client.get(f"/v1/trips/{trip_id}/expenses", headers=headers(ORG_B, USER_B))
        assert stolen.status_code == 404

        deleted = client.delete(f"/v1/expenses/{first['id']}", headers=headers())
        assert deleted.status_code == 204
        after = client.get(f"/v1/trips/{trip_id}/expenses", headers=headers())
        assert len(after.json()) == 2
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))



def test_expense_idempotency_is_durable_for_long_offline_retries(client: TestClient) -> None:
    now = datetime.now(UTC)
    trip = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"离线费用幂等·{uuid.uuid4().hex[:8]}",
            "window_start": (now + timedelta(days=5)).isoformat(),
            "window_end": (now + timedelta(days=8)).isoformat(),
            "countries": ["DE"],
            "currency": "EUR",
            "budget_amount": 500,
        },
    )
    assert trip.status_code == 201, trip.text
    trip_id = trip.json()["id"]
    key = f"mobile-expense-{uuid.uuid4()}"
    body = {
        "category": "MEALS",
        "amount": "42.50",
        "currency": "EUR",
        "occurred_on": date.today().isoformat(),
        "note": "offline retry fixture",
    }
    request_headers = {**headers(), "Idempotency-Key": key}
    try:
        first_response = client.post(
            f"/v1/trips/{trip_id}/expenses",
            headers=request_headers,
            json=body,
        )
        replay = client.post(
            f"/v1/trips/{trip_id}/expenses",
            headers=request_headers,
            json=body,
        )
        assert first_response.status_code == 201, first_response.text
        assert replay.status_code == 201, replay.text
        assert replay.json()["id"] == first_response.json()["id"]
        assert replay.headers.get("Idempotent-Replay") == "true"

        conflicting = client.post(
            f"/v1/trips/{trip_id}/expenses",
            headers=request_headers,
            json={**body, "amount": "99.00"},
        )
        assert conflicting.status_code == 409, conflicting.text

        listed = client.get(f"/v1/trips/{trip_id}/expenses", headers=headers())
        assert listed.status_code == 200
        assert len(listed.json()) == 1

        with org_session(UUID(ORG_A)) as session:
            record = session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.organization_id == UUID(ORG_A),
                    IdempotencyRecord.endpoint == "POST /v1/trips/{trip_id}/expenses",
                    IdempotencyRecord.key == key,
                )
            ).one()
            assert record.expires_at is None
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(
                delete(IdempotencyRecord).where(
                    IdempotencyRecord.organization_id == UUID(ORG_A),
                    IdempotencyRecord.endpoint == "POST /v1/trips/{trip_id}/expenses",
                    IdempotencyRecord.key == key,
                )
            )
            session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))
