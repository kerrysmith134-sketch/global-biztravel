"""Queue evidence-backed answer drafting for a Workspace research question."""

from __future__ import annotations

from hashlib import sha256
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.api.errors import Conflict, NotFound, Unprocessable
from app.llm.prompts import MARKET_QUESTION_ANSWER_PROMPT_VERSION
from app.models.market import (
    MarketActivityEvent,
    MarketResearchQuestion,
    MarketWorkspace,
    MarketWorkspaceWorkflow,
)
from app.models.reliability import BackgroundJob
from app.models.workflow import Evidence, WorkflowInstance
from app.services import job_service

WORKFLOW_TYPE = "market_question_answer"
MAX_EVIDENCE_PER_DRAFT = 24


def _evidence_scope(
    organization_id: UUID | str,
    workspace_id: UUID,
    workflow_ids: list[UUID],
):
    return and_(
        Evidence.organization_id == organization_id,
        or_(
            and_(
                Evidence.subject_type == "market_workspace",
                Evidence.subject_id == workspace_id,
            ),
            and_(
                Evidence.subject_type == "RESEARCH_PLAN",
                Evidence.subject_id.in_(workflow_ids),
            )
            if workflow_ids
            else False,
        ),
    )


def enqueue_answer_draft(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    question_id: UUID,
    *,
    evidence_ids: list[UUID] | None,
    created_by_user_id: UUID,
) -> dict[str, Any]:
    """Create a durable Agent workflow and enqueue one idempotent draft operation."""
    workspace = session.scalars(
        select(MarketWorkspace)
        .where(
            MarketWorkspace.organization_id == organization_id,
            MarketWorkspace.id == workspace_id,
        )
        .with_for_update()
    ).one_or_none()
    if workspace is None:
        raise NotFound("市场工作空间不存在", details={"workspace_id": str(workspace_id)})

    question = session.scalars(
        select(MarketResearchQuestion).where(
            MarketResearchQuestion.organization_id == organization_id,
            MarketResearchQuestion.workspace_id == workspace_id,
            MarketResearchQuestion.id == question_id,
        )
    ).one_or_none()
    if question is None:
        raise NotFound("研究问题不存在", details={"question_id": str(question_id)})
    if question.status in {"BLOCKED", "CANCELLED"}:
        raise Conflict("已阻塞或取消的问题不能生成答案草稿")

    workflow_ids = list(
        session.scalars(
            select(MarketWorkspaceWorkflow.workflow_id).where(
                MarketWorkspaceWorkflow.organization_id == organization_id,
                MarketWorkspaceWorkflow.workspace_id == workspace_id,
            )
        ).all()
    )
    scope = _evidence_scope(organization_id, workspace_id, workflow_ids)
    if evidence_ids is not None:
        if not evidence_ids:
            selected_rows: list[Evidence] = []
        else:
            if len(evidence_ids) > MAX_EVIDENCE_PER_DRAFT:
                raise Unprocessable(
                    f"一次答案草拟最多选择 {MAX_EVIDENCE_PER_DRAFT} 条证据"
                )
            if len(evidence_ids) != len(set(evidence_ids)):
                raise Unprocessable("证据列表中存在重复 ID")
            rows = session.scalars(
                select(Evidence).where(scope, Evidence.id.in_(evidence_ids))
            ).all()
            by_id = {row.id: row for row in rows}
            if len(by_id) != len(evidence_ids):
                raise Conflict("部分证据不属于当前 Workspace 或其关联 ResearchPlan")
            selected_rows = [by_id[item] for item in evidence_ids]
    else:
        # Default selection is a bounded snapshot, not an unbounded prompt dump.
        selected_rows = list(
            session.scalars(
                select(Evidence)
                .where(scope)
                .order_by(
                    Evidence.retrieved_at.desc().nullslast(),
                    Evidence.created_at.desc(),
                    Evidence.id,
                )
                .limit(MAX_EVIDENCE_PER_DRAFT + 1)
            ).all()
        )
        if len(selected_rows) > MAX_EVIDENCE_PER_DRAFT:
            selected_rows = selected_rows[:MAX_EVIDENCE_PER_DRAFT]

    # Only evidence with an actual captured body/snippet can be passed to the model.
    content_rows = [
        row
        for row in selected_rows
        if isinstance((row.payload or {}).get("content"), str)
        and (row.payload or {}).get("content", "").strip()
    ]
    if evidence_ids is not None and len(content_rows) != len(selected_rows):
        raise Unprocessable("所选证据缺少可供分析的正文或摘要内容")
    selected_ids = [row.id for row in content_rows]
    evidence_digest = sha256(
        "|".join(str(item) for item in sorted(selected_ids)).encode("utf-8")
    ).hexdigest()
    dedupe_key = (
        f"market_question_answer:{question_id}:{MARKET_QUESTION_ANSWER_PROMPT_VERSION}:"
        f"{evidence_digest}"
    )

    existing = job_service.find_inflight(session, organization_id, dedupe_key)
    if existing is not None:
        payload = existing.payload or {}
        return {
            "job_id": existing.id,
            "workflow_id": payload.get("workflow_id"),
            "status": existing.status,
            "evidence_count": len(selected_ids),
            "reused": True,
        }

    workflow = WorkflowInstance(
        organization_id=organization_id,
        workflow_type=WORKFLOW_TYPE,
        status="RUNNING",
        payload={
            "workspace_id": str(workspace_id),
            "question_id": str(question_id),
            "evidence_ids": [str(item) for item in selected_ids],
            "evidence_count": len(selected_ids),
            "prompt_template_id": "market_question_answer",
            "prompt_version": MARKET_QUESTION_ANSWER_PROMPT_VERSION,
            "generation_status": "QUEUED",
        },
        created_by_user_id=created_by_user_id,
    )
    session.add(workflow)
    session.flush()
    session.add(
        MarketWorkspaceWorkflow(
            organization_id=organization_id,
            workspace_id=workspace_id,
            workflow_id=workflow.id,
            linked_by_user_id=created_by_user_id,
        )
    )
    job: BackgroundJob = job_service.enqueue(
        session,
        organization_id,
        job_type=job_service.JOB_TYPE_MARKET_QUESTION_ANSWER,
        payload={
            "workspace_id": str(workspace_id),
            "question_id": str(question_id),
            "workflow_id": str(workflow.id),
            "evidence_ids": [str(item) for item in selected_ids],
        },
        dedupe_key=dedupe_key,
        created_by_user_id=created_by_user_id,
    )
    workflow.job_id = job.id
    session.add(
        MarketActivityEvent(
            organization_id=organization_id,
            workspace_id=workspace_id,
            actor_type="USER",
            actor_user_id=created_by_user_id,
            aggregate_type="market_research_question",
            aggregate_id=question_id,
            event_type="MARKET_QUESTION_ANSWER_DRAFT_QUEUED",
            payload={
                "workflow_id": str(workflow.id),
                "job_id": str(job.id),
                "evidence_count": len(selected_ids),
                "prompt_version": MARKET_QUESTION_ANSWER_PROMPT_VERSION,
            },
        )
    )
    session.flush()
    return {
        "job_id": job.id,
        "workflow_id": workflow.id,
        "status": job.status,
        "evidence_count": len(selected_ids),
        "reused": False,
    }
