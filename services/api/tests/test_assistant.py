"""AI 助手对话（PRD 页面 05）验收：分离、事实卡片、执行边界、删除不影响任务。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.assistant import AssistantConversation, AssistantMessage
from app.models.trip import Trip
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "33333333-3333-3333-3333-333333333333"
USER_A = "22222222-2222-2222-2222-222222222222"
USER_B = "44444444-4444-4444-4444-444444444444"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers(org: str = ORG_A, user: str = USER_A) -> dict[str, str]:
    return {
        "X-Org-Id": org,
        "X-User-Id": user,
        "Idempotency-Key": str(uuid.uuid4()),
    }


def test_assistant_conversation_uses_facts_and_separates_state(client: TestClient) -> None:
    now = datetime.now(UTC)
    trip = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"助手对话·{uuid.uuid4().hex[:8]}",
            "window_start": (now + timedelta(days=5)).isoformat(),
            "window_end": (now + timedelta(days=8)).isoformat(),
            "countries": ["DE"],
        },
    )
    assert trip.status_code == 201, trip.text
    trip_id = trip.json()["id"]

    try:
        created = client.post(
            "/v1/assistant/conversations",
            headers=headers(),
            json={"trip_id": trip_id, "title": ""},
        )
        assert created.status_code == 201, created.text
        conversation_id = created.json()["id"]

        reply = client.post(
            f"/v1/assistant/conversations/{conversation_id}/messages",
            headers=headers(),
            json={"content": "这趟行程现在怎么样？需要我做什么？"},
        )
        assert reply.status_code == 200, reply.text
        bot = reply.json()
        assert bot["role"] == "ASSISTANT"
        assert bot["content"]
        assert bot["llm_invocation_id"]  # 可回溯 llm_invocations
        card_types = {card["type"] for card in bot["cards"]}
        assert {"TRIP_STATUS", "ACTION_CENTER"} <= card_types
        trip_card = next(card for card in bot["cards"] if card["type"] == "TRIP_STATUS")
        assert trip_id in trip_card["detail"] or trip_card["entity_id"] == trip_id

        detail = client.get(
            f"/v1/assistant/conversations/{conversation_id}", headers=headers()
        )
        assert detail.status_code == 200
        roles = [row["role"] for row in detail.json()["messages"]]
        assert roles == ["USER", "ASSISTANT"]

        # 清聊天只删对话两张表；出行与任务不受影响。
        deleted = client.delete(
            f"/v1/assistant/conversations/{conversation_id}", headers=headers()
        )
        assert deleted.status_code == 204
        with org_session(UUID(ORG_A)) as session:
            assert (
                session.get(AssistantConversation, UUID(conversation_id)) is None
            )
            assert session.get(Trip, UUID(trip_id)) is not None
            leftovers = session.scalars(
                select(AssistantMessage).where(
                    AssistantMessage.conversation_id == UUID(conversation_id)
                )
            ).all()
            assert list(leftovers) == []

        # 跨租户不可见。
        other = client.get(
            f"/v1/assistant/conversations/{conversation_id}",
            headers=headers(ORG_B, USER_B),
        )
        assert other.status_code == 404
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))


def test_assistant_rejects_empty_and_oversized(client: TestClient) -> None:
    created = client.post(
        "/v1/assistant/conversations", headers=headers(), json={}
    )
    assert created.status_code == 201
    conversation_id = created.json()["id"]
    try:
        empty = client.post(
            f"/v1/assistant/conversations/{conversation_id}/messages",
            headers=headers(),
            json={"content": "   "},
        )
        assert empty.status_code in (400, 422)
        huge = client.post(
            f"/v1/assistant/conversations/{conversation_id}/messages",
            headers=headers(),
            json={"content": "x" * 4001},
        )
        assert huge.status_code in (400, 422)
    finally:
        assert (
            client.delete(
                f"/v1/assistant/conversations/{conversation_id}", headers=headers()
            ).status_code
            == 204
        )
