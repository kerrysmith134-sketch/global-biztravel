"""通用、用户意图驱动的研究计划 API 验收。"""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.workflow import AgentRun, Evidence, WorkflowInstance
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


def headers(org: str = ORG_A, user: str = USER_A, key: str | None = None) -> dict[str, str]:
    return {
        "X-Org-Id": org,
        "X-User-Id": user,
        "Idempotency-Key": key or str(uuid.uuid4()),
    }


def test_research_plan_is_user_intent_driven_and_does_not_fake_retrieval(client: TestClient) -> None:
    idempotency_key = str(uuid.uuid4())
    payload = {
        "request": "研究 ACME GmbH 的企业背景，并为下周拜访准备 8 个问题",
        "source_scope": "EXTERNAL_ONLY",
        "context_text": "用户补充：希望关注对方近期业务变化。",
    }
    created = client.post(
        "/v1/research/plans",
        headers=headers(key=idempotency_key),
        json=payload,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    plan_id = body["id"]

    assert body["status"] == "PLANNED"
    assert body["retrieval_status"] == "NOT_STARTED"
    assert body["requested_scope"] == "EXTERNAL_ONLY"
    assert body["recommended_scope"] == "EXTERNAL_ONLY"
    assert body["plan"]["subject"]
    assert body["plan"]["search_queries"]
    assert body["plan"]["external_research_needed"] is True
    # The offline provider has no industry defaults and no fabricated market facts.
    assert body["plan"]["domain"] is None
    assert body["plan"]["geographies"] == []
    assert body["plan"]["entities"] == []
    evidence_kinds = {item["kind"] for item in body["evidence"]}
    assert evidence_kinds == {"USER_REQUEST", "USER_PROVIDED_CONTEXT"}
    assert not any(item["kind"] == "EXTERNAL_SOURCE" for item in body["evidence"])

    # Same idempotency key replays the same plan without creating another workflow/run.
    replay = client.post(
        "/v1/research/plans",
        headers=headers(key=idempotency_key),
        json=payload,
    )
    assert replay.status_code == 201
    assert replay.headers.get("Idempotent-Replay") == "true"
    assert replay.json()["id"] == plan_id

    detail = client.get(f"/v1/research/plans/{plan_id}", headers=headers())
    assert detail.status_code == 200
    assert detail.json()["id"] == plan_id
    assert detail.json()["planner"]["llm_invocation_id"]

    with org_session(UUID(ORG_A)) as session:
        workflows = session.scalars(
            select(WorkflowInstance).where(
                WorkflowInstance.id == UUID(plan_id),
                WorkflowInstance.workflow_type == "research_plan",
            )
        ).all()
        runs = session.scalars(
            select(AgentRun).where(AgentRun.workflow_id == UUID(plan_id))
        ).all()
        evidence = session.scalars(
            select(Evidence).where(
                Evidence.subject_type == "RESEARCH_PLAN",
                Evidence.subject_id == UUID(plan_id),
            )
        ).all()
        assert len(workflows) == 1
        assert len(runs) == 1
        assert "user_provided_context" not in runs[0].input
        assert runs[0].input["user_provided_context_sha256"]
        assert {row.kind for row in evidence} == {"USER_REQUEST", "USER_PROVIDED_CONTEXT"}

    with org_session(UUID(ORG_A)) as session:
        session.execute(delete(Evidence).where(Evidence.subject_id == UUID(plan_id)))
        session.execute(delete(AgentRun).where(AgentRun.workflow_id == UUID(plan_id)))
        session.execute(delete(WorkflowInstance).where(WorkflowInstance.id == UUID(plan_id)))


def test_research_plan_internal_only_has_no_external_queries(client: TestClient) -> None:
    created = client.post(
        "/v1/research/plans",
        headers=headers(),
        json={
            "request": "整理这次行程的现有拜访记录，列出尚未确认的事项",
            "source_scope": "INTERNAL_ONLY",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["recommended_scope"] == "INTERNAL_ONLY"
    assert body["plan"]["search_queries"] == []
    assert body["plan"]["external_research_needed"] is False
    plan_id = body["id"]
    with org_session(UUID(ORG_A)) as session:
        session.execute(delete(Evidence).where(Evidence.subject_id == UUID(plan_id)))
        session.execute(delete(AgentRun).where(AgentRun.workflow_id == UUID(plan_id)))
        session.execute(delete(WorkflowInstance).where(WorkflowInstance.id == UUID(plan_id)))


def test_research_plan_is_tenant_scoped(client: TestClient) -> None:
    created = client.post(
        "/v1/research/plans",
        headers=headers(),
        json={"request": "Research the publicly available background of a named company"},
    )
    assert created.status_code == 201, created.text
    plan_id = created.json()["id"]
    hidden = client.get(
        f"/v1/research/plans/{plan_id}",
        headers=headers(ORG_B, USER_B),
    )
    assert hidden.status_code == 404
    with org_session(UUID(ORG_A)) as session:
        session.execute(delete(Evidence).where(Evidence.subject_id == UUID(plan_id)))
        session.execute(delete(AgentRun).where(AgentRun.workflow_id == UUID(plan_id)))
        session.execute(delete(WorkflowInstance).where(WorkflowInstance.id == UUID(plan_id)))
