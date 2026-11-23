"""PostgreSQL acceptance tests for user-driven Market Context extraction."""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from app.db import org_session
from app.llm.contracts import LLMResponse
from app.main import app
from app.models.market import (
    MarketContextSnapshot,
    MarketWorkspace,
    MarketWorkspaceWorkflow,
)
from app.models.workflow import AgentRun, WorkflowInstance
from app.services import market_context_extraction_service
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG = UUID("11111111-1111-1111-1111-111111111111")
USER = UUID("55555555-5555-5555-5555-555555555555")


def headers() -> dict[str, str]:
    return {
        "X-Org-Id": str(ORG),
        "X-User-Id": str(USER),
        "Idempotency-Key": str(uuid.uuid4()),
    }


class FakeGateway:
    def __init__(self, output: dict, *, degraded: bool = False) -> None:
        self.output = output
        self.degraded = degraded
        self.requests: list[dict] = []

    def complete(self, session, organization_id, request, *, trace_id=None):
        self.requests.append(
            {
                "organization_id": str(organization_id),
                "request": request,
                "trace_id": trace_id,
            }
        )
        return LLMResponse(
            output=self.output,
            invocation_id=UUID("99999999-9999-4999-8999-999999999999"),
            model="test-context-extractor",
            degraded=self.degraded,
            error="provider unavailable" if self.degraded else None,
        )


def _cleanup(workspace_id: UUID | None) -> None:
    if workspace_id is None:
        return
    with org_session(ORG) as session:
        workflow_ids = list(
            session.scalars(
                select(MarketWorkspaceWorkflow.workflow_id).where(
                    MarketWorkspaceWorkflow.organization_id == ORG,
                    MarketWorkspaceWorkflow.workspace_id == workspace_id,
                )
            ).all()
        )
        if workflow_ids:
            session.execute(
                delete(AgentRun).where(
                    AgentRun.organization_id == ORG,
                    AgentRun.workflow_id.in_(workflow_ids),
                )
            )
            session.execute(
                delete(WorkflowInstance).where(
                    WorkflowInstance.organization_id == ORG,
                    WorkflowInstance.id.in_(workflow_ids),
                )
            )
        session.execute(
            delete(MarketWorkspace).where(
                MarketWorkspace.organization_id == ORG,
                MarketWorkspace.id == workspace_id,
            )
        )


def _create_workspace(client: TestClient, raw_input: str) -> tuple[UUID, UUID]:
    response = client.post(
        "/v1/market/workspaces",
        headers=headers(),
        json={
            "name": f"Context extraction {uuid.uuid4().hex[:8]}",
            "objective": "Organize only the business context supplied by the user.",
            "initial_context": raw_input,
        },
    )
    assert response.status_code == 201, response.text
    workspace_id = UUID(response.json()["id"])
    with org_session(ORG) as session:
        snapshot = session.scalars(
            select(MarketContextSnapshot).where(
                MarketContextSnapshot.organization_id == ORG,
                MarketContextSnapshot.workspace_id == workspace_id,
            )
        ).one()
        context_id = snapshot.id
    return workspace_id, context_id


def test_context_extraction_is_grounded_and_preserves_user_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(app)
    raw_input = "We provide digital design services to clinics in Kenya."
    workspace_id: UUID | None = None
    output = {
        "offerings": [
            {
                "label": "Digital design services",
                "normalized_label": "digital design",
                "description": "A service offering explicitly supplied by the user.",
                "source_quote": "We provide digital design services",
                "confidence": 0.98,
            },
            {
                "label": "Unstated hardware",
                "source_quote": "We manufacture industrial hardware",
            },
        ],
        "business_models": [],
        "industries": [
            {
                "label": "Healthcare",
                "source_quote": "clinics",
                "confidence": 0.9,
            }
        ],
        "customer_segments": [],
        "value_propositions": [],
        "target_markets": [
            {
                "label": "Kenya",
                "source_quote": "in Kenya",
                "confidence": 0.95,
            }
        ],
        "goals": [],
        "constraints": [],
        "assumptions": ["The user may want to expand to additional countries."],
        "unknowns": ["The user's preferred customer size is not specified."],
    }
    fake = FakeGateway(output)
    monkeypatch.setattr(
        market_context_extraction_service, "get_gateway", lambda: fake
    )
    try:
        workspace_id, context_id = _create_workspace(client, raw_input)
        with org_session(ORG) as session:
            snapshot = session.get(MarketContextSnapshot, context_id)
            assert snapshot is not None
            snapshot.user_overrides = {
                "industries": [{"label": "User-confirmed segment"}]
            }

        response = client.post(
            f"/v1/market/workspaces/{workspace_id}/contexts/{context_id}:extract",
            headers=headers(),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["extraction_status"] == "COMPLETED"
        assert body["extracted_context"]["offerings"][0]["label"] == "Digital design services"
        assert body["extracted_context"]["industries"] == [
            {"label": "User-confirmed segment"}
        ]
        assert body["extracted_context"]["target_markets"][0]["label"] == "Kenya"
        assert any("逐字引文校验" in item for item in body["unknowns"])
        assert body["provenance"]["industries"]["source"] == "USER_EDIT"
        assert body["provenance"]["offerings"]["source"] == "MODEL"

        request = fake.requests[0]["request"]
        assert request.variables["raw_input"] == raw_input
        assert request.prompt_template_id == "market_context_extract"
        with org_session(ORG) as session:
            run = session.scalars(
                select(AgentRun).where(
                    AgentRun.organization_id == ORG,
                    AgentRun.agent_type == "market_context_extraction",
                ).order_by(AgentRun.created_at.desc())
            ).first()
            assert run is not None
            assert "raw_input" not in run.input
            assert run.input["input_sha256"]
            assert run.output["status"] == "COMPLETED"

        # A new idempotency key does not create a second extraction for the same prompt version.
        again = client.post(
            f"/v1/market/workspaces/{workspace_id}/contexts/{context_id}:extract",
            headers=headers(),
        )
        assert again.status_code == 200, again.text
        assert len(fake.requests) == 1
    finally:
        _cleanup(workspace_id)


def test_degraded_gateway_leaves_context_unextracted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(app)
    raw_input = "The user runs a small architecture studio in Nairobi."
    workspace_id: UUID | None = None
    fake = FakeGateway(
        {
            "offerings": [],
            "business_models": [],
            "industries": [],
            "customer_segments": [],
            "value_propositions": [],
            "target_markets": [],
            "goals": [],
            "constraints": [],
            "assumptions": [],
            "unknowns": [],
        },
        degraded=True,
    )
    monkeypatch.setattr(
        market_context_extraction_service, "get_gateway", lambda: fake
    )
    try:
        workspace_id, context_id = _create_workspace(client, raw_input)
        response = client.post(
            f"/v1/market/workspaces/{workspace_id}/contexts/{context_id}:extract",
            headers=headers(),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["extraction_status"] == "FAILED"
        assert body["extracted_context"] == {}
        assert body["provenance"]["extraction"]["status"] == "FAILED"
    finally:
        _cleanup(workspace_id)
