"""PostgreSQL acceptance tests for the durable Workspace research DAG scheduler."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.db import org_session
from app.llm.contracts import LLMResponse
from app.main import app
from app.models.market import (
    MarketEvidenceDerivation,
    MarketResearchPlanEvidence,
    MarketWorkspace,
    MarketWorkspaceWorkflow,
)
from app.models.reliability import BackgroundJob
from app.models.workflow import AgentRun, Evidence, WorkflowInstance
from app.services import (
    job_service,
    market_insight_service,
    market_research_dag_service,
)
from app.workers import main as workers_main
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

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


def _claim_and_execute(job_id: str, worker_id: str) -> bool:
    """Claim one exact test job, then pass it through the production Worker executor."""
    with org_session(UUID(ORG_A)) as session:
        job = session.scalars(
            select(BackgroundJob).where(
                BackgroundJob.organization_id == UUID(ORG_A),
                BackgroundJob.id == UUID(job_id),
            ).with_for_update()
        ).one()
        assert job.status == "PENDING"
        now = session.scalar(select(func.clock_timestamp()))
        job.status = "RUNNING"
        job.attempts += 1
        job.locked_at = now
        job.locked_by = worker_id
        job.heartbeat_at = now
        job.lease_expires_at = now + timedelta(seconds=60)
        if job.started_at is None:
            job.started_at = now
        job.last_error = None
    return workers_main.execute_one(
        ORG_A,
        job_id,
        worker_id=worker_id,
        lease_seconds=60,
    )


def test_research_dag_execution_releases_dependency_only_after_human_acceptance(
    client: TestClient,
) -> None:
    workspace_id: str | None = None
    execution_id: str | None = None
    schedule_job_id: str | None = None
    deadline_job_id: str | None = None
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
        with org_session(UUID(ORG_A)) as session:
            deadline_job = session.scalars(
                select(BackgroundJob).where(
                    BackgroundJob.organization_id == UUID(ORG_A),
                    BackgroundJob.dedupe_key == f"market_research_dag_deadline:{execution_id}",
                )
            ).one()
            deadline_job_id = str(deadline_job.id)
            assert deadline_job.status == "PENDING"

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

        with org_session(UUID(ORG_A)) as session:
            assert deadline_job_id is not None
            deadline_job = session.get(BackgroundJob, UUID(deadline_job_id))
            assert deadline_job is not None
            assert deadline_job.status == "CANCELLED"

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
                if execution_id:
                    all_workflow_ids.add(UUID(execution_id))
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
                if execution_id:
                    all_job_ids.update(
                        session.scalars(
                            select(BackgroundJob.id).where(
                                BackgroundJob.organization_id == UUID(ORG_A),
                                BackgroundJob.dedupe_key == f"market_research_dag_deadline:{execution_id}",
                            )
                        ).all()
                    )
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



def test_answer_worker_completion_wakes_dag_and_waits_for_human_review(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id: str | None = None
    execution_id: str | None = None
    job_ids: list[str] = []
    try:
        workspace = client.post(
            "/v1/market/workspaces",
            headers=headers(),
            json={
                "name": f"Worker wakeup {uuid.uuid4().hex[:8]}",
                "objective": "Verify the real Worker-to-DAG wakeup lifecycle.",
            },
        )
        assert workspace.status_code == 201, workspace.text
        workspace_id = workspace.json()["id"]

        question = client.post(
            f"/v1/market/workspaces/{workspace_id}/questions",
            headers=headers(),
            json={
                "question": "What does the verified market evidence indicate?",
                "rationale": "A draft must be prepared from Workspace evidence and reviewed by a person.",
            },
        )
        assert question.status_code == 201, question.text
        question_id = question.json()["id"]

        evidence = client.post(
            f"/v1/market/workspaces/{workspace_id}/evidence",
            headers=headers(),
            json={
                "canonical_ref": "https://worker-wakeup.example.test/market",
                "content": "The published report documents sustained investment and a measurable buyer demand signal.",
                "kind": "WEB_SOURCE",
                "source": "worker-wakeup-test",
                "observation": "Investment and buyer demand",
            },
        )
        assert evidence.status_code == 201, evidence.text
        evidence_id = evidence.json()["id"]

        class FakeGateway:
            def complete(
                self,
                _session,
                _organization_id,
                _request,
                *,
                trace_id=None,
            ):
                return LLMResponse(
                    output={
                        "answer_summary": (
                            "The supplied source indicates sustained investment and a buyer demand signal."
                        ),
                        "assumptions": [],
                        "unknowns": ["The final approved procurement budget is not established."],
                        "confidence_level": "MEDIUM",
                        "evidence_assessments": [
                            {
                                "evidence_id": evidence_id,
                                "relation_type": "SUPPORTS",
                                "rationale": "The source directly describes investment and demand.",
                            }
                        ],
                        "evidence_gap": "Budget approval and procurement timing still require confirmation.",
                    },
                    invocation_id=uuid.uuid4(),
                    model="test-market-answer-model",
                    degraded=False,
                )

        monkeypatch.setattr("app.workers.tasks.get_gateway", lambda: FakeGateway())

        started = client.post(
            f"/v1/market/workspaces/{workspace_id}/research-graph/executions",
            headers=headers(),
            json={
                "max_concurrency": 1,
                "max_questions": 1,
                "max_duration_minutes": 30,
            },
        )
        assert started.status_code == 202, started.text
        execution_id = started.json()["execution_id"]
        initial_schedule_job_id = started.json()["job_id"]
        job_ids.append(initial_schedule_job_id)

        # Execute the actual persisted DAG scheduler BackgroundJob via Worker.main.
        assert _claim_and_execute(initial_schedule_job_id, "dag-scheduler-worker")
        with org_session(UUID(ORG_A)) as session:
            answer_workflow = session.scalars(
                select(WorkflowInstance).where(
                    WorkflowInstance.organization_id == UUID(ORG_A),
                    WorkflowInstance.workflow_type == "market_question_answer",
                    WorkflowInstance.payload["question_id"].as_string() == question_id,
                    WorkflowInstance.payload["dag_execution_workflow_id"].as_string() == execution_id,
                )
            ).one()
            assert answer_workflow.job_id is not None
            answer_job_id = str(answer_workflow.job_id)
            job_ids.append(answer_job_id)
            answer_job = session.get(BackgroundJob, answer_workflow.job_id)
            assert answer_job is not None and answer_job.status == "PENDING"

        # The Answer Agent runs through the production task handler and Worker lease path.
        assert _claim_and_execute(answer_job_id, "dag-answer-worker")

        # Successful answer generation must enqueue a fresh parent scheduler run.
        with org_session(UUID(ORG_A)) as session:
            wake_job = session.scalars(
                select(BackgroundJob).where(
                    BackgroundJob.organization_id == UUID(ORG_A),
                    BackgroundJob.job_type == job_service.JOB_TYPE_MARKET_RESEARCH_DAG_SCHEDULE,
                    BackgroundJob.dedupe_key == f"market_research_dag_schedule:{execution_id}",
                    BackgroundJob.status == "PENDING",
                )
            ).one()
            wake_job_id = str(wake_job.id)
            job_ids.append(wake_job_id)

        assert _claim_and_execute(wake_job_id, "dag-review-wakeup-worker")

        answer_list = client.get(
            f"/v1/market/workspaces/{workspace_id}/questions/{question_id}/answers",
            headers=headers(),
        )
        assert answer_list.status_code == 200, answer_list.text
        assert len(answer_list.json()) == 1
        assert answer_list.json()[0]["status"] == "NEEDS_REVIEW"

        execution = client.get(
            f"/v1/market/workspaces/{workspace_id}/research-graph/executions/{execution_id}",
            headers=headers(),
        )
        assert execution.status_code == 200, execution.text
        assert execution.json()["execution_state"] == "WAITING_FOR_REVIEW"
        assert execution.json()["waiting_review_count"] == 1
        assert execution.json()["active_count"] == 0

        # A wakeup must not re-enqueue the same node while its answer awaits human review.
        with org_session(UUID(ORG_A)) as session:
            answer_workflows = session.scalars(
                select(WorkflowInstance).where(
                    WorkflowInstance.organization_id == UUID(ORG_A),
                    WorkflowInstance.workflow_type == "market_question_answer",
                    WorkflowInstance.payload["question_id"].as_string() == question_id,
                    WorkflowInstance.payload["dag_execution_workflow_id"].as_string() == execution_id,
                )
            ).all()
            assert len(answer_workflows) == 1
            assert answer_workflows[0].status == "COMPLETED"

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
                if execution_id:
                    all_workflow_ids.add(UUID(execution_id))
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
                all_job_ids.update(UUID(item) for item in job_ids)
                if execution_id:
                    all_job_ids.update(
                        session.scalars(
                            select(BackgroundJob.id).where(
                                BackgroundJob.organization_id == UUID(ORG_A),
                                BackgroundJob.dedupe_key == f"market_research_dag_deadline:{execution_id}",
                            )
                        ).all()
                    )
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
                        delete(AgentRun).where(
                            AgentRun.organization_id == UUID(ORG_A),
                            AgentRun.workflow_id.in_(all_workflow_ids),
                        )
                    )
                    session.execute(
                        delete(WorkflowInstance).where(
                            WorkflowInstance.organization_id == UUID(ORG_A),
                            WorkflowInstance.id.in_(all_workflow_ids),
                        )
                    )


def test_idle_research_execution_is_stopped_by_persisted_deadline_job(
    client: TestClient,
) -> None:
    workspace_id: str | None = None
    execution_id: str | None = None
    job_ids: list[str] = []
    try:
        workspace = client.post(
            "/v1/market/workspaces",
            headers=headers(),
            json={
                "name": f"Deadline wakeup {uuid.uuid4().hex[:8]}",
                "objective": "An idle execution must expire without another user event.",
            },
        )
        assert workspace.status_code == 201, workspace.text
        workspace_id = workspace.json()["id"]

        question = client.post(
            f"/v1/market/workspaces/{workspace_id}/questions",
            headers=headers(),
            json={"question": "What public evidence is still needed to evaluate this market?"},
        )
        assert question.status_code == 201, question.text

        started = client.post(
            f"/v1/market/workspaces/{workspace_id}/research-graph/executions",
            headers=headers(),
            json={
                "max_concurrency": 1,
                "max_questions": 1,
                "max_duration_minutes": 1,
            },
        )
        assert started.status_code == 202, started.text
        execution_id = started.json()["execution_id"]
        job_ids.append(started.json()["job_id"])

        with org_session(UUID(ORG_A)) as session:
            deadline_job = session.scalars(
                select(BackgroundJob).where(
                    BackgroundJob.organization_id == UUID(ORG_A),
                    BackgroundJob.job_type == job_service.JOB_TYPE_MARKET_RESEARCH_DAG_SCHEDULE,
                    BackgroundJob.dedupe_key == f"market_research_dag_deadline:{execution_id}",
                    BackgroundJob.status == "PENDING",
                )
            ).one()
            deadline_job_id = str(deadline_job.id)
            job_ids.append(deadline_job_id)

            workflow = session.get(WorkflowInstance, UUID(execution_id))
            assert workflow is not None
            planned_deadline = datetime.fromisoformat(str(workflow.payload["deadline_at"]))
            assert abs((deadline_job.available_at - planned_deadline).total_seconds()) <= 1

            # Simulate the deadline passing while the execution is idle in WAITING_FOR_EVIDENCE.
            workflow.payload = {
                **(workflow.payload or {}),
                "deadline_at": (datetime.now(UTC) - timedelta(seconds=5)).isoformat(),
            }
            deadline_job.available_at = session.scalar(select(func.clock_timestamp()))

        assert _claim_and_execute(deadline_job_id, "dag-deadline-worker")

        detail = client.get(
            f"/v1/market/workspaces/{workspace_id}/research-graph/executions/{execution_id}",
            headers=headers(),
        )
        assert detail.status_code == 200, detail.text
        assert detail.json()["execution_state"] == "TIMED_OUT"
        assert detail.json()["workflow_status"] == "FAILED"

        with org_session(UUID(ORG_A)) as session:
            child_workflows = session.scalars(
                select(WorkflowInstance).where(
                    WorkflowInstance.organization_id == UUID(ORG_A),
                    WorkflowInstance.workflow_type == "market_question_answer",
                    WorkflowInstance.payload["dag_execution_workflow_id"].as_string() == execution_id,
                )
            ).all()
            assert child_workflows == []

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
                if execution_id:
                    all_workflow_ids.add(UUID(execution_id))
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
                all_job_ids.update(UUID(item) for item in job_ids)
                if execution_id:
                    all_job_ids.update(
                        session.scalars(
                            select(BackgroundJob.id).where(
                                BackgroundJob.organization_id == UUID(ORG_A),
                                BackgroundJob.dedupe_key == f"market_research_dag_deadline:{execution_id}",
                            )
                        ).all()
                    )
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
                        delete(AgentRun).where(
                            AgentRun.organization_id == UUID(ORG_A),
                            AgentRun.workflow_id.in_(all_workflow_ids),
                        )
                    )
                    session.execute(
                        delete(WorkflowInstance).where(
                            WorkflowInstance.organization_id == UUID(ORG_A),
                            WorkflowInstance.id.in_(all_workflow_ids),
                        )
                    )


def test_question_scoped_evidence_does_not_leak_between_research_questions(
    client: TestClient,
) -> None:
    workspace_id: str | None = None
    workflow_ids: list[UUID] = []
    evidence_ids: list[UUID] = []
    try:
        workspace_response = client.post(
            "/v1/market/workspaces",
            headers=headers(),
            json={
                "name": f"Question evidence scope {uuid.uuid4().hex[:8]}",
                "objective": "验证问题级证据隔离和网页正文血缘",
                "initial_context": "两个研究问题分别需要不同的来源证据。",
            },
        )
        assert workspace_response.status_code == 201, workspace_response.text
        workspace_id = workspace_response.json()["id"]

        question_ids: list[str] = []
        for question_text in (
            "核验目标市场的法规要求",
            "核验目标市场的渠道结构",
        ):
            created = client.post(
                f"/v1/market/workspaces/{workspace_id}/questions",
                headers=headers(),
                json={
                    "question": question_text,
                    "rationale": "问题级来源不得跨节点污染",
                    "priority": 1,
                    "acceptance_criteria": [],
                },
            )
            assert created.status_code == 201, created.text
            question_ids.append(created.json()["id"])
        q1, q2 = (UUID(item) for item in question_ids)

        with org_session(UUID(ORG_A)) as session:
            plan_workflows: dict[UUID, WorkflowInstance] = {}
            plan_runs: dict[UUID, AgentRun] = {}
            for question_id in (q1, q2):
                plan = WorkflowInstance(
                    organization_id=UUID(ORG_A),
                    workflow_type="research_plan",
                    status="COMPLETED",
                    payload={
                        "workspace_id": workspace_id,
                        "target_question_id": str(question_id),
                        "retrieval_status": "COMPLETED",
                        "request": f"question scoped plan for {question_id}",
                        "requested_scope": "EXTERNAL_ONLY",
                        "recommended_scope": "EXTERNAL_ONLY",
                        "plan": {
                            "intent_summary": "Question scoped evidence test",
                            "objective": "Verify evidence visibility for one question",
                            "subject": "A single Workspace research question",
                            "external_research_needed": True,
                        },
                        "planner": {
                            "agent_run_id": "00000000-0000-0000-0000-000000000001",
                            "model": "test-planner",
                        },
                    },
                    created_by_user_id=UUID(USER_A),
                )
                session.add(plan)
                session.flush()
                plan_workflows[question_id] = plan
                session.add(
                    MarketWorkspaceWorkflow(
                        organization_id=UUID(ORG_A),
                        workspace_id=UUID(workspace_id),
                        workflow_id=plan.id,
                        linked_by_user_id=UUID(USER_A),
                    )
                )
                run = AgentRun(
                    organization_id=UUID(ORG_A),
                    workflow_id=plan.id,
                    agent_type="research_executor_test",
                    status="SUCCEEDED",
                    input={"target_question_id": str(question_id)},
                    output={"provider": "test"},
                    finished_at=None,
                )
                session.add(run)
                session.flush()
                plan_runs[question_id] = run
                workflow_ids.append(plan.id)

            def record(
                canonical_ref: str,
                body: str,
                *,
                managed: bool | None,
                target_question_id: UUID | None = None,
            ) -> Evidence:
                payload = {"content": body}
                if managed is not None:
                    payload["question_scope_managed"] = managed
                if target_question_id is not None:
                    payload["target_question_id"] = str(target_question_id)
                row = market_insight_service.record_market_evidence(
                    session,
                    UUID(ORG_A),
                    UUID(workspace_id),
                    canonical_ref=canonical_ref,
                    content=body,
                    kind="WEB_SEARCH_RESULT",
                    source="question-scope-test",
                    published_at=None,
                    observation=body,
                    verification_status="UNVERIFIED",
                    quality=None,
                    payload=payload,
                    actor_user_id=None,
                )
                evidence_ids.append(row.id)
                return row

            q1_source = record(
                "https://scope.example.test/q1",
                "Q1 专属法规来源摘要。",
                managed=True,
                target_question_id=q1,
            )
            q2_source = record(
                "https://scope.example.test/q2",
                "Q2 专属渠道来源摘要。",
                managed=True,
                target_question_id=q2,
            )
            shared_source = record(
                "https://scope.example.test/shared",
                "用户主动添加的 Workspace 共享背景资料。",
                managed=False,
            )
            legacy_shared_source = record(
                "https://scope.example.test/legacy-shared",
                "旧版本未包含 question_scope_managed 字段的共享来源。",
                managed=None,
            )

            market_insight_service.link_research_plan_evidence(
                session,
                UUID(ORG_A),
                UUID(workspace_id),
                plan_workflows[q1].id,
                q1_source.id,
                search_query="Q1 regulations",
                collected_by_run_id=plan_runs[q1].id,
                question_id=q1,
            )
            market_insight_service.link_research_plan_evidence(
                session,
                UUID(ORG_A),
                UUID(workspace_id),
                plan_workflows[q2].id,
                q2_source.id,
                search_query="Q2 channels",
                collected_by_run_id=plan_runs[q2].id,
                question_id=q2,
            )

            # Legacy ResearchPlan-subject Evidence remains available only to its
            # own target question (or all questions for an old untargeted plan).
            q1_legacy = Evidence(
                organization_id=UUID(ORG_A),
                subject_type="RESEARCH_PLAN",
                subject_id=plan_workflows[q1].id,
                kind="LEGACY_RESEARCH_BODY",
                ref=f"legacy:{uuid.uuid4()}",
                source="legacy-test",
                payload={"content": "旧版 Q1 研究计划来源正文。"},
            )
            q2_legacy = Evidence(
                organization_id=UUID(ORG_A),
                subject_type="RESEARCH_PLAN",
                subject_id=plan_workflows[q2].id,
                kind="LEGACY_RESEARCH_BODY",
                ref=f"legacy:{uuid.uuid4()}",
                source="legacy-test",
                payload={"content": "旧版 Q2 研究计划来源正文。"},
            )
            session.add_all([q1_legacy, q2_legacy])
            session.flush()
            evidence_ids.extend([q1_legacy.id, q2_legacy.id])

            q1_visible = set(
                session.scalars(
                    select(Evidence.id).where(
                        market_insight_service.question_evidence_scope(
                            UUID(ORG_A),
                            UUID(workspace_id),
                            q1,
                            [plan_workflows[q1].id, plan_workflows[q2].id],
                        )
                    )
                ).all()
            )
            q2_visible = set(
                session.scalars(
                    select(Evidence.id).where(
                        market_insight_service.question_evidence_scope(
                            UUID(ORG_A),
                            UUID(workspace_id),
                            q2,
                            [plan_workflows[q1].id, plan_workflows[q2].id],
                        )
                    )
                ).all()
            )
            assert q1_visible == {
                q1_source.id, shared_source.id, legacy_shared_source.id, q1_legacy.id
            }
            assert q2_visible == {
                q2_source.id, shared_source.id, legacy_shared_source.id, q2_legacy.id
            }
            assert q2_source.id not in q1_visible
            assert q1_source.id not in q2_visible

            # The DAG scheduler uses the same question scope when deriving READY /
            # WAITING_FOR_EVIDENCE, not a Workspace-wide evidence count.
            q1_scheduler_evidence = set(
                market_research_dag_service._content_evidence_ids(
                    session, UUID(ORG_A), UUID(workspace_id), q1
                )
            )
            q2_scheduler_evidence = set(
                market_research_dag_service._content_evidence_ids(
                    session, UUID(ORG_A), UUID(workspace_id), q2
                )
            )
            assert q1_scheduler_evidence == q1_visible
            assert q2_scheduler_evidence == q2_visible

            # A user-selected page extraction creates a new immutable Evidence
            # version and inherits the exact question/query provenance.
            extraction_workflow = WorkflowInstance(
                organization_id=UUID(ORG_A),
                workflow_type="market_source_extraction",
                status="COMPLETED",
                payload={"workspace_id": workspace_id, "source_evidence_id": str(q1_source.id)},
                created_by_user_id=UUID(USER_A),
            )
            session.add(extraction_workflow)
            session.flush()
            workflow_ids.append(extraction_workflow.id)
            session.add(
                MarketWorkspaceWorkflow(
                    organization_id=UUID(ORG_A),
                    workspace_id=UUID(workspace_id),
                    workflow_id=extraction_workflow.id,
                    linked_by_user_id=UUID(USER_A),
                )
            )
            extraction_run = AgentRun(
                organization_id=UUID(ORG_A),
                workflow_id=extraction_workflow.id,
                agent_type="market_source_page_extractor",
                status="SUCCEEDED",
                input={"source_evidence_id": str(q1_source.id)},
                output={"verification_status": "UNVERIFIED"},
            )
            session.add(extraction_run)
            session.flush()

            derived = market_insight_service.record_market_evidence(
                session,
                UUID(ORG_A),
                UUID(workspace_id),
                canonical_ref="https://scope.example.test/q1/full-page",
                content="Q1 网页正文中可复核的法规段落。",
                kind="WEB_PAGE_EXTRACT",
                source="safe-http-test",
                published_at=None,
                observation="Q1 网页正文",
                verification_status="UNVERIFIED",
                quality=None,
                payload={
                    "content": "Q1 网页正文中可复核的法规段落。",
                    "question_scope_managed": True,
                    "target_question_id": str(q1),
                    "derived_from_evidence_id": str(q1_source.id),
                },
                actor_user_id=None,
            )
            evidence_ids.append(derived.id)
            session.add(
                MarketEvidenceDerivation(
                    organization_id=UUID(ORG_A),
                    workspace_id=UUID(workspace_id),
                    source_evidence_id=q1_source.id,
                    derived_evidence_id=derived.id,
                    workflow_id=extraction_workflow.id,
                    agent_run_id=extraction_run.id,
                    derivation_type="WEB_PAGE_TEXT_EXTRACTION",
                    extractor_version="scope-test-v1",
                    source_url="https://scope.example.test/q1/full-page",
                    content_hash=derived.content_hash,
                )
            )
            session.flush()
            copied = market_insight_service.inherit_research_plan_evidence_links(
                session,
                UUID(ORG_A),
                UUID(workspace_id),
                q1_source.id,
                derived.id,
                extraction_workflow_id=extraction_workflow.id,
                extraction_agent_run_id=extraction_run.id,
            )
            assert copied == 1
            inherited_link = session.scalars(
                select(MarketResearchPlanEvidence).where(
                    MarketResearchPlanEvidence.organization_id == UUID(ORG_A),
                    MarketResearchPlanEvidence.workspace_id == UUID(workspace_id),
                    MarketResearchPlanEvidence.research_plan_id == plan_workflows[q1].id,
                    MarketResearchPlanEvidence.evidence_id == derived.id,
                    MarketResearchPlanEvidence.question_id == q1,
                )
            ).one()
            assert inherited_link.collected_by_run_id == plan_runs[q1].id
            assert inherited_link.collected_by_run_id != extraction_run.id

            q1_after_extract = set(
                session.scalars(
                    select(Evidence.id).where(
                        market_insight_service.question_evidence_scope(
                            UUID(ORG_A),
                            UUID(workspace_id),
                            q1,
                            [plan_workflows[q1].id, plan_workflows[q2].id, extraction_workflow.id],
                        )
                    )
                ).all()
            )
            q2_after_extract = set(
                session.scalars(
                    select(Evidence.id).where(
                        market_insight_service.question_evidence_scope(
                            UUID(ORG_A),
                            UUID(workspace_id),
                            q2,
                            [plan_workflows[q1].id, plan_workflows[q2].id, extraction_workflow.id],
                        )
                    )
                ).all()
            )
            assert derived.id in q1_after_extract
            assert derived.id not in q2_after_extract

        # A question-targeted ResearchPlan must not recursively create more DAG nodes.
        for question_id in question_ids:
            planned = client.post(
                "/v1/research/plans",
                headers=headers(),
                json={
                    "request": f"研究该问题并寻找可核验来源：{question_id}",
                    "source_scope": "EXTERNAL_ONLY",
                    "workspace_id": workspace_id,
                    "target_question_id": question_id,
                },
            )
            assert planned.status_code == 201, planned.text
            assert planned.json()["target_question_id"] == question_id
            assert planned.json()["generated_question_ids"] == []
            workflow_ids.append(UUID(planned.json()["id"]))

        listed = client.get(
            f"/v1/market/workspaces/{workspace_id}/research-plans",
            headers=headers(),
            params={"target_question_id": question_ids[0]},
        )
        assert listed.status_code == 200, listed.text
        assert all(
            item["target_question_id"] == question_ids[0]
            for item in listed.json()
        )

    finally:
        if workspace_id is not None:
            with org_session(UUID(ORG_A)) as session:
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
                            AgentRun.workflow_id.in_(set(workflow_ids)),
                        )
                    )
                    session.execute(
                        delete(WorkflowInstance).where(
                            WorkflowInstance.organization_id == UUID(ORG_A),
                            WorkflowInstance.id.in_(set(workflow_ids)),
                        )
                    )
                if evidence_ids:
                    session.execute(
                        delete(Evidence).where(
                            Evidence.organization_id == UUID(ORG_A),
                            Evidence.id.in_(set(evidence_ids)),
                        )
                    )
