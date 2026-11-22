"""Market source versioning and evidence-backed Insight use cases."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from sqlalchemy import and_, exists, not_, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.api.errors import Conflict, NotFound, Unprocessable
from app.models.market import (
    MarketActivityEvent,
    MarketInsight,
    MarketInsightEvidence,
    MarketResearchPlanEvidence,
    MarketResearchQuestion,
    MarketEvidenceDerivation,
    MarketWorkspace,
    MarketWorkspaceWorkflow,
)
from app.models.workflow import AgentRun, Evidence, WorkflowInstance


def canonicalize_source_ref(value: str) -> str:
    """Normalize only URL syntax, not source semantics or arbitrary query parameters."""
    raw = value.strip()
    try:
        parts = urlsplit(raw)
        scheme = parts.scheme.lower()
        if scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("scheme")
        if parts.username is not None or parts.password is not None:
            raise ValueError("credentials")
        hostname = parts.hostname.encode("idna").decode("ascii").lower()
        port = parts.port
    except (ValueError, UnicodeError) as exc:
        raise Unprocessable(
            "来源地址必须是有效的 HTTP(S) URL，且不得包含用户名或密码"
        ) from exc
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = hostname if port is None or default_port else f"{hostname}:{port}"
    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def _workspace(
    session: Session, organization_id: UUID | str, workspace_id: UUID
) -> MarketWorkspace:
    row = session.scalars(
        select(MarketWorkspace).where(
            MarketWorkspace.organization_id == organization_id,
            MarketWorkspace.id == workspace_id,
        )
    ).one_or_none()
    if row is None:
        raise NotFound("市场工作空间不存在", details={"workspace_id": str(workspace_id)})
    return row


def record_market_evidence(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    *,
    canonical_ref: str,
    content: str,
    kind: str,
    source: str | None,
    published_at: datetime | None,
    observation: str | None,
    verification_status: str,
    quality: dict[str, Any] | None,
    payload: dict[str, Any],
    actor_user_id: UUID | None,
    content_hash_override: str | None = None,
) -> Evidence:
    _workspace(session, organization_id, workspace_id)
    canonical = canonicalize_source_ref(canonical_ref)
    if content_hash_override is None:
        digest = sha256(content.encode("utf-8")).hexdigest()
    else:
        digest = content_hash_override.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise Unprocessable("content_hash_override 必须是 64 位 SHA-256 十六进制值")
    identity = sha256(f"{canonical}\n{digest}".encode()).hexdigest()
    version_ref = f"market-source:v1:{identity}"
    stored_payload = {**payload, "content": content}
    statement = (
        pg_insert(Evidence.__table__)
        .values(
            organization_id=str(organization_id),
            subject_type="market_workspace",
            subject_id=str(workspace_id),
            kind=kind.strip().upper(),
            ref=version_ref,
            source=source.strip() if source and source.strip() else None,
            payload=stored_payload,
            retrieved_at=datetime.now(UTC),
            published_at=published_at,
            canonical_ref=canonical,
            content_hash=digest,
            observation=observation,
            verification_status=verification_status,
            quality=quality,
        )
        .on_conflict_do_nothing(constraint="uq_evidence_org_subject_kind_ref")
        .returning(Evidence.__table__.c.id)
    )
    evidence_id = session.execute(statement).scalar_one_or_none()
    if evidence_id is None:
        row = session.scalars(
            select(Evidence).where(
                Evidence.organization_id == organization_id,
                Evidence.subject_type == "market_workspace",
                Evidence.subject_id == workspace_id,
                Evidence.kind == kind.strip().upper(),
                Evidence.ref == version_ref,
            )
        ).one_or_none()
        if row is None:
            raise Conflict("来源证据写入发生并发冲突，请重试")
        return row
    session.flush()
    row = session.get(Evidence, evidence_id)
    if row is None:
        raise Conflict("来源证据写入后不可见")
    session.add(
        MarketActivityEvent(
            organization_id=organization_id,
            workspace_id=workspace_id,
            actor_type="USER" if actor_user_id is not None else "SYSTEM",
            actor_user_id=actor_user_id,
            aggregate_type="market_evidence",
            aggregate_id=row.id,
            event_type="MARKET_EVIDENCE_RECORDED",
            payload={
                "kind": kind.strip().upper(),
                "canonical_ref": canonical,
                "content_hash": digest,
            },
        )
    )
    session.flush()
    return row


def question_evidence_scope(
    organization_id: UUID | str,
    workspace_id: UUID,
    question_id: UUID,
    workflow_ids: list[UUID],
):
    """Return Evidence visible to one question, preserving legacy/shared Workspace data.

    Question-scoped Evidence is visible only through an explicit relation. Legacy
    rows with question_id NULL and user-added Workspace Evidence remain shared.
    """
    links_for_evidence = exists(
        select(1).where(
            MarketResearchPlanEvidence.organization_id == organization_id,
            MarketResearchPlanEvidence.workspace_id == workspace_id,
            MarketResearchPlanEvidence.evidence_id == Evidence.id,
            MarketResearchPlanEvidence.question_id.is_not(None),
        )
    )
    target_link = exists(
        select(1).where(
            MarketResearchPlanEvidence.organization_id == organization_id,
            MarketResearchPlanEvidence.workspace_id == workspace_id,
            MarketResearchPlanEvidence.evidence_id == Evidence.id,
            MarketResearchPlanEvidence.question_id == question_id,
        )
    )
    shared_link = exists(
        select(1).where(
            MarketResearchPlanEvidence.organization_id == organization_id,
            MarketResearchPlanEvidence.workspace_id == workspace_id,
            MarketResearchPlanEvidence.evidence_id == Evidence.id,
            MarketResearchPlanEvidence.question_id.is_(None),
        )
    )
    scope_managed = Evidence.payload["question_scope_managed"].as_boolean().is_(True)
    workspace_evidence = and_(
        Evidence.subject_type == "market_workspace",
        Evidence.subject_id == workspace_id,
        or_(
            target_link,
            shared_link,
            and_(not_(links_for_evidence), not_(scope_managed)),
        ),
    )
    # Legacy ResearchPlans that predate question targeting are shared for
    # backwards compatibility. Once a plan has target_question_id, its subject-scoped
    # Evidence must stay inside that exact question.
    legacy_or_matching_plan_ids = select(WorkflowInstance.id).where(
        WorkflowInstance.organization_id == organization_id,
        WorkflowInstance.id.in_(workflow_ids) if workflow_ids else WorkflowInstance.id.is_(None),
        WorkflowInstance.workflow_type == "research_plan",
        or_(
            WorkflowInstance.payload["target_question_id"].as_string().is_(None),
            WorkflowInstance.payload["target_question_id"].as_string() == str(question_id),
        ),
    )
    scopes = [
        workspace_evidence,
        and_(
            Evidence.subject_type == "RESEARCH_PLAN",
            Evidence.subject_id.in_(legacy_or_matching_plan_ids),
        ),
    ]
    return and_(Evidence.organization_id == organization_id, or_(*scopes))


def link_research_plan_evidence(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    research_plan_id: UUID,
    evidence_id: UUID,
    *,
    search_query: str,
    collected_by_run_id: UUID,
    question_id: UUID | None = None,
) -> bool:
    """Attach shared Workspace Evidence to the ResearchPlan/query that discovered it.

    Returns True only for a newly inserted relation. The Evidence row itself remains
    immutable and deduplicated by source identity + content hash at Workspace scope.
    """
    query_text = search_query.strip()[:500]
    if not query_text:
        raise Unprocessable("ResearchPlan Evidence 关联必须有明确的搜索查询")

    workflow_link = session.scalars(
        select(MarketWorkspaceWorkflow).where(
            MarketWorkspaceWorkflow.organization_id == organization_id,
            MarketWorkspaceWorkflow.workspace_id == workspace_id,
            MarketWorkspaceWorkflow.workflow_id == research_plan_id,
        )
    ).one_or_none()
    if workflow_link is None:
        raise Conflict("ResearchPlan 未关联到当前 Workspace")
    research_plan = session.scalars(
        select(WorkflowInstance).where(
            WorkflowInstance.organization_id == organization_id,
            WorkflowInstance.id == research_plan_id,
            WorkflowInstance.workflow_type == "research_plan",
        )
    ).one_or_none()
    if research_plan is None:
        raise Conflict("来源 Workflow 不是有效的 ResearchPlan")
    target_question_id = (research_plan.payload or {}).get("target_question_id")
    if target_question_id:
        if question_id is None or str(target_question_id) != str(question_id):
            raise Conflict("Evidence 的 question_id 必须与 ResearchPlan 的定向问题一致")
    elif question_id is not None:
        raise Conflict("未定向 ResearchPlan 不能写入问题级 Evidence 关联")
    if question_id is not None:
        target_question = session.scalars(
            select(MarketResearchQuestion).where(
                MarketResearchQuestion.organization_id == organization_id,
                MarketResearchQuestion.workspace_id == workspace_id,
                MarketResearchQuestion.id == question_id,
            )
        ).one_or_none()
        if target_question is None:
            raise Conflict("目标研究问题不属于当前 Workspace")

    run = session.scalars(
        select(AgentRun).where(
            AgentRun.organization_id == organization_id,
            AgentRun.id == collected_by_run_id,
            AgentRun.workflow_id == research_plan_id,
        )
    ).one_or_none()
    if run is None:
        raise Conflict("证据采集 AgentRun 不属于当前 ResearchPlan")

    evidence = session.scalars(
        select(Evidence).where(
            Evidence.organization_id == organization_id,
            Evidence.id == evidence_id,
            Evidence.subject_type == "market_workspace",
            Evidence.subject_id == workspace_id,
        )
    ).one_or_none()
    if evidence is None:
        raise Conflict("只能将当前 Workspace 的版本化 Evidence 关联到 ResearchPlan")

    query_sha256 = sha256(query_text.encode()).hexdigest()
    statement = (
        pg_insert(MarketResearchPlanEvidence.__table__)
        .values(
            organization_id=str(organization_id),
            workspace_id=str(workspace_id),
            research_plan_id=str(research_plan_id),
            evidence_id=str(evidence_id),
            question_id=str(question_id) if question_id is not None else None,
            search_query=query_text,
            query_sha256=query_sha256,
            collected_by_run_id=str(collected_by_run_id),
        )
        .on_conflict_do_nothing(constraint="uq_market_research_plan_evidence_query")
        .returning(MarketResearchPlanEvidence.__table__.c.id)
    )
    link_id = session.execute(statement).scalar_one_or_none()
    if link_id is None:
        return False

    session.add(
        MarketActivityEvent(
            organization_id=organization_id,
            workspace_id=workspace_id,
            actor_type="SYSTEM",
            actor_user_id=None,
            aggregate_type="research_plan",
            aggregate_id=research_plan_id,
            event_type="MARKET_RESEARCH_EVIDENCE_LINKED",
            payload={
                "evidence_id": str(evidence_id),
                "question_id": str(question_id) if question_id is not None else None,
                "query_sha256": query_sha256,
                "agent_run_id": str(collected_by_run_id),
            },
        )
    )
    session.flush()
    return True


def inherit_research_plan_evidence_links(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    source_evidence_id: UUID,
    derived_evidence_id: UUID,
    *,
    extraction_workflow_id: UUID,
    extraction_agent_run_id: UUID,
) -> int:
    """Copy query/question provenance from a selected source to its extracted page version."""
    derivation = session.scalars(
        select(MarketEvidenceDerivation).where(
            MarketEvidenceDerivation.organization_id == organization_id,
            MarketEvidenceDerivation.workspace_id == workspace_id,
            MarketEvidenceDerivation.workflow_id == extraction_workflow_id,
            MarketEvidenceDerivation.source_evidence_id == source_evidence_id,
            MarketEvidenceDerivation.derived_evidence_id == derived_evidence_id,
            MarketEvidenceDerivation.agent_run_id == extraction_agent_run_id,
        )
    ).one_or_none()
    if derivation is None:
        raise Conflict("网页正文派生关系不存在，不能继承问题级证据范围")

    source_links = session.scalars(
        select(MarketResearchPlanEvidence).where(
            MarketResearchPlanEvidence.organization_id == organization_id,
            MarketResearchPlanEvidence.workspace_id == workspace_id,
            MarketResearchPlanEvidence.evidence_id == source_evidence_id,
        )
    ).all()
    if not source_links:
        return 0

    inserted = 0
    for link in source_links:
        statement = (
            pg_insert(MarketResearchPlanEvidence.__table__)
            .values(
                organization_id=str(organization_id),
                workspace_id=str(workspace_id),
                research_plan_id=str(link.research_plan_id),
                question_id=str(link.question_id) if link.question_id is not None else None,
                evidence_id=str(derived_evidence_id),
                search_query=link.search_query,
                query_sha256=link.query_sha256,
                collected_by_run_id=str(extraction_agent_run_id),
            )
            .on_conflict_do_nothing(constraint="uq_market_research_plan_evidence_query")
            .returning(MarketResearchPlanEvidence.__table__.c.id)
        )
        if session.execute(statement).scalar_one_or_none() is not None:
            inserted += 1
    if inserted:
        session.add(
            MarketActivityEvent(
                organization_id=organization_id,
                workspace_id=workspace_id,
                actor_type="SYSTEM",
                actor_user_id=None,
                aggregate_type="market_evidence",
                aggregate_id=derived_evidence_id,
                event_type="MARKET_EVIDENCE_SCOPE_INHERITED",
                payload={
                    "source_evidence_id": str(source_evidence_id),
                    "extraction_workflow_id": str(extraction_workflow_id),
                    "linked_research_plan_count": inserted,
                },
            )
        )
        session.flush()
    return inserted


def list_research_plan_linked_evidence(
    session: Session,
    organization_id: UUID | str,
    research_plan_id: UUID,
) -> list[Evidence]:
    """Return deduplicated Workspace-level Evidence attached to one ResearchPlan."""
    evidence_ids = select(MarketResearchPlanEvidence.evidence_id).where(
        MarketResearchPlanEvidence.organization_id == organization_id,
        MarketResearchPlanEvidence.research_plan_id == research_plan_id,
    )
    return list(
        session.scalars(
            select(Evidence)
            .where(
                Evidence.organization_id == organization_id,
                Evidence.id.in_(evidence_ids),
            )
            .order_by(Evidence.retrieved_at.desc().nullslast(), Evidence.created_at.desc())
        ).all()
    )


def list_workspace_evidence(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[Evidence]:
    _workspace(session, organization_id, workspace_id)
    workflow_ids = list(
        session.scalars(
            select(MarketWorkspaceWorkflow.workflow_id).where(
                MarketWorkspaceWorkflow.organization_id == organization_id,
                MarketWorkspaceWorkflow.workspace_id == workspace_id,
            )
        ).all()
    )
    scope = [
        (Evidence.subject_type == "market_workspace")
        & (Evidence.subject_id == workspace_id)
    ]
    if workflow_ids:
        scope.append(
            (Evidence.subject_type == "RESEARCH_PLAN")
            & Evidence.subject_id.in_(workflow_ids)
        )
    return list(
        session.scalars(
            select(Evidence)
            .where(Evidence.organization_id == organization_id, or_(*scope))
            .order_by(Evidence.retrieved_at.desc().nullslast(), Evidence.created_at.desc())
            .limit(limit)
            .offset(offset)
        ).all()
    )


def _workspace_evidence_map(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    evidence_ids: list[UUID],
) -> dict[UUID, Evidence]:
    if not evidence_ids:
        return {}
    rows = session.scalars(
        select(Evidence).where(
            Evidence.organization_id == organization_id,
            Evidence.id.in_(evidence_ids),
        )
    ).all()
    by_id = {row.id: row for row in rows}
    if len(by_id) != len(set(evidence_ids)):
        raise NotFound("部分证据不存在", details={"evidence_ids": [str(x) for x in evidence_ids]})
    workflow_ids = set(
        session.scalars(
            select(MarketWorkspaceWorkflow.workflow_id).where(
                MarketWorkspaceWorkflow.organization_id == organization_id,
                MarketWorkspaceWorkflow.workspace_id == workspace_id,
            )
        ).all()
    )
    for row in by_id.values():
        belongs = (
            row.subject_type == "market_workspace" and row.subject_id == workspace_id
        ) or (row.subject_type == "RESEARCH_PLAN" and row.subject_id in workflow_ids)
        if not belongs:
            raise Conflict(
                "Insight 只能引用当前 Workspace 或其关联 ResearchPlan 的 Evidence",
                details={"evidence_id": str(row.id)},
            )
    return by_id


def _serialize_insights(
    session: Session,
    organization_id: UUID | str,
    rows: list[MarketInsight],
) -> list[dict[str, Any]]:
    if not rows:
        return []
    insight_ids = [row.id for row in rows]
    links = session.scalars(
        select(MarketInsightEvidence)
        .where(
            MarketInsightEvidence.organization_id == organization_id,
            MarketInsightEvidence.insight_id.in_(insight_ids),
        )
        .order_by(MarketInsightEvidence.created_at, MarketInsightEvidence.id)
    ).all()
    evidence_ids = list({link.evidence_id for link in links})
    evidence_rows = (
        session.scalars(
            select(Evidence).where(
                Evidence.organization_id == organization_id,
                Evidence.id.in_(evidence_ids),
            )
        ).all()
        if evidence_ids else []
    )
    evidence_by_id = {row.id: row for row in evidence_rows}
    links_by_insight: dict[UUID, list[dict[str, Any]]] = {}
    for link in links:
        evidence = evidence_by_id.get(link.evidence_id)
        if evidence is not None:
            links_by_insight.setdefault(link.insight_id, []).append(
                {
                    "relation_type": link.relation_type,
                    "rationale": link.rationale,
                    "evidence": evidence,
                }
            )
    result = []
    for row in rows:
        result.append(
            {
                "id": row.id,
                "workspace_id": row.workspace_id,
                "version": row.version,
                "insight_key": row.insight_key,
                "insight_type": row.insight_type,
                "statement": row.statement,
                "assumptions": row.assumptions,
                "unknowns": row.unknowns,
                "implications": row.implications,
                "confidence_level": row.confidence_level,
                "status": row.status,
                "generated_by_run_id": row.generated_by_run_id,
                "supersedes_id": row.supersedes_id,
                "insufficient_evidence_reason": row.insufficient_evidence_reason,
                "created_by_user_id": row.created_by_user_id,
                "reviewed_by_user_id": row.reviewed_by_user_id,
                "reviewed_at": row.reviewed_at,
                "review_note": row.review_note,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
                "evidence": links_by_insight.get(row.id, []),
            }
        )
    return result


def list_insights(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    _workspace(session, organization_id, workspace_id)
    rows = list(
        session.scalars(
            select(MarketInsight)
            .where(
                MarketInsight.organization_id == organization_id,
                MarketInsight.workspace_id == workspace_id,
            )
            .order_by(MarketInsight.created_at.desc(), MarketInsight.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
    )
    return _serialize_insights(session, organization_id, rows)


def create_insight(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    *,
    insight_key: str,
    insight_type: str,
    statement: str,
    assumptions: list[Any],
    unknowns: list[Any],
    implications: list[Any],
    confidence_level: str,
    generated_by_run_id: UUID | None,
    evidence: list[dict[str, Any]],
    insufficient_evidence_reason: str | None,
    created_by_user_id: UUID,
) -> dict[str, Any]:
    # Lock the Workspace so allocation is race-safe even for the first version of a key.
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

    if generated_by_run_id is not None:
        workspace_workflow_ids = set(
            session.scalars(
                select(MarketWorkspaceWorkflow.workflow_id).where(
                    MarketWorkspaceWorkflow.organization_id == organization_id,
                    MarketWorkspaceWorkflow.workspace_id == workspace_id,
                )
            ).all()
        )
        run = session.scalars(
            select(AgentRun).where(
                AgentRun.organization_id == organization_id,
                AgentRun.id == generated_by_run_id,
                AgentRun.workflow_id.in_(workspace_workflow_ids),
            )
        ).one_or_none()
        if run is None:
            raise Conflict(
                "Insight 的 AgentRun 必须属于当前 Workspace 关联的 ResearchPlan",
                details={"run_id": str(generated_by_run_id)},
            )

    evidence_ids = [item["evidence_id"] for item in evidence]
    evidence_map = _workspace_evidence_map(session, organization_id, workspace_id, evidence_ids)
    latest = session.scalars(
        select(MarketInsight)
        .where(
            MarketInsight.organization_id == organization_id,
            MarketInsight.workspace_id == workspace_id,
            MarketInsight.insight_key == insight_key.strip(),
        )
        .order_by(MarketInsight.version.desc())
        .limit(1)
    ).one_or_none()
    version = latest.version + 1 if latest is not None else 1
    link_types = {item["relation_type"] for item in evidence}
    status = (
        "CONFLICTED"
        if "SUPPORTS" in link_types and "CONTRADICTS" in link_types
        else "NEEDS_REVIEW"
    )
    row = MarketInsight(
        organization_id=organization_id,
        workspace_id=workspace_id,
        version=version,
        insight_key=insight_key.strip(),
        insight_type=insight_type.strip(),
        statement=statement.strip(),
        assumptions=assumptions,
        unknowns=unknowns,
        implications=implications,
        confidence_level=confidence_level,
        status=status,
        generated_by_run_id=generated_by_run_id,
        supersedes_id=latest.id if latest is not None else None,
        insufficient_evidence_reason=(
            insufficient_evidence_reason.strip()
            if insufficient_evidence_reason and insufficient_evidence_reason.strip()
            else None
        ),
        created_by_user_id=created_by_user_id,
    )
    session.add(row)
    session.flush()
    for item in evidence:
        if item["evidence_id"] not in evidence_map:
            raise NotFound("证据不存在")
        session.add(
            MarketInsightEvidence(
                organization_id=organization_id,
                insight_id=row.id,
                evidence_id=item["evidence_id"],
                relation_type=item["relation_type"],
                rationale=item.get("rationale"),
            )
        )
    session.add(
        MarketActivityEvent(
            organization_id=organization_id,
            workspace_id=workspace_id,
            actor_type="USER",
            actor_user_id=created_by_user_id,
            aggregate_type="market_insight",
            aggregate_id=row.id,
            event_type="MARKET_INSIGHT_CREATED",
            payload={"insight_key": row.insight_key, "version": version, "status": status},
        )
    )
    session.flush()
    return _serialize_insights(session, organization_id, [row])[0]


def review_insight(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    insight_id: UUID,
    *,
    decision: str,
    actor_user_id: UUID,
    review_note: str | None,
) -> dict[str, Any]:
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
    row = session.scalars(
        select(MarketInsight)
        .where(
            MarketInsight.organization_id == organization_id,
            MarketInsight.workspace_id == workspace_id,
            MarketInsight.id == insight_id,
        )
        .with_for_update()
    ).one_or_none()
    if row is None:
        raise NotFound("市场判断不存在", details={"insight_id": str(insight_id)})
    if row.status in {"ACCEPTED", "REJECTED", "SUPERSEDED"}:
        raise Conflict("该版本已完成审核；如需修订，请创建新版本", details={"status": row.status})
    if decision == "ACCEPTED":
        relation_types = session.scalars(
            select(MarketInsightEvidence.relation_type).where(
                MarketInsightEvidence.organization_id == organization_id,
                MarketInsightEvidence.insight_id == insight_id,
            )
        ).all()
        if "SUPPORTS" not in relation_types or "CONTRADICTS" in relation_types:
            raise Conflict(
                "采纳判断前必须至少有一条支持证据，且不得存在未解决的反驳证据",
                details={"relations": list(relation_types)},
            )
        previous = session.scalars(
            select(MarketInsight).where(
                MarketInsight.organization_id == organization_id,
                MarketInsight.workspace_id == workspace_id,
                MarketInsight.insight_key == row.insight_key,
                MarketInsight.status == "ACCEPTED",
                MarketInsight.id != row.id,
            )
        ).all()
        for item in previous:
            item.status = "SUPERSEDED"
        # Release the partial unique slot before marking this version ACCEPTED.
        session.flush()
    elif decision != "REJECTED":
        raise Unprocessable("不支持的 Insight 审核动作")
    row.status = decision
    row.reviewed_by_user_id = actor_user_id
    row.reviewed_at = datetime.now(UTC)
    row.review_note = review_note.strip() if review_note and review_note.strip() else None
    session.add(
        MarketActivityEvent(
            organization_id=organization_id,
            workspace_id=workspace_id,
            actor_type="USER",
            actor_user_id=actor_user_id,
            aggregate_type="market_insight",
            aggregate_id=row.id,
            event_type=f"MARKET_INSIGHT_{decision}",
            payload={"insight_key": row.insight_key, "version": row.version},
        )
    )
    session.flush()
    return _serialize_insights(session, organization_id, [row])[0]
