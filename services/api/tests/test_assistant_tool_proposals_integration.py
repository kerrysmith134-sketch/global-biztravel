"""Integration coverage for model-assisted Assistant tool proposal filtering."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.db import org_session
from app.llm.contracts import LLMResponse
from app.main import app
from app.models.assistant import AssistantConversation
from app.models.trip import Trip
from app.models.workflow import AgentRun
from app.services import assistant_service
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
USER_A = "22222222-2222-2222-2222-222222222222"


class _SuggestionGateway:
    def __init__(self) -> None:
        self.request = None

    def complete(self, session, organization_id, request):
        self.request = request
        return LLMResponse(
            output={
                "reply": "我可以读取这趟出行的生效计划。",
                "tool_suggestions": ["trip.get_plan", "trip.delete"],
            },
            invocation_id=None,
            model="test-assistant",
        )


def _headers() -> dict[str, str]:
    return {
        "X-Org-Id": ORG_A,
        "X-User-Id": USER_A,
    }


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def test_llm_can_propose_only_allowlisted_tools_without_executing_them(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    trip_response = client.post(
        "/v1/trips",
        headers=_headers(),
        json={
            "name": f"Assistant 模型工具建议·{uuid.uuid4().hex[:8]}",
            "window_start": (now + timedelta(days=6)).isoformat(),
            "window_end": (now + timedelta(days=9)).isoformat(),
            "countries": ["DE"],
        },
    )
    assert trip_response.status_code == 201, trip_response.text
    trip_id = trip_response.json()["id"]
    conversation_id: str | None = None
    gateway = _SuggestionGateway()
    monkeypatch.setattr(assistant_service, "get_gateway", lambda: gateway)

    try:
        conversation = client.post(
            "/v1/assistant/conversations",
            headers=_headers(),
            json={"trip_id": trip_id, "title": "工具建议"},
        )
        assert conversation.status_code == 201, conversation.text
        conversation_id = conversation.json()["id"]

        response = client.post(
            f"/v1/assistant/conversations/{conversation_id}/messages",
            headers=_headers(),
            json={"content": "请帮我梳理下一步的重点"},
        )
        assert response.status_code == 200, response.text
        tool_cards = [
            card for card in response.json()["cards"]
            if card.get("type") == "TOOL_PROPOSAL"
        ]
        assert [card["entity_id"] for card in tool_cards] == ["trip.get_plan"]

        assert gateway.request is not None
        assert gateway.request.prompt_version == "v2"
        available_names = {
            item["name"] for item in gateway.request.variables["available_tools"]
        }
        assert "trip.get_plan" in available_names

        # Producing a proposal card is not an AgentRun and must not call a tool handler.
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
            assert runs == []
    finally:
        with org_session(UUID(ORG_A)) as session:
            if conversation_id is not None:
                session.execute(
                    delete(AssistantConversation).where(
                        AssistantConversation.id == UUID(conversation_id)
                    )
                )
            session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))
