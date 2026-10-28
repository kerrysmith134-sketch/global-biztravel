"""契约验收：嵌套计划详情路径与列表计数（设计文档 §13.2）。"""

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
from sqlalchemy import delete, select

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
USER_A = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers() -> dict[str, str]:
    return {"X-Org-Id": ORG_A, "X-User-Id": USER_A}


def test_nested_plan_detail_and_list_counts() -> None:
    client = TestClient(app)
    with org_session(UUID(ORG_A)) as session:
        row = session.execute(
            select(PlanVersion.trip_id, PlanVersion.id)
            .where(PlanVersion.organization_id == UUID(ORG_A))
            .order_by(PlanVersion.created_at.desc())
            .limit(1)
        ).one_or_none()
    assert row is not None, "需要先跑 seed_dev 或至少生成一份计划"
    trip_id, plan_id = row

    plans = client.get(f"/v1/trips/{trip_id}/plans", headers=headers())
    assert plans.status_code == 200, plans.text
    versions = plans.json()
    target = next(item for item in versions if item["id"] == str(plan_id))
    assert target["stop_count"] >= 0
    assert target["day_count"] >= 0

    nested = client.get(f"/v1/trips/{trip_id}/plans/{plan_id}", headers=headers())
    assert nested.status_code == 200, nested.text
    detail = nested.json()
    assert detail["trip_id"] == str(trip_id)
    assert len(detail["stops"]) == detail["stop_count"]
    assert detail["day_count"] == len({stop["day_index"] for stop in detail["stops"]})

    trip = client.get(f"/v1/trips/{trip_id}", headers=headers())
    assert trip.status_code == 200
    body = trip.json()
    assert body["visit_count"] >= 0
    assert body["customer_count"] >= 0
    assert body["plan_version_count"] >= len(versions)

    # 用另一趟没有计划的出行访问同一个 plan id → 404，而不是泄漏计划的归属。
    now = datetime.now(UTC)
    other = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"契约负例·{uuid.uuid4().hex[:6]}",
            "window_start": (now + timedelta(days=30)).isoformat(),
            "window_end": (now + timedelta(days=32)).isoformat(),
            "countries": ["DE"],
        },
    )
    assert other.status_code == 201, other.text
    other_id = other.json()["id"]
    try:
        wrong = client.get(f"/v1/trips/{other_id}/plans/{plan_id}", headers=headers())
        assert wrong.status_code == 404
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(delete(Trip).where(Trip.id == UUID(other_id)))
