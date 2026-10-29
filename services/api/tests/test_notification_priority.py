"""通知等级策略与分级未读（PRD S8-08）验收。"""

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
from app.services import notification_policy_service, notification_service, outbox_service, proactive_service
from app.workers.consumers import CONSUMER_HANDLERS
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "33333333-3333-3333-3333-333333333333"
ADMIN_A = "55555555-5555-5555-5555-555555555555"
USER_B = "44444444-4444-4444-4444-444444444444"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers(user: str = ADMIN_A, org: str = ORG_A) -> dict[str, str]:
    return {"X-Org-Id": org, "X-User-Id": user}


def test_classify_rules_are_deterministic() -> None:
    classify = notification_policy_service.classify
    # P0：关键交通中断 / 重要预约可能失败
    assert classify(suggestion_type="FLIGHT_STATUS_ALERT", severity="HIGH") == "P0"
    assert classify(suggestion_type="PLAN_UNPLACED_VISITS", severity="HIGH") == "P0"
    # P1：入境材料类与其余 HIGH 风险
    assert classify(suggestion_type="VISA_REQUIREMENT_REMINDER", severity="HIGH") == "P1"
    assert classify(suggestion_type="VISA_POLICY_UPDATE", severity="HIGH") == "P1"
    assert classify(suggestion_type="PLACE_COORDINATES_MISSING", severity="HIGH") == "P1"
    # P2/P3：一般与信息性
    assert classify(suggestion_type="WEATHER_DISRUPTION_ALERT", severity="MEDIUM") == "P2"
    assert classify(suggestion_type="FX_RATE_ALERT", severity="LOW") == "P3"
    # 未知风险等级退回中档，绝不静默按最低打扰
    assert classify(suggestion_type="FX_RATE_ALERT", severity="??") == "P2"
    assert notification_policy_service.label("P0") == "紧急"


def test_notification_carries_priority_and_feed_counts(client: TestClient) -> None:
    now = datetime.now(UTC)
    trip = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"通知分级·{uuid.uuid4().hex[:8]}",
            "window_start": (now + timedelta(hours=36)).isoformat(),
            "window_end": (now + timedelta(days=4)).isoformat(),
            "countries": ["DE"],
        },
    )
    assert trip.status_code == 201, trip.text
    trip_id = trip.json()["id"]

    try:
        with org_session(UUID(ORG_A)) as session:
            report = proactive_service.scan_trip(session, UUID(ORG_A), UUID(trip_id))
            assert report.pushed >= 1
            suggestion = session.scalars(
                select(ProactiveSuggestion).where(
                    ProactiveSuggestion.trip_id == UUID(trip_id)
                )
            ).one()
            assert suggestion.push_status == "PUSHED"
            expected = notification_policy_service.classify(
                suggestion_type=suggestion.suggestion_type,
                severity=suggestion.severity,
            )

            consumer = outbox_service.CONSUMER_NOTIFICATION_DISPATCH
            handlers = dict(CONSUMER_HANDLERS[consumer])
            outbox_service.publish(session, consumer=consumer, handlers=handlers, batch_size=50)
            outbox_service.publish(session, consumer=consumer, handlers=handlers, batch_size=50)

            rows = list(
                session.scalars(
                    select(Notification).where(
                        Notification.organization_id == UUID(ORG_A),
                        Notification.suggestion_id == suggestion.id,
                    )
                )
            )
            assert len(rows) == 1
            assert rows[0].priority == expected
            assert rows[0].issue_key == suggestion.dedupe_key

            # 投递后成为"已送达未读"，分级未读才计数。
            dispatch = notification_service.dispatch_pending(session, ORG_A)
            assert dispatch.sent >= 1

        feed = client.get("/v1/notifications", headers=headers())
        assert feed.status_code == 200, feed.text
        body = feed.json()
        mine = [item for item in body["items"] if item["suggestion_id"] == str(suggestion.id)]
        assert mine and mine[0]["priority"] == expected
        assert body["unread"] >= 1
        assert sum(body["unread_by_priority"].values()) == body["unread"]
        assert body["unread_by_priority"].get(expected, 0) >= 1

        filtered = client.get(
            "/v1/notifications", headers=headers(), params={"priority": "P0"}
        )
        assert filtered.status_code == 200
        assert all(item["priority"] == "P0" for item in filtered.json()["items"])

        bad = client.get(
            "/v1/notifications", headers=headers(), params={"priority": "PX"}
        )
        assert bad.status_code in (400, 422)

        stolen = client.get("/v1/notifications", headers=headers(USER_B, ORG_B))
        assert stolen.status_code == 200
        assert all(
            str(item["id"]) != str(mine[0]["id"]) for item in stolen.json()["items"]
        )
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(
                delete(OutboxEvent).where(OutboxEvent.aggregate_id == UUID(trip_id))
            )
            session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))
