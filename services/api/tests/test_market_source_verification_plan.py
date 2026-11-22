"""Source-verification plan policy and PostgreSQL API acceptance tests."""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from app.db import org_session, owner_session
from app.main import app
from app.models.market import (
    MarketClaim,
    MarketClaimEvidence,
    MarketClaimVerificationRun,
    MarketWorkspace,
    MarketWorkspaceWorkflow,
)
from app.models.workflow import AgentRun, Evidence, WorkflowInstance
from app.services.market_source_verification_service import _claim_origin_state
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "33333333-3333-3333-3333-333333333333"
ADMIN_A = "55555555-5555-5555-5555-555555555555"
USER_B = "44444444-4444-4444-4444-444444444444"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers(org: str = ORG_A, user: str = ADMIN_A) -> dict[str, str]:
    return {
        "X-Org-Id": org,
        "X-User-Id": user,
        "Idempotency-Key": str(uuid.uuid4()),
    }


def create_workspace(client: TestClient) -> str:
    response = client.post(
        "/v1/market/workspaces",
        headers=headers(),
        json={"name": f"Source plan {uuid.uuid4().hex[:8]}"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def cleanup_workspace(workspace_id: str | None) -> None:
    if workspace_id is None:
        return
    with org_session(UUID(ORG_A)) as session:
        session.execute(
            delete(MarketWorkspace).where(
                MarketWorkspace.organization_id == UUID(ORG_A),
                MarketWorkspace.id == UUID(workspace_id),
            )
        )
        session.execute(
            delete(Evidence).where(
                Evidence.organization_id == UUID(ORG_A),
                Evidence.subject_type == "market_workspace",
                Evidence.subject_id == UUID(workspace_id),
            )
        )


def test_claim_source_origin_policy_never_promotes_domain_count_to_independence() -> None:
    one_source = [
        {"source_host": "publisher.example", "content_fingerprint": "a" * 64}
    ]
    assert _claim_origin_state(one_source, 0) == "SINGLE_SOURCE_ONLY"
    assert _claim_origin_state(
        [
            {"source_host": "publisher.example", "content_fingerprint": "a" * 64},
            {"source_host": "publisher.example", "content_fingerprint": "b" * 64},
        ],
        0,
    ) == "SAME_HOST_ONLY"
    assert _claim_origin_state(
        [
            {"source_host": "publisher.example", "content_fingerprint": "a" * 64},
            {"source_host": "wire.example", "content_fingerprint": "a" * 64},
        ],
        0,
    ) == "POSSIBLE_SYNDICATION"
    assert _claim_origin_state(
        [
            {"source_host": "publisher.example", "content_fingerprint": "a" * 64},
            {"source_host": "wire.example", "content_fingerprint": "b" * 64},
        ],
        0,
    ) == "MULTIPLE_HOSTS_UNCONFIRMED"
    assert _claim_origin_state(one_source, 1) == "CONFLICTING_SIGNALS"
    assert _claim_origin_state([], 0) == "NO_SUPPORTING_EVIDENCE"


def test_source_verification_plan_generates_bounded_review_actions(
    client: TestClient,
) -> None:
    workspace_id: str | None = None
    try:
        workspace_id = create_workspace(client)
        original = (
            "A named institution published an identical procurement notice in 2026."
        )
        records = [
            {
                "canonical_ref": "https://publisher.example/reports/procurement",
                "content": original,
                "kind": "WEB_SEARCH_RESULT",
                "source": "Publisher",
            },
            {
                "canonical_ref": "https://publisher.example/reports/procurement",
                "content": "A revised procurement notice was published later in 2026.",
                "kind": "WEB_SEARCH_RESULT",
                "source": "Publisher",
            },
            {
                "canonical_ref": "https://wire.example/republication",
                "content": original,
                "kind": "WEB_SEARCH_RESULT",
                "source": "Wire service",
            },
        ]
        for payload in records:
            created = client.post(
                f"/v1/market/workspaces/{workspace_id}/evidence",
                headers=headers(),
                json=payload,
            )
            assert created.status_code == 201, created.text

        with org_session(UUID(ORG_A)) as session:
            before_count = session.scalar(
                select(func.count()).select_from(Evidence).where(
                    Evidence.organization_id == UUID(ORG_A),
                    Evidence.subject_type == "market_workspace",
                    Evidence.subject_id == UUID(workspace_id),
                )
            )

        response = client.get(
            f"/v1/market/workspaces/{workspace_id}/source-verification-plan",
            headers=headers(),
        )
        assert response.status_code == 200, response.text
        plan = response.json()
        assert plan["total_evidence_count"] == 3
        assert plan["analyzed_evidence_count"] == 3
        assert plan["evidence_truncated"] is False
        assert plan["total_claim_count"] == 0
        assert plan["action_count"] == len(plan["actions"])
        action_types = {item["action_type"] for item in plan["actions"]}
        assert "EXTRACT_SOURCE_PAGE" in action_types
        assert "COMPLETE_SOURCE_METADATA" in action_types
        assert "INVESTIGATE_POSSIBLE_SYNDICATION" in action_types
        assert "REVIEW_SOURCE_VERSION_DRIFT" in action_types
        assert any("不同域名" in item for item in plan["limitations"])

        with org_session(UUID(ORG_A)) as session:
            after_count = session.scalar(
                select(func.count()).select_from(Evidence).where(
                    Evidence.organization_id == UUID(ORG_A),
                    Evidence.subject_type == "market_workspace",
                    Evidence.subject_id == UUID(workspace_id),
                )
            )
        assert after_count == before_count

        denied = client.get(
            f"/v1/market/workspaces/{workspace_id}/source-verification-plan",
            headers=headers(org=ORG_B, user=USER_B),
        )
        assert denied.status_code == 404, denied.text
    finally:
        cleanup_workspace(workspace_id)


def test_plan_flags_claim_when_new_source_version_appears(
    client: TestClient,
) -> None:
    workspace_id: str | None = None
    workflow_ids: list[UUID] = []
    try:
        workspace_id = create_workspace(client)
        original_body = "The public authority approved the policy in November 2026."
        original = client.post(
            f"/v1/market/workspaces/{workspace_id}/evidence",
            headers=headers(),
            json={
                "canonical_ref": "https://authority.example/policy",
                "content": original_body,
                "kind": "WEB_PAGE_EXTRACT",
                "source": "Public authority",
            },
        )
        assert original.status_code == 201, original.text
        original_id = UUID(original.json()["id"])

        question = client.post(
            f"/v1/market/workspaces/{workspace_id}/questions",
            headers=headers(),
            json={"question": "What is the current policy status?"},
        )
        assert question.status_code == 201, question.text
        answer = client.post(
            f"/v1/market/workspaces/{workspace_id}/questions/{question.json()['id']}/answers",
            headers=headers(),
            json={
                "answer_summary": "The authority approved the policy in November 2026.",
                "assumptions": [],
                "unknowns": ["The implementation date may change after approval."],
                "confidence_level": "LOW",
                "evidence": [
                    {
                        "evidence_id": str(original_id),
                        "relation_type": "SUPPORTS",
                        "rationale": "The extracted page states the approval.",
                    }
                ],
            },
        )
        assert answer.status_code == 201, answer.text
        answer_id = UUID(answer.json()["id"])

        with org_session(UUID(ORG_A)) as session:
            workflow = WorkflowInstance(
                organization_id=UUID(ORG_A),
                workflow_type="market_claim_verification",
                status="COMPLETED",
                payload={"workspace_id": workspace_id, "answer_id": str(answer_id)},
                created_by_user_id=UUID(ADMIN_A),
            )
            session.add(workflow)
            session.flush()
            workflow_ids.append(workflow.id)
            session.add(
                MarketWorkspaceWorkflow(
                    organization_id=UUID(ORG_A),
                    workspace_id=UUID(workspace_id),
                    workflow_id=workflow.id,
                    linked_by_user_id=UUID(ADMIN_A),
                )
            )
            session.flush()
            agent_run = AgentRun(
                organization_id=UUID(ORG_A),
                workflow_id=workflow.id,
                agent_type="MARKET_CLAIM_VERIFICATION",
                status="SUCCEEDED",
                input={},
                output={},
            )
            session.add(agent_run)
            session.flush()
            verification_run = MarketClaimVerificationRun(
                organization_id=UUID(ORG_A),
                workspace_id=UUID(workspace_id),
                answer_id=answer_id,
                workflow_id=workflow.id,
                agent_run_id=agent_run.id,
                status="COMPLETED",
                prompt_version="test-v1",
                input_digest="a" * 64,
                claim_count=1,
                degraded=False,
                limitations=[],
            )
            session.add(verification_run)
            session.flush()
            claim = MarketClaim(
                organization_id=UUID(ORG_A),
                workspace_id=UUID(workspace_id),
                answer_id=answer_id,
                verification_run_id=verification_run.id,
                generated_by_run_id=agent_run.id,
                version=1,
                claim_key="c" * 64,
                statement="The public authority approved the policy in November 2026.",
                claim_type="FACT",
                scope={"jurisdiction": "user-selected market"},
                reasoning="The selected source contains the approval statement.",
                verification_verdict="PROVISIONAL_SUPPORT",
                confidence_level="LOW",
                status="NEEDS_REVIEW",
                supporting_evidence_count=1,
                contradicting_evidence_count=0,
                verified_support_count=0,
                verified_contradiction_count=0,
                distinct_host_count=1,
                confirmed_independent_source_count=0,
                limitations=[],
            )
            session.add(claim)
            session.flush()
            session.add(
                MarketClaimEvidence(
                    organization_id=UUID(ORG_A),
                    workspace_id=UUID(workspace_id),
                    claim_id=claim.id,
                    evidence_id=original_id,
                    assessed_by_run_id=agent_run.id,
                    relation_type="SUPPORTS",
                    rationale="Snapshot at verification time.",
                    source_host="authority.example",
                    content_hash_snapshot=original.json()["content_hash"],
                    verification_status_snapshot="UNVERIFIED",
                    origin_assessment="SINGLE_SOURCE",
                )
            )

        revised = client.post(
            f"/v1/market/workspaces/{workspace_id}/evidence",
            headers=headers(),
            json={
                "canonical_ref": "https://authority.example/policy",
                "content": "The authority amended the policy and postponed implementation.",
                "kind": "WEB_PAGE_EXTRACT",
                "source": "Public authority",
            },
        )
        assert revised.status_code == 201, revised.text

        plan_response = client.get(
            f"/v1/market/workspaces/{workspace_id}/source-verification-plan",
            headers=headers(),
        )
        assert plan_response.status_code == 200, plan_response.text
        plan = plan_response.json()
        assert plan["claims_with_changed_sources_count"] == 1
        claim_plan = plan["claims"][0]
        assert claim_plan["source_origin_state"] == "SINGLE_SOURCE_ONLY"
        assert claim_plan["changed_source_count"] == 1
        assert "SOURCE_CONTENT_CHANGED_AFTER_CLAIM_RUN" in claim_plan["issues"]
        action = next(
            item for item in plan["actions"]
            if item["action_type"] == "REVERIFY_CLAIM_AFTER_SOURCE_CHANGE"
        )
        assert action["priority"] == "HIGH"
        assert action["target_type"] == "CLAIM"

    finally:
        with owner_session(UUID(ORG_A)) as session:
            if workspace_id:
                # Workspace deletion cascades the Claim -> VerificationRun -> Workflow link graph.
                session.execute(
                    delete(MarketWorkspace).where(
                        MarketWorkspace.organization_id == UUID(ORG_A),
                        MarketWorkspace.id == UUID(workspace_id),
                    )
                )
            if workflow_ids:
                session.execute(
                    delete(AgentRun).where(
                        AgentRun.organization_id == UUID(ORG_A),
                        AgentRun.workflow_id.in_(workflow_ids),
                    )
                )
                session.execute(
                    delete(WorkflowInstance).where(
                        WorkflowInstance.organization_id == UUID(ORG_A),
                        WorkflowInstance.id.in_(workflow_ids),
                    )
                )
            if workspace_id:
                # Evidence is append-only for the application role but owner cleanup is allowed.
                session.execute(
                    delete(Evidence).where(
                        Evidence.organization_id == UUID(ORG_A),
                        Evidence.subject_type == "market_workspace",
                        Evidence.subject_id == UUID(workspace_id),
                    )
                )

