"""User-selected research source page extraction workflow."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import Conflict, NotFound, Unprocessable
from app.integrations.research_page import EXTRACTOR_VERSION
from app.models.market import (
    MarketActivityEvent,
    MarketWorkspace,
    MarketWorkspaceWorkflow,
)
from app.models.workflow import Evidence, WorkflowInstance
from app.services import job_service, market_insight_service

WORKFLOW_TYPE = "market_source_extraction"


def enqueue_page_extraction(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    source_evidence_id: UUID,
    *,
    created_by_user_id: UUID,
) -> dict[str, Any]:
    """Queue extraction for exactly one search Evidence explicitly selected by a user."""
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

    source = session.scalars(
        select(Evidence).where(
            Evidence.organization_id == organization_id,
            Evidence.id == source_evidence_id,
            Evidence.subject_type == "market_workspace",
            Evidence.subject_id == workspace_id,
        )
    ).one_or_none()
    if source is None:
        raise NotFound("来源证据不存在或不属于当前 Workspace")
    if source.kind != "WEB_SEARCH_RESULT":
        raise Unprocessable("只能对用户明确选中的 WEB_SEARCH_RESULT 执行网页正文提取")
    if source.verification_status == "REJECTED":
        raise Conflict("已被驳回的来源不能继续提取")
    if not source.canonical_ref:
        raise Unprocessable("搜索结果缺少可访问的来源 URL")
    canonical_url = market_insight_service.canonicalize_source_ref(source.canonical_ref)

    identity = source.content_hash or source.ref
    dedupe_key = (
        f"market_page_extract:{workspace_id}:{source.id}:{identity}:{EXTRACTOR_VERSION}"
    )
    existing = job_service.find_inflight(session, organization_id, dedupe_key)
    if existing is not None:
        payload = existing.payload or {}
        return {
            "workspace_id": workspace_id,
            "source_evidence_id": source.id,
            "workflow_id": payload.get("workflow_id"),
            "job_id": existing.id,
            "status": existing.status,
            "reused": True,
        }

    # Do not enqueue another completed extraction for the same immutable source/version.
    completed = session.scalars(
        select(WorkflowInstance)
        .join(
            MarketWorkspaceWorkflow,
            (MarketWorkspaceWorkflow.organization_id == WorkflowInstance.organization_id)
            & (MarketWorkspaceWorkflow.workflow_id == WorkflowInstance.id),
        )
        .where(
            WorkflowInstance.organization_id == organization_id,
            WorkflowInstance.workflow_type == WORKFLOW_TYPE,
            MarketWorkspaceWorkflow.workspace_id == workspace_id,
            WorkflowInstance.payload["source_evidence_id"].astext == str(source.id),
            WorkflowInstance.payload["extractor_version"].astext == EXTRACTOR_VERSION,
            WorkflowInstance.status == "COMPLETED",
        )
        .order_by(WorkflowInstance.created_at.desc())
        .limit(1)
    ).one_or_none()
    if completed is not None:
        payload = completed.payload or {}
        return {
            "workspace_id": workspace_id,
            "source_evidence_id": source.id,
            "workflow_id": completed.id,
            "job_id": completed.job_id,
            "status": "COMPLETED",
            "reused": True,
        }

    workflow = WorkflowInstance(
        organization_id=organization_id,
        workflow_type=WORKFLOW_TYPE,
        status="ACCEPTED",
        payload={
            "workspace_id": str(workspace_id),
            "source_evidence_id": str(source.id),
            "source_url": canonical_url,
            "source_content_hash": source.content_hash,
            "extractor_version": EXTRACTOR_VERSION,
            "extraction_status": "QUEUED",
            "requested_by_user_id": str(created_by_user_id),
            "requested_at": datetime.now(UTC).isoformat(),
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
    job = job_service.enqueue(
        session,
        organization_id,
        job_type=job_service.JOB_TYPE_MARKET_PAGE_EXTRACT,
        payload={
            "workspace_id": str(workspace_id),
            "workflow_id": str(workflow.id),
            "source_evidence_id": str(source.id),
        },
        dedupe_key=dedupe_key,
        created_by_user_id=created_by_user_id,
    )
    workflow.job_id = job.id
    workflow.status = "RUNNING"
    session.add(
        MarketActivityEvent(
            organization_id=organization_id,
            workspace_id=workspace_id,
            actor_type="USER",
            actor_user_id=created_by_user_id,
            aggregate_type="market_source_extraction",
            aggregate_id=workflow.id,
            event_type="MARKET_SOURCE_EXTRACTION_QUEUED",
            payload={
                "source_evidence_id": str(source.id),
                "job_id": str(job.id),
                "extractor_version": EXTRACTOR_VERSION,
            },
        )
    )
    session.flush()
    return {
        "workspace_id": workspace_id,
        "source_evidence_id": source.id,
        "workflow_id": workflow.id,
        "job_id": job.id,
        "status": "QUEUED" if job.status == "PENDING" else "RUNNING",
        "reused": False,
    }


def get_page_extraction(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    workflow_id: UUID,
) -> dict[str, Any]:
    workflow = session.scalars(
        select(WorkflowInstance).where(
            WorkflowInstance.organization_id == organization_id,
            WorkflowInstance.id == workflow_id,
            WorkflowInstance.workflow_type == WORKFLOW_TYPE,
        )
    ).one_or_none()
    if workflow is None:
        raise NotFound("来源提取任务不存在")
    link = session.scalars(
        select(MarketWorkspaceWorkflow).where(
            MarketWorkspaceWorkflow.organization_id == organization_id,
            MarketWorkspaceWorkflow.workspace_id == workspace_id,
            MarketWorkspaceWorkflow.workflow_id == workflow_id,
        )
    ).one_or_none()
    if link is None:
        raise NotFound("来源提取任务不属于当前 Workspace")

    payload = workflow.payload or {}
    source_id = UUID(str(payload["source_evidence_id"]))
    job = (
        job_service.get_job(session, organization_id, workflow.job_id)
        if workflow.job_id is not None
        else None
    )
    extraction_status = str(payload.get("extraction_status") or "QUEUED")
    if job is not None and job.status == "RUNNING" and extraction_status == "QUEUED":
        extraction_status = "RUNNING"
    failure_reason = payload.get("failure_reason")
    if job is not None and job.status == "DEAD":
        extraction_status = "FAILED"
        failure_reason = job.last_error or failure_reason or "任务超过最大重试次数"
    elif job is not None and job.status == "FAILED":
        extraction_status = "FAILED"
        failure_reason = job.last_error or failure_reason
    return {
        "workspace_id": workspace_id,
        "source_evidence_id": source_id,
        "workflow_id": workflow.id,
        "job_id": workflow.job_id,
        "job_status": job.status if job is not None else None,
        "extraction_status": extraction_status,
        "extracted_evidence_id": payload.get("extracted_evidence_id"),
        "agent_run_id": payload.get("agent_run_id"),
        "failure_code": payload.get("failure_code"),
        "failure_reason": failure_reason,
        "source_url": payload.get("source_url"),
        "extractor_version": payload.get("extractor_version"),
        "created_at": workflow.created_at,
        "updated_at": workflow.updated_at,
    }


def mark_retry_exhausted(
    session: Session,
    organization_id: UUID | str,
    job_id: UUID,
    *,
    error: str,
) -> None:
    """Persist a terminal DEAD state after the shared retry policy is exhausted."""
    workflow = session.scalars(
        select(WorkflowInstance)
        .where(
            WorkflowInstance.organization_id == organization_id,
            WorkflowInstance.workflow_type == WORKFLOW_TYPE,
            WorkflowInstance.job_id == job_id,
        )
        .with_for_update()
    ).one_or_none()
    if workflow is None:
        return
    payload = {
        **(workflow.payload or {}),
        "extraction_status": "FAILED",
        "failure_code": "RETRIES_EXHAUSTED",
        "failure_reason": error[:2000],
    }
    workflow.payload = payload
    workflow.status = "FAILED"
    workspace_id = UUID(str(payload["workspace_id"]))
    session.add(
        MarketActivityEvent(
            organization_id=organization_id,
            workspace_id=workspace_id,
            actor_type="SYSTEM",
            aggregate_type="market_source_extraction",
            aggregate_id=workflow.id,
            event_type="MARKET_SOURCE_EXTRACTION_FAILED",
            payload={"job_id": str(job_id), "failure_code": "RETRIES_EXHAUSTED"},
        )
    )
    session.flush()
