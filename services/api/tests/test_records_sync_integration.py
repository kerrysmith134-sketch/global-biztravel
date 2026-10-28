"""MS-5 验收：现场记录与离线同步（真实 PostgreSQL）。

覆盖设计文档第 10.5 章的冲突矩阵与 AT-010 / AT-011 的后端部分：

- 现场记录写入后拜访状态推进；同一 ``client_record_id`` 重放不产生第二条记录。
- LWW：较旧的客户端时间落败，落败版本以 ``applied=false`` 留档，服务端当前值不变。
- ``sync:push`` 逐项返回：拜访记录 applied、计划停靠 rejected、缺字段 rejected。
- 整批幂等：同 ``Idempotency-Key`` 重放不会写第二遍。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.geo import Place
from app.models.records import VisitRecord
from app.models.trip import Trip
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

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


def create_visit(client: TestClient) -> tuple[str, str, str]:
    """建一套 trip + place + visit，返回三个 id。"""
    now = datetime.now(UTC)
    name = uuid.uuid4().hex[:8]
    trip = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"MS5 现场记录·{name}",
            "window_start": (now + timedelta(days=5)).isoformat(),
            "window_end": (now + timedelta(days=8)).isoformat(),
            "countries": ["DE"],
        },
    )
    assert trip.status_code == 201, trip.text
    trip_id = trip.json()["id"]

    place = client.post(
        "/v1/places",
        headers=headers(),
        json={
            "name": f"MS5 客户总部·{name}",
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
        json={"place_id": place_id, "title": "现场拜访", "service_minutes": 60},
    )
    assert visit.status_code == 201, visit.text
    return trip_id, place_id, visit.json()["id"]


def drop_fixture(trip_id: str, place_id: str) -> None:
    with org_session(UUID(ORG_A)) as session:
        session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))
        session.execute(delete(Place).where(Place.id == UUID(place_id)))


def visit_status(client: TestClient, trip_id: str, visit_id: str) -> str:
    resp = client.get(f"/v1/trips/{trip_id}/visits", headers=headers())
    assert resp.status_code == 200, resp.text
    return next(item for item in resp.json() if item["id"] == visit_id)["status"]


def record_count(visit_id: str) -> int:
    with org_session(UUID(ORG_A)) as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(VisitRecord)
                .where(VisitRecord.visit_id == UUID(visit_id))
            )
            or 0
        )


def record_payload(*, client_record_id: str, outcome: str, at: datetime, notes: str = "") -> dict:
    return {
        "client_record_id": client_record_id,
        "outcome": outcome,
        "notes": notes,
        "client_updated_at": at.isoformat(),
        "photos": [],
        "follow_up_tasks": [],
    }


# ------------------------------------------------------------ 直接写入
def test_record_write_is_idempotent_and_advances_visit(client: TestClient) -> None:
    trip_id, place_id, visit_id = create_visit(client)
    client_record_id = str(uuid.uuid4())
    at = datetime.now(UTC) - timedelta(minutes=10)
    payload = record_payload(
        client_record_id=client_record_id, outcome="MET", at=at, notes="与客户 CTO 会面 45 分钟"
    )
    try:
        first = client.post(
            f"/v1/visits/{visit_id}/records",
            headers={**headers(), "Idempotency-Key": f"rec-{client_record_id}"},
            json=payload,
        )
        assert first.status_code == 201, first.text
        body = first.json()
        assert body["applied"] is True
        assert body["server_version"] == 1
        assert body["conflict"] is None

        second = client.post(
            f"/v1/visits/{visit_id}/records",
            headers={**headers(), "Idempotency-Key": f"rec-{client_record_id}"},
            json=payload,
        )
        assert second.status_code == 201
        assert second.json()["id"] == body["id"]
        assert record_count(visit_id) == 1
        assert visit_status(client, trip_id, visit_id) == "DONE"

        # 换一个非幂等键重试：记录级 client_record_id 仍然挡住第二次落库。
        third = client.post(
            f"/v1/visits/{visit_id}/records",
            headers=headers(),
            json=payload,
        )
        assert third.status_code == 201
        assert third.json()["id"] == body["id"]
        assert record_count(visit_id) == 1
    finally:
        drop_fixture(trip_id, place_id)


def test_last_writer_wins_keeps_losing_copy(client: TestClient) -> None:
    trip_id, place_id, visit_id = create_visit(client)
    now = datetime.now(UTC)
    newer_id = str(uuid.uuid4())
    older_id = str(uuid.uuid4())
    latest_id = str(uuid.uuid4())
    try:
        first = client.post(
            f"/v1/visits/{visit_id}/records",
            headers=headers(),
            json=record_payload(
                client_record_id=newer_id, outcome="MET", at=now, notes="已会面"
            ),
        )
        assert first.status_code == 201, first.text

        # 更旧的客户端时间：落败，不改拜访当前值，但保留冲突副本。
        loser = client.post(
            f"/v1/visits/{visit_id}/records",
            headers=headers(),
            json=record_payload(
                client_record_id=older_id,
                outcome="NOT_MET",
                at=now - timedelta(hours=2),
                notes="离线时写的旧记录",
            ),
        )
        assert loser.status_code == 201, loser.text
        loser_body = loser.json()
        assert loser_body["applied"] is False
        assert loser_body["conflict"]["reason"] == "LAST_WRITER_WINS"
        assert loser_body["conflict"]["server"]["outcome"] == "MET"
        assert loser_body["conflict"]["client"]["outcome"] == "NOT_MET"
        assert visit_status(client, trip_id, visit_id) == "DONE"
        assert record_count(visit_id) == 2  # 1 生效 + 1 冲突副本

        # 更新的时间：正常生效，版本递增。
        latest = client.post(
            f"/v1/visits/{visit_id}/records",
            headers=headers(),
            json=record_payload(
                client_record_id=latest_id,
                outcome="CANCELLED",
                at=now + timedelta(minutes=5),
                notes="客户临时取消",
            ),
        )
        assert latest.status_code == 201
        latest_body = latest.json()
        assert latest_body["applied"] is True
        assert latest_body["server_version"] == 2
        assert visit_status(client, trip_id, visit_id) == "CANCELLED"
    finally:
        drop_fixture(trip_id, place_id)


# ------------------------------------------------------------ 批次同步
def test_sync_push_returns_per_item_verdicts_and_is_idempotent(client: TestClient) -> None:
    trip_id, place_id, visit_id = create_visit(client)
    client_record_id = str(uuid.uuid4())
    at = datetime.now(UTC)
    payload = record_payload(
        client_record_id=client_record_id, outcome="MET", at=at, notes="离线笔记"
    )
    payload["visit_id"] = visit_id
    body = {
        "device_id": "at-011-device",
        "items": [
            {
                "item_id": client_record_id,
                "entity": "visit_record",
                "operation": "upsert",
                "payload": payload,
                "client_updated_at": at.isoformat(),
            },
            {
                "item_id": str(uuid.uuid4()),
                "entity": "plan_stop",
                "operation": "upsert",
                "payload": {"visit_id": visit_id, "sequence": 9},
                "client_updated_at": at.isoformat(),
            },
            {
                "item_id": str(uuid.uuid4()),
                "entity": "visit_record",
                "operation": "upsert",
                "payload": {"outcome": "MET"},
                "client_updated_at": at.isoformat(),
            },
        ],
    }
    key = f"sync-{uuid.uuid4().hex}"
    try:
        resp = client.post(
            "/v1/sync:push",
            headers={**headers(), "Idempotency-Key": key},
            json=body,
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()
        assert result["results"][0]["item_id"] == client_record_id
        assert result["results"][0]["status"] == "applied"
        assert result["results"][0]["server_version"] == 1
        plan_stop = next(i for i in result["results"] if i["entity"] == "plan_stop")
        assert plan_stop["status"] == "rejected"
        assert plan_stop["conflict"]["reason"] == "PLAN_IMMUTABLE"
        missing_visit = next(
            i for i in result["results"] if i["item_id"] not in (client_record_id, plan_stop["item_id"])
        )
        assert missing_visit["status"] == "rejected"
        assert missing_visit["conflict"]["reason"] == "MISSING_VISIT_ID"
        assert result["summary"] == {"applied": 1, "conflict": 0, "rejected": 2}

        # 整批同键重放：字节一致，记录不重复。
        replay = client.post(
            "/v1/sync:push",
            headers={**headers(), "Idempotency-Key": key},
            json=body,
        )
        assert replay.status_code == 200
        assert replay.headers.get("Idempotent-Replay") == "true"
        assert replay.content == resp.content
        assert record_count(visit_id) == 1
        assert visit_status(client, trip_id, visit_id) == "DONE"
    finally:
        drop_fixture(trip_id, place_id)


def test_cross_tenant_cannot_write_record(client: TestClient) -> None:
    trip_id, place_id, visit_id = create_visit(client)
    try:
        resp = client.post(
            f"/v1/visits/{visit_id}/records",
            headers=headers(ORG_B, USER_B),
            json=record_payload(
                client_record_id=str(uuid.uuid4()),
                outcome="MET",
                at=datetime.now(UTC),
            ),
        )
        assert resp.status_code == 404
    finally:
        drop_fixture(trip_id, place_id)
