"""Claim-level verification use cases for one explicitly selected answer version."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import Conflict, NotFound, Unprocessable
from app.llm.prompts import MARKET_CLAIM_VERIFY_PROMPT_VERSION
from app.models.market import (
    MarketActivityEvent,
    MarketClaim,
    MarketClaimEvidence,
    MarketClaimVerificationRun,
    MarketResearchQuestionAnswer,
    MarketResearchQuestionAnswerEvidence,
    MarketWorkspace,
    MarketWorkspaceWorkflow,
)
from app.models.reliability import BackgroundJob
from app.models.workflow import Evidence, WorkflowInstance
from app.services import job_service

WORKFLOW_TYPE = "market_claim_verification"
MAX_EVIDENCE_PER_VERIFICATION = 24


def compute_input_digest(
    answer: MarketResearchQuestionAnswer,
    evidence_rows: list[Evidence],
) -> str:
    """Hash every material input sent to the agent plus the full source body fingerprint."""
    evidence_material: list[dict[str, Any]] = []
    for row in evidence_rows:
        body = str((row.payload or {}).get("content") or "").strip()
        reference = row.canonical_ref or row.ref
        try:
            host = urlsplit(reference).hostname
        except ValueError:
            host = None
        evidence_material.append(
            {
                "id": str(row.id),
                "kind": row.kind,
                "source": row.source,
                "source_ref": reference,
                "source_host": host.casefold().rstrip(".") if host else None,
                "published_at": row.published_at.isoformat() if row.published_at else None,
                "content_hash": row.content_hash,
                "verification_status": row.verification_status or "UNVERIFIED",
                "content_sha256": sha256(body.encode("utf-8")).hexdigest(),
                "agent_excerpt_sha256": sha256(body[:4000].encode("utf-8")).hexdigest(),
                "content_truncated": len(body) > 4000,
            }
        )
    material = {
        "answer_id": str(answer.id),
        "answer_version": answer.version,
        "answer_summary": answer.answer_summary,
        "answer_assumptions": answer.assumptions or [],
        "answer_unknowns": answer.unknowns or [],
        "prompt_version": MARKET_CLAIM_VERIFY_PROMPT_VERSION,
        "evidence": evidence_material,
    }
    return sha256(
        json.dumps(
            material,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _workspace(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    *,
    lock: bool = False,
) -> MarketWorkspace:
    query = select(MarketWorkspace).where(
        MarketWorkspace.organization_id == organization_id,
        MarketWorkspace.id == workspace_id,
    )
    if lock:
        query = query.with_for_update()
    row = session.scalars(query).one_or_none()
    if row is None:
        raise NotFound("市场工作空间不存在", details={"workspace_id": str(workspace_id)})
    return row


def enqueue_claim_verification(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    answer_id: UUID,
    *,
    created_by_user_id: UUID,
) -> dict[str, Any]:
    """Queue a claim analysis over an answer's already-linked Evidence snapshot."""
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

    answer = session.scalars(
        select(MarketResearchQuestionAnswer).where(
            MarketResearchQuestionAnswer.organization_id == organization_id,
            MarketResearchQuestionAnswer.workspace_id == workspace_id,
            MarketResearchQuestionAnswer.id == answer_id,
        )
    ).one_or_none()
    if answer is None:
        raise NotFound("研究答案版本不存在或不属于当前 Workspace")
    if answer.status in {"REJECTED", "SUPERSEDED"}:
        raise Conflict("已驳回或已被替代的答案版本不能启动主张核验")

    answer_links = session.scalars(
        select(MarketResearchQuestionAnswerEvidence)
        .where(
            MarketResearchQuestionAnswerEvidence.organization_id == organization_id,
            MarketResearchQuestionAnswerEvidence.workspace_id == workspace_id,
            MarketResearchQuestionAnswerEvidence.answer_id == answer_id,
        )
        .order_by(
            MarketResearchQuestionAnswerEvidence.created_at,
            MarketResearchQuestionAnswerEvidence.id,
        )
    ).all()
    if not answer_links:
        raise Conflict("该答案没有关联 Evidence，不能启动主张级核验")
    evidence_ids = list(dict.fromkeys(link.evidence_id for link in answer_links))
    if len(evidence_ids) > MAX_EVIDENCE_PER_VERIFICATION:
        raise Unprocessable(
            f"一次主张核验最多使用 {MAX_EVIDENCE_PER_VERIFICATION} 条 Evidence"
        )

    evidence_rows = list(
        session.scalars(
            select(Evidence).where(
                Evidence.organization_id == organization_id,
                Evidence.id.in_(evidence_ids),
            )
        ).all()
    )
    by_id = {row.id: row for row in evidence_rows}
    if len(by_id) != len(evidence_ids):
        raise Conflict("答案关联的部分 Evidence 已删除或不再可用")
    workflow_ids = set(
        session.scalars(
            select(MarketWorkspaceWorkflow.workflow_id).where(
                MarketWorkspaceWorkflow.organization_id == organization_id,
                MarketWorkspaceWorkflow.workspace_id == workspace_id,
            )
        ).all()
    )
    for row in by_id.values():
        valid_scope = (
            row.subject_type == "market_workspace" and row.subject_id == workspace_id
        ) or (
            row.subject_type == "RESEARCH_PLAN" and row.subject_id in workflow_ids
        )
        if not valid_scope:
            raise Conflict(
                "答案关联了不属于当前 Workspace 或其 ResearchPlan 的 Evidence",
                details={"evidence_id": str(row.id)},
            )
    ordered_rows = [by_id[item] for item in evidence_ids]
    content_rows = [
        row for row in ordered_rows
        if isinstance((row.payload or {}).get("content"), str)
        and (row.payload or {}).get("content", "").strip()
    ]
    if not content_rows:
        raise Conflict("答案关联的 Evidence 均没有可分析的正文或摘要")

    input_digest = compute_input_digest(answer, content_rows)
    dedupe_key = (
        f"market_claim_verify:{answer_id}:"
        f"{MARKET_CLAIM_VERIFY_PROMPT_VERSION}:{input_digest}"
    )

    existing_job = job_service.find_inflight(session, organization_id, dedupe_key)
    if existing_job is not None:
        payload = existing_job.payload or {}
        return {
            "job_id": existing_job.id,
            "workflow_id": payload.get("workflow_id"),
            "verification_run_id": payload.get("verification_run_id"),
            "status": existing_job.status,
            "evidence_count": len(content_rows),
            "reused": True,
        }

    completed = session.scalars(
        select(MarketClaimVerificationRun)
        .where(
            MarketClaimVerificationRun.organization_id == organization_id,
            MarketClaimVerificationRun.workspace_id == workspace_id,
            MarketClaimVerificationRun.answer_id == answer_id,
            MarketClaimVerificationRun.input_digest == input_digest,
            MarketClaimVerificationRun.status == "COMPLETED",
            MarketClaimVerificationRun.degraded.is_(False),
        )
        .order_by(MarketClaimVerificationRun.created_at.desc())
        .limit(1)
    ).one_or_none()
    if completed is not None:
        return {
            "job_id": None,
            "workflow_id": completed.workflow_id,
            "verification_run_id": completed.id,
            "status": "COMPLETED",
            "evidence_count": len(content_rows),
            "reused": True,
        }

    workflow = WorkflowInstance(
        organization_id=organization_id,
        workflow_type=WORKFLOW_TYPE,
        status="ACCEPTED",
        payload={
            "workspace_id": str(workspace_id),
            "answer_id": str(answer_id),
            "answer_version": answer.version,
            "input_digest": input_digest,
            "prompt_version": MARKET_CLAIM_VERIFY_PROMPT_VERSION,
            "evidence_ids": [str(row.id) for row in content_rows],
            "evidence_count": len(content_rows),
            "verification_status": "QUEUED",
            "requested_by_user_id": str(created_by_user_id),
            "requested_at": datetime.now(UTC).isoformat(),
        },
        created_by_user_id=created_by_user_id,
    )
    session.add(workflow)
    session.flush()
    workflow_link = MarketWorkspaceWorkflow(
        organization_id=organization_id,
        workspace_id=workspace_id,
        workflow_id=workflow.id,
        linked_by_user_id=created_by_user_id,
    )
    session.add(workflow_link)
    # The composite FK on the run points at this association's unique key.
    # Flush explicitly because no ORM relationship connects these two new objects.
    session.flush()

    verification_run = MarketClaimVerificationRun(
        organization_id=organization_id,
        workspace_id=workspace_id,
        answer_id=answer_id,
        workflow_id=workflow.id,
        status="QUEUED",
        prompt_version=MARKET_CLAIM_VERIFY_PROMPT_VERSION,
        input_digest=input_digest,
        claim_count=0,
        limitations=[],
    )
    session.add(verification_run)
    session.flush()

    job: BackgroundJob = job_service.enqueue(
        session,
        organization_id,
        job_type=job_service.JOB_TYPE_MARKET_CLAIM_VERIFY,
        payload={
            "workspace_id": str(workspace_id),
            "answer_id": str(answer_id),
            "workflow_id": str(workflow.id),
            "verification_run_id": str(verification_run.id),
            "evidence_ids": [str(row.id) for row in content_rows],
        },
        dedupe_key=dedupe_key,
        created_by_user_id=created_by_user_id,
    )
    queued_workflow_id = (job.payload or {}).get("workflow_id")
    if queued_workflow_id and str(queued_workflow_id) != str(workflow.id):
        existing_run_id = (job.payload or {}).get("verification_run_id")
        existing_workflow_id = UUID(str(queued_workflow_id))
        session.delete(verification_run)
        session.flush()
        session.delete(workflow_link)
        session.delete(workflow)
        session.flush()
        existing_run = session.scalars(
            select(MarketClaimVerificationRun).where(
                MarketClaimVerificationRun.organization_id == organization_id,
                MarketClaimVerificationRun.workspace_id == workspace_id,
                MarketClaimVerificationRun.id == UUID(str(existing_run_id)),
                MarketClaimVerificationRun.workflow_id == existing_workflow_id,
            )
        ).one_or_none()
        if existing_run is None:
            raise Conflict("检测到并发幂等任务，但未找到对应核验运行记录")
        return {
            "job_id": job.id,
            "workflow_id": existing_workflow_id,
            "verification_run_id": existing_run.id,
            "status": job.status,
            "evidence_count": len(content_rows),
            "reused": True,
        }

    workflow.job_id = job.id
    session.add(
        MarketActivityEvent(
            organization_id=organization_id,
            workspace_id=workspace_id,
            actor_type="USER",
            actor_user_id=created_by_user_id,
            aggregate_type="market_claim_verification",
            aggregate_id=verification_run.id,
            event_type="MARKET_CLAIM_VERIFICATION_QUEUED",
            payload={
                "answer_id": str(answer_id),
                "workflow_id": str(workflow.id),
                "job_id": str(job.id),
                "evidence_count": len(content_rows),
                "prompt_version": MARKET_CLAIM_VERIFY_PROMPT_VERSION,
            },
        )
    )
    session.flush()
    return {
        "job_id": job.id,
        "workflow_id": workflow.id,
        "verification_run_id": verification_run.id,
        "status": job.status,
        "evidence_count": len(content_rows),
        "reused": False,
    }


