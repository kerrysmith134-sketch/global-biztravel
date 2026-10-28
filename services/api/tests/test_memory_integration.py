"""M2 验收：记忆层（事实 / 过程 / 情景）与混合检索（真实 PostgreSQL）。

验证设计文档第 9 章的关键行为：

- 行程完成后复盘生成情景记忆（摘要 + 决策 + 待办 + 教训），重复复盘幂等。
- 过程记忆（规划尝试日志）随行程记忆一起返回。
- 复盘把可观察的统计写回事实记忆（出差人画像），旧版本以 ``valid_until`` 失活。
- ``GET /v1/memory:search`` 的关键词 + 向量融合能召回该摘要。
- 跨租户读不到记忆。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.geo import Place
from app.models.memory import TravelerProfile, TripMemorySummary
from app.models.trip import Trip
from app.services import memory_service
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "33333333-3333-3333-3333-333333333333"
USER_A2 = "55555555-5555-5555-5555-555555555555"  # mia@anari.example（ADMIN）
USER_B = "44444444-4444-4444-4444-444444444444"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers(org: str = ORG_A, user: str = USER_A2) -> dict[str, str]:
    return {"X-Org-Id": org, "X-User-Id": user}


def build_completed_trip(client: TestClient) -> tuple[str, str, str]:
    now = datetime.now(UTC)
    name = uuid.uuid4().hex[:8]
    trip = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"M2 记忆验收·{name}",
            "window_start": (now - timedelta(days=2)).isoformat(),
            "window_end": (now - timedelta(days=1)).isoformat(),
            "countries": ["DE"],
        },
    )
    assert trip.status_code == 201, trip.text
    trip_id = trip.json()["id"]

    place = client.post(
        "/v1/places",
        headers=headers(),
        json={
            "name": f"M2 客户·{name}",
            "city": "Berlin",
            "country_code": "DE",
            "lat": 52.52,
            "lon": 13.405,
        },
    )
    assert place.status_code == 201, place.text
    place_id = place.json()["id"]

    visit = client.post(
        f"/v1/trips/{trip_id}/visits",
        headers=headers(),
        json={"place_id": place_id, "title": "季度复盘拜访", "service_minutes": 90},
    )
    assert visit.status_code == 201, visit.text
    visit_id = visit.json()["id"]

    record = client.post(
        f"/v1/visits/{visit_id}/records",
        headers=headers(),
        json={
            "client_record_id": str(uuid.uuid4()),
            "outcome": "MET",
            "notes": "客户确认明年预算，要求两周内给方案",
            "client_updated_at": now.isoformat(),
            "photos": [],
            "follow_up_tasks": [{"title": "两周内提交续约方案", "due_date": "2026-11-15"}],
        },
    )
    assert record.status_code == 201, record.text

    completed = client.patch(
        f"/v1/trips/{trip_id}",
        headers=headers(),
        json={"status": "COMPLETED"},
    )
    assert completed.status_code == 200, completed.text
    return trip_id, place_id, visit_id


def cleanup(trip_id: str, place_id: str) -> None:
    with org_session(UUID(ORG_A)) as session:
        session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))
        session.execute(delete(Place).where(Place.id == UUID(place_id)))
        # 复盘会为 USER_A2 写画像；测试结束把该用户的画像全部清掉，回到基线。
        session.execute(
            delete(TravelerProfile).where(TravelerProfile.user_id == UUID(USER_A2))
        )


def test_trip_review_builds_memory_and_is_idempotent(client: TestClient) -> None:
    trip_id, place_id, visit_id = build_completed_trip(client)
    try:
        # 先写一条过程记忆（模拟一次被否决的规划），复盘时它应出现在 procedure 里。
        with org_session(UUID(ORG_A)) as session:
            memory_service.log_attempt(
                session,
                UUID(ORG_A),
                UUID(trip_id),
                attempt_type=memory_service.ATTEMPT_REJECTED,
                outcome="TIME_WINDOW_INFEASIBLE",
                reason="客户时间窗与航班落地时间冲突",
            )

        review = client.post(f"/v1/trips/{trip_id}/memory:review", headers=headers())
        assert review.status_code == 200, review.text
        body = review.json()
        assert len(body["summaries"]) == 1
        summary = body["summaries"][0]
        assert summary["kind"] == "TRIP_REVIEW"
        assert "完成 1" in summary["summary"]
        assert "两周内提交续约方案" in summary["follow_ups"]
        assert any("TIME_WINDOW_INFEASIBLE" in lesson for lesson in summary["lessons"])
        assert 0 < summary["confidence"] <= 1
        assert body["procedure"], "过程记忆应随行程记忆返回"
        assert any(item["outcome"] == "PLAN_REJECTED" for item in body["procedure"])

        # 再触发一次：不新增摘要（幂等）。
        again = client.post(f"/v1/trips/{trip_id}/memory:review", headers=headers())
        assert again.status_code == 200
        assert len(again.json()["summaries"]) == 1

        with org_session(UUID(ORG_A)) as session:
            count = session.scalar(
                select(func.count())
                .select_from(TripMemorySummary)
                .where(TripMemorySummary.trip_id == UUID(trip_id))
            )
        assert count == 1

        # 事实记忆：复盘根据拜访统计写入偏好；GET 画像应能看到。
        profile = client.get("/v1/traveler/profile", headers=headers())
        assert profile.status_code == 200, profile.text
        data = profile.json()
        assert data["version"] >= 1
        assert data["preferences"]["preferred_movement_mode"] == "GROUND"
        assert data["preferences"]["average_service_minutes"] == 90.0

        with org_session(UUID(ORG_A)) as session:
            active_profiles = session.scalar(
                select(func.count())
                .select_from(TravelerProfile)
                .where(
                    TravelerProfile.user_id == UUID(USER_A2),
                    TravelerProfile.valid_until.is_(None),
                )
            )
        assert active_profiles == 1
    finally:
        cleanup(trip_id, place_id)


def test_memory_search_returns_review_and_respects_tenant(client: TestClient) -> None:
    trip_id, place_id, visit_id = build_completed_trip(client)
    try:
        review = client.post(f"/v1/trips/{trip_id}/memory:review", headers=headers())
        assert review.status_code == 200, review.text
        summary_id = review.json()["summaries"][0]["id"]

        search = client.get(
            "/v1/memory:search", headers=headers(), params={"q": "续约方案"}
        )
        assert search.status_code == 200, search.text
        items = search.json()["items"]
        assert any(item["id"] == summary_id for item in items)
        assert items[0]["score"] > 0

        # 限定一次没有记忆的出行 → 空结果，而不是越权。
        other_trip = client.post(
            "/v1/trips",
            headers=headers(),
            json={
                "name": f"M2 空记忆·{uuid.uuid4().hex[:6]}",
                "window_start": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
                "window_end": (datetime.now(UTC) + timedelta(days=32)).isoformat(),
                "countries": ["DE"],
            },
        )
        assert other_trip.status_code == 201
        other_id = other_trip.json()["id"]
        try:
            empty = client.get(
                "/v1/memory:search",
                headers=headers(),
                params={"q": "续约方案", "trip_id": other_id},
            )
            assert empty.status_code == 200
            assert empty.json()["items"] == []
        finally:
            with org_session(UUID(ORG_A)) as session:
                session.execute(delete(Trip).where(Trip.id == UUID(other_id)))

        # 跨租户：另一个组织看同一次出行的记忆 → 404（连存在都不暴露）。
        stolen = client.get(f"/v1/trips/{trip_id}/memory", headers=headers(ORG_B, USER_B))
        assert stolen.status_code == 404
    finally:
        cleanup(trip_id, place_id)


def test_embedding_is_deterministic() -> None:
    first = memory_service.embed_text("柏林客户续约方案")
    second = memory_service.embed_text("柏林客户续约方案")
    other = memory_service.embed_text("完全不同的一句话")
    assert first == second
    assert first != other
    assert abs(sum(value * value for value in first) - 1.0) < 1e-6


def test_forget_redacts_content_and_hides_from_reads(client: TestClient) -> None:
    trip_id, place_id, visit_id = build_completed_trip(client)
    try:
        review = client.post(f"/v1/trips/{trip_id}/memory:review", headers=headers())
        assert review.status_code == 200, review.text
        summary_id = review.json()["summaries"][0]["id"]

        forgotten = client.delete(f"/v1/memory/{summary_id}", headers=headers())
        assert forgotten.status_code == 200, forgotten.text
        assert forgotten.json()["forgotten"] is True
        assert forgotten.json()["valid_until"] is not None

        with org_session(UUID(ORG_A)) as session:
            row = session.get(TripMemorySummary, UUID(summary_id))
            assert row is not None
            assert row.summary == "[已按合规要求删除]"
            assert row.decisions == [] and row.follow_ups == [] and row.lessons == []
            assert row.embedding is None
            assert row.valid_until is not None

        after = client.get(f"/v1/trips/{trip_id}/memory", headers=headers())
        assert after.status_code == 200
        assert after.json()["summaries"] == []

        # 跨租户删除同一条记忆 → 403/404。ORG_B 的这位用户不是 ADMIN，会先在角色门被拒；
        # 即使是 ADMIN，RLS 也会让该行不可见。两种结果都证明"删不到"。
        stolen = client.delete(
            f"/v1/memory/{summary_id}", headers=headers(ORG_B, USER_B)
        )
        assert stolen.status_code in (403, 404)
    finally:
        cleanup(trip_id, place_id)
