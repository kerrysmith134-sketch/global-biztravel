"""PostgreSQL acceptance tests for the durable Workspace research DAG scheduler."""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.market import MarketResearchQuestion, MarketWorkspace, MarketWorkspaceWorkflow
from app.models.reliability import BackgroundJob
from app.models.workflow import Evidence, WorkflowInstance
from app.services import job_service, market_research_dag_service
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
USER_A = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers(key: str | None = None) -> dict[str, str]:
    return {
        "X-Org-Id": ORG_A,
        "X-User-Id": USER_A,
        "Idempotency-Key": key or str(uuid.uuid4()),
    }


def test_research_dag_execution_releases_dependency_only_after_human_acceptance(
    client: TestClient,
) -> None:
    workspace_id: str | None = None
    execution_id: str | None = None
    schedule_job_id: str | None = None
    question_ids: list[str] = []
    answer_job_ids: list[str] = []
    try:
        workspace_response = client.post(
            "/v1/market/workspaces",
            headers=headers(),
            json={
                "name": f"DAG execution {uuid.uuid4().hex[:8]}",
                "objective": "验证研究图的持久化执行和人审依赖",
                "initial_context": "用户希望先核验市场基础事实，再评估下游渠道条件。",
            },
        )
        assert workspace_response.status_code == 201, workspace_response.text
        workspace_id = workspace_response.json()["id"]

        root = client.post(
            f"/v1/market/workspaces/{workspace_id}/questions",
            headers=headers(),
            json={
                "question": "核验目标市场的基本规模和公开事实",
                "rationale": "下游研究需要建立在可核验的市场背景之上",
                "priority": 1,
                "acceptance_criteria": [],
            },
        )
        assert root.status_code == 201, root.text
        root_id = root.json()["id"]
        question_ids.append(root_id)

        downstream = client.post(
            f"/v1/market/workspaces/{workspace_id}/questions",
            headers=headers(),
            json={
                "question": "基于市场事实评估可行的渠道结构",
                "rationale": "渠道判断依赖市场事实核验",
                "priority": 2,
                "acceptance_criteria": [],
            },
        )
        assert downstream.status_code == 201, downstream.text
        downstream_id = downstream.json()["id"]
        question_ids.append(downstream_id)

        edge = client.post(
            f"/v1/market/workspaces/{workspace_id}/questions/{downstream_id}/dependencies",
            headers=headers(),
            json={"depends_on_question_id": root_id},
        )
        assert edge.status_code == 201, edge.text

        evidence_response = client.post(
            f"/v1/market/workspaces/{workspace_id}/evidence",
            headers=headers(),
            json={
                "canonical_ref": "https://research.example.test/market-overview",
                "content": "公开来源显示，该市场存在持续的基础设施投资和明确的年度统计口径。",
                "kind": "WEB_SOURCE",
                "source": "test-source",
                "observation": "市场统计摘要",
                "payload": {"title": "Market overview"},
            },
        )
        assert evidence_response.status_code == 201, evidence_response.text
        evidence_id = evidence_response.json()["id"]

        started = client.post(
            f"/v1/market/workspaces/{workspace_id}/research-graph/executions",
            headers=headers(),
            json={
                "max_concurrency": 1,
                "max_questions": 2,
                "max_duration_minutes": 30,
            },
        )
        assert started.status_code == 202, started.text
        execution_id = started.json()["execution_id"]
        schedule_job_id = started.json()["job_id"]

        # Run the persisted scheduler handler's domain service once. It should queue
        # only the root node; the dependent node remains blocked on human acceptance.
        with org_session(UUID(ORG_A)) as session:
            scheduler_result = market_research_dag_service.run_scheduler(
                session, UUID(ORG_A), UUID(execution_id)
            )
            assert scheduler_result["state"] == "RUNNING"
            execution_workflow = session.get(WorkflowInstance, UUID(execution_id))
            assert execution_workflow is not None
            root_answer_workflow = session.scalars(
                select(WorkflowInstance).where(
                    WorkflowInstance.organization_id == UUID(ORG_A),
                    WorkflowInstance.workflow_type == "market_question_answer",
                    WorkflowInstance.payload["question_id"].as_string() == root_id,
                    WorkflowInstance.payload["dag_execution_workflow_id"].as_string() == execution_id,
                )
            ).one()
            root_answer_job_id = root_answer_workflow.job_id
            assert root_answer_job_id is not None
            answer_job_ids.append(str(root_answer_job_id))

            assert session.scalars(
                select(WorkflowInstance).where(
                    WorkflowInstance.organization_id == UUID(ORG_A),
                    WorkflowInstance.workflow_type == "market_question_answer",
                    WorkflowInstance.payload["question_id"].as_string() == downstream_id,
                    WorkflowInstance.payload["dag_execution_workflow_id"].as_string() == execution_id,
                )
            ).first() is None

            node_states, counts = market_research_dag_service._node_statuses(
                session, UUID(ORG_A), UUID(workspace_id), execution_workflow
            )
            state_by_question = {str(row["question_id"]): row["state"] for row in node_states}
            assert state_by_question[root_id] == "QUEUED"
            assert state_by_question[downstream_id] == "WAITING_FOR_DEPENDENCY"
            assert counts["active_count"] == 1

            # The answer queue created by the DAG is the existing answer-agent job.
            job_service.cancel(session, UUID(ORG_A), root_answer_job_id)

        # Store an evidence-backed draft and explicitly accept it. The review service
        # wakes the DAG in the same transaction; only now is the dependent node READY.
        answer_response = client.post(
            f"/v1/market/workspaces/{workspace_id}/questions/{root_id}/answers",
            headers=headers(),
            json={
                "answer_summary": "现有公开资料支持该市场具备明确的基础规模和持续投资活动。",
                "confidence_level": "MEDIUM",
                "evidence": [
                    {
                        "evidence_id": evidence_id,
                        "relation_type": "SUPPORTS",
                        "rationale": "来源摘要直接支持市场背景判断",
                    }
                ],
            },
        )
        assert answer_response.status_code == 201, answer_response.text
        answer_id = answer_response.json()["id"]
        accepted = client.post(
            f"/v1/market/workspaces/{workspace_id}/questions/{root_id}/answers/{answer_id}:accept",
            headers=headers(),
            json={"review_note": "已核对来源内容及其与研究问题的直接关联。"},
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["status"] == "ACCEPTED"

        # The post-review callback schedules another run. It must release the child
        # node, while the accepted parent remains complete and is never re-generated.
        with org_session(UUID(ORG_A)) as session:
            second_result = market_research_dag_service.run_scheduler(
                session, UUID(ORG_A), UUID(execution_id)
            )
            assert second_result["state"] == "RUNNING"
            child_workflow = session.scalars(
                select(WorkflowInstance).where(
                    WorkflowInstance.organization_id == UUID(ORG_A),
                    WorkflowInstance.workflow_type == "market_question_answer",
                    WorkflowInstance.payload["question_id"].as_string() == downstream_id,
                    WorkflowInstance.payload["dag_execution_workflow_id"].as_string() == execution_id,
                )
            ).one()
            assert child_workflow.job_id is not None
            answer_job_ids.append(str(child_workflow.job_id))

            child_job = session.get(BackgroundJob, child_workflow.job_id)
            assert child_job is not None
            assert child_job.status == "PENDING"

            refreshed_execution = session.get(WorkflowInstance, UUID(execution_id))
            assert refreshed_execution is not None
            node_states, counts = market_research_dag_service._node_statuses(
                session, UUID(ORG_A), UUID(workspace_id), refreshed_execution
            )
            state_by_question = {str(row["question_id"]): row["state"] for row in node_states}
            assert state_by_question[root_id] == "ANSWERED"
            assert state_by_question[downstream_id] == "QUEUED"
            assert counts["completed_count"] == 1
            assert counts["active_count"] == 1

        # Cancellation is scoped to children owned by this execution and only cancels
        # jobs that have not started. A running provider call is not falsely reported
        # as interrupted.
        cancelled = client.post(
            f"/v1/market/workspaces/{workspace_id}/research-graph/executions/{execution_id}:cancel",
            headers=headers(),
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["cancelled"] is True
        assert cancelled.json()["cancelled_jobs"] == 1
        assert cancelled.json()["running_jobs"] == 0

        detail = client.get(
            f"/v1/market/workspaces/{workspace_id}/research-graph/executions/{execution_id}",
            headers=headers(),
        )
        assert detail.status_code == 200, detail.text
        assert detail.json()["execution_state"] == "CANCELLED"
        assert detail.json()["workflow_status"] == "REJECTED"

        with org_session(UUID(ORG_A)) as session:
            child_job = session.get(BackgroundJob, UUID(answer_job_ids[-1]))
            assert child_job is not None
            assert child_job.status == "CANCELLED"

    finally:
        if workspace_id is not None:
            with org_session(UUID(ORG_A)) as session:
                linked_workflow_ids = list(
                    session.scalars(
                        select(MarketWorkspaceWorkflow.workflow_id).where(
                            MarketWorkspaceWorkflow.organization_id == UUID(ORG_A),
                            MarketWorkspaceWorkflow.workspace_id == UUID(workspace_id),
                        )
                    ).all()
                )
                all_workflow_ids = set(linked_workflow_ids)
                all_workflow_ids.add(UUID(execution_id)) if execution_id else None
                discovered_job_ids = list(
                    session.scalars(
                        select(WorkflowInstance.job_id).where(
                            WorkflowInstance.organization_id == UUID(ORG_A),
                            WorkflowInstance.id.in_(all_workflow_ids),
                            WorkflowInstance.job_id.is_not(None),
                        )
                    ).all()
                ) if all_workflow_ids else []
                all_job_ids = set(discovered_job_ids)
                if schedule_job_id:
                    all_job_ids.add(UUID(schedule_job_id))
                all_job_ids.update(UUID(item) for item in answer_job_ids)
                if all_job_ids:
                    session.execute(
                        delete(BackgroundJob).where(
                            BackgroundJob.organization_id == UUID(ORG_A),
                            BackgroundJob.id.in_(all_job_ids),
                        )
                    )
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
                if all_workflow_ids:
                    session.execute(
                        delete(WorkflowInstance).where(
                            WorkflowInstance.organization_id == UUID(ORG_A),
                            WorkflowInstance.id.in_(all_workflow_ids),
                        )
                    )
