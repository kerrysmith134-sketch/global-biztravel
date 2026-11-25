"""Assistant read-only Agent tool execution and idempotency acceptance tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.assistant import AssistantConversation
from app.models.trip import Trip
from app.models.workflow import AgentRun
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
USER_A = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers(idempotency_key: str | None = None) -> dict[str, str]:
    return {
        "X-Org-Id": ORG_A,
        "X-User-Id": USER_A,
        "Idempotency-Key": idempotency_key or str(uuid.uuid4()),
    }


def test_trip_get_plan_tool_is_executable_and_idempotent(client: TestClient) -> None:
    now = datetime.now(UTC)
    trip = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"Agent 工具验收·{uuid.uuid4().hex[:8]}",
            "window_start": (now + timedelta(days=5)).isoformat(),
            "window_end": (now + timedelta(days=8)).isoformat(),
            "countries": ["DE"],
        },
    )
    assert trip.status_code == 201, trip.text
    trip_id = trip.json()["id"]
    conversation_id: str | None = None

    try:
        conversation = client.post(
            "/v1/assistant/conversations",
            headers=headers(),
            json={"trip_id": trip_id, "title": "计划读取"},
        )
        assert conversation.status_code == 201, conversation.text
        conversation_id = conversation.json()["id"]

        proposed = client.post(
            f"/v1/assistant/conversations/{conversation_id}/messages",
            headers=headers(),
            json={"content": "请读取这趟出行的计划"},
        )
        assert proposed.status_code == 200, proposed.text
        tool_cards = [
            card for card in proposed.json()["cards"]
            if card.get("type") == "TOOL_PROPOSAL"
        ]
        assert len(tool_cards) == 1
        assert tool_cards[0]["entity_id"] == "trip.get_plan"
        assert tool_cards[0]["link"] == "tool"

        key = str(uuid.uuid4())
        request_headers = headers(key)
        payload = {
            "tool_name": "trip.get_plan",
            "arguments": {"trip_id": trip_id},
        }
        endpoint = f"/v1/assistant/conversations/{conversation_id}/tools:execute"

        first = client.post(endpoint, headers=request_headers, json=payload)
        assert first.status_code == 200, first.text
        result = first.json()
        assert result["role"] == "ASSISTANT"
        assert result["cards"][0]["type"] == "TOOL_RESULT"
        assert result["cards"][0]["entity_id"] == trip_id
        assert "没有生效计划" in result["content"]

        replay = client.post(endpoint, headers=request_headers, json=payload)
        assert replay.status_code == 200, replay.text
        assert replay.headers.get("Idempotent-Replay") == "true"
        assert replay.json() == result

        wrong_trip = client.post(
            endpoint,
            headers=headers(),
            json={
                "tool_name": "trip.get_plan",
                "arguments": {"trip_id": str(uuid.uuid4())},
            },
        )
        assert wrong_trip.status_code == 422, wrong_trip.text

        approval_gated = client.post(
            endpoint,
            headers=headers(),
            json={
                "tool_name": "visa.prepare_checklist",
                "arguments": {"trip_id": trip_id, "countries": ["DE"]},
            },
        )
        assert approval_gated.status_code == 403, approval_gated.text

        unknown = client.post(
            endpoint,
            headers=headers(),
            json={"tool_name": "trip.delete", "arguments": {"trip_id": trip_id}},
        )
        assert unknown.status_code == 403, unknown.text

        malformed = client.post(
            endpoint,
            headers=headers(),
            json={
                "tool_name": "trip.get_plan",
                "arguments": {"trip_id": trip_id, "write_back": True},
            },
        )
        assert malformed.status_code == 422, malformed.text

        detail = client.get(
            f"/v1/assistant/conversations/{conversation_id}",
            headers=headers(),
        )
        assert detail.status_code == 200, detail.text
        result_cards = [
            card
            for message in detail.json()["messages"]
            for card in message.get("cards", [])
            if card.get("type") == "TOOL_RESULT"
        ]
        assert len(result_cards) == 1

        with org_session(UUID(ORG_A)) as session:
            runs = list(
                session.scalars(
                    select(AgentRun).where(
                        AgentRun.organization_id == UUID(ORG_A),
                        AgentRun.agent_type == "tool:trip.get_plan",
                        AgentRun.input["arguments"]["trip_id"].astext == trip_id,
                    )
                )
            )
            assert len(runs) == 1
            assert runs[0].status == "SUCCEEDED"
            assert runs[0].output["status"] == "NO_ACTIVE_PLAN"
    finally:
        with org_session(UUID(ORG_A)) as session:
            if conversation_id is not None:
                session.execute(
                    delete(AssistantConversation).where(
                        AssistantConversation.id == UUID(conversation_id)
                    )
                )
            session.execute(
                delete(AgentRun).where(
                    AgentRun.organization_id == UUID(ORG_A),
                    AgentRun.agent_type == "tool:trip.get_plan",
                    AgentRun.input["arguments"]["trip_id"].astext == trip_id,
                )
            )
            session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))
