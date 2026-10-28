"""拜访前工作台：客户、地点、目标、历史、跟进、记忆与显式缺口。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.geo import Customer, Place
from app.models.memory import TripMemorySummary
from app.models.trip import Trip
from fastapi.testclient import TestClient
from sqlalchemy import delete

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


def create_place(client: TestClient, city: str = "Berlin") -> str:
    resp = client.post(
        "/v1/places",
        headers=headers(),
        json={
            "name": f"简报地点·{uuid.uuid4().hex[:6]}",
            "city": city,
            "country_code": "DE",
            "lat": 52.52,
            "lon": 13.405,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def create_trip(client: TestClient, start: datetime, days: int = 3) -> str:
    resp = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"简报出行·{uuid.uuid4().hex[:8]}",
            "window_start": start.isoformat(),
            "window_end": (start + timedelta(days=days)).isoformat(),
            "countries": ["DE"],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_visit_briefing_aggregates_history_and_memory(client: TestClient) -> None:
    now = datetime.now(UTC)
    customer = client.post(
        "/v1/customers",
        headers=headers(),
        json={
            "name": f"简报客户·{uuid.uuid4().hex[:8]}",
            "tier": "A",
            "industry": "新能源",
            "primary_contact_name": "Maria",
            "primary_contact_email": "maria@example.com",
        },
    )
    assert customer.status_code == 201, customer.text
    customer_id = customer.json()["id"]

    current_place = create_place(client)
    past_place = create_place(client, "Munich")
    other_place = create_place(client, "Hamburg")
    current_trip = create_trip(client, now + timedelta(days=10))
    past_trip = create_trip(client, now - timedelta(days=60), days=2)
    memory_id = uuid.uuid4()

    try:
        past_visit = client.post(
            f"/v1/trips/{past_trip}/visits",
            headers=headers(),
            json={
                "place_id": past_place,
                "customer_id": customer_id,
                "title": "上次拜访",
                "service_minutes": 60,
            },
        )
        assert past_visit.status_code == 201, past_visit.text
        past_visit_id = past_visit.json()["id"]
        record = client.post(
            f"/v1/visits/{past_visit_id}/records",
            headers=headers(),
            json={
                "client_record_id": str(uuid.uuid4()),
                "outcome": "MET",
                "notes": "客户关注交付周期，要求补充产能证明。",
                "client_updated_at": now.isoformat(),
                "photos": [],
                "follow_up_tasks": [{"title": "补充产能证明", "due_date": "2026-11-15"}],
            },
        )
        assert record.status_code == 201, record.text
        done = client.patch(
            f"/v1/visits/{past_visit_id}", headers=headers(), json={"status": "DONE"}
        )
        assert done.status_code == 200, done.text

        with org_session(UUID(ORG_A)) as session:
            session.add(
                TripMemorySummary(
                    id=memory_id,
                    organization_id=UUID(ORG_A),
                    trip_id=UUID(past_trip),
                    customer_id=UUID(customer_id),
                    kind="CUSTOMER_NOTE",
                    summary="客户重视交付确定性，价格敏感度中等。",
                    decisions=["优先提供产能证明"],
                    follow_ups=["补充产能证明"],
                    lessons=[],
                    confidence=0.9,
                )
            )

        visit = client.post(
            f"/v1/trips/{current_trip}/visits",
            headers=headers(),
            json={
                "place_id": current_place,
                "customer_id": customer_id,
                "title": "续约沟通",
                "objective": "确认续约条款并拿到采购时间表",
                "service_minutes": 90,
            },
        )
        assert visit.status_code == 201, visit.text
        visit_id = visit.json()["id"]
        assert visit.json()["objective"] == "确认续约条款并拿到采购时间表"

        other = client.post(
            f"/v1/trips/{current_trip}/visits",
            headers=headers(),
            json={"place_id": other_place, "title": "无客户拜访"},
        )
        assert other.status_code == 201

        briefing = client.get(f"/v1/visits/{visit_id}/briefing", headers=headers())
        assert briefing.status_code == 200, briefing.text
        body = briefing.json()
        assert body["visit"]["objective"] == "确认续约条款并拿到采购时间表"
        assert body["customer"]["name"].startswith("简报客户")
        assert body["customer"]["primary_contact_email"] == "maria@example.com"
        assert body["place"]["city"] == "Berlin"
        assert len(body["history"]) == 1
        assert body["history"][0]["trip_id"] == past_trip
        assert body["history"][0]["outcome"] == "MET"
        assert "交付周期" in body["history"][0]["notes"]
        assert [item["title"] for item in body["follow_ups"]] == ["补充产能证明"]
        assert body["memories"][0]["summary"].startswith("客户重视交付确定性")
        gap_codes = {item["code"] for item in body["data_gaps"]}
        assert "CRM_OPPORTUNITY_NOT_CONNECTED" in gap_codes
        assert "VISIT_MATERIALS_NOT_MODELED" in gap_codes
        assert "CUSTOMER_NOT_LINKED" not in gap_codes

        updated = client.patch(
            f"/v1/visits/{visit_id}",
            headers=headers(),
            json={"objective": "确认续约条款、采购时间表与验收标准"},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["objective"] == "确认续约条款、采购时间表与验收标准"

        linked_missing = client.get(
            f"/v1/visits/{other.json()['id']}/briefing", headers=headers()
        )
        assert linked_missing.status_code == 200
        assert linked_missing.json()["history"] == []
        assert "CUSTOMER_NOT_LINKED" in {
            item["code"] for item in linked_missing.json()["data_gaps"]
        }

        stolen = client.get(
            f"/v1/visits/{visit_id}/briefing", headers=headers(USER_B, ORG_B)
        )
        assert stolen.status_code == 404
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(delete(TripMemorySummary).where(TripMemorySummary.id == memory_id))
            session.execute(delete(Trip).where(Trip.id.in_([UUID(current_trip), UUID(past_trip)])))
            session.execute(
                delete(Place).where(
                    Place.id.in_([UUID(current_place), UUID(past_place), UUID(other_place)])
                )
            )
            session.execute(delete(Customer).where(Customer.id == UUID(customer_id)))
