"""契约验收：嵌套计划详情路径与列表计数（设计文档 §13.2）。

用例自建出行、地点、拜访与计划版本（含两日停靠），**不依赖库里已有数据**：
早先版本取"ORG_A 里最新的一份计划"，在全新数据库上直接失败，也把断言结果
交给了环境存量。自建数据让本用例在任何库状态下语义一致。
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.geo import Place
from app.models.planning import PlanStop, PlanVersion
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


def create_trip_with_plan(client: TestClient) -> tuple[str, str, str]:
    """建一次出行 + 地点 + 两个拜访 + 一份两日计划版本，返回 (trip_id, place_id, plan_id)。"""
    now = datetime.now(UTC)
    trip = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"契约验收·{uuid.uuid4().hex[:8]}",
            "window_start": (now + timedelta(days=10)).isoformat(),
            "window_end": (now + timedelta(days=13)).isoformat(),
            "countries": ["DE"],
        },
    )
    assert trip.status_code == 201, trip.text
    trip_id = trip.json()["id"]

    place = client.post(
        "/v1/places",
        headers=headers(),
        json={
            "name": f"契约地点·{uuid.uuid4().hex[:6]}",
            "city": "Berlin",
            "country_code": "DE",
            "lat": 52.52,
            "lon": 13.405,
        },
    )
    assert place.status_code == 201, place.text
    place_id = place.json()["id"]

    visit_ids: list[str] = []
    for title in ("契约拜访·第一天", "契约拜访·第二天"):
        visit = client.post(
            f"/v1/trips/{trip_id}/visits",
            headers=headers(),
            json={"place_id": place_id, "title": title, "service_minutes": 60},
        )
        assert visit.status_code == 201, visit.text
        visit_ids.append(visit.json()["id"])

    start = now + timedelta(days=10)
    with org_session(UUID(ORG_A)) as session:
        trip_row = session.get(Trip, UUID(trip_id))
        assert trip_row is not None
        version = PlanVersion(
            organization_id=UUID(ORG_A),
            trip_id=UUID(trip_id),
            version_number=1,
            status="ACTIVE",
            problem_hash=f"sha256:contract-{uuid.uuid4().hex[:12]}",
            solver="heuristic",
            objective_value=1.0,
            solution_status="FEASIBLE",
            trip_version=trip_row.version,
            validation={"feasible": True},
            unplaced=[],
        )
        session.add(version)
        session.flush()
        for index, visit_id in enumerate(visit_ids):
            day = start + timedelta(days=index)
            arrive = datetime(day.year, day.month, day.day, 9, 0, tzinfo=UTC)
            session.add(
                PlanStop(
                    organization_id=UUID(ORG_A),
                    plan_version_id=version.id,
                    visit_id=UUID(visit_id),
                    day_index=index,
                    sequence=1,
                    day_date=date(day.year, day.month, day.day),
                    arrive_at=arrive,
                    depart_at=arrive + timedelta(hours=1),
                    travel_minutes=30,
                    service_minutes=60,
                )
            )
        trip_row.active_plan_version_id = version.id
        trip_row.status = "ACTIVE"
    return trip_id, place_id, str(version.id)


def test_nested_plan_detail_and_list_counts() -> None:
    client = TestClient(app)
    trip_id, place_id, plan_id = create_trip_with_plan(client)
    other_id: str | None = None
    try:
        plans = client.get(f"/v1/trips/{trip_id}/plans", headers=headers())
        assert plans.status_code == 200, plans.text
        versions = plans.json()
        target = next(item for item in versions if item["id"] == plan_id)
        # 计数来自 plan_stops 的真实聚合，不是版本行自报。
        assert target["stop_count"] == 2
        assert target["day_count"] == 2

        nested = client.get(f"/v1/trips/{trip_id}/plans/{plan_id}", headers=headers())
        assert nested.status_code == 200, nested.text
        detail = nested.json()
        assert detail["trip_id"] == str(trip_id)
        assert len(detail["stops"]) == detail["stop_count"] == 2
        assert detail["day_count"] == len({stop["day_index"] for stop in detail["stops"]}) == 2

        trip = client.get(f"/v1/trips/{trip_id}", headers=headers())
        assert trip.status_code == 200
        body = trip.json()
        assert body["visit_count"] == 2
        assert body["customer_count"] == 0  # 拜访未关联客户
        assert body["plan_version_count"] == len(versions) == 1

        # 用另一趟没有该计划的出行访问同一个 plan id → 404，而不是泄漏计划的归属。
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
        wrong = client.get(f"/v1/trips/{other_id}/plans/{plan_id}", headers=headers())
        assert wrong.status_code == 404
    finally:
        # 出行删除级联计划版本/停靠/拜访（FK ON DELETE CASCADE），地点随后单独清理。
        with org_session(UUID(ORG_A)) as session:
            if other_id:
                session.execute(delete(Trip).where(Trip.id == UUID(other_id)))
            session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))
            session.execute(delete(Place).where(Place.id == UUID(place_id)))
