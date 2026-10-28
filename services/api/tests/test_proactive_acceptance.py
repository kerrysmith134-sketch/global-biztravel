"""主动式运行时验收：AT-P01 ~ AT-P04（真实 PostgreSQL，不走模拟）。

这组用例对应设计文档 v2.0 第 16.3 章的四个主动式场景，并补一条 STATE 触发（缺坐标）：

- AT-P01 出发前签证时效触发：无用户请求下产生建议，含理由与证据，需人审。
- AT-P02 重复触发抑制：同 dedupe_key 只产生 1 条；超每日上限只入库不推送。
- AT-P03 人审 → 受控执行 → 审计：未审批不可执行；审批后执行并落审计链。
- AT-P04 驳回 → 误报回收：标记 DISMISSED，同类建议置信度下降（可观测）。

测试卫生：主动式建议会污染"每日打扰上限"的计数，因此本文件创建的出行在用例结束时
**直接删掉**（出行没有对外删除接口，这里是测试夹具的清理，不是业务路径）。删 Trip 会
级联删触发/建议/审计；``approvals`` 是通用的、不挂 FK，所以显式先删。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.geo import Place
from app.models.proactive import Approval, ProactiveSuggestion, ProactiveTrigger, SuggestionAudit
from app.models.trip import Trip
from app.services import proactive_service
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"  # 安纳瑞能源
# 用 ADMIN 而不是默认 OWNER：冒烟测试会以 OWNER 身份建很多出行，用同一个 owner 会让
# "每日推送上限"与历史建议纠缠不清。ADMIN 名下没有主动式建议。
USER_PROACTIVE = "55555555-5555-5555-5555-555555555555"  # mia@anari.example（ADMIN）

SUGGESTION_VISA = "VISA_REQUIREMENT_REMINDER"
SUGGESTION_COORDS = "PLACE_COORDINATES_MISSING"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers() -> dict[str, str]:
    return {"X-Org-Id": ORG_A, "X-User-Id": USER_PROACTIVE}


def create_trip(client: TestClient, *, hours_ahead: int = 48, countries: list[str] | None = None) -> str:
    now = datetime.now(UTC)
    payload = {
        "name": f"AT-P 主动式验收·{uuid.uuid4().hex[:8]}",
        "window_start": (now + timedelta(hours=hours_ahead)).isoformat(),
        "window_end": (now + timedelta(hours=hours_ahead + 48)).isoformat(),
        "countries": countries if countries is not None else ["DE"],
        "currency": "EUR",
    }
    resp = client.post("/v1/trips", headers=headers(), json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def drop_trip(trip_id: str) -> None:
    """删除本用例造出的出行及其主动式数据（测试夹具清理）。"""
    with org_session(UUID(ORG_A)) as session:
        suggestion_ids = list(
            session.scalars(
                select(ProactiveSuggestion.id).where(ProactiveSuggestion.trip_id == UUID(trip_id))
            )
        )
        if suggestion_ids:
            session.execute(
                delete(Approval).where(
                    Approval.subject_type == proactive_service.APPROVAL_SUBJECT_SUGGESTION,
                    Approval.subject_id.in_(suggestion_ids),
                )
            )
        session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))


def scan(trip_id: str, *, now: datetime | None = None) -> proactive_service.ScanReport:
    with org_session(UUID(ORG_A)) as session:
        return proactive_service.scan_trip(session, UUID(ORG_A), UUID(trip_id), now=now)


def feed(client: TestClient, trip_id: str) -> dict:
    resp = client.get(
        "/v1/proactive/suggestions", headers=headers(), params={"trip_id": trip_id}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def suggestion_row(suggestion_id: str) -> ProactiveSuggestion:
    with org_session(UUID(ORG_A)) as session:
        return session.scalars(
            select(ProactiveSuggestion).where(ProactiveSuggestion.id == UUID(suggestion_id))
        ).one()


def audits_for(suggestion_id: str) -> list[SuggestionAudit]:
    with org_session(UUID(ORG_A)) as session:
        return list(
            session.scalars(
                select(SuggestionAudit)
                .where(SuggestionAudit.suggestion_id == UUID(suggestion_id))
                .order_by(SuggestionAudit.created_at, SuggestionAudit.id)
            )
        )


# ------------------------------------------------------------------ AT-P01
def test_at_p01_scheduled_visa_trigger_proposes_with_evidence(client: TestClient) -> None:
    trip_id = create_trip(client, hours_ahead=48, countries=["DE"])
    try:
        report = scan(trip_id)
        assert report.errors == []
        assert report.trips_scanned == 1
        assert report.suggestions == 1
        assert report.triggers == 1

        body = feed(client, trip_id)
        visa = [item for item in body["items"] if item["suggestion_type"] == SUGGESTION_VISA]
        assert len(visa) == 1
        item = visa[0]
        assert item["status"] == "PENDING"  # 对外等价于设计文档的 PROPOSED
        assert item["requires_approval"] is True
        assert item["severity"] in {"HIGH", "MEDIUM"}
        assert item["confidence"] >= 0.6
        assert item["rationale"].strip()
        assert len(item["evidence"]) >= 3
        assert {e["kind"] for e in item["evidence"]} >= {
            "TRIP_WINDOW",
            "VISA_RULE_CATALOG",
            "VISA_RULE",
        }
        assert item["proposed_action"]["kind"] == "PREPARE_CHECKLIST"
        assert body["daily_cap"]["cap"] >= 1
    finally:
        drop_trip(trip_id)


# ------------------------------------------------------------------ AT-P02
def test_at_p02_same_dedupe_key_never_proposes_twice(client: TestClient) -> None:
    trip_id = create_trip(client, hours_ahead=48)
    try:
        first = scan(trip_id)
        second = scan(trip_id)
        assert first.suggestions == 1
        # 第二次扫描：同一触发键已存在，且建议已存在 ⇒ 不重复登记、不重复提案。
        assert second.suggestions == 0
        assert second.deduped >= 1

        with org_session(UUID(ORG_A)) as session:
            suggestion_count = session.scalar(
                select(func.count())
                .select_from(ProactiveSuggestion)
                .where(ProactiveSuggestion.trip_id == UUID(trip_id))
            )
            trigger_count = session.scalar(
                select(func.count())
                .select_from(ProactiveTrigger)
                .where(ProactiveTrigger.trip_id == UUID(trip_id))
            )
        assert suggestion_count == 1
        assert trigger_count == 1
    finally:
        drop_trip(trip_id)


def test_at_p02_daily_cap_stores_but_does_not_push(client: TestClient) -> None:
    cap = 3  # .env 的 GBT_DAILY_SUGGESTION_CAP
    trips = [create_trip(client, hours_ahead=48 + index * 2) for index in range(cap + 1)]
    try:
        reports = [scan(trip_id) for trip_id in trips]
        assert sum(report.pushed for report in reports) == cap
        assert sum(report.withheld for report in reports) == 1

        with org_session(UUID(ORG_A)) as session:
            rows = list(
                session.scalars(
                    select(ProactiveSuggestion)
                    .where(ProactiveSuggestion.trip_id.in_([UUID(t) for t in trips]))
                    .order_by(ProactiveSuggestion.created_at)
                )
            )
        assert len(rows) == cap + 1
        withheld = [row for row in rows if row.push_status == "WITHHELD"]
        assert len(withheld) == 1
        assert withheld[0].push_reason == "DAILY_CAP"
        assert withheld[0].status == "PENDING"
        assert withheld[0].notified_at is None

        # 用户可见列表里没有那条被保留的建议。
        visible_ids: set[str] = set()
        for trip_id in trips:
            visible_ids.update(item["id"] for item in feed(client, trip_id)["items"])
        assert str(withheld[0].id) not in visible_ids
    finally:
        for trip_id in trips:
            drop_trip(trip_id)


# ------------------------------------------------------------------ AT-P03
def test_at_p03_approval_executes_and_writes_audit_chain(client: TestClient) -> None:
    trip_id = create_trip(client, hours_ahead=48)
    try:
        scan(trip_id)
        item = feed(client, trip_id)["items"][0]
        suggestion_id = item["id"]

        # 未审批：没有审批记录、没有执行结果。
        row = suggestion_row(suggestion_id)
        assert row.status == "PENDING"
        assert row.execution_result is None
        with org_session(UUID(ORG_A)) as session:
            approvals = session.scalar(
                select(func.count())
                .select_from(Approval)
                .where(Approval.subject_id == UUID(suggestion_id))
            )
        assert approvals == 0

        key = f"at-p03-{uuid.uuid4().hex}"
        first = client.post(
            f"/v1/proactive/suggestions/{suggestion_id}:approve",
            headers={**headers(), "Idempotency-Key": key},
        )
        assert first.status_code == 200, first.text
        assert first.headers.get("Idempotent-Replay") is None
        body = first.json()
        assert body["status"] == "EXECUTED"
        assert body["approval"]["approved_by"] == USER_PROACTIVE
        assert body["execution"]["status"] == "PREPARED"
        assert "未" in body["execution"]["detail"]  # 明确"未对外提交/发送"

        # 同键重放：字节一致、不产生第二条审批。
        replay = client.post(
            f"/v1/proactive/suggestions/{suggestion_id}:approve",
            headers={**headers(), "Idempotency-Key": key},
        )
        assert replay.status_code == 200
        assert replay.headers.get("Idempotent-Replay") == "true"
        assert replay.content == first.content

        row = suggestion_row(suggestion_id)
        assert row.status == "EXECUTED"
        assert row.executed_at is not None
        assert row.execution_result["status"] == "PREPARED"
        assert row.execution_result["catalog_version"]

        with org_session(UUID(ORG_A)) as session:
            approvals = session.scalar(
                select(func.count())
                .select_from(Approval)
                .where(Approval.subject_id == UUID(suggestion_id))
            )
        assert approvals == 1

        actions = [audit.action for audit in audits_for(suggestion_id)]
        assert actions == [
            "DETECTED",
            "EVALUATED",
            "PROPOSED",
            "APPROVED",
            "EXECUTED",
            "AUDITED",
        ]
    finally:
        drop_trip(trip_id)


# ------------------------------------------------------------------ AT-P04
def test_at_p04_dismiss_marks_false_positive_and_lowers_confidence(client: TestClient) -> None:
    first_trip = create_trip(client, hours_ahead=48)
    second_trip = create_trip(client, hours_ahead=50)
    try:
        scan(first_trip)
        item = feed(client, first_trip)["items"][0]
        original_confidence = item["confidence"]

        resp = client.post(
            f"/v1/proactive/suggestions/{item['id']}:dismiss",
            headers=headers(),
            json={"reason": "误报：签证已持有"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "DISMISSED"
        assert body["dismissed_reason"] == "误报：签证已持有"
        assert body["memory_feedback"]["type"] == SUGGESTION_VISA
        assert body["memory_feedback"]["weight_delta"] < 0

        actions = [audit.action for audit in audits_for(item["id"])]
        assert actions[-1] == "DISMISSED"

        # 同类建议（另一趟出行，避开冷却窗口）的置信度应被负反馈下调。
        scan(second_trip)
        next_item = feed(client, second_trip)["items"][0]
        assert next_item["confidence"] < original_confidence
        row = suggestion_row(next_item["id"])
        assert row.feedback_penalty > 0
    finally:
        drop_trip(first_trip)
        drop_trip(second_trip)


# ------------------------------------------------- STATE 触发（缺坐标）
def test_state_trigger_detects_missing_coordinates(client: TestClient) -> None:
    now = datetime.now(UTC)
    trip = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"AT-P STATE·{uuid.uuid4().hex[:8]}",
            "window_start": (now + timedelta(days=30)).isoformat(),
            "window_end": (now + timedelta(days=33)).isoformat(),
            "countries": ["DE"],
        },
    )
    assert trip.status_code == 201, trip.text
    trip_id = trip.json()["id"]

    place = client.post(
        "/v1/places",
        headers=headers(),
        json={"name": "无坐标客户总部"},
    )
    assert place.status_code == 201, place.text
    place_id = place.json()["id"]

    visit = client.post(
        f"/v1/trips/{trip_id}/visits",
        headers=headers(),
        json={"place_id": place_id, "title": "客户拜访", "service_minutes": 90},
    )
    assert visit.status_code == 201, visit.text

    try:
        report = scan(trip_id)
        assert report.errors == []
        items = feed(client, trip_id)["items"]
        coords = [item for item in items if item["suggestion_type"] == SUGGESTION_COORDS]
        assert len(coords) == 1
        assert "坐标" in coords[0]["title"]
        assert coords[0]["status"] == "PENDING"

        with org_session(UUID(ORG_A)) as session:
            trigger = session.scalars(
                select(ProactiveTrigger).where(ProactiveTrigger.trip_id == UUID(trip_id))
            ).one()
        assert trigger.type == "STATE"
        assert trigger.rule_id == "missing_coordinates"
    finally:
        drop_trip(trip_id)
        # 地点挂在组织上、不随 Trip 级联；没有删除接口，测试夹具直接删掉，避免每次跑测试
        # 在开发库里留下一个无拜访的无坐标地点。
        with org_session(UUID(ORG_A)) as session:
            session.execute(delete(Place).where(Place.id == UUID(place_id)))
