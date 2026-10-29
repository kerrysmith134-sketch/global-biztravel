"""会话内执行：适配既有审批服务，人审门槛不降级（PRD 6.2/6.3）。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.proactive import ProactiveSuggestion
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
    return {
        "X-Org-Id": ORG_A,
        "X-User-Id": USER_A,
        "Idempotency-Key": str(uuid.uuid4()),
    }


def test_conversation_action_runs_via_business_service(client: TestClient) -> None:
    now = datetime.now(UTC)
    trip = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"会话动作·{uuid.uuid4().hex[:8]}",
            "window_start": (now + timedelta(hours=36)).isoformat(),
            "window_end": (now + timedelta(days=4)).isoformat(),
            "countries": ["DE"],
        },
    )
    assert trip.status_code == 201, trip.text
    trip_id = trip.json()["id"]
    suggestion_id: str | None = None
    conversation_id: str | None = None

    try:
        from app.services import proactive_service

        with org_session(UUID(ORG_A)) as session:
            proactive_service.scan_trip(session, UUID(ORG_A), UUID(trip_id))
            suggestion = session.scalars(
                select(ProactiveSuggestion).where(
                    ProactiveSuggestion.trip_id == UUID(trip_id)
                )
            ).one()
            suggestion_id = str(suggestion.id)

        conv = client.post(
            "/v1/assistant/conversations", headers=headers(), json={"trip_id": trip_id}
        )
        assert conv.status_code == 201, conv.text
        conversation_id = conv.json()["id"]

        # 无审批策略时 OWNER 批准成功；动作结果以消息形式回到对话。
        action = client.post(
            f"/v1/assistant/conversations/{conversation_id}/actions",
            headers=headers(),
            json={"action": "approve_suggestion", "subject_id": suggestion_id},
        )
        assert action.status_code == 200, action.text
        row = action.json()
        assert row["role"] == "ASSISTANT"
        assert row["cards"][0]["type"] == "ACTION_RESULT"

        detail = client.get(
            f"/v1/assistant/conversations/{conversation_id}", headers=headers()
        )
        assert detail.status_code == 200
        assert any(
            card.get("type") == "ACTION_RESULT"
            for message in detail.json()["messages"]
            for card in message.get("cards", [])
        )

        # 不支持的动作被拒（执行面保持白名单）。
        bad = client.post(
            f"/v1/assistant/conversations/{conversation_id}/actions",
            headers=headers(),
            json={"action": "delete_trip", "subject_id": trip_id},
        )
        assert bad.status_code in (400, 422)
    finally:
        with org_session(UUID(ORG_A)) as session:
            if suggestion_id:
                session.execute(
                    delete(ProactiveSuggestion).where(
                        ProactiveSuggestion.id == UUID(suggestion_id)
                    )
                )
            session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))
