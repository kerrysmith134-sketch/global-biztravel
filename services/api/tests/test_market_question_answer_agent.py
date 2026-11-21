"""PostgreSQL integration tests for the asynchronous market Research Answer Agent."""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from app.db import org_session
from app.llm.contracts import LLMResponse
from app.main import app
from app.models.identity import Organization
from app.models.market import MarketWorkspace
from app.models.reliability import BackgroundJob
from app.models.workflow import AgentRun, Evidence, WorkflowInstance
from app.services import job_service
from app.workers.tasks import handle_market_question_answer_draft
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
            "name": f"Answer agent {uuid.uuid4().hex[:8]}",
            "objective": "Generate a draft from evidence explicitly held by this Workspace.",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def create_question(client: TestClient, workspace_id: str) -> str:
    response = client.post(
        f"/v1/market/workspaces/{workspace_id}/questions",
        headers=headers(),
        json={"question": "What does the available evidence indicate about buyer demand?"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def create_evidence(
    client: TestClient, workspace_id: str, url: str, content: str
) -> str:
    response = client.post(
        f"/v1/market/workspaces/{workspace_id}/evidence",
        headers=headers(),
        json={"canonical_ref": url, "content": content, "kind": "WEB_SOURCE"},
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
            model="test-answer-model",
            degraded=False,
        )


def execute_job(job_id: str) -> dict:
    with org_session(UUID(ORG_A)) as session:
        job = session.get(BackgroundJob, UUID(job_id))
        assert job is not None
        job.status = "RUNNING"
        job.locked_by = "market-question-answer-test-worker"
        result = handle_market_question_answer_draft(session, job)
        job_service.complete(session, job.id, result=result)
        return result


def cleanup(
    workspace_ids: list[str], workflow_ids: list[str], job_ids: list[str]
) -> None:
    with org_session(UUID(ORG_A)) as session:
        if workspace_ids:
            session.execute(
                delete(MarketWorkspace).where(
                    MarketWorkspace.organization_id == UUID(ORG_A),
                    MarketWorkspace.id.in_([UUID(item) for item in workspace_ids]),
                )
            )
            session.execute(
                delete(Evidence).where(
                    Evidence.organization_id == UUID(ORG_A),
                    Evidence.subject_type == "market_workspace",
                    Evidence.subject_id.in_([UUID(item) for item in workspace_ids]),
                )
            )
        if workflow_ids:
            workflow_uuids = [UUID(item) for item in workflow_ids]
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
        if job_ids:
            session.execute(
                delete(BackgroundJob).where(
                    BackgroundJob.organization_id == UUID(ORG_A),
                    BackgroundJob.id.in_([UUID(item) for item in job_ids]),
                )
            )


def test_answer_agent_drafts_cited_answer_and_never_auto_accepts(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_ids: list[str] = []
    workflow_ids: list[str] = []
    job_ids: list[str] = []
    try:
        workspace_id = create_workspace(client)
        workspace_ids.append(workspace_id)
        question_id = create_question(client, workspace_id)
        first_evidence = create_evidence(
            client,
            workspace_id,
            "https://buyer.example.test/procurement",
            "A buyer published a procurement plan with an expected purchase window.",
        )
        second_evidence = create_evidence(
            client,
            workspace_id,
            "https://industry.example.test/demand",
            "An industry report describes an increase in purchasing enquiries.",
        )
        model_output = {
            "answer_summary": (
                "The available materials indicate a demand signal, but they do not establish "
                "a funded or approved procurement decision."
            ),
            "assumptions": ["Both sources refer to the same broad purchasing category."],
            "unknowns": ["Approved budget", "Final purchasing decision"],
            "confidence_level": "HIGH",
            "evidence_assessments": [
                {
                    "evidence_id": first_evidence,
                    "relation_type": "SUPPORTS",
                    "rationale": "The buyer published a specific procurement plan.",
                },
                {
                    "evidence_id": second_evidence,
                    "relation_type": "CONTEXT",
                    "rationale": "The report provides broader demand context.",
                },
                {
                    "evidence_id": str(uuid.uuid4()),
                    "relation_type": "SUPPORTS",
                    "rationale": "This ID was not supplied to the model.",
                },
            ],
            "evidence_gap": "Budget approval and final procurement confirmation remain unknown.",
        }
        gateway = FakeGateway(model_output)
        monkeypatch.setattr("app.workers.tasks.get_gateway", lambda: gateway)

        queued = client.post(
            f"/v1/market/workspaces/{workspace_id}/questions/{question_id}:draft-answer",
            headers=headers(),
            json={},
        )
        assert queued.status_code == 202, queued.text
        queued_body = queued.json()
        workflow_ids.append(queued_body["workflow_id"])
        job_ids.append(queued_body["job_id"])
        assert queued_body["evidence_count"] == 2
        assert queued_body["reused"] is False

        replay = client.post(
            f"/v1/market/workspaces/{workspace_id}/questions/{question_id}:draft-answer",
            headers=headers(),
            json={},
        )
        assert replay.status_code == 202, replay.text
        assert replay.json()["job_id"] == queued_body["job_id"]
        assert replay.json()["reused"] is True

        result = execute_job(queued_body["job_id"])
        assert result["answer_status"] == "NEEDS_REVIEW"
        assert result["linked_evidence_count"] == 2
        assert result["degraded"] is False

        answers = client.get(
            f"/v1/market/workspaces/{workspace_id}/questions/{question_id}/answers",
            headers=headers(),
        )
        assert answers.status_code == 200, answers.text
        assert len(answers.json()) == 1
        answer = answers.json()[0]
        assert answer["status"] == "NEEDS_REVIEW"
        assert answer["confidence_level"] == "MEDIUM"
        assert answer["generated_by_run_id"] == result["agent_run_id"]
        assert {item["evidence"]["id"] for item in answer["evidence"]} == {
            first_evidence,
            second_evidence,
        }
        assert any("未能映射" in item for item in answer["unknowns"])
        assert answer["reviewed_by_user_id"] is None
        assert answer["reviewed_at"] is None

        questions = client.get(
            f"/v1/market/workspaces/{workspace_id}/questions", headers=headers()
        )
        question = next(
            row for row in questions.json()["items"] if row["id"] == question_id
        )
        assert question["status"] == "OPEN"
        assert question["answer_summary"] is None

        assert len(gateway.requests) == 1
        request = gateway.requests[0]["request"]
        assert request.prompt_template_id == "market_question_answer"
        assert request.prompt_version == "v1"
        assert request.variables["question_id"] == question_id
        assert len(request.variables["evidence"]) == 2
        assert "不可信" in request.system

        with org_session(UUID(ORG_A)) as session:
            run = session.get(AgentRun, UUID(result["agent_run_id"]))
            assert run is not None
            assert run.status == "SUCCEEDED"
            assert run.output["llm_invocation_id"] == str(
                UUID("99999999-9999-4999-8999-999999999999")
            )
            workflow = session.get(WorkflowInstance, UUID(queued_body["workflow_id"]))
            assert workflow is not None
            assert workflow.status == "COMPLETED"
            assert workflow.payload["answer_id"] == answer["id"]
    finally:
        cleanup(workspace_ids, workflow_ids, job_ids)


def test_answer_agent_without_evidence_records_explicit_gap(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_ids: list[str] = []
    workflow_ids: list[str] = []
    job_ids: list[str] = []
    try:
        workspace_id = create_workspace(client)
        workspace_ids.append(workspace_id)
        question_id = create_question(client, workspace_id)
        gateway = FakeGateway(
            {
                "answer_summary": "Fabricated unsupported claim.",
                "assumptions": [],
                "unknowns": [],
                "confidence_level": "HIGH",
                "evidence_assessments": [],
                "evidence_gap": "",
            }
        )
        monkeypatch.setattr("app.workers.tasks.get_gateway", lambda: gateway)

        queued = client.post(
            f"/v1/market/workspaces/{workspace_id}/questions/{question_id}:draft-answer",
            headers=headers(),
            json={},
        )
        assert queued.status_code == 202, queued.text
        workflow_ids.append(queued.json()["workflow_id"])
        job_ids.append(queued.json()["job_id"])
        assert queued.json()["evidence_count"] == 0

        result = execute_job(queued.json()["job_id"])
        answers = client.get(
            f"/v1/market/workspaces/{workspace_id}/questions/{question_id}/answers",
            headers=headers(),
        )
        answer = answers.json()[0]
        assert result["answer_status"] == "NEEDS_REVIEW"
        assert answer["confidence_level"] == "INSUFFICIENT"
        assert answer["evidence"] == []
        assert "尚无可引用" in answer["answer_summary"]
        assert answer["evidence_gap"]
        assert answer["unknowns"]
        assert gateway.requests
    finally:
        cleanup(workspace_ids, workflow_ids, job_ids)
