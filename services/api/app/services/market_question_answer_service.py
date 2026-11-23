"""Versioned, evidence-backed answers for Workspace research questions."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import Conflict, NotFound, Unprocessable
from app.models.market import (
    MarketActivityEvent,
    MarketResearchQuestion,
    MarketResearchQuestionAnswer,
    MarketResearchQuestionAnswerEvidence,
    MarketWorkspace,
    MarketWorkspaceWorkflow,
)
from app.models.workflow import AgentRun, Evidence


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


def _question(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    question_id: UUID,
    *,
    lock: bool = False,
) -> MarketResearchQuestion:
    query = select(MarketResearchQuestion).where(
        MarketResearchQuestion.organization_id == organization_id,
        MarketResearchQuestion.workspace_id == workspace_id,
        MarketResearchQuestion.id == question_id,
    )
    if lock:
        query = query.with_for_update()
    row = session.scalars(query).one_or_none()
    if row is None:
        raise NotFound("研究问题不存在", details={"question_id": str(question_id)})
    return row


def _answer(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    question_id: UUID,
    answer_id: UUID,
    *,
    lock: bool = False,
) -> MarketResearchQuestionAnswer:
    query = select(MarketResearchQuestionAnswer).where(
        MarketResearchQuestionAnswer.organization_id == organization_id,
        MarketResearchQuestionAnswer.workspace_id == workspace_id,
        MarketResearchQuestionAnswer.question_id == question_id,
        MarketResearchQuestionAnswer.id == answer_id,
    )
    if lock:
        query = query.with_for_update()
    row = session.scalars(query).one_or_none()
    if row is None:
        raise NotFound("研究问题答案版本不存在", details={"answer_id": str(answer_id)})
    return row


def _validate_run(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    run_id: UUID | None,
) -> None:
    if run_id is None:
        return
    workflow_ids = set(
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
            AgentRun.id == run_id,
            AgentRun.workflow_id.in_(workflow_ids),
        )
    ).one_or_none()
    if run is None:
        raise Conflict(
            "AI 答案的 AgentRun 必须属于当前 Workspace 关联的 Workflow",
            details={"run_id": str(run_id)},
        )


def _evidence_map(
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
        raise NotFound("部分答案证据不存在")
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
                "答案只能引用当前 Workspace 或其 ResearchPlan 的 Evidence",
                details={"evidence_id": str(row.id)},
            )
    return by_id


def _serialize(
    session: Session,
    organization_id: UUID | str,
    rows: list[MarketResearchQuestionAnswer],
) -> list[dict[str, Any]]:
    if not rows:
        return []
    answer_ids = [row.id for row in rows]
    links = session.scalars(
        select(MarketResearchQuestionAnswerEvidence)
        .where(
            MarketResearchQuestionAnswerEvidence.organization_id == organization_id,
            MarketResearchQuestionAnswerEvidence.answer_id.in_(answer_ids),
        )
        .order_by(
            MarketResearchQuestionAnswerEvidence.created_at,
            MarketResearchQuestionAnswerEvidence.id,
        )
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
    links_by_answer: dict[UUID, list[dict[str, Any]]] = {}
    for link in links:
        evidence = evidence_by_id.get(link.evidence_id)
        if evidence is not None:
            links_by_answer.setdefault(link.answer_id, []).append(
                {
                    "relation_type": link.relation_type,
                    "rationale": link.rationale,
                    "evidence": evidence,
                }
            )
    return [
        {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "question_id": row.question_id,
            "version": row.version,
            "answer_summary": row.answer_summary,
            "assumptions": row.assumptions,
            "unknowns": row.unknowns,
            "confidence_level": row.confidence_level,
            "status": row.status,
            "evidence_gap": row.evidence_gap,
            "generated_by_run_id": row.generated_by_run_id,
            "supersedes_id": row.supersedes_id,
            "created_by_user_id": row.created_by_user_id,
            "reviewed_by_user_id": row.reviewed_by_user_id,
            "reviewed_at": row.reviewed_at,
            "review_note": row.review_note,
            "created_at": row.created_at,
            "evidence": links_by_answer.get(row.id, []),
        }
        for row in rows
    ]


def create_answer(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    question_id: UUID,
    *,
    answer_summary: str,
    assumptions: list[Any],
    unknowns: list[Any],
    confidence_level: str,
    generated_by_run_id: UUID | None,
    evidence: list[dict[str, Any]],
    evidence_gap: str | None,
    created_by_user_id: UUID | None,
) -> dict[str, Any]:
    _workspace(session, organization_id, workspace_id, lock=True)
    question = _question(session, organization_id, workspace_id, question_id, lock=True)
    if question.status in {"BLOCKED", "CANCELLED"}:
        raise Conflict(
            "已阻塞或取消的问题不能创建答案版本",
            details={"status": question.status},
        )
    _validate_run(session, organization_id, workspace_id, generated_by_run_id)
    evidence_ids = [item["evidence_id"] for item in evidence]
    evidence_by_id = _evidence_map(session, organization_id, workspace_id, evidence_ids)
    if not evidence and not (evidence_gap and evidence_gap.strip()):
        raise Unprocessable("答案必须关联证据，或明确记录证据缺口")
    if len({(item["evidence_id"], item["relation_type"]) for item in evidence}) != len(evidence):
        raise Unprocessable("答案中存在重复的 Evidence 关系")

    latest = session.scalars(
        select(MarketResearchQuestionAnswer)
        .where(
            MarketResearchQuestionAnswer.organization_id == organization_id,
            MarketResearchQuestionAnswer.workspace_id == workspace_id,
            MarketResearchQuestionAnswer.question_id == question_id,
        )
        .order_by(MarketResearchQuestionAnswer.version.desc())
        .limit(1)
    ).one_or_none()
    version = latest.version + 1 if latest is not None else 1
    relations = {item["relation_type"] for item in evidence}
    status = (
        "CONFLICTED"
        if "SUPPORTS" in relations and "CONTRADICTS" in relations
        else "NEEDS_REVIEW"
    )
    row = MarketResearchQuestionAnswer(
        organization_id=organization_id,
        workspace_id=workspace_id,
        question_id=question_id,
        version=version,
        answer_summary=answer_summary.strip(),
        assumptions=assumptions,
        unknowns=unknowns,
        confidence_level=confidence_level,
        status=status,
        evidence_gap=evidence_gap.strip() if evidence_gap and evidence_gap.strip() else None,
        generated_by_run_id=generated_by_run_id,
        supersedes_id=latest.id if latest is not None else None,
        created_by_user_id=created_by_user_id,
    )
    session.add(row)
    session.flush()
    for item in evidence:
        if item["evidence_id"] not in evidence_by_id:
            raise NotFound("答案证据不存在")
        session.add(
            MarketResearchQuestionAnswerEvidence(
                organization_id=organization_id,
                workspace_id=workspace_id,
                answer_id=row.id,
                evidence_id=item["evidence_id"],
                relation_type=item["relation_type"],
                rationale=item.get("rationale"),
            )
        )
    session.add(
        MarketActivityEvent(
            organization_id=organization_id,
            workspace_id=workspace_id,
            actor_type="USER" if created_by_user_id is not None else "SYSTEM",
            actor_user_id=created_by_user_id,
            aggregate_type="market_research_question",
            aggregate_id=question_id,
            event_type="RESEARCH_QUESTION_ANSWER_CREATED",
            payload={"answer_id": str(row.id), "version": version, "status": status},
        )
    )
    session.flush()
    # Human review changes the dependency graph's readiness. Wake the durable
    # scheduler in the same transaction so accepted answers release downstream nodes.
    from app.services.market_research_dag_service import wake_workspace_executions

    wake_workspace_executions(session, organization_id, workspace_id)
    return _serialize(session, organization_id, [row])[0]


def list_answers(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    question_id: UUID,
) -> list[dict[str, Any]]:
    _question(session, organization_id, workspace_id, question_id)
    rows = list(
        session.scalars(
            select(MarketResearchQuestionAnswer)
            .where(
                MarketResearchQuestionAnswer.organization_id == organization_id,
                MarketResearchQuestionAnswer.workspace_id == workspace_id,
                MarketResearchQuestionAnswer.question_id == question_id,
            )
            .order_by(MarketResearchQuestionAnswer.version.desc())
        ).all()
    )
    return _serialize(session, organization_id, rows)


def _candidate_source_group_count(evidence_rows: list[Evidence]) -> int:
    """Count conservative source groups, not independently verified publishers.

    Evidence from one host is one candidate group. Identical content across hosts
    is also one group (likely syndication/republication). A human must still review
    publisher ownership and content lineage before treating groups as independent.
    """
    parent = list(range(len(evidence_rows)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    first_by_host: dict[str, int] = {}
    first_by_digest: dict[str, int] = {}
    for index, evidence in enumerate(evidence_rows):
        ref = evidence.canonical_ref or evidence.ref
        try:
            parsed = urlsplit(ref)
            host = parsed.hostname.casefold().rstrip(".") if parsed.hostname else ref.casefold()
        except ValueError:
            host = ref.casefold()
        body = str((evidence.payload or {}).get("content") or "")
        digest = (
            evidence.content_hash.casefold()
            if evidence.content_hash
            else sha256(body.encode("utf-8")).hexdigest()
        )
        if host in first_by_host:
            union(index, first_by_host[host])
        else:
            first_by_host[host] = index
        if digest in first_by_digest:
            union(index, first_by_digest[digest])
        else:
            first_by_digest[digest] = index
    return len({find(index) for index in range(len(evidence_rows))})


def _validate_acceptance_criteria(
    question: MarketResearchQuestion,
    supporting_evidence: list[Evidence],
    review_note: str | None,
) -> None:
    for criterion in question.acceptance_criteria or []:
        if not isinstance(criterion, dict):
            raise Conflict("研究问题验收标准格式无效，需先修订验收标准")
        kind = criterion.get("kind")
        if kind == "independent_sources":
            minimum = criterion.get("minimum", 1)
            if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 1:
                raise Conflict("independent_sources 验收标准必须提供正整数 minimum")
            actual = _candidate_source_group_count(supporting_evidence)
            if actual < minimum:
                raise Conflict(
                    "支持证据的候选来源组数量未达到要求；同一 host 或相同内容只计为一个候选组",
                    details={
                        "required_sources": minimum,
                        "actual_sources": actual,
                        "source_count_basis": "distinct_host_and_content_groups",
                        "independence_confirmed_by_system": False,
                    },
                )
            if not (review_note and review_note.strip()):
                raise Conflict(
                    "不同 host 及不同内容不能自动证明来源独立；管理员必须人工核查出版方归属、所有权及转载关系，并填写审核说明",
                    details={
                        "required_sources": minimum,
                        "candidate_source_groups": actual,
                        "manual_independence_review_required": True,
                    },
                )
        else:
            if not (review_note and review_note.strip()):
                raise Conflict(
                    "存在需要人工判断的验收标准，采纳前必须填写审核说明",
                    details={"criterion_kind": str(kind)},
                )


def review_answer(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    question_id: UUID,
    answer_id: UUID,
    *,
    decision: str,
    actor_user_id: UUID,
    review_note: str | None,
) -> dict[str, Any]:
    _workspace(session, organization_id, workspace_id, lock=True)
    question = _question(session, organization_id, workspace_id, question_id, lock=True)
    row = _answer(
        session, organization_id, workspace_id, question_id, answer_id, lock=True
    )
    if row.status in {"ACCEPTED", "REJECTED", "SUPERSEDED"}:
        raise Conflict(
            "该答案版本已完成审核；如需修订，请创建新版本",
            details={"status": row.status},
        )
    if decision == "ACCEPTED":
        links = session.scalars(
            select(MarketResearchQuestionAnswerEvidence).where(
                MarketResearchQuestionAnswerEvidence.organization_id == organization_id,
                MarketResearchQuestionAnswerEvidence.workspace_id == workspace_id,
                MarketResearchQuestionAnswerEvidence.answer_id == answer_id,
            )
        ).all()
        relations = [link.relation_type for link in links]
        if "SUPPORTS" not in relations:
            raise Conflict("采纳答案至少需要一条 SUPPORTS 证据")
        if "CONTRADICTS" in relations:
            raise Conflict("存在未解决的反驳证据，不能采纳答案")
        supporting_ids = [link.evidence_id for link in links if link.relation_type == "SUPPORTS"]
        supporting_evidence = list(
            session.scalars(
                select(Evidence).where(
                    Evidence.organization_id == organization_id,
                    Evidence.id.in_(supporting_ids),
                )
            ).all()
        )
        _validate_acceptance_criteria(question, supporting_evidence, review_note)
        previous = session.scalars(
            select(MarketResearchQuestionAnswer).where(
                MarketResearchQuestionAnswer.organization_id == organization_id,
                MarketResearchQuestionAnswer.workspace_id == workspace_id,
                MarketResearchQuestionAnswer.question_id == question_id,
                MarketResearchQuestionAnswer.status == "ACCEPTED",
                MarketResearchQuestionAnswer.id != answer_id,
            )
        ).all()
        for old in previous:
            old.status = "SUPERSEDED"
        session.flush()
        row.status = "ACCEPTED"
        question.answer_summary = row.answer_summary
        question.status = "ANSWERED"
        question.blocked_reason = None
    elif decision == "REJECTED":
        row.status = "REJECTED"
        if question.status != "ANSWERED":
            question.status = "IN_PROGRESS"
    else:
        raise Unprocessable("不支持的答案审核动作")
    row.reviewed_by_user_id = actor_user_id
    row.reviewed_at = datetime.now(UTC)
    row.review_note = review_note.strip() if review_note and review_note.strip() else None
    session.add(
        MarketActivityEvent(
            organization_id=organization_id,
            workspace_id=workspace_id,
            actor_type="USER",
            actor_user_id=actor_user_id,
            aggregate_type="market_research_question",
            aggregate_id=question_id,
            event_type=f"RESEARCH_QUESTION_ANSWER_{decision}",
            payload={"answer_id": str(answer_id), "version": row.version},
        )
    )
    session.flush()
    return _serialize(session, organization_id, [row])[0]
