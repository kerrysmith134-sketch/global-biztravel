"""用户通知偏好：静音等级不产生通知，但建议本身保留（PRD S8-08）。"""

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
from app.services import notification_policy_service, outbox_service, proactive_service
from app.workers.consumers import CONSUMER_HANDLERS
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
ADMIN_A = "55555555-5555-5555-5555-555555555555"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers() -> dict[str, str]:
    return {"X-Org-Id": ORG_A, "X-User-Id": ADMIN_A}


def test_muted_priority_suppresses_notification_not_suggestion(client: TestClient) -> None:
    now = datetime.now(UTC)
    trip = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"偏好测试·{uuid.uuid4().hex[:8]}",
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
            priority = notification_policy_service.classify(
                suggestion_type=suggestion.suggestion_type,
                severity=suggestion.severity,
            )

        # 静音该等级。
        put = client.put(
            "/v1/notifications/preferences", headers=headers(),
            json={"muted_priorities": [priority]},
        )
        assert put.status_code == 200, put.text
        assert put.json()["muted_priorities"] == [priority]

        with org_session(UUID(ORG_A)) as session:
            handlers = dict(CONSUMER_HANDLERS[outbox_service.CONSUMER_NOTIFICATION_DISPATCH])
            outbox_service.publish(
                session,
                consumer=outbox_service.CONSUMER_NOTIFICATION_DISPATCH,
                handlers=handlers,
                batch_size=50,
            )
            # 静音：不产生通知；但建议本身仍在。
            notifications = list(
                session.scalars(
                    select(Notification).where(Notification.suggestion_id == suggestion.id)
                )
            )
            assert notifications == []
            still = session.get(ProactiveSuggestion, suggestion.id)
            assert still is not None and still.status == "PENDING"

        # 取消静音后可正常返回偏好；非法等级被拒。
        clear = client.put(
            "/v1/notifications/preferences", headers=headers(),
            json={"muted_priorities": []},
        )
        assert clear.status_code == 200 and clear.json()["muted_priorities"] == []
        bad = client.put(
            "/v1/notifications/preferences", headers=headers(),
            json={"muted_priorities": ["PX"]},
        )
        assert bad.status_code in (400, 422)
        get = client.get("/v1/notifications/preferences", headers=headers())
        assert get.status_code == 200 and get.json()["muted_priorities"] == []
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(
                delete(OutboxEvent).where(OutboxEvent.aggregate_id == UUID(trip_id))
            )
            session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))
            from app.models.notification_preferences import NotificationPreference

            session.execute(
                delete(NotificationPreference)
                .where(
                    NotificationPreference.organization_id == UUID(ORG_A),
                    NotificationPreference.user_id == UUID(ADMIN_A),
                )
            )