def _serialize_claims(
    session: Session,
    organization_id: UUID | str,
    claims: list[MarketClaim],
) -> list[dict[str, Any]]:
    if not claims:
        return []
    claim_ids = [row.id for row in claims]
    links = session.scalars(
        select(MarketClaimEvidence)
        .where(
            MarketClaimEvidence.organization_id == organization_id,
            MarketClaimEvidence.claim_id.in_(claim_ids),
        )
        .order_by(MarketClaimEvidence.created_at, MarketClaimEvidence.id)
    ).all()
    evidence_ids = list({link.evidence_id for link in links})
    evidence_rows = (
        session.scalars(
            select(Evidence).where(
                Evidence.organization_id == organization_id,
                Evidence.id.in_(evidence_ids),
            )
        ).all()
        if evidence_ids
        else []
    )
    evidence_by_id = {row.id: row for row in evidence_rows}
    workspace_id = claims[0].workspace_id
    linked_workflow_ids = set(
        session.scalars(
            select(MarketWorkspaceWorkflow.workflow_id).where(
                MarketWorkspaceWorkflow.organization_id == organization_id,
                MarketWorkspaceWorkflow.workspace_id == workspace_id,
            )
        ).all()
    )
    for evidence in evidence_rows:
        valid_scope = (
            evidence.subject_type == "market_workspace"
            and evidence.subject_id == workspace_id
        ) or (
            evidence.subject_type == "RESEARCH_PLAN"
            and evidence.subject_id in linked_workflow_ids
        )
        if not valid_scope:
            raise Conflict("主张证据关联包含不属于当前 Workspace 的 Evidence")
    links_by_claim: dict[UUID, list[dict[str, Any]]] = {}
    for link in links:
        evidence = evidence_by_id.get(link.evidence_id)
        if evidence is not None:
            links_by_claim.setdefault(link.claim_id, []).append(
                {
                    "evidence_id": evidence.id,
                    "relation_type": link.relation_type,
                    "rationale": link.rationale,
                    "source_host": link.source_host,
                    "content_hash_snapshot": link.content_hash_snapshot,
                    "verification_status_snapshot": link.verification_status_snapshot,
                    "origin_assessment": link.origin_assessment,
                    "evidence": {
                        "id": evidence.id,
                        "kind": evidence.kind,
                        "source": evidence.source,
                        "canonical_ref": evidence.canonical_ref,
                        "verification_status": evidence.verification_status or "UNVERIFIED",
                        "content_hash": evidence.content_hash,
                        "observation": evidence.observation,
                        "created_at": evidence.created_at,
                    },
                }
            )
    return [
        {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "answer_id": row.answer_id,
            "verification_run_id": row.verification_run_id,
            "version": row.version,
            "claim_key": row.claim_key,
            "statement": row.statement,
            "claim_type": row.claim_type,
            "scope": row.scope,
            "reasoning": row.reasoning,
            "verification_verdict": row.verification_verdict,
            "confidence_level": row.confidence_level,
            "status": row.status,
            "supporting_evidence_count": row.supporting_evidence_count,
            "contradicting_evidence_count": row.contradicting_evidence_count,
            "verified_support_count": row.verified_support_count,
            "verified_contradiction_count": row.verified_contradiction_count,
            "distinct_host_count": row.distinct_host_count,
            "confirmed_independent_source_count": row.confirmed_independent_source_count,
            "limitations": row.limitations,
            "supersedes_id": row.supersedes_id,
            "reviewed_by_user_id": row.reviewed_by_user_id,
            "reviewed_at": row.reviewed_at,
            "review_note": row.review_note,
            "created_at": row.created_at,
            "evidence": links_by_claim.get(row.id, []),
        }
        for row in claims
    ]


