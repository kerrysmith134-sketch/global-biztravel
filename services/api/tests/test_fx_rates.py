"""汇率录入与预算折算：只用显式汇率；缺汇率保持未折算。"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from app.db import org_session, owner_session
from app.main import app
from app.models.fx import FxRate
from app.models.identity import Membership, User
from app.models.planning import PlanVersion
from app.models.trip import Trip
from fastapi.testclient import TestClient
from sqlalchemy import delete

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "33333333-3333-3333-3333-333333333333"
USER_A = "22222222-2222-2222-2222-222222222222"
USER_B = "44444444-4444-4444-4444-444444444444"
ADMIN_USER = "55555555-5555-5555-5555-555555555555"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers(user: str = ADMIN_USER, org: str = ORG_A) -> dict[str, str]:
    return {"X-Org-Id": org, "X-User-Id": user}


def test_fx_rates_and_budget_conversion(client: TestClient) -> None:
    now = datetime.now(UTC)
    tag = f"fx-test-{uuid.uuid4().hex[:8]}"
    as_of = datetime(2026, 1, 1, tzinfo=UTC)
    traveler_id = uuid.uuid4()
    with owner_session() as session:
        session.add(
            User(
                id=traveler_id,
                email=f"fx-traveler-{uuid.uuid4().hex[:8]}@anari.example",
                display_name="FX Traveler",
            )
        )
        session.flush()
        session.add(
            Membership(organization_id=UUID(ORG_A), user_id=traveler_id, role="TRAVELER")
        )
    trip = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"汇率折算·{uuid.uuid4().hex[:8]}",
            "window_start": (now + timedelta(days=5)).isoformat(),
            "window_end": (now + timedelta(days=8)).isoformat(),
            "countries": ["DE"],
            "currency": "EUR",
            "budget_amount": 1000,
        },
    )
    assert trip.status_code == 201, trip.text
    trip_id = trip.json()["id"]

    def add_expense(amount: str, currency: str) -> None:
        resp = client.post(
            f"/v1/trips/{trip_id}/expenses",
            headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
            json={
                "category": "MEALS",
                "amount": amount,
                "currency": currency,
                "occurred_on": date.today().isoformat(),
            },
        )
        assert resp.status_code == 201, resp.text

    try:
        add_expense("200.00", "EUR")
        add_expense("110.00", "USD")
        add_expense("100.00", "GBP")
        with org_session(UUID(ORG_A)) as session:
            session.add(
                PlanVersion(
                    organization_id=UUID(ORG_A),
                    trip_id=UUID(trip_id),
                    version_number=1,
                    status="ACTIVE",
                    problem_hash="sha256:fx",
                    solver="heuristic",
                    objective_value=1.0,
                    solution_status="FEASIBLE",
                    cost_total=300.0,
                    cost_currency="USD",
                    cost_unknown_count=0,
                    trip_version=1,
                    validation={"feasible": True},
                    unplaced=[],
                )
            )

        def rate(
            base: str,
            quote: str,
            value: str,
            source: str = tag,
            at: datetime = as_of,
        ) -> None:
            resp = client.post(
                "/v1/fx-rates",
                headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
                json={
                    "base_currency": base,
                    "quote_currency": quote,
                    "rate": value,
                    "as_of": at.isoformat(),
                    "source": source,
                },
            )
            assert resp.status_code == 201, resp.text

        # 1 EUR = 1.1 USD；GBP→EUR 走反向记录：1 GBP = 1.2 EUR。
        rate("EUR", "USD", "1.1")
        rate("GBP", "EUR", "1.2")

        partial = client.get(
            f"/v1/trips/{trip_id}/budget", headers=headers(USER_A), params={"base_currency": "EUR"}
        )
        assert partial.status_code == 200, partial.text
        partial_body = partial.json()
        assert partial_body["conversion_base_currency"] == "EUR"
        assert partial_body["conversion_complete"] is True
        # 200 + 110/1.1 + 100*1.2 = 420
        assert Decimal(str(partial_body["converted_actual_total"])) == Decimal("420.00")
        # 300 USD / 1.1 = 272.73
        assert Decimal(str(partial_body["converted_planned_cost_total"])) == Decimal("272.73")
        assert Decimal(str(partial_body["converted_variance_vs_budget"])) == Decimal("580.00")
        assert partial_body["unconverted_currencies"] == []
        assert len(partial_body["fx_conversions"]) == 3
        usd = next(item for item in partial_body["fx_conversions"] if item["currency"] == "USD")
        assert usd["via_inverse"] is False
        gbp = next(item for item in partial_body["fx_conversions"] if item["currency"] == "GBP")
        assert gbp["via_inverse"] is True

        # 新汇率生效后，同一时间点覆盖而不是追加一条。
        rate("EUR", "USD", "1.05")
        listed = client.get("/v1/fx-rates", headers=headers(USER_A))
        assert listed.status_code == 200
        eur_usd = [
            item for item in listed.json()
            if item["base_currency"] == "EUR" and item["quote_currency"] == "USD"
        ]
        assert len(eur_usd) == 1
        assert Decimal(str(eur_usd[0]["rate"])) == Decimal("1.05")

        # 未来生效的汇率不参与当前折算。
        rate(
            "EUR",
            "USD",
            "2.0",
            source=tag,
            at=now + timedelta(days=1),
        )
        future = client.get(
            f"/v1/trips/{trip_id}/budget", headers=headers(USER_A), params={"base_currency": "EUR"}
        )
        assert Decimal(str(future.json()["converted_actual_total"])) == Decimal("424.76")

        # 去掉 GBP 汇率后，预算折算不完整，也不给出"看似完整"的合计。
        with org_session(UUID(ORG_A)) as session:
            session.execute(
                delete(FxRate).where(
                    FxRate.organization_id == UUID(ORG_A),
                    FxRate.base_currency == "GBP",
                )
            )
        incomplete = client.get(
            f"/v1/trips/{trip_id}/budget", headers=headers(USER_A), params={"base_currency": "EUR"}
        )
        assert incomplete.status_code == 200
        body = incomplete.json()
        assert body["conversion_complete"] is False
        assert body["converted_actual_total"] is None
        assert body["unconverted_currencies"] == ["GBP"]

        # 其他组织看不到这些汇率；非管理员不能录入。
        other = client.get("/v1/fx-rates", headers=headers(USER_B, ORG_B))
        assert other.status_code == 200
        assert other.json() == []
        denied = client.post(
            "/v1/fx-rates",
            headers={**headers(str(traveler_id)), "Idempotency-Key": str(uuid.uuid4())},
            json={
                "base_currency": "EUR",
                "quote_currency": "JPY",
                "rate": "160",
                "source": tag,
            },
        )
        assert denied.status_code == 403
        same = client.post(
            "/v1/fx-rates",
            headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
            json={"base_currency": "EUR", "quote_currency": "EUR", "rate": "1"},
        )
        assert same.status_code == 422
    finally:
        with owner_session() as session:
            session.execute(delete(User).where(User.id == traveler_id))
        with org_session(UUID(ORG_A)) as session:
            session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))
            session.execute(delete(FxRate).where(FxRate.source == tag))
