"""出差结果分析与客户机会复盘（PRD P1）验收。

只读聚合：拜访结果（计划 vs 完成 vs 未会面）、费用计划 vs 实际（多币种分列不折算）、
纪要分类洞察按类别计数、商机信号原文附上且标注"待确认"。红线：不写 CRM、不当已确认事实。
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.crm import CrmSyncJob
from app.models.planning import PlanVersion
from app.models.trip import Trip
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "33333333-3333-3333-3333-333333333333"
ADMIN_A = "55555555-5555-5555-5555-555555555555"
USER_B = "44444444-4444-4444-4444-444444444444"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers(user: str = ADMIN_A, org: str = ORG_A) -> dict[str, str]:
    return {
        "X-Org-Id": org,
        "X-User-Id": user,
        "Idempotency-Key": str(uuid.uuid4()),
    }


def make_visit(client: TestClient, trip_id: str, *, status: str = "DONE") -> str:
    place = client.post(
        "/v1/places",
        headers=headers(),
        json={
            "name": f"结果地点·{uuid.uuid4().hex[:6]}",
            "city": "Berlin",
            "country_code": "DE",
            "lat": 52.52,
            "lon": 13.405,
        },
    )
    assert place.status_code == 201, place.text
    visit = client.post(
        f"/v1/trips/{trip_id}/visits",
        headers=headers(),
        json={"place_id": place.json()["id"], "title": "结果拜访", "service_minutes": 60},
    )
    assert visit.status_code == 201, visit.text
    visit_id = visit.json()["id"]
    if status != "PLANNED":
        patched = client.patch(
            f"/v1/visits/{visit_id}", headers=headers(), json={"status": status}
        )
        assert patched.status_code == 200, patched.text
    return visit_id


def make_record(client: TestClient, visit_id: str, outcome: str) -> None:
    record = client.post(
        f"/v1/visits/{visit_id}/records",
        headers=headers(),
        json={
            "client_record_id": str(uuid.uuid4()),
            "outcome": outcome,
            "notes": f"结果记录 {outcome}",
            "client_updated_at": datetime.now(UTC).isoformat(),
            "photos": [],
            "follow_up_tasks": [],
        },
    )
    assert record.status_code == 201, record.text


def make_minutes_with_insights(
    client: TestClient, visit_id: str
) -> None:
    resp = client.post(f"/v1/visits/{visit_id}/minutes:draft", headers=headers(), json={})
    assert resp.status_code == 200, resp.text


def test_outcome_aggregates_visits_cost_and_opportunities(client: TestClient) -> None:
    now = datetime.now(UTC)
    trip = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"结果分析·{uuid.uuid4().hex[:8]}",
            "window_start": (now + timedelta(days=3)).isoformat(),
            "window_end": (now + timedelta(days=5)).isoformat(),
            "countries": ["DE"],
        },
    )
    assert trip.status_code == 201, trip.text
    trip_id = trip.json()["id"]

    v_done = make_visit(client, trip_id, status="DONE")
    make_visit(client, trip_id, status="DONE")
    make_visit(client, trip_id, status="MISSED")
    make_record(client, v_done, "MET")

    with org_session(UUID(ORG_A)) as session:
        session.add(
            PlanVersion(
                organization_id=UUID(ORG_A),
                trip_id=UUID(trip_id),
                version_number=1,
                status="ACTIVE",
                problem_hash="sha256:outcome",
                solver="heuristic",
                objective_value=1.0,
                solution_status="FEASIBLE",
                cost_total=400.0,
                cost_currency="EUR",
                cost_unknown_count=0,
                trip_version=1,
                validation={"feasible": True},
                unplaced=[],
            )
        )
    for currency, amount in (("EUR", "150.00"), ("USD", "80.00")):
        expense = client.post(
            f"/v1/trips/{trip_id}/expenses",
            headers=headers(),
            json={
                "category": "MEALS",
                "amount": amount,
                "currency": currency,
                "occurred_on": date.today().isoformat(),
            },
        )
        assert expense.status_code == 201, expense.text

    try:
        outcome = client.get(f"/v1/trips/{trip_id}/outcome", headers=headers())
        assert outcome.status_code == 200, outcome.text
        body = outcome.json()

        assert body["trip_id"] == trip_id
        assert body["visits"] == {"DONE": 2, "MISSED": 1}
        assert body["record_outcomes"] == {"MET": 1}
        assert body["plan_cost"]["total"] == 400.0
        assert body["plan_cost"]["currency"] == "EUR"
        assert {c["currency"] for c in body["actual_cost"]} == {"EUR", "USD"}

        # 纪要洞察：生成后按类别计数，商机信号原文附上且标注待确认。
        make_minutes_with_insights(client, v_done)
        outcome2 = client.get(f"/v1/trips/{trip_id}/outcome", headers=headers())
        assert outcome2.status_code == 200, outcome2.text
        body2 = outcome2.json()
        assert sum(body2["insights_by_kind"].values()) >= 1
        for signal in body2["opportunity_signals"]:
            assert signal["requires_confirmation"] is True, signal
            assert signal["text"].strip()

        # 跨租户 404。
        stolen = client.get(f"/v1/trips/{trip_id}/outcome", headers=headers(USER_B, ORG_B))
        assert stolen.status_code == 404
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))


def test_outcome_never_writes_crm(client: TestClient) -> None:
    now = datetime.now(UTC)
    trip = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"结果不写CRM·{uuid.uuid4().hex[:8]}",
            "window_start": (now + timedelta(days=3)).isoformat(),
            "window_end": (now + timedelta(days=5)).isoformat(),
            "countries": ["DE"],
        },
    )
    assert trip.status_code == 201, trip.text
    trip_id = trip.json()["id"]
    visit_id = make_visit(client, trip_id, status="DONE")
    make_minutes_with_insights(client, visit_id)
    try:
        outcome = client.get(f"/v1/trips/{trip_id}/outcome", headers=headers())
        assert outcome.status_code == 200
        with org_session(UUID(ORG_A)) as session:
            crm_count = session.scalar(
                select(func.count()).select_from(CrmSyncJob).where(
                    CrmSyncJob.organization_id == UUID(ORG_A)
                )
            )
            assert int(crm_count or 0) == 0
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))
