"""EXTERNAL 触发源验收：外部信号接入 → 主动建议 → 人审执行（真实 PostgreSQL）。

对应设计文档 §8.1（EXTERNAL 触发源）与第 14 章（外部集成边界）。信号由测试以真实结构注入
（模拟供应商回调），不使用伪造的天气/汇率数据。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.integrations import ExternalSignal
from app.models.proactive import ProactiveSuggestion, ProactiveTrigger
from app.models.trip import Trip
from app.services import proactive_service
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "33333333-3333-3333-3333-333333333333"
USER_A2 = "55555555-5555-5555-5555-555555555555"  # ADMIN
USER_B = "44444444-4444-4444-4444-444444444444"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers(org: str = ORG_A, user: str = USER_A2) -> dict[str, str]:
    return {"X-Org-Id": org, "X-User-Id": user}


def create_trip(client: TestClient) -> str:
    now = datetime.now(UTC)
    resp = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"EXTERNAL 验收·{uuid.uuid4().hex[:8]}",
            "window_start": (now + timedelta(days=20)).isoformat(),
            "window_end": (now + timedelta(days=23)).isoformat(),
            "countries": ["DE"],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def ingest(client: TestClient, dedupe_key: str, *, summary: str) -> dict:
    now = datetime.now(UTC)
    resp = client.post(
        "/v1/integrations/signals",
        headers=headers(),
        json={
            "provider": "official-visa-feed",
            "signal_type": "VISA_POLICY_CHANGE",
            "subject_type": "COUNTRY",
            "subject_ref": "DE",
            "payload": {
                "title": "德国商务签证受理周期变化",
                "summary": summary,
                "severity": "HIGH",
            },
            "confidence": 0.9,
            "observed_at": (now - timedelta(minutes=1)).isoformat(),
            "valid_until": (now + timedelta(days=30)).isoformat(),
            "dedupe_key": dedupe_key,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def cleanup(trip_id: str, dedupe_prefix: str) -> None:
    with org_session(UUID(ORG_A)) as session:
        session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))
        session.execute(
            delete(ExternalSignal).where(
                ExternalSignal.organization_id == UUID(ORG_A),
                ExternalSignal.dedupe_key.like(f"{dedupe_prefix}%"),
            )
        )


def test_external_signal_becomes_reviewable_suggestion(client: TestClient) -> None:
    trip_id = create_trip(client)
    dedupe = f"ext-{uuid.uuid4().hex[:8]}"
    try:
        signal = ingest(client, dedupe, summary="受理周期由 10 个工作日延长至 15 个工作日")
        assert signal["dedupe_key"] == dedupe

        with org_session(UUID(ORG_A)) as session:
            report = proactive_service.scan_trip(session, UUID(ORG_A), UUID(trip_id))
        assert report.errors == []
        assert report.suggestions == 1

        feed = client.get(
            "/v1/proactive/suggestions", headers=headers(), params={"trip_id": trip_id}
        )
        assert feed.status_code == 200, feed.text
        items = [item for item in feed.json()["items"] if item["suggestion_type"] == "VISA_POLICY_UPDATE"]
        assert len(items) == 1
        item = items[0]
        assert item["status"] == "PENDING"
        assert item["requires_approval"] is True
        assert item["evidence"][0]["kind"] == "EXTERNAL_SIGNAL"
        assert "官方渠道复核" in item["rationale"]

        with org_session(UUID(ORG_A)) as session:
            trigger = session.scalars(
                select(ProactiveTrigger).where(ProactiveTrigger.trip_id == UUID(trip_id))
            ).one()
        assert trigger.type == "EXTERNAL"
        assert trigger.rule_id == "external_signal"

        # 人审后只准备摘要，不对外改签/发送。
        approved = client.post(
            f"/v1/proactive/suggestions/{item['id']}:approve", headers=headers()
        )
        assert approved.status_code == 200, approved.text
        body = approved.json()
        assert body["status"] == "EXECUTED"
        assert body["execution"]["status"] == "PREPARED"
        assert "未对外发送" in body["execution"]["detail"]

        # 同一 provider + dedupe_key 重推：更新原信号，不新增行。
        ingest(client, dedupe, summary="受理周期由 10 周延长至 15 周（更正）")
        with org_session(UUID(ORG_A)) as session:
            count = session.scalar(
                select(func.count())
                .select_from(ExternalSignal)
                .where(
                    ExternalSignal.organization_id == UUID(ORG_A),
                    ExternalSignal.dedupe_key == dedupe,
                )
            )
        assert count == 1

        # 内容变化会形成新的建议，但同类型在 24h 冷却内应被抑制（入库不打扰）。
        with org_session(UUID(ORG_A)) as session:
            again = proactive_service.scan_trip(session, UUID(ORG_A), UUID(trip_id))
        assert again.suppressed == 1
        with org_session(UUID(ORG_A)) as session:
            rows = list(
                session.scalars(
                    select(ProactiveSuggestion).where(
                        ProactiveSuggestion.trip_id == UUID(trip_id),
                        ProactiveSuggestion.suggestion_type == "VISA_POLICY_UPDATE",
                    )
                )
            )
        assert len(rows) == 2
        assert sorted(row.status for row in rows) == ["EXECUTED", "SUPPRESSED"]
    finally:
        cleanup(trip_id, dedupe)


def test_external_signals_are_tenant_isolated(client: TestClient) -> None:
    trip_id = create_trip(client)
    dedupe = f"ext-{uuid.uuid4().hex[:8]}"
    try:
        ingest(client, dedupe, summary="仅供组织 A")
        listing = client.get(
            "/v1/integrations/signals", headers=headers(ORG_B, USER_B)
        )
        assert listing.status_code == 200
        assert listing.json() == []
        with org_session(UUID(ORG_B)) as session:
            report = proactive_service.scan_trip(session, UUID(ORG_B), UUID(trip_id))
        assert report.suggestions == 0
    finally:
        cleanup(trip_id, dedupe)