def list_claims(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    *,
    answer_id: UUID | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    _workspace(session, organization_id, workspace_id)
    query = select(MarketClaim).where(
        MarketClaim.organization_id == organization_id,
        MarketClaim.workspace_id == workspace_id,
    )
    if answer_id is not None:
        query = query.where(MarketClaim.answer_id == answer_id)
    rows = list(
        session.scalars(
            query.order_by(MarketClaim.created_at.desc(), MarketClaim.id)
            .offset(offset)
            .limit(limit)
        )
    )
    return _serialize_claims(session, organization_id, rows)


def get_verification_run(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    verification_run_id: UUID,
) -> dict[str, Any]:
    row = session.scalars(
        select(MarketClaimVerificationRun).where(
            MarketClaimVerificationRun.organization_id == organization_id,
            MarketClaimVerificationRun.workspace_id == workspace_id,
            MarketClaimVerificationRun.id == verification_run_id,
        )
    ).one_or_none()
    if row is None:
        raise NotFound("主张核验任务不存在或不属于当前 Workspace")
    workflow = session.scalars(
        select(WorkflowInstance).where(
            WorkflowInstance.organization_id == organization_id,
            WorkflowInstance.id == row.workflow_id,
        )
    ).one_or_none()
    job = (
        session.scalars(
            select(BackgroundJob).where(
                BackgroundJob.organization_id == organization_id,
                BackgroundJob.id == workflow.job_id,
            )
        ).one_or_none()
        if workflow is not None and workflow.job_id is not None
        else None
    )
    claims = list(
        session.scalars(
            select(MarketClaim)
            .where(
                MarketClaim.organization_id == organization_id,
                MarketClaim.workspace_id == workspace_id,
                MarketClaim.verification_run_id == verification_run_id,
            )
            .order_by(MarketClaim.created_at, MarketClaim.id)
        )
    )
    status_value = row.status
    failure_reason = row.failure_reason
    if job is not None and job.status in {"DEAD", "CANCELLED"} and status_value not in {"COMPLETED", "FAILED"}:
        status_value = "FAILED"
        failure_reason = job.last_error or (
            "核验任务在执行前被取消。" if job.status == "CANCELLED" else failure_reason
        )
    return {
        "id": row.id,
        "workspace_id": row.workspace_id,
        "answer_id": row.answer_id,
        "workflow_id": row.workflow_id,
        "agent_run_id": row.agent_run_id,
        "job_id": job.id if job is not None else None,
        "job_status": job.status if job is not None else None,
        "status": status_value,
        "prompt_version": row.prompt_version,
        "input_digest": row.input_digest,
        "claim_count": row.claim_count,
        "degraded": row.degraded,
        "limitations": row.limitations,
        "failure_reason": failure_reason,
        "started_at": row.started_at,
        "completed_at": row.completed_at,
        "created_at": row.created_at,
        "claims": _serialize_claims(session, organization_id, claims),
    }


def review_claim(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    claim_id: UUID,
    *,
    decision: str,
    actor_user_id: UUID,
    review_note: str | None,
) -> dict[str, Any]:
    _workspace(session, organization_id, workspace_id, lock=True)
    row = session.scalars(
        select(MarketClaim)
        .where(
            MarketClaim.organization_id == organization_id,
            MarketClaim.workspace_id == workspace_id,
            MarketClaim.id == claim_id,
        )
        .with_for_update()
    ).one_or_none()
    if row is None:
        raise NotFound("研究主张不存在或不属于当前 Workspace")
    if row.status in {"ACCEPTED", "REJECTED", "SUPERSEDED"}:
        raise Conflict("该主张版本已完成审核；如需重新核验，请启动新的核验运行")
    session.scalars(
        select(MarketResearchQuestionAnswer)
        .where(
            MarketResearchQuestionAnswer.organization_id == organization_id,
            MarketResearchQuestionAnswer.workspace_id == workspace_id,
            MarketResearchQuestionAnswer.id == row.answer_id,
        )
        .with_for_update()
    ).one()
    note = review_note.strip() if review_note and review_note.strip() else None
    if not note:
        raise Unprocessable("人工审核主张必须填写审核说明")
    if decision == "ACCEPTED":
        if row.verification_verdict != "PROVISIONAL_SUPPORT":
            raise Conflict(
                "只有存在支持证据且没有检测到反驳证据的主张才能进入人工采纳",
                details={"verification_verdict": row.verification_verdict},
            )
        if row.supporting_evidence_count < 1 or row.contradicting_evidence_count > 0:
            raise Conflict("主张的证据关系尚不满足人工采纳条件")
    elif decision != "REJECTED":
        raise Unprocessable("不支持的主张审核动作")

    if decision == "ACCEPTED":
        previous = session.scalars(
            select(MarketClaim).where(
                MarketClaim.organization_id == organization_id,
                MarketClaim.workspace_id == workspace_id,
                MarketClaim.answer_id == row.answer_id,
                MarketClaim.claim_key == row.claim_key,
                MarketClaim.status == "ACCEPTED",
                MarketClaim.id != row.id,
            )
        ).all()
        for item in previous:
            item.status = "SUPERSEDED"
        session.flush()

    row.status = decision
    row.reviewed_by_user_id = actor_user_id
    row.reviewed_at = datetime.now(UTC)
    row.review_note = note
    session.add(
        MarketActivityEvent(
            organization_id=organization_id,
            workspace_id=workspace_id,
            actor_type="USER",
            actor_user_id=actor_user_id,
            aggregate_type="market_claim",
            aggregate_id=row.id,
            event_type=f"MARKET_CLAIM_{decision}",
            payload={
                "answer_id": str(row.answer_id),
                "claim_key": row.claim_key,
                "version": row.version,
                "verification_verdict": row.verification_verdict,
            },
        )
    )
    session.flush()
    return _serialize_claims(session, organization_id, [row])[0]


def mark_job_failed(
    session: Session,
    organization_id: UUID | str,
    job_id: UUID | str,
    *,
    error: str,
) -> None:
    """Persist a terminal claim-verification failure after the shared retry policy."""
    job_uuid = UUID(str(job_id))
    workflow = session.scalars(
        select(WorkflowInstance)
        .where(
            WorkflowInstance.organization_id == organization_id,
            WorkflowInstance.workflow_type == WORKFLOW_TYPE,
            WorkflowInstance.job_id == job_uuid,
        )
        .with_for_update()
    ).one_or_none()
    if workflow is None:
        return
    payload = {
        **(workflow.payload or {}),
        "verification_status": "FAILED",
        "failure_reason": error[:2000],
        "failed_at": datetime.now(UTC).isoformat(),
    }
    workflow.payload = payload
    workflow.status = "FAILED"
    workspace_id = UUID(str(payload["workspace_id"]))
    verification_run = session.scalars(
        select(MarketClaimVerificationRun)
        .where(
            MarketClaimVerificationRun.organization_id == organization_id,
            MarketClaimVerificationRun.workspace_id == workspace_id,
            MarketClaimVerificationRun.workflow_id == workflow.id,
        )
        .with_for_update()
    ).one_or_none()
    if verification_run is not None:
        verification_run.status = "FAILED"
        verification_run.failure_reason = error[:2000]
        verification_run.completed_at = verification_run.completed_at or datetime.now(UTC)
    aggregate_id = verification_run.id if verification_run is not None else workflow.id
    failure_event_exists = session.scalars(
        select(MarketActivityEvent.id).where(
            MarketActivityEvent.organization_id == organization_id,
            MarketActivityEvent.workspace_id == workspace_id,
            MarketActivityEvent.aggregate_type == "market_claim_verification",
            MarketActivityEvent.aggregate_id == aggregate_id,
            MarketActivityEvent.event_type == "MARKET_CLAIM_VERIFICATION_FAILED",
        ).limit(1)
    ).first()
    if failure_event_exists is None:
        session.add(
            MarketActivityEvent(
                organization_id=organization_id,
                workspace_id=workspace_id,
                actor_type="SYSTEM",
                aggregate_type="market_claim_verification",
                aggregate_id=aggregate_id,
                event_type="MARKET_CLAIM_VERIFICATION_FAILED",
                payload={"job_id": str(job_uuid), "failure_reason": error[:1000]},
            )
        )
    session.flush()


__all__ = [
    "WORKFLOW_TYPE",
    "MAX_EVIDENCE_PER_VERIFICATION",
    "enqueue_claim_verification",
    "compute_input_digest",
    "get_verification_run",
    "list_claims",
    "review_claim",
    "mark_job_failed",
]
