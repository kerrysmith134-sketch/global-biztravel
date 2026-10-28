"""推送通道验收：建议 → Outbox → 通知 → 投递 → 已读（真实 PostgreSQL）。

对应设计文档 §8.6：主动动作通过 Outbox 投递、幂等、可恢复。验证：

- 已推送建议产生一条通知；抑制/保留的建议不产生。
- Outbox 重投不产生第二条通知（唯一约束）。
- Worker 投递后状态为 SENT；用户能看到并可标记已读。
- 跨租户看不到通知。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.notifications import Notification
from app.models.proactive import ProactiveSuggestion
from app.models.reliability import OutboxEvent
from app.models.trip import Trip
from app.services import notification_service, outbox_service, proactive_service
from app.workers.consumers import CONSUMER_HANDLERS
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "33333333-3333-3333-3333-333333333333"
USER_A2 = "55555555-5555-5555-5555-555555555555"
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
            "name": f"通知验收·{uuid.uuid4().hex[:8]}",
            "window_start": (now + timedelta(hours=48)).isoformat(),
            "window_end": (now + timedelta(days=4)).isoformat(),
            "countries": ["DE"],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def cleanup(trip_id: str) -> None:
    with org_session(UUID(ORG_A)) as session:
        session.execute(
            delete(OutboxEvent).where(OutboxEvent.aggregate_id == UUID(trip_id))
        )
        session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))


def test_pushed_suggestion_reaches_notification_inbox(client: TestClient) -> None:
    trip_id = create_trip(client)
    try:
        with org_session(UUID(ORG_A)) as session:
            report = proactive_service.scan_trip(session, UUID(ORG_A), UUID(trip_id))
            assert report.pushed == 1
            suggestion = session.scalars(
                select(ProactiveSuggestion).where(ProactiveSuggestion.trip_id == UUID(trip_id))
            ).one()
            assert suggestion.push_status == "PUSHED"

        # 消费者把"建议已提出"翻译成通知；重投一次不应产生第二条。
        consumer = outbox_service.CONSUMER_NOTIFICATION_DISPATCH
        handlers = dict(CONSUMER_HANDLERS[consumer])
        for _ in range(2):
            with org_session(UUID(ORG_A)) as session:
                outbox_service.publish(
                    session, consumer=consumer, handlers=handlers, batch_size=50
                )
        with org_session(UUID(ORG_A)) as session:
            rows = list(
                session.scalars(
                    select(Notification).where(Notification.suggestion_id == suggestion.id)
                )
            )
        assert len(rows) == 1
        assert rows[0].status == "PENDING"
        assert rows[0].user_id == UUID(USER_A2)

        with org_session(UUID(ORG_A)) as session:
            dispatch = notification_service.dispatch_pending(session, UUID(ORG_A))
        assert dispatch.claimed >= 1

        with org_session(UUID(ORG_A)) as session:
            row = session.scalars(
                select(Notification).where(Notification.suggestion_id == suggestion.id)
            ).one()
        assert row.status == "SENT"
        assert row.sent_at is not None
        assert row.attempts == 1

        feed = client.get("/v1/notifications", headers=headers())
        assert feed.status_code == 200, feed.text
        body = feed.json()
        ids = {item["id"] for item in body["items"]}
        assert str(row.id) in ids
        assert body["unread"] >= 1

        read = client.post(f"/v1/notifications/{row.id}:read", headers=headers())
        assert read.status_code == 200, read.text
        assert read.json()["read_at"] is not None

        again = client.post(f"/v1/notifications/{row.id}:read", headers=headers())
        assert again.status_code == 200

        # 跨租户看不到组织 A 的通知。
        other = client.get("/v1/notifications", headers=headers(ORG_B, USER_B))
        assert other.status_code == 200
        assert all(item["id"] != str(row.id) for item in other.json()["items"])
    finally:
        cleanup(trip_id)


def test_withheld_suggestion_does_not_create_notification() -> None:
    suggestion = ProactiveSuggestion(
        organization_id=UUID(ORG_A),
        trip_id=UUID("11111111-1111-1111-1111-111111111111"),
        suggestion_type="VISA_REQUIREMENT_REMINDER",
        status="PENDING",
        title="保留不推送",
        rationale="低置信度",
        evidence=[],
        proposed_action={},
        confidence=0.2,
        requires_approval=True,
        severity="LOW",
        dedupe_key=f"test-{uuid.uuid4().hex}",
        push_status="WITHHELD",
        push_reason="LOW_CONFIDENCE",
        feedback_penalty=0,
    )
    with org_session(UUID(ORG_A)) as session:
        assert (
            notification_service.enqueue_for_suggestion(
                session, suggestion, user_id=UUID(USER_A2)
            )
            is None
        )
        count = session.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.suggestion_id == suggestion.id)
        )
    assert count == 0
