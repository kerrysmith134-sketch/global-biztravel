"""Durable scheduler for evidence-backed Workspace Research DAGs.

The execution aggregate is an existing WorkflowInstance. Each question continues to
run through the existing answer-draft Workflow, BackgroundJob, AgentRun and Evidence
models; this module coordinates them without creating another queue/runtime.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.api.errors import Conflict, NotFound, Unprocessable
from app.config import get_settings
from app.models.llm import LLMInvocation
from app.models.market import (
    MarketActivityEvent,
    MarketResearchQuestion,
    MarketResearchQuestionAnswer,
    MarketWorkspace,
    MarketWorkspaceWorkflow,
)
from app.models.reliability import BackgroundJob
from app.models.workflow import AgentRun, Evidence, WorkflowInstance
from app.services import job_service, market_question_answer_agent_service, market_research_graph_service

WORKFLOW_TYPE = "market_research_graph_execution"
ANSWER_WORKFLOW_TYPE = market_question_answer_agent_service.WORKFLOW_TYPE
MAX_EVIDENCE_PER_QUESTION = market_question_answer_agent_service.MAX_EVIDENCE_PER_DRAFT


def _digest(value: dict[str, Any]) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _execution_workflow(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    execution_id: UUID,
    *,
    lock: bool = False,
) -> WorkflowInstance:
    statement = (
        select(WorkflowInstance)
        .join(
            MarketWorkspaceWorkflow,
            and_(
                MarketWorkspaceWorkflow.organization_id == WorkflowInstance.organization_id,
                MarketWorkspaceWorkflow.workflow_id == WorkflowInstance.id,
            ),
        )
        .where(
            WorkflowInstance.organization_id == organization_id,
            WorkflowInstance.id == execution_id,
            WorkflowInstance.workflow_type == WORKFLOW_TYPE,
            MarketWorkspaceWorkflow.workspace_id == workspace_id,
        )
    )
    if lock:
        statement = statement.with_for_update()
    workflow = session.scalars(statement).one_or_none()
    if workflow is None:
        raise NotFound("市场研究图执行不存在", details={"execution_id": str(execution_id)})
    return workflow


def _workspace(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    *,
    lock: bool = False,
) -> MarketWorkspace:
    statement = select(MarketWorkspace).where(
        MarketWorkspace.organization_id == organization_id,
        MarketWorkspace.id == workspace_id,
    )
    if lock:
        statement = statement.with_for_update()
    row = session.scalars(statement).one_or_none()
    if row is None:
        raise NotFound("市场工作空间不存在", details={"workspace_id": str(workspace_id)})
    return row


def _linked_workflows(
    session: Session, organization_id: UUID | str, workspace_id: UUID
) -> list[WorkflowInstance]:
    ids = list(
        session.scalars(
            select(MarketWorkspaceWorkflow.workflow_id).where(
                MarketWorkspaceWorkflow.organization_id == organization_id,
                MarketWorkspaceWorkflow.workspace_id == workspace_id,
            )
        ).all()
    )
    if not ids:
        return []
    return list(
        session.scalars(
            select(WorkflowInstance)
            .where(
                WorkflowInstance.organization_id == organization_id,
                WorkflowInstance.id.in_(ids),
            )
            .order_by(WorkflowInstance.created_at.desc())
        ).all()
    )


def _content_evidence_ids(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
) -> list[UUID]:
    linked_ids = list(
        session.scalars(
            select(MarketWorkspaceWorkflow.workflow_id).where(
                MarketWorkspaceWorkflow.organization_id == organization_id,
                MarketWorkspaceWorkflow.workspace_id == workspace_id,
            )
        ).all()
    )
    workspace_scope = and_(
        Evidence.subject_type == "market_workspace",
        Evidence.subject_id == workspace_id,
    )
    scope = and_(
        Evidence.organization_id == organization_id,
        or_(
            workspace_scope,
            and_(
                Evidence.subject_type == "RESEARCH_PLAN",
                Evidence.subject_id.in_(linked_ids) if linked_ids else Evidence.subject_id.is_(None),
            ),
        ),
    )
    rows = session.scalars(
        select(Evidence.id)
        .where(
            scope,
            Evidence.payload["content"].as_string().is_not(None),
            func.length(func.btrim(Evidence.payload["content"].as_string())) > 0,
        )
        .order_by(
            Evidence.retrieved_at.desc().nullslast(),
            Evidence.created_at.desc(),
            Evidence.id,
        )
        .limit(MAX_EVIDENCE_PER_QUESTION)
    ).all()
    return list(rows)


def _node_statuses(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    execution: WorkflowInstance,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = execution.payload or {}
    snapshot = payload.get("graph_snapshot") or {}
    nodes = snapshot.get("nodes") or []
    edges = snapshot.get("edges") or []
    question_ids = [UUID(str(node["id"])) for node in nodes if node.get("id")]

    questions = list(
        session.scalars(
            select(MarketResearchQuestion).where(
                MarketResearchQuestion.organization_id == organization_id,
                MarketResearchQuestion.workspace_id == workspace_id,
                MarketResearchQuestion.id.in_(question_ids),
            )
        ).all()
    ) if question_ids else []
    question_by_id = {row.id: row for row in questions}

    answers = list(
        session.scalars(
            select(MarketResearchQuestionAnswer)
            .where(
                MarketResearchQuestionAnswer.organization_id == organization_id,
                MarketResearchQuestionAnswer.workspace_id == workspace_id,
                MarketResearchQuestionAnswer.question_id.in_(question_ids),
            )
            .order_by(
                MarketResearchQuestionAnswer.question_id,
                MarketResearchQuestionAnswer.version.desc(),
                MarketResearchQuestionAnswer.created_at.desc(),
            )
        ).all()
    ) if question_ids else []
    latest_answer: dict[UUID, MarketResearchQuestionAnswer] = {}
    for answer in answers:
        latest_answer.setdefault(answer.question_id, answer)

    linked = _linked_workflows(session, organization_id, workspace_id)
    answer_workflows: dict[UUID, list[WorkflowInstance]] = {}
    for workflow in linked:
        data = workflow.payload or {}
        if workflow.workflow_type != ANSWER_WORKFLOW_TYPE:
            continue
        child_execution_id = data.get("dag_execution_workflow_id")
        if child_execution_id and str(child_execution_id) != str(execution.id):
            # Completed/failed answer jobs from an older DAG batch must not poison
            # the current batch's derived node state.
            continue
        if not data.get("question_id"):
            continue
        try:
            question_id = UUID(str(data["question_id"]))
        except ValueError:
            continue
        if question_id not in set(question_ids):
            continue
        answer_workflows.setdefault(question_id, []).append(workflow)

    job_ids = [
        workflow.job_id
        for rows in answer_workflows.values()
        for workflow in rows
        if workflow.job_id is not None
    ]
    jobs = list(
        session.scalars(
            select(BackgroundJob).where(
                BackgroundJob.organization_id == organization_id,
                BackgroundJob.id.in_(job_ids),
            )
        ).all()
    ) if job_ids else []
    job_by_id = {row.id: row for row in jobs}

    dependency_map: dict[UUID, list[UUID]] = {item: [] for item in question_ids}
    for edge in edges:
        try:
            question_id = UUID(str(edge["question_id"]))
            depends_on_id = UUID(str(edge["depends_on_question_id"]))
        except (KeyError, TypeError, ValueError):
            continue
        if question_id in dependency_map and depends_on_id in dependency_map:
            dependency_map[question_id].append(depends_on_id)

    evidence_ids = _content_evidence_ids(session, organization_id, workspace_id)
    evidence_available = bool(evidence_ids)

    node_states: list[dict[str, Any]] = []
    by_question: dict[UUID, str] = {}
    for node in nodes:
        question_id = UUID(str(node["id"]))
        question = question_by_id.get(question_id)
        if question is None:
            state = "BLOCKED"
            error = "研究问题已不存在"
            answer_id = None
            answer_status = None
            job_id = None
        else:
            answer = latest_answer.get(question_id)
            answer_id = answer.id if answer is not None else None
            answer_status = answer.status if answer is not None else None
            linked_jobs = [
                (workflow, job_by_id.get(workflow.job_id) if workflow.job_id else None)
                for workflow in answer_workflows.get(question_id, [])
            ]
            active_pairs = [
                (workflow, job)
                for workflow, job in linked_jobs
                if job is not None and job.status in {"PENDING", "RUNNING"}
            ]
            terminal_failed = [
                (workflow, job)
                for workflow, job in linked_jobs
                if workflow.status == "FAILED"
                or (job is not None and job.status in {"FAILED", "DEAD"})
            ]
            job_id = active_pairs[-1][1].id if active_pairs else (
                terminal_failed[-1][1].id if terminal_failed else None
            )
            error = None
            if question.status == "ANSWERED" or answer_status == "ACCEPTED":
                state = "ANSWERED"
            elif question.status == "CANCELLED":
                state = "CANCELLED"
            elif question.status == "BLOCKED":
                state = "BLOCKED"
            elif answer_status in {"NEEDS_REVIEW", "CONFLICTED"}:
                state = "WAITING_FOR_REVIEW"
            elif active_pairs:
                # A user may explicitly retry a previously rejected/failed node.
                # The active job takes precedence over the older terminal result.
                state = "RUNNING" if any(job.status == "RUNNING" for _, job in active_pairs) else "QUEUED"
            elif answer_status == "REJECTED":
                state = "REJECTED"
            elif any(job is not None and job.status == "CANCELLED" for _, job in linked_jobs):
                state = "CANCELLED"
            elif terminal_failed:
                state = "FAILED"
                error = (
                    terminal_failed[-1][1].last_error
                    if terminal_failed[-1][1] is not None
                    else "答案草拟工作流失败"
                )
            elif any(by_question.get(dep_id) != "ANSWERED" for dep_id in dependency_map.get(question_id, [])):
                state = "WAITING_FOR_DEPENDENCY"
            elif not evidence_available:
                state = "WAITING_FOR_EVIDENCE"
            else:
                state = "READY"

        by_question[question_id] = state
        node_states.append(
            {
                "question_id": question_id,
                "state": state,
                "latest_answer_id": answer_id,
                "latest_answer_status": answer_status,
                "job_id": job_id,
                "error": error,
            }
        )

    # The graph snapshot is topologically sorted when the execution is created.
    # Run a small fixed-point pass so dependency states do not depend on JSON order.
    for _ in range(max(1, len(node_states))):
        changed = False
        for row in node_states:
            question_id = row["question_id"]
            if row["state"] in {
                "ANSWERED",
                "QUEUED",
                "RUNNING",
                "WAITING_FOR_REVIEW",
                "FAILED",
                "REJECTED",
                "BLOCKED",
                "CANCELLED",
            }:
                continue
            if any(by_question.get(dep_id) != "ANSWERED" for dep_id in dependency_map.get(question_id, [])):
                if row["state"] != "WAITING_FOR_EVIDENCE":
                    row["state"] = "WAITING_FOR_DEPENDENCY"
                    by_question[question_id] = row["state"]
                    changed = True
        if not changed:
            break

    counts = {
        "dispatched_count": int(payload.get("dispatched_count") or 0),
        "completed_count": sum(1 for value in by_question.values() if value == "ANSWERED"),
        "active_count": sum(1 for value in by_question.values() if value in {"QUEUED", "RUNNING"}),
        "ready_count": sum(1 for value in by_question.values() if value == "READY"),
        "waiting_review_count": sum(1 for value in by_question.values() if value == "WAITING_FOR_REVIEW"),
        "waiting_evidence_count": sum(1 for value in by_question.values() if value == "WAITING_FOR_EVIDENCE"),
        "failed_count": sum(1 for value in by_question.values() if value in {"FAILED", "REJECTED", "BLOCKED"}),
    }
    invocation_ids: set[UUID] = set()
    answer_workflow_ids = [
        workflow.id for rows in answer_workflows.values() for workflow in rows
    ]
    agent_runs = list(
        session.scalars(
            select(AgentRun).where(
                AgentRun.organization_id == organization_id,
                AgentRun.workflow_id.in_(answer_workflow_ids),
                AgentRun.agent_type == "MARKET_QUESTION_ANSWER",
            )
        ).all()
    ) if answer_workflow_ids else []
    for run in agent_runs:
        raw_id = (run.output or {}).get("llm_invocation_id")
        try:
            if raw_id:
                invocation_ids.add(UUID(str(raw_id)))
        except ValueError:
            continue
    invocations = list(
        session.scalars(
            select(LLMInvocation).where(
                LLMInvocation.organization_id == organization_id,
                LLMInvocation.id.in_(invocation_ids),
            )
        ).all()
    ) if invocation_ids else []
    settings = get_settings()
    loaded_invocation_ids = {row.id for row in invocations}
    missing_invocation = invocation_ids - loaded_invocation_ids
    # A configured per-token rate is not enough: real provider usage must be present.
    # Cached invocations are explicitly zero-cost; non-cached calls with missing usage
    # or a degraded response make the cumulative cost unknown, never silently zero.
    unmetered_call = any(
        row.degraded or (not row.cached and row.tokens_in + row.tokens_out <= 0)
        for row in invocations
    )
    successful_runs_without_invocation = any(
        run.status == "SUCCEEDED"
        and not (run.output or {}).get("llm_invocation_id")
        for run in agent_runs
    )
    cost_metering_available = (
        settings.llm_cost_per_1k_tokens > 0
        and not missing_invocation
        and not unmetered_call
        and not successful_runs_without_invocation
    )
    total_cost = sum((row.cost_estimate for row in invocations), Decimal("0"))
    return node_states, {
        **counts,
        "evidence_count": len(evidence_ids),
        "cost_estimate": total_cost if cost_metering_available else None,
        "cost_metering_available": cost_metering_available,
        "question_ids": question_ids,
        "all_answered": bool(node_states) and all(row["state"] == "ANSWERED" for row in node_states),
    }


def _serialize_execution(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    workflow: WorkflowInstance,
) -> dict[str, Any]:
    payload = workflow.payload or {}
    node_states, counts = _node_statuses(session, organization_id, workspace_id, workflow)
    if workflow.status == "COMPLETED":
        state = "COMPLETED"
    elif workflow.status == "FAILED":
        state = str(payload.get("execution_state") or "FAILED")
    elif workflow.status == "REJECTED":
        state = "CANCELLED"
    elif counts["active_count"]:
        state = "RUNNING"
    elif counts["ready_count"]:
        state = "READY"
    elif counts["waiting_review_count"]:
        state = "WAITING_FOR_REVIEW"
    elif counts["waiting_evidence_count"]:
        state = "WAITING_FOR_EVIDENCE"
    else:
        state = str(payload.get("execution_state") or "WAITING_FOR_DEPENDENCY")

    return {
        "execution_id": workflow.id,
        "workspace_id": workspace_id,
        "workflow_status": workflow.status,
        "execution_state": state,
        "graph_digest": payload.get("graph_digest") or "",
        "max_concurrency": int(payload.get("max_concurrency") or 1),
        "max_questions": int(payload.get("max_questions") or 1),
        "max_duration_minutes": int(payload.get("max_duration_minutes") or 1),
        "max_cost": (
            Decimal(str(payload["max_cost"]))
            if payload.get("max_cost") is not None
            else None
        ),
        "cost_estimate": counts["cost_estimate"],
        "cost_metering_available": counts["cost_metering_available"],
        "dispatched_count": counts["dispatched_count"],
        "completed_count": counts["completed_count"],
        "active_count": counts["active_count"],
        "ready_count": counts["ready_count"],
        "waiting_review_count": counts["waiting_review_count"],
        "waiting_evidence_count": counts["waiting_evidence_count"],
        "failed_count": counts["failed_count"],
        "node_states": node_states,
        "stop_reason": payload.get("stop_reason"),
        "created_at": workflow.created_at,
        "started_at": payload.get("started_at"),
        "deadline_at": payload.get("deadline_at"),
        "finished_at": payload.get("finished_at"),
    }


def start_execution(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    *,
    user_id: UUID,
    max_concurrency: int | None = None,
    max_questions: int | None = None,
    max_duration_minutes: int | None = None,
    max_cost: Decimal | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    _workspace(session, organization_id, workspace_id, lock=True)
    graph = market_research_graph_service.get_research_graph(session, organization_id, workspace_id)
    if graph["cycle_detected"] or not graph["nodes"]:
        raise Unprocessable("研究图为空或存在循环，不能启动执行", code="RESEARCH_GRAPH_NOT_EXECUTABLE")

    active = session.scalars(
        select(WorkflowInstance)
        .join(
            MarketWorkspaceWorkflow,
            and_(
                MarketWorkspaceWorkflow.organization_id == WorkflowInstance.organization_id,
                MarketWorkspaceWorkflow.workflow_id == WorkflowInstance.id,
            ),
        )
        .where(
            WorkflowInstance.organization_id == organization_id,
            WorkflowInstance.workflow_type == WORKFLOW_TYPE,
            WorkflowInstance.status == "RUNNING",
            MarketWorkspaceWorkflow.workspace_id == workspace_id,
        )
        .with_for_update()
    ).first()
    graph_snapshot = {
        "nodes": [
            {
                "id": str(node["id"]),
                "question": node["question"],
                "priority": node["priority"],
                "depends_on_question_ids": [str(item) for item in node["depends_on_question_ids"]],
            }
            for node in graph["nodes"]
        ],
        "edges": [
            {
                "question_id": str(edge["question_id"]),
                "depends_on_question_id": str(edge["depends_on_question_id"]),
            }
            for edge in graph["edges"]
        ],
        "topological_order": [str(item) for item in graph["topological_order"]],
    }
    graph_digest = _digest(graph_snapshot)
    if active is not None:
        if (active.payload or {}).get("graph_digest") == graph_digest:
            job = job_service.find_inflight(
                session, organization_id, f"market_research_dag_schedule:{active.id}"
            )
            if job is None:
                job = _enqueue_schedule(session, organization_id, active.id, user_id=user_id)
                active.job_id = job.id
            return {
                **_serialize_execution(session, organization_id, workspace_id, active),
                "job_id": job.id,
                "workflow_status": active.status,
                "reused": True,
            }
        raise Conflict(
            "该 Workspace 已有另一张研究图正在执行，请先等待或取消当前执行",
            code="MARKET_RESEARCH_DAG_ALREADY_RUNNING",
            details={"execution_id": str(active.id)},
        )

    max_concurrency_value = max(1, min(
        max_concurrency or settings.market_research_graph_max_concurrency,
        settings.market_research_graph_max_concurrency,
    ))
    max_questions_value = max(1, min(
        max_questions or settings.market_research_graph_max_questions,
        settings.market_research_graph_max_questions,
        len(graph_snapshot["nodes"]),
    ))
    max_duration_value = max(1, min(
        max_duration_minutes or settings.market_research_graph_max_duration_minutes,
        settings.market_research_graph_max_duration_minutes,
    ))
    cost_metering_available = settings.llm_cost_per_1k_tokens > 0
    max_cost_value: Decimal | None = None
    configured_cap = Decimal(str(settings.market_research_graph_max_cost))
    if cost_metering_available and configured_cap > 0:
        max_cost_value = configured_cap
    if max_cost is not None:
        if not cost_metering_available or configured_cap <= 0:
            raise Unprocessable(
                "当前部署未配置 LLM 成本估算单价或金额 hard cap，无法执行金额预算限制",
                code="MARKET_RESEARCH_COST_METERING_UNAVAILABLE",
            )
        if max_cost <= 0 or max_cost > configured_cap:
            raise Unprocessable(
                "金额预算必须大于 0，且不能高于部署级 hard cap",
                code="MARKET_RESEARCH_COST_BUDGET_INVALID",
                details={"max_cost": str(configured_cap)},
            )
        max_cost_value = max_cost

    # A cumulative hard cap cannot be respected with parallel unreserved calls.
    # Any active monetary budget therefore serializes the execution, whether it came
    # from the deployment hard cap or a tighter user-supplied cap.
    if max_cost_value is not None:
        max_concurrency_value = 1

    now = datetime.now(UTC)
    deadline = now + timedelta(minutes=max_duration_value)
    workflow = WorkflowInstance(
        organization_id=organization_id,
        workflow_type=WORKFLOW_TYPE,
        status="RUNNING",
        payload={
            "workspace_id": str(workspace_id),
            "graph_snapshot": graph_snapshot,
            "graph_digest": graph_digest,
            "max_concurrency": max_concurrency_value,
            "max_questions": max_questions_value,
            "max_duration_minutes": max_duration_value,
            "max_cost": str(max_cost_value) if max_cost_value is not None else None,
            "dispatched_count": 0,
            "dispatched_question_ids": [],
            "execution_state": "QUEUED",
            "started_at": now.isoformat(),
            "deadline_at": deadline.isoformat(),
            "finished_at": None,
            "stop_reason": None,
        },
        created_by_user_id=user_id,
    )
    session.add(workflow)
    session.flush()
    session.add(
        MarketWorkspaceWorkflow(
            organization_id=organization_id,
            workspace_id=workspace_id,
            workflow_id=workflow.id,
            linked_by_user_id=user_id,
        )
    )
    job = _enqueue_schedule(session, organization_id, workflow.id, user_id=user_id)
    workflow.job_id = job.id
    session.add(
        MarketActivityEvent(
            organization_id=organization_id,
            workspace_id=workspace_id,
            actor_type="USER",
            actor_user_id=user_id,
            aggregate_type="market_research_graph_execution",
            aggregate_id=workflow.id,
            event_type="MARKET_RESEARCH_GRAPH_EXECUTION_STARTED",
            payload={
                "graph_digest": graph_digest,
                "node_count": len(graph_snapshot["nodes"]),
                "max_concurrency": max_concurrency_value,
                "max_questions": max_questions_value,
                "max_duration_minutes": max_duration_value,
                "max_cost": str(max_cost_value) if max_cost_value is not None else None,
            },
        )
    )
    session.flush()
    return {
        **_serialize_execution(session, organization_id, workspace_id, workflow),
        "job_id": job.id,
        "reused": False,
    }


def _enqueue_schedule(
    session: Session,
    organization_id: UUID | str,
    execution_id: UUID,
    *,
    user_id: UUID | None = None,
) -> BackgroundJob:
    key = f"market_research_dag_schedule:{execution_id}"
    return job_service.enqueue(
        session,
        organization_id,
        job_type=job_service.JOB_TYPE_MARKET_RESEARCH_DAG_SCHEDULE,
        payload={"execution_workflow_id": str(execution_id)},
        dedupe_key=key,
        created_by_user_id=user_id,
        max_attempts=8,
    )


def wake_workspace_executions(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
) -> int:
    """Wake every active DAG execution when a node answer or new Evidence changes readiness.

    Lock order is Workspace -> execution Workflow, matching start/cancel/retry/scheduler.
    This prevents cross-request deadlocks when several workers touch one Workspace.
    """
    _workspace(session, organization_id, workspace_id, lock=True)
    executions = session.scalars(
        select(WorkflowInstance)
        .join(
            MarketWorkspaceWorkflow,
            and_(
                MarketWorkspaceWorkflow.organization_id == WorkflowInstance.organization_id,
                MarketWorkspaceWorkflow.workflow_id == WorkflowInstance.id,
            ),
        )
        .where(
            WorkflowInstance.organization_id == organization_id,
            WorkflowInstance.workflow_type == WORKFLOW_TYPE,
            WorkflowInstance.status == "RUNNING",
            MarketWorkspaceWorkflow.workspace_id == workspace_id,
        )
        .with_for_update()
    ).all()
    for workflow in executions:
        job = _enqueue_schedule(session, organization_id, workflow.id)
        workflow.job_id = job.id
    session.flush()
    return len(executions)


def run_scheduler(
    session: Session,
    organization_id: UUID | str,
    execution_id: UUID,
) -> dict[str, Any]:
    # Read the immutable Workspace owner first, then take locks in the common
    # Workspace -> execution Workflow order used by start/cancel/retry/wake.
    candidate = session.scalars(
        select(WorkflowInstance).where(
            WorkflowInstance.organization_id == organization_id,
            WorkflowInstance.id == execution_id,
            WorkflowInstance.workflow_type == WORKFLOW_TYPE,
        )
    ).one_or_none()
    if candidate is None:
        raise NotFound("研究图执行不存在", details={"execution_id": str(execution_id)})
    workspace_id = UUID(str((candidate.payload or {}).get("workspace_id")))
    _workspace(session, organization_id, workspace_id, lock=True)
    workflow = session.scalars(
        select(WorkflowInstance)
        .where(
            WorkflowInstance.organization_id == organization_id,
            WorkflowInstance.id == execution_id,
            WorkflowInstance.workflow_type == WORKFLOW_TYPE,
        )
        .with_for_update()
    ).one_or_none()
    if workflow is None:
        raise NotFound("研究图执行不存在", details={"execution_id": str(execution_id)})
    payload = dict(workflow.payload or {})
    if UUID(str(payload.get("workspace_id"))) != workspace_id:
        raise Conflict("研究图执行的 Workspace 归属发生变化")
    if workflow.status != "RUNNING":
        return {"execution_id": str(execution_id), "state": payload.get("execution_state", workflow.status)}

    node_states, counts = _node_statuses(session, organization_id, workspace_id, workflow)
    now = datetime.now(UTC)
    deadline = datetime.fromisoformat(str(payload["deadline_at"]))
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)

    def stop(state: str, reason: str) -> dict[str, Any]:
        payload["execution_state"] = state
        payload["stop_reason"] = reason
        payload["finished_at"] = now.isoformat()
        workflow.payload = payload
        workflow.status = "FAILED" if state != "CANCELLED" else "REJECTED"
        session.add(
            MarketActivityEvent(
                organization_id=organization_id,
                workspace_id=workspace_id,
                actor_type="SYSTEM",
                actor_user_id=None,
                aggregate_type="market_research_graph_execution",
                aggregate_id=workflow.id,
                event_type="MARKET_RESEARCH_GRAPH_EXECUTION_STOPPED",
                payload={"state": state, "reason": reason},
            )
        )
        session.flush()
        return {"execution_id": str(execution_id), "state": state, "stop_reason": reason}

    if now >= deadline:
        return stop("TIMED_OUT", "执行时间预算已耗尽")
    max_cost = Decimal(str(payload["max_cost"])) if payload.get("max_cost") is not None else None
    if max_cost is not None and counts["cost_estimate"] is None:
        return stop("BUDGET_EXHAUSTED", "部署的 LLM 成本计量已不可用，无法继续执行预算受限任务")
    if (
        max_cost is not None
        and counts["cost_estimate"] is not None
        and counts["cost_estimate"] >= max_cost
    ):
        return stop("BUDGET_EXHAUSTED", "LLM 成本估算已达到成本预算")

    if counts["all_answered"]:
        payload["execution_state"] = "COMPLETED"
        payload["finished_at"] = now.isoformat()
        workflow.payload = payload
        workflow.status = "COMPLETED"
        session.add(
            MarketActivityEvent(
                organization_id=organization_id,
                workspace_id=workspace_id,
                actor_type="SYSTEM",
                actor_user_id=None,
                aggregate_type="market_research_graph_execution",
                aggregate_id=workflow.id,
                event_type="MARKET_RESEARCH_GRAPH_EXECUTION_COMPLETED",
                payload={"completed_count": counts["completed_count"], "graph_digest": payload["graph_digest"]},
            )
        )
        session.flush()
        return {"execution_id": str(execution_id), "state": "COMPLETED"}

    active_count = counts["active_count"]
    slots = max(0, int(payload["max_concurrency"]) - active_count)
    remaining_budget = max(0, int(payload["max_questions"]) - int(payload.get("dispatched_count") or 0))
    if slots and remaining_budget and counts["ready_count"] and counts["evidence_count"]:
        node_by_id = {str(row["question_id"]): row for row in node_states}
        for question_id in payload["graph_snapshot"]["topological_order"]:
            if slots <= 0 or remaining_budget <= 0:
                break
            node_state = node_by_id.get(question_id)
            if node_state is None or node_state["state"] != "READY":
                continue
            result = market_question_answer_agent_service.enqueue_answer_draft(
                session,
                organization_id,
                workspace_id,
                UUID(question_id),
                evidence_ids=None,
                created_by_user_id=workflow.created_by_user_id or UUID(str(settings_user_fallback())),
                execution_id=workflow.id,
            )
            slots -= 1
            remaining_budget -= 1
            dispatched_question_ids = {
                str(item) for item in payload.get("dispatched_question_ids", [])
            }
            if not result.get("reused") and question_id not in dispatched_question_ids:
                dispatched_question_ids.add(question_id)
                payload["dispatched_question_ids"] = sorted(dispatched_question_ids)
                payload["dispatched_count"] = len(dispatched_question_ids)
    elif remaining_budget == 0 and counts["ready_count"] and counts["active_count"] == 0:
        return stop("PARTIAL", "本次执行达到最大问题数预算")

    node_states, counts = _node_statuses(session, organization_id, workspace_id, workflow)
    if counts["all_answered"]:
        payload["execution_state"] = "COMPLETED"
        payload["finished_at"] = datetime.now(UTC).isoformat()
        workflow.payload = payload
        workflow.status = "COMPLETED"
    elif counts["active_count"]:
        payload["execution_state"] = "RUNNING"
        workflow.payload = payload
    elif counts["waiting_review_count"]:
        payload["execution_state"] = "WAITING_FOR_REVIEW"
        workflow.payload = payload
    elif counts["waiting_evidence_count"]:
        payload["execution_state"] = "WAITING_FOR_EVIDENCE"
        workflow.payload = payload
    elif counts["ready_count"] and int(payload.get("dispatched_count") or 0) >= int(payload["max_questions"]):
        payload["execution_state"] = "PARTIAL"
        payload["stop_reason"] = "达到最大问题数预算"
        payload["finished_at"] = datetime.now(UTC).isoformat()
        workflow.payload = payload
        workflow.status = "FAILED"
    elif counts["failed_count"]:
        payload["execution_state"] = "PARTIAL"
        payload["stop_reason"] = "一个或多个节点失败、被驳回或阻塞"
        workflow.payload = payload
        workflow.status = "FAILED"
        payload["finished_at"] = datetime.now(UTC).isoformat()
    else:
        payload["execution_state"] = "WAITING_FOR_DEPENDENCY"
        workflow.payload = payload

    if workflow.status in {"COMPLETED", "FAILED"}:
        session.add(
            MarketActivityEvent(
                organization_id=organization_id,
                workspace_id=workspace_id,
                actor_type="SYSTEM",
                actor_user_id=None,
                aggregate_type="market_research_graph_execution",
                aggregate_id=workflow.id,
                event_type="MARKET_RESEARCH_GRAPH_EXECUTION_FINISHED",
                payload={
                    "state": payload.get("execution_state"),
                    "completed_count": counts["completed_count"],
                    "failed_count": counts["failed_count"],
                    "dispatched_count": payload.get("dispatched_count", 0),
                },
            )
        )
    session.flush()
    return {
        "execution_id": str(execution_id),
        "state": payload.get("execution_state"),
        "dispatched_count": payload.get("dispatched_count", 0),
        "active_count": counts["active_count"],
    }


def settings_user_fallback() -> str:
    """Only used when legacy workflows have no creator; resolve the configured dev user."""
    from app.config import get_settings

    return str(get_settings().dev_user_id)


def retry_question(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    execution_id: UUID,
    question_id: UUID,
    *,
    actor_user_id: UUID,
) -> dict[str, Any]:
    """Explicitly retry a failed/rejected node without changing its dependency snapshot."""
    _workspace(session, organization_id, workspace_id, lock=True)
    workflow = _execution_workflow(
        session, organization_id, workspace_id, execution_id, lock=True
    )
    if workflow.status == "REJECTED":
        raise Conflict("已取消的研究图执行不能重试")
    payload = dict(workflow.payload or {})
    snapshot = payload.get("graph_snapshot") or {}
    question_ids = {str(node.get("id")) for node in snapshot.get("nodes", [])}
    if str(question_id) not in question_ids:
        raise NotFound("研究问题不属于本次执行的图快照")

    node_states, counts = _node_statuses(
        session, organization_id, workspace_id, workflow
    )
    target = next(
        (row for row in node_states if row["question_id"] == question_id),
        None,
    )
    if target is None:
        raise NotFound("研究问题不存在")
    if target["state"] not in {"FAILED", "REJECTED"}:
        raise Conflict(
            "只有失败或被驳回的问题才允许手动重试",
            details={"state": target["state"]},
        )

    dependency_ids = [
        UUID(str(edge["depends_on_question_id"]))
        for edge in snapshot.get("edges", [])
        if str(edge.get("question_id")) == str(question_id)
    ]
    if dependency_ids:
        dependencies = list(
            session.scalars(
                select(MarketResearchQuestion).where(
                    MarketResearchQuestion.organization_id == organization_id,
                    MarketResearchQuestion.workspace_id == workspace_id,
                    MarketResearchQuestion.id.in_(dependency_ids),
                )
            ).all()
        )
        if len(dependencies) != len(dependency_ids) or any(
            row.status != "ANSWERED" for row in dependencies
        ):
            raise Conflict("前置问题尚未全部通过人工审核，不能重试当前节点")

    now = datetime.now(UTC)
    deadline = datetime.fromisoformat(str(payload["deadline_at"]))
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    if now >= deadline:
        raise Conflict("本次执行已超过时间预算，请创建新的执行批次")
    max_cost = Decimal(str(payload["max_cost"])) if payload.get("max_cost") is not None else None
    if max_cost is not None and counts["cost_estimate"] is None:
        raise Conflict("当前部署无法继续计量本次执行成本，请先恢复 LLM 成本计量")
    if (
        max_cost is not None
        and counts["cost_estimate"] is not None
        and counts["cost_estimate"] >= max_cost
    ):
        raise Conflict("本次执行已达到成本预算，请创建新的执行批次")

    dispatched_ids = {
        str(item) for item in payload.get("dispatched_question_ids", [])
    }
    if str(question_id) not in dispatched_ids and len(dispatched_ids) >= int(payload["max_questions"]):
        raise Conflict("本次执行已达到问题数上限，请创建新的执行批次")

    if not counts["evidence_count"]:
        raise Conflict(
            "当前 Workspace 没有可分析的内容型 Evidence；补充证据后再重试",
            code="MARKET_RESEARCH_EVIDENCE_REQUIRED",
        )

    result = market_question_answer_agent_service.enqueue_answer_draft(
        session,
        organization_id,
        workspace_id,
        question_id,
        evidence_ids=None,
        created_by_user_id=actor_user_id,
        execution_id=execution_id,
    )
    if not result.get("reused"):
        dispatched_ids.add(str(question_id))
        payload["dispatched_question_ids"] = sorted(dispatched_ids)
        payload["dispatched_count"] = len(dispatched_ids)

    workflow.status = "RUNNING"
    payload["execution_state"] = "QUEUED"
    payload["stop_reason"] = None
    payload["finished_at"] = None
    workflow.payload = payload
    schedule_job = _enqueue_schedule(
        session, organization_id, execution_id, user_id=actor_user_id
    )
    workflow.job_id = schedule_job.id
    session.add(
        MarketActivityEvent(
            organization_id=organization_id,
            workspace_id=workspace_id,
            actor_type="USER",
            actor_user_id=actor_user_id,
            aggregate_type="market_research_graph_execution",
            aggregate_id=execution_id,
            event_type="MARKET_RESEARCH_GRAPH_NODE_RETRY_REQUESTED",
            payload={
                "question_id": str(question_id),
                "answer_workflow_id": result.get("workflow_id"),
                "job_id": str(result["job_id"]),
                "reused": bool(result.get("reused")),
            },
        )
    )
    session.flush()
    return {
        "execution_id": execution_id,
        "question_id": question_id,
        "job_id": result["job_id"],
        "reused": bool(result.get("reused")),
    }


def get_execution(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    execution_id: UUID,
) -> dict[str, Any]:
    workflow = _execution_workflow(session, organization_id, workspace_id, execution_id)
    return _serialize_execution(session, organization_id, workspace_id, workflow)


def cancel_execution(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    execution_id: UUID,
    *,
    actor_user_id: UUID,
) -> dict[str, Any]:
    _workspace(session, organization_id, workspace_id, lock=True)
    workflow = _execution_workflow(
        session, organization_id, workspace_id, execution_id, lock=True
    )
    if workflow.status == "REJECTED":
        return {"execution_id": workflow.id, "cancelled": True, "cancelled_jobs": 0, "running_jobs": 0}
    if workflow.status != "RUNNING":
        raise Conflict("研究图执行已结束，不能再次取消")
    payload = dict(workflow.payload or {})
    linked = _linked_workflows(session, organization_id, workspace_id)
    cancelled_jobs = 0
    running_jobs = 0
    for child in linked:
        data = child.payload or {}
        if child.workflow_type != ANSWER_WORKFLOW_TYPE:
            continue
        if str(data.get("dag_execution_workflow_id") or "") != str(execution_id):
            continue
        question_id = str(data.get("question_id") or "")
        if question_id not in {str(item["id"]) for item in payload.get("graph_snapshot", {}).get("nodes", [])}:
            continue
        if child.job_id is None:
            continue
        job = session.scalars(
            select(BackgroundJob).where(
                BackgroundJob.organization_id == organization_id,
                BackgroundJob.id == child.job_id,
            ).with_for_update()
        ).one_or_none()
        if job is None:
            continue
        if job.status == "PENDING":
            job_service.cancel(session, organization_id, job.id)
            child.status = "REJECTED"
            child.payload = {**data, "generation_status": "CANCELLED", "generation_error": "父研究图执行已取消"}
            cancelled_jobs += 1
        elif job.status == "RUNNING":
            running_jobs += 1
    payload["execution_state"] = "CANCELLED"
    payload["stop_reason"] = "用户取消"
    payload["finished_at"] = datetime.now(UTC).isoformat()
    workflow.payload = payload
    workflow.status = "REJECTED"
    session.add(
        MarketActivityEvent(
            organization_id=organization_id,
            workspace_id=workspace_id,
            actor_type="USER",
            actor_user_id=actor_user_id,
            aggregate_type="market_research_graph_execution",
            aggregate_id=workflow.id,
            event_type="MARKET_RESEARCH_GRAPH_EXECUTION_CANCELLED",
            payload={"cancelled_jobs": cancelled_jobs, "running_jobs": running_jobs},
        )
    )
    session.flush()
    return {
        "execution_id": workflow.id,
        "cancelled": True,
        "cancelled_jobs": cancelled_jobs,
        "running_jobs": running_jobs,
    }


__all__ = [
    "WORKFLOW_TYPE",
    "cancel_execution",
    "get_execution",
    "run_scheduler",
    "start_execution",
    "retry_question",
    "wake_workspace_executions",
]
