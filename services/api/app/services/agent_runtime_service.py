"""Shared persistence lifecycle for bounded Agent executions.

WorkflowInstance owns the business process. BackgroundJob owns durable scheduling and retries.
AgentRun owns one bounded Agent execution and its input/output audit. This module centralizes
only the AgentRun lifecycle; domain services remain responsible for workflow state, approvals,
evidence provenance, and business side effects.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import Conflict, NotFound
from app.models.workflow import AgentRun, WorkflowInstance


class AgentRunTransitionError(ValueError):
    """Raised when an AgentRun is finalized more than once or from an invalid state."""


def start_agent_run(
    session: Session,
    organization_id: UUID | str,
    *,
    workflow_id: UUID | str | None,
    agent_type: str,
    input_snapshot: dict[str, Any],
) -> AgentRun:
    """Create an auditable run under an active, tenant-owned workflow.

    Callers must minimize/redact input_snapshot before passing it here. Prefer stable
    entity references, content hashes, prompt versions, and character counts over raw files
    or free-form source material.
    """
    normalized_agent_type = agent_type.strip()
    if not normalized_agent_type:
        raise ValueError("agent_type must not be empty")
    if len(normalized_agent_type) > 120:
        raise ValueError("agent_type must be at most 120 characters")
    if not isinstance(input_snapshot, dict):
        raise TypeError("input_snapshot must be a dictionary")

    normalized_workflow_id: str | None = None
    if workflow_id is not None:
        normalized_workflow_id = str(workflow_id)
        workflow = session.scalars(
            select(WorkflowInstance).where(
                WorkflowInstance.organization_id == str(organization_id),
                WorkflowInstance.id == normalized_workflow_id,
            )
        ).one_or_none()
        if workflow is None:
            raise NotFound(
                "AgentRun 所属工作流不存在",
                details={"workflow_id": normalized_workflow_id},
            )
        if workflow.status not in {"ACCEPTED", "RUNNING"}:
            raise Conflict(
                "工作流已进入终态，不能启动新的 AgentRun",
                details={"workflow_id": normalized_workflow_id, "status": workflow.status},
            )

    run = AgentRun(
        organization_id=str(organization_id),
        workflow_id=normalized_workflow_id,
        agent_type=normalized_agent_type,
        status="RUNNING",
        input=input_snapshot,
    )
    session.add(run)
    session.flush()
    return run


def succeed_agent_run(
    run: AgentRun,
    *,
    output_snapshot: dict[str, Any],
    finished_at: datetime | None = None,
) -> AgentRun:
    """Finalize one running AgentRun successfully."""
    _require_running(run)
    _validate_timestamp(finished_at)
    if not isinstance(output_snapshot, dict):
        raise TypeError("output_snapshot must be a dictionary")

    run.status = "SUCCEEDED"
    run.output = output_snapshot
    run.error = None
    run.finished_at = finished_at or datetime.now(UTC)
    return run


def fail_agent_run(
    run: AgentRun,
    *,
    error: str,
    output_snapshot: dict[str, Any] | None = None,
    finished_at: datetime | None = None,
) -> AgentRun:
    """Finalize one running AgentRun as failed, retaining a bounded diagnostic."""
    _require_running(run)
    _validate_timestamp(finished_at)
    message = str(error).strip()
    if not message:
        raise ValueError("error must not be empty")

    run.status = "FAILED"
    run.error = message[:2000]
    run.output = output_snapshot
    run.finished_at = finished_at or datetime.now(UTC)
    return run


def _require_running(run: AgentRun) -> None:
    if run.status != "RUNNING":
        raise AgentRunTransitionError(
            f"AgentRun {run.id or '<unpersisted>'} cannot transition from {run.status!r}"
        )


def _validate_timestamp(value: datetime | None) -> None:
    if value is not None and value.tzinfo is None:
        raise ValueError("finished_at must be timezone-aware")


__all__ = [
    "AgentRunTransitionError",
    "fail_agent_run",
    "start_agent_run",
    "succeed_agent_run",
]
