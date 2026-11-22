"""PostgreSQL acceptance tests for claim-level verification over answer Evidence snapshots."""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from app.db import owner_session, org_session
from app.llm.contracts import LLMResponse
from app.main import app
from app.models.market import (
    MarketClaim,
    MarketClaimEvidence,
    MarketClaimVerificationRun,
    MarketResearchQuestionAnswerEvidence,
    MarketWorkspace,
)
from app.models.reliability import BackgroundJob
from app.models.workflow import AgentRun, Evidence, WorkflowInstance
from app.services import job_service
from app.workers.tasks import handle_market_claim_verification
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
ADMIN_A = "55555555-5555-5555-5555-555555555555"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers(key: str | None = None) -> dict[str, str]:
    return {
        "X-Org-Id": ORG_A,
        "X-User-Id": ADMIN_A,
        "Idempotency-Key": key or str(uuid.uuid4()),
    }


def create_workspace(client: TestClient) -> str:
    response = client.post(
        "/v1/market/workspaces",
        headers=headers(),
        json={
            "name": f"Claim verification {uuid.uuid4().hex[:8]}",
            "objective": "Verify atomic assertions against explicitly linked research evidence.",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def create_question(client: TestClient, workspace_id: str) -> str:
    response = client.post(
        f"/v1/market/workspaces/{workspace_id}/questions",
        headers=headers(),
        json={"question": "What can the selected sources establish about the target market?"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def create_evidence(
    client: TestClient, workspace_id: str, url: str, content: str
) -> str:
    response = client.post(
        f"/v1/market/workspaces/{workspace_id}/evidence",
        headers=headers(),
        json={
            "canonical_ref": url,
            "content": content,
            "kind": "WEB_PAGE_EXTRACT",
            "source": "test-source",
            "payload": {"content": content},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def create_answer(
    client: TestClient,
    workspace_id: str,
    question_id: str,
    evidence_relations: list[tuple[str, str]],
) -> str:
    response = client.post(
        f"/v1/market/workspaces/{workspace_id}/questions/{question_id}/answers",
        headers=headers(),
        json={
            "answer_summary": (
                "The selected research materials indicate possible demand, but the evidence "
                "does not yet establish an independently verified market fact."
            ),
            "assumptions": [],
            "unknowns": ["The final buyer decision and approved budget."],
            "confidence_level": "LOW",
            "evidence": [
                {
                    "evidence_id": evidence_id,
                    "relation_type": relation,
                    "rationale": "Evidence attached to the answer version.",
                }
                for evidence_id, relation in evidence_relations
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


class FakeGateway:
    def __init__(self, output: dict) -> None:
        self.output = output
        self.requests = []

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
            model="test-claim-verification-model",
            degraded=False,
        )


def execute_job(job_id: str) -> dict:
    with org_session(UUID(ORG_A)) as session:
        job = session.get(BackgroundJob, UUID(job_id))
        assert job is not None
        job.status = "RUNNING"
        job.locked_by = "market-claim-verification-test-worker"
        result = handle_market_claim_verification(session, job)
        job_service.complete(session, job.id, result=result)
        return result


def cleanup(
    workspace_ids: list[str],
    workflow_ids: list[str],
    job_ids: list[str],
) -> None:
    with owner_session(UUID(ORG_A)) as session:
        workspace_uuids = [UUID(item) for item in workspace_ids]
        workflow_uuids = [UUID(item) for item in workflow_ids]
        if workspace_uuids:
            session.execute(
                delete(MarketClaimEvidence).where(
                    MarketClaimEvidence.organization_id == UUID(ORG_A),
                    MarketClaimEvidence.workspace_id.in_(workspace_uuids),
                )
            )
            session.execute(
                delete(MarketClaim).where(
                    MarketClaim.organization_id == UUID(ORG_A),
                    MarketClaim.workspace_id.in_(workspace_uuids),
                )
            )
            session.execute(
                delete(MarketClaimVerificationRun).where(
                    MarketClaimVerificationRun.organization_id == UUID(ORG_A),
                    MarketClaimVerificationRun.workspace_id.in_(workspace_uuids),
                )
            )
        if workflow_uuids:
            session.execute(
                delete(AgentRun).where(
                    AgentRun.organization_id == UUID(ORG_A),
                    AgentRun.workflow_id.in_(workflow_uuids),
                )
            )
            session.execute(
                delete(WorkflowInstance).where(
                    WorkflowInstance.organization_id == UUID(ORG_A),
                    WorkflowInstance.id.in_(workflow_uuids),
                )
            )
        if workspace_uuids:
            session.execute(
                delete(MarketWorkspace).where(
                    MarketWorkspace.organization_id == UUID(ORG_A),
                    MarketWorkspace.id.in_(workspace_uuids),
                )
            )
            session.execute(
                delete(Evidence).where(
                    Evidence.organization_id == UUID(ORG_A),
                    Evidence.subject_type == "market_workspace",
                    Evidence.subject_id.in_(workspace_uuids),
                )
            )
        if job_ids:
            session.execute(
                delete(BackgroundJob).where(
                    BackgroundJob.organization_id == UUID(ORG_A),
                    BackgroundJob.id.in_([UUID(item) for item in job_ids]),
                )
            )


def test_claim_agent_extracts_atomic_claims_and_keeps_independence_unconfirmed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_ids: list[str] = []
    workflow_ids: list[str] = []
    job_ids: list[str] = []
    gateway = FakeGateway(
        {
            "claims": [
                {
                    "claim_text": "A named buyer published a procurement plan for the relevant category.",
                    "claim_type": "FACT",
                    "scope": {"buyer": "Named buyer", "period": "2026"},
                    "reasoning": "This is a discrete, attributable assertion.",
                    "evidence_assessments": [
                        {
                            "evidence_id": "REPLACE_SUPPORT",
                            "relation_type": "SUPPORTS",
                            "rationale": "The buyer page states that a procurement plan was published.",
                        },
                        {
                            "evidence_id": "REPLACE_CONTRADICT",
                            "relation_type": "CONTRADICTS",
                            "rationale": "The second source says the plan was withdrawn.",
                        },
                    ],
                },
                {
                    "claim_text": "The broader category has seen increased purchasing enquiries.",
                    "claim_type": "ESTIMATE",
                    "scope": {"geography": "Target region", "period": "2026"},
                    "reasoning": "A separate assertion with broader scope.",
                    "evidence_assessments": [
                        {
                            "evidence_id": "REPLACE_CONTEXT",
                            "relation_type": "SUPPORTS",
                            "rationale": "The industry source reports an increase in enquiries.",
                        },
                        {
                            "evidence_id": "REPLACE_SUPPORT",
                            "relation_type": "CONTEXT",
                            "rationale": "The buyer page provides a separate context signal.",
                        }
                    ],
                },
            ],
            "limitations": ["The sources do not prove final purchasing decisions."],
        }
    )
    try:
        workspace_id = create_workspace(client)
        workspace_ids.append(workspace_id)
        question_id = create_question(client, workspace_id)
        support_id = create_evidence(
            client,
            workspace_id,
            "https://buyer.example.test/procurement",
            "The buyer published a procurement plan for the relevant category in 2026.",
        )
        contradict_id = create_evidence(
            client,
            workspace_id,
            "https://issuer.example.org/withdrawal",
            "The buyer subsequently announced that the procurement plan was withdrawn.",
        )
        context_id = create_evidence(
            client,
            workspace_id,
            "https://industry.example.net/enquiries",
            "The industry report describes increased purchasing enquiries across the category.",
        )
        answer_id = create_answer(
            client,
            workspace_id,
            question_id,
            [
                (support_id, "SUPPORTS"),
                (contradict_id, "CONTRADICTS"),
                (context_id, "CONTEXT"),
            ],
        )
        gateway.output["claims"][0]["evidence_assessments"][0]["evidence_id"] = support_id
        gateway.output["claims"][0]["evidence_assessments"][1]["evidence_id"] = contradict_id
        gateway.output["claims"][1]["evidence_assessments"][0]["evidence_id"] = context_id
        gateway.output["claims"][1]["evidence_assessments"][1]["evidence_id"] = support_id

        monkeypatch.setattr("app.workers.tasks.get_gateway", lambda: gateway)
        request_key = str(uuid.uuid4())
        queued = client.post(
            f"/v1/market/workspaces/{workspace_id}/answers/{answer_id}:verify-claims",
            headers=headers(request_key),
        )
        assert queued.status_code == 202, queued.text
        accepted = queued.json()
        workflow_ids.append(accepted["workflow_id"])
        job_ids.append(accepted["job_id"])
        assert accepted["evidence_count"] == 3
        assert accepted["status"] == "PENDING"
        assert accepted["reused"] is False

        replay = client.post(
            f"/v1/market/workspaces/{workspace_id}/answers/{answer_id}:verify-claims",
            headers=headers(request_key),
        )
        assert replay.status_code == 202, replay.text
        assert replay.json()["workflow_id"] == accepted["workflow_id"]
        assert replay.json()["reused"] is True

        result = execute_job(accepted["job_id"])
        assert result["claim_count"] == 2
        assert result["degraded"] is False

        run_response = client.get(
            f"/v1/market/workspaces/{workspace_id}/claim-verification-runs/{accepted['verification_run_id']}",
            headers=headers(),
        )
        assert run_response.status_code == 200, run_response.text
        run = run_response.json()
        assert run["status"] == "COMPLETED"
        assert run["claim_count"] == 2
        assert run["agent_run_id"] == result["agent_run_id"]
        assert len(run["claims"]) == 2

        conflicting = next(
            row for row in run["claims"] if row["verification_verdict"] == "CONFLICTED"
        )
        provisional = next(
            row for row in run["claims"] if row["verification_verdict"] == "PROVISIONAL_SUPPORT"
        )
        assert conflicting["status"] == "CONFLICTED"
        assert conflicting["supporting_evidence_count"] == 1
        assert conflicting["contradicting_evidence_count"] == 1
        assert conflicting["confidence_level"] == "INSUFFICIENT"
        assert provisional["status"] == "NEEDS_REVIEW"
        assert provisional["confidence_level"] == "LOW"
        assert provisional["distinct_host_count"] == 1
        assert provisional["confirmed_independent_source_count"] == 0
        assert all(
            item["verification_status_snapshot"] == "UNVERIFIED"
            for row in run["claims"]
            for item in row["evidence"]
        )
        assert any(
            item["origin_assessment"] == "DISTINCT_HOST_UNCONFIRMED"
            for item in conflicting["evidence"]
        )
        assert "不能证明出版方或来源彼此独立" in " ".join(conflicting["limitations"])

        blocked = client.post(
            f"/v1/market/workspaces/{workspace_id}/claims/{conflicting['id']}:accept",
            headers=headers(),
            json={"review_note": "The two sources conflict and need further review."},
        )
        assert blocked.status_code == 409, blocked.text

        reviewed = client.post(
            f"/v1/market/workspaces/{workspace_id}/claims/{provisional['id']}:accept",
            headers=headers(),
            json={"review_note": "I reviewed the source text and accept this limited assertion for internal use."},
        )
        assert reviewed.status_code == 200, reviewed.text
        assert reviewed.json()["status"] == "ACCEPTED"
        assert reviewed.json()["verification_verdict"] == "PROVISIONAL_SUPPORT"

        answers = client.get(
            f"/v1/market/workspaces/{workspace_id}/questions/{question_id}/answers",
            headers=headers(),
        )
        assert answers.status_code == 200, answers.text
        assert answers.json()[0]["status"] == "CONFLICTED"

        assert len(gateway.requests) == 1
        request = gateway.requests[0]["request"]
        assert request.prompt_template_id == "market_claim_verify"
        assert request.prompt_version == "v1"
        assert "不能证明来源独立" in request.system or "不同域名不等于独立来源" in request.system
        assert len(request.variables["evidence"]) == 3

        with org_session(UUID(ORG_A)) as session:
            verification_run = session.get(
                MarketClaimVerificationRun, UUID(accepted["verification_run_id"])
            )
            assert verification_run is not None
            assert verification_run.status == "COMPLETED"
            assert verification_run.claim_count == 2
            claims = list(
                session.scalars(
                    select(MarketClaim).where(
                        MarketClaim.organization_id == UUID(ORG_A),
                        MarketClaim.verification_run_id == verification_run.id,
                    )
                ).all()
            )
            assert len(claims) == 2
            assert all(claim.confirmed_independent_source_count == 0 for claim in claims)
            links = list(
                session.scalars(
                    select(MarketClaimEvidence).where(
                        MarketClaimEvidence.organization_id == UUID(ORG_A),
                        MarketClaimEvidence.claim_id.in_([claim.id for claim in claims]),
                    )
                ).all()
            )
            assert len(links) == 4
            assert all(link.verification_status_snapshot == "UNVERIFIED" for link in links)
    finally:
        cleanup(workspace_ids, workflow_ids, job_ids)


def test_unmapped_model_evidence_ids_are_dropped_and_claim_stays_insufficient(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_ids: list[str] = []
    workflow_ids: list[str] = []
    job_ids: list[str] = []
    try:
        workspace_id = create_workspace(client)
        workspace_ids.append(workspace_id)
        question_id = create_question(client, workspace_id)
        evidence_id = create_evidence(
            client,
            workspace_id,
            "https://source.example.test/fact",
            "The selected source gives contextual background but does not substantiate the numeric assertion.",
        )
        answer_id = create_answer(
            client,
            workspace_id,
            question_id,
            [(evidence_id, "CONTEXT")],
        )
        fabricated_id = str(uuid.uuid4())
        gateway = FakeGateway(
            {
                "claims": [
                    {
                        "claim_text": "The market grew by exactly 40 percent.",
                        "claim_type": "FACT",
                        "scope": {},
                        "reasoning": "The model attempted to introduce an unsupported numeric claim.",
                        "evidence_assessments": [
                            {
                                "evidence_id": fabricated_id,
                                "relation_type": "SUPPORTS",
                                "rationale": "This evidence does not exist in the input.",
                            }
                        ],
                    }
                ],
                "limitations": [],
            }
        )
        monkeypatch.setattr("app.workers.tasks.get_gateway", lambda: gateway)
        queued = client.post(
            f"/v1/market/workspaces/{workspace_id}/answers/{answer_id}:verify-claims",
            headers=headers(),
        )
        assert queued.status_code == 202, queued.text
        workflow_ids.append(queued.json()["workflow_id"])
        job_ids.append(queued.json()["job_id"])

        result = execute_job(queued.json()["job_id"])
        assert result["claim_count"] == 1
        run_response = client.get(
            f"/v1/market/workspaces/{workspace_id}/claim-verification-runs/{queued.json()['verification_run_id']}",
            headers=headers(),
        )
        claim = run_response.json()["claims"][0]
        assert claim["verification_verdict"] == "INSUFFICIENT"
        assert claim["confidence_level"] == "INSUFFICIENT"
        assert claim["evidence"] == []
        assert claim["supporting_evidence_count"] == 0
        assert any("未能映射" in item for item in claim["limitations"])
        with org_session(UUID(ORG_A)) as session:
            rows = list(
                session.scalars(
                    select(MarketClaimEvidence).where(
                        MarketClaimEvidence.organization_id == UUID(ORG_A),
                        MarketClaimEvidence.claim_id == UUID(claim["id"]),
                    )
                ).all()
            )
            assert rows == []
    finally:
        cleanup(workspace_ids, workflow_ids, job_ids)


def test_answer_without_linked_evidence_cannot_start_claim_verification(
    client: TestClient,
) -> None:
    workspace_ids: list[str] = []
    try:
        workspace_id = create_workspace(client)
        workspace_ids.append(workspace_id)
        question_id = create_question(client, workspace_id)
        answer = client.post(
            f"/v1/market/workspaces/{workspace_id}/questions/{question_id}/answers",
            headers=headers(),
            json={
                "answer_summary": "No factual answer can be made from the current material.",
                "assumptions": [],
                "unknowns": ["No source evidence is available."],
                "confidence_level": "INSUFFICIENT",
                "evidence": [],
                "evidence_gap": "No linked Evidence was collected.",
            },
        )
        assert answer.status_code == 201, answer.text
        rejected = client.post(
            f"/v1/market/workspaces/{workspace_id}/answers/{answer.json()['id']}:verify-claims",
            headers=headers(),
        )
        assert rejected.status_code == 409, rejected.text
    finally:
        cleanup(workspace_ids, [], [])


def test_answer_evidence_from_another_workspace_cannot_be_verified(
    client: TestClient,
) -> None:
    workspace_ids: list[str] = []
    try:
        workspace_a = create_workspace(client)
        workspace_ids.append(workspace_a)
        workspace_b = create_workspace(client)
        workspace_ids.append(workspace_b)
        question_id = create_question(client, workspace_a)
        evidence_b = create_evidence(
            client,
            workspace_b,
            "https://other-workspace.example.test/source",
            "This evidence belongs only to another Workspace.",
        )
        answer = client.post(
            f"/v1/market/workspaces/{workspace_a}/questions/{question_id}/answers",
            headers=headers(),
            json={
                "answer_summary": "There is not yet any evidence in this Workspace.",
                "assumptions": [],
                "unknowns": ["A source from this Workspace is required."],
                "confidence_level": "INSUFFICIENT",
                "evidence": [],
                "evidence_gap": "No linked local source exists.",
            },
        )
        assert answer.status_code == 201, answer.text
        answer_id = answer.json()["id"]

        # Simulate a legacy/corrupt same-organization link. The service must not
        # trust the answer-evidence join as proof of Workspace ownership.
        with org_session(UUID(ORG_A)) as session:
            session.add(
                MarketResearchQuestionAnswerEvidence(
                    organization_id=UUID(ORG_A),
                    workspace_id=UUID(workspace_a),
                    answer_id=UUID(answer_id),
                    evidence_id=UUID(evidence_b),
                    relation_type="CONTEXT",
                )
            )

        rejected = client.post(
            f"/v1/market/workspaces/{workspace_a}/answers/{answer_id}:verify-claims",
            headers=headers(),
        )
        assert rejected.status_code == 409, rejected.text
        assert "不属于当前 Workspace" in rejected.json()["error"]["message"]
    finally:
        cleanup(workspace_ids, [], [])


def test_duplicate_verified_content_across_hosts_does_not_raise_claim_confidence(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_ids: list[str] = []
    workflow_ids: list[str] = []
    job_ids: list[str] = []
    try:
        workspace_id = create_workspace(client)
        workspace_ids.append(workspace_id)
        question_id = create_question(client, workspace_id)
        duplicate_body = (
            "The named buyer announced a procurement programme for the target category in 2026."
        )
        source_one = create_evidence(
            client,
            workspace_id,
            "https://publisher-one.example.test/procurement",
            duplicate_body,
        )
        source_two = create_evidence(
            client,
            workspace_id,
            "https://publisher-two.example.org/republication",
            duplicate_body,
        )
        with org_session(UUID(ORG_A)) as session:
            rows = list(
                session.scalars(
                    select(Evidence).where(
                        Evidence.organization_id == UUID(ORG_A),
                        Evidence.id.in_([UUID(source_one), UUID(source_two)]),
                    )
                ).all()
            )
            assert len(rows) == 2
            assert rows[0].content_hash == rows[1].content_hash
            for row in rows:
                row.verification_status = "VERIFIED"

        answer_id = create_answer(
            client,
            workspace_id,
            question_id,
            [(source_one, "SUPPORTS"), (source_two, "SUPPORTS")],
        )
        gateway = FakeGateway(
            {
                "claims": [
                    {
                        "claim_text": "The named buyer announced a procurement programme for the target category in 2026.",
                        "claim_type": "FACT",
                        "scope": {"buyer": "Named buyer", "period": "2026"},
                        "reasoning": "Both provided URLs contain the same published text.",
                        "evidence_assessments": [
                            {
                                "evidence_id": source_one,
                                "relation_type": "SUPPORTS",
                                "rationale": "The source contains the publication text.",
                            },
                            {
                                "evidence_id": source_two,
                                "relation_type": "SUPPORTS",
                                "rationale": "The second URL reproduces the same publication text.",
                            },
                        ],
                    }
                ],
                "limitations": [],
            }
        )
        monkeypatch.setattr("app.workers.tasks.get_gateway", lambda: gateway)
        queued = client.post(
            f"/v1/market/workspaces/{workspace_id}/answers/{answer_id}:verify-claims",
            headers=headers(),
        )
        assert queued.status_code == 202, queued.text
        workflow_ids.append(queued.json()["workflow_id"])
        job_ids.append(queued.json()["job_id"])

        result = execute_job(queued.json()["job_id"])
        assert result["claim_count"] == 1
        response = client.get(
            f"/v1/market/workspaces/{workspace_id}/claim-verification-runs/{queued.json()['verification_run_id']}",
            headers=headers(),
        )
        assert response.status_code == 200, response.text
        claim = response.json()["claims"][0]
        assert claim["verification_verdict"] == "PROVISIONAL_SUPPORT"
        assert claim["verified_support_count"] == 2
        assert claim["distinct_host_count"] == 2
        assert claim["confidence_level"] == "LOW"
        assert claim["confirmed_independent_source_count"] == 0
        assert all(
            item["origin_assessment"] == "DUPLICATE_CONTENT"
            for item in claim["evidence"]
        )
        assert any("相同内容指纹" in item for item in claim["limitations"])
    finally:
        cleanup(workspace_ids, workflow_ids, job_ids)
