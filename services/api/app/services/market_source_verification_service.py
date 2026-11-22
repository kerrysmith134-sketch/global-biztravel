"""Deterministic source-verification planning over a Workspace's evidence graph.

This planner turns stored evidence provenance, immutable claim snapshots and source-version
history into reviewable next steps. It deliberately does not claim publisher independence,
truth, or freshness and never mutates evidence or claim state.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.errors import Conflict, NotFound
from app.models.market import (
    MarketClaim,
    MarketClaimEvidence,
    MarketEvidenceDerivation,
    MarketResearchQuestion,
    MarketWorkspace,
    MarketWorkspaceWorkflow,
)
from app.models.workflow import AgentRun, Evidence

MAX_ANALYZED_EVIDENCE = 500
MAX_ANALYZED_CLAIMS = 200
MAX_RETURNED_ACTIONS = 250
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SEARCH_RESULT_KINDS = {"WEB_SEARCH_RESULT"}


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


def _source_host(row: Evidence) -> str | None:
    reference = (row.canonical_ref or row.ref or "").strip()
    try:
        parsed = urlsplit(reference)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return None
        return parsed.hostname.encode("idna").decode("ascii").casefold().rstrip(".")
    except (ValueError, UnicodeError):
        return None


def _fingerprint(row: Evidence) -> tuple[str | None, str]:
    stored = (row.content_hash or "").strip().casefold()
    if _SHA256.fullmatch(stored):
        return stored, "STORED_HASH"
    body = (row.payload or {}).get("content")
    if isinstance(body, str) and body.strip():
        normalized = " ".join(body.split())
        return sha256(normalized.encode("utf-8")).hexdigest(), "BODY_SHA256"
    return None, "UNAVAILABLE"


def _is_search_result(row: Evidence) -> bool:
    return (
        row.kind.upper() in _SEARCH_RESULT_KINDS
        or (row.payload or {}).get("evidence_state") == "UNVERIFIED_SEARCH_RESULT"
    )


def _recency(row: Evidence) -> tuple[datetime, datetime, str]:
    retrieved = row.retrieved_at or row.created_at
    if retrieved.tzinfo is None:
        retrieved = retrieved.replace(tzinfo=UTC)
    created = row.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return retrieved.astimezone(UTC), created.astimezone(UTC), str(row.id)


def _claim_origin_state(
    support_rows: list[dict[str, Any]],
    contradicting_count: int,
) -> str:
    if not support_rows:
        return "NO_SUPPORTING_EVIDENCE"
    if contradicting_count:
        return "CONFLICTING_SIGNALS"
    if len(support_rows) == 1:
        return "SINGLE_SOURCE_ONLY"
    hosts = {row["source_host"] for row in support_rows if row["source_host"]}
    if len(hosts) <= 1:
        return "SAME_HOST_ONLY"
    fingerprints: dict[str, set[str]] = {}
    for row in support_rows:
        fingerprint = row.get("content_fingerprint")
        host = row.get("source_host")
        if fingerprint and host:
            fingerprints.setdefault(fingerprint, set()).add(host)
    if any(len(group_hosts) > 1 for group_hosts in fingerprints.values()):
        return "POSSIBLE_SYNDICATION"
    return "MULTIPLE_HOSTS_UNCONFIRMED"


def build_plan(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
) -> dict[str, Any]:
    """Build an auditable source-verification plan without changing any persisted row."""
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
    evidence_filters = (Evidence.organization_id == organization_id, or_(*scope))
    total_evidence_count = int(
        session.scalar(
            select(func.count()).select_from(Evidence).where(*evidence_filters)
        )
        or 0
    )
    evidence_rows = list(
        session.scalars(
            select(Evidence)
            .where(*evidence_filters)
            .order_by(
                Evidence.retrieved_at.desc().nullslast(),
                Evidence.created_at.desc(),
                Evidence.id,
            )
            .limit(MAX_ANALYZED_EVIDENCE + 1)
        ).all()
    )
    evidence_truncated = (
        total_evidence_count > MAX_ANALYZED_EVIDENCE
        or len(evidence_rows) > MAX_ANALYZED_EVIDENCE
    )
    evidence_rows = evidence_rows[:MAX_ANALYZED_EVIDENCE]

    claim_scope = (
        MarketClaim.organization_id == organization_id,
        MarketClaim.workspace_id == workspace_id,
    )
    total_claim_count = int(
        session.scalar(
            select(func.count(func.distinct(MarketClaim.claim_lineage_id)))
            .where(*claim_scope)
        )
        or 0
    )
    ranked_claims = (
        select(
            MarketClaim.id.label("claim_id"),
            func.row_number().over(
                partition_by=MarketClaim.claim_lineage_id,
                order_by=(
                    MarketClaim.version.desc(),
                    MarketClaim.created_at.desc(),
                    MarketClaim.id.desc(),
                ),
            ).label("lineage_rank"),
        )
        .where(*claim_scope)
        .subquery()
    )
    claim_rows = list(
        session.scalars(
            select(MarketClaim)
            .join(ranked_claims, ranked_claims.c.claim_id == MarketClaim.id)
            .where(ranked_claims.c.lineage_rank == 1)
            .order_by(MarketClaim.created_at.desc(), MarketClaim.id)
            .limit(MAX_ANALYZED_CLAIMS + 1)
        ).all()
    )
    claims_truncated = (
        total_claim_count > MAX_ANALYZED_CLAIMS
        or len(claim_rows) > MAX_ANALYZED_CLAIMS
    )
    claim_rows = claim_rows[:MAX_ANALYZED_CLAIMS]
    claim_ids = [row.id for row in claim_rows]

    claim_links = (
        list(
            session.scalars(
                select(MarketClaimEvidence)
                .where(
                    MarketClaimEvidence.organization_id == organization_id,
                    MarketClaimEvidence.workspace_id == workspace_id,
                    MarketClaimEvidence.claim_id.in_(claim_ids),
                )
                .order_by(
                    MarketClaimEvidence.created_at,
                    MarketClaimEvidence.id,
                )
            ).all()
        )
        if claim_ids
        else []
    )
    linked_evidence_ids = list({row.evidence_id for row in claim_links})
    linked_evidence_rows = (
        list(
            session.scalars(
                select(Evidence).where(
                    Evidence.organization_id == organization_id,
                    Evidence.id.in_(linked_evidence_ids),
                )
            ).all()
        )
        if linked_evidence_ids
        else []
    )
    evidence_by_id = {row.id: row for row in evidence_rows}
    evidence_by_id.update({row.id: row for row in linked_evidence_rows})

    # Re-check workspace ownership at read time: the Evidence table is shared with other
    # workflows, so organization scope alone is not enough for a Workspace report.
    for row in evidence_by_id.values():
        valid_scope = (
            row.subject_type == "market_workspace" and row.subject_id == workspace_id
        ) or (
            row.subject_type == "RESEARCH_PLAN" and row.subject_id in workflow_ids
        )
        if not valid_scope:
            raise Conflict(
                "来源核验计划包含不属于当前 Workspace 的 Evidence",
                details={"evidence_id": str(row.id)},
            )

    all_evidence_ids = list(evidence_by_id)
    derivations_by_source: dict[UUID, list[UUID]] = {}
    if all_evidence_ids:
        derivations = session.scalars(
            select(MarketEvidenceDerivation).where(
                MarketEvidenceDerivation.organization_id == organization_id,
                MarketEvidenceDerivation.workspace_id == workspace_id,
                MarketEvidenceDerivation.source_evidence_id.in_(all_evidence_ids),
            )
        ).all()
        for derivation in derivations:
            derivations_by_source.setdefault(
                derivation.source_evidence_id, []
            ).append(derivation.derived_evidence_id)

    hosts = {row.id: _source_host(row) for row in evidence_by_id.values()}
    fingerprints: dict[UUID, str | None] = {}
    fingerprint_sources: dict[UUID, str] = {}
    for row in evidence_by_id.values():
        fingerprints[row.id], fingerprint_sources[row.id] = _fingerprint(row)

    ids_by_fingerprint: dict[str, list[UUID]] = {}
    ids_by_host: dict[str, list[UUID]] = {}
    ids_by_canonical_ref: dict[str, list[UUID]] = {}
    for row in evidence_by_id.values():
        fingerprint = fingerprints[row.id]
        if fingerprint:
            ids_by_fingerprint.setdefault(fingerprint, []).append(row.id)
        host = hosts[row.id]
        if host:
            ids_by_host.setdefault(host, []).append(row.id)
        canonical_ref = (row.canonical_ref or "").strip()
        if canonical_ref:
            ids_by_canonical_ref.setdefault(canonical_ref, []).append(row.id)

    latest_by_canonical_ref: dict[str, Evidence] = {}
    for row in evidence_by_id.values():
        canonical_ref = (row.canonical_ref or "").strip()
        if not canonical_ref:
            continue
        current = latest_by_canonical_ref.get(canonical_ref)
        if current is None or _recency(row) > _recency(current):
            latest_by_canonical_ref[canonical_ref] = row

    links_by_claim: dict[UUID, list[MarketClaimEvidence]] = {}
    for link in claim_links:
        links_by_claim.setdefault(link.claim_id, []).append(link)

    actions: list[dict[str, Any]] = []
    action_keys: set[tuple[str, str, UUID]] = set()

    def add_action(
        action_type: str,
        priority: str,
        target_type: str,
        target_id: UUID,
        evidence_ids: list[UUID],
        *,
        claim_id: UUID | None = None,
        title: str,
        rationale: str,
    ) -> None:
        key = (action_type, target_type, target_id)
        if key in action_keys:
            return
        action_keys.add(key)
        stable_id = sha256(
            f"{workspace_id}:{action_type}:{target_type}:{target_id}".encode()
        ).hexdigest()[:24]
        actions.append(
            {
                "action_id": stable_id,
                "action_type": action_type,
                "priority": priority,
                "target_type": target_type,
                "target_id": target_id,
                "evidence_ids": sorted(set(evidence_ids), key=str),
                "claim_id": claim_id,
                "title": title,
                "rationale": rationale,
            }
        )

    # Workspace-level source tasks are derived only from the bounded recent evidence set.
    for row in evidence_rows:
        derived_ids = derivations_by_source.get(row.id, [])
        if _is_search_result(row) and not derived_ids:
            add_action(
                "EXTRACT_SOURCE_PAGE",
                "MEDIUM",
                "EVIDENCE",
                row.id,
                [row.id],
                title="提取网页正文",
                rationale="当前记录只有搜索结果或摘要，尚无对应网页正文版本；先提取用户选定的页面，再将其作为可核验材料。",
            )
        if (
            hosts[row.id] is None
            or row.published_at is None
            or not (row.content_hash or "").strip()
        ):
            add_action(
                "COMPLETE_SOURCE_METADATA",
                "LOW",
                "EVIDENCE",
                row.id,
                [row.id],
                title="补全来源元数据",
                rationale="来源主机、发布日期或已存储内容指纹至少有一项缺失；补齐后才能更可靠地追踪来源与版本。",
            )

    for members in ids_by_fingerprint.values():
        distinct_hosts = {hosts[item] for item in members if hosts[item]}
        if len(distinct_hosts) > 1:
            add_action(
                "INVESTIGATE_POSSIBLE_SYNDICATION",
                "MEDIUM",
                "EVIDENCE",
                min(members, key=str),
                members,
                title="核查转载或内容复用关系",
                rationale="不同主机出现完全相同的内容指纹，可能属于转载、联合发布或同源复制；不能仅按域名数量计为独立证据。",
            )

    for canonical_ref, members in ids_by_canonical_ref.items():
        versioned = [item for item in members if fingerprints[item] is not None]
        unique_versions = {fingerprints[item] for item in versioned}
        if len(unique_versions) > 1:
            latest = latest_by_canonical_ref.get(canonical_ref)
            target_id = latest.id if latest is not None else min(versioned, key=str)
            add_action(
                "REVIEW_SOURCE_VERSION_DRIFT",
                "MEDIUM",
                "EVIDENCE",
                target_id,
                versioned,
                title="复核来源内容版本",
                rationale="同一规范化来源 URL 存在不同内容指纹；确认当前研究结论引用的版本及其发布日期、抓取时间。",
            )

    claim_assessments: list[dict[str, Any]] = []
    claims_needing_corroboration = 0
    claims_with_contradictions = 0
    claims_with_changed_sources = 0

    for claim in claim_rows:
        claim_evidence: list[dict[str, Any]] = []
        support_rows: list[dict[str, Any]] = []
        contradicting_count = 0
        changed_source_count = 0
        source_status_changed_count = 0
        unextracted_count = 0
        for link in links_by_claim.get(claim.id, []):
            evidence = evidence_by_id.get(link.evidence_id)
            if evidence is None:
                continue
            host = hosts[evidence.id]
            fingerprint = fingerprints[evidence.id]
            derived_ids = derivations_by_source.get(evidence.id, [])
            is_unextracted = _is_search_result(evidence) and not derived_ids
            latest = latest_by_canonical_ref.get((evidence.canonical_ref or "").strip())
            source_version_changed = (
                latest is not None
                and latest.id != evidence.id
                and fingerprints[latest.id] is not None
                and fingerprint is not None
                and fingerprints[latest.id] != fingerprint
                and _recency(latest) > _recency(evidence)
            )
            content_changed = (
                bool(link.content_hash_snapshot)
                and bool(evidence.content_hash)
                and link.content_hash_snapshot != evidence.content_hash
            )
            current_status = (evidence.verification_status or "UNVERIFIED").upper()
            status_changed = link.verification_status_snapshot != current_status
            if source_version_changed or content_changed:
                changed_source_count += 1
            if status_changed:
                source_status_changed_count += 1
            if is_unextracted:
                unextracted_count += 1
            issue_codes: list[str] = []
            if content_changed or source_version_changed:
                issue_codes.append("SOURCE_CONTENT_CHANGED_AFTER_CLAIM_RUN")
            if status_changed:
                issue_codes.append("SOURCE_STATUS_CHANGED_AFTER_CLAIM_RUN")
            if is_unextracted:
                issue_codes.append("SEARCH_RESULT_NOT_PAGE_EXTRACTED")
            if not host or not evidence.published_at or not evidence.content_hash:
                issue_codes.append("SOURCE_METADATA_INCOMPLETE")
            detail = {
                "evidence_id": evidence.id,
                "relation_type": link.relation_type,
                "source_host": host,
                "content_fingerprint": fingerprint,
                "fingerprint_source": fingerprint_sources[evidence.id],
                "content_changed_since_assessment": content_changed,
                "status_changed_since_assessment": status_changed,
                "source_version_changed": source_version_changed,
                "latest_source_evidence_id": latest.id if source_version_changed and latest else None,
                "unextracted": is_unextracted,
                "issues": issue_codes,
            }
            claim_evidence.append(detail)
            if link.relation_type == "SUPPORTS":
                support_rows.append(detail)
            elif link.relation_type == "CONTRADICTS":
                contradicting_count += 1

        origin_state = _claim_origin_state(support_rows, contradicting_count)
        support_count = len(support_rows)
        if origin_state in {
            "NO_SUPPORTING_EVIDENCE",
            "SINGLE_SOURCE_ONLY",
            "SAME_HOST_ONLY",
            "POSSIBLE_SYNDICATION",
        }:
            claims_needing_corroboration += 1
        if contradicting_count:
            claims_with_contradictions += 1
        if changed_source_count or source_status_changed_count:
            claims_with_changed_sources += 1

        claim_issues: list[str] = []
        if not support_count:
            claim_issues.append("NO_SUPPORTING_EVIDENCE")
        elif support_count == 1:
            claim_issues.append("SINGLE_SUPPORTING_SOURCE")
        elif origin_state == "SAME_HOST_ONLY":
            claim_issues.append("SUPPORT_SOURCES_SHARE_HOST")
        elif origin_state == "POSSIBLE_SYNDICATION":
            claim_issues.append("POSSIBLE_CROSS_HOST_SYNDICATION")
        if contradicting_count:
            claim_issues.append("CONTRADICTING_EVIDENCE_PRESENT")
        if changed_source_count:
            claim_issues.append("SOURCE_CONTENT_CHANGED_AFTER_CLAIM_RUN")
        if source_status_changed_count:
            claim_issues.append("SOURCE_STATUS_CHANGED_AFTER_CLAIM_RUN")
        if unextracted_count:
            claim_issues.append("UNEXTRACTED_SEARCH_RESULTS_PRESENT")

        claim_assessments.append(
            {
                "claim_id": claim.id,
                "claim_lineage_id": claim.claim_lineage_id,
                "claim_version": claim.version,
                "statement": claim.statement,
                "claim_type": claim.claim_type,
                "claim_status": claim.status,
                "verification_verdict": claim.verification_verdict,
                "supporting_evidence_count": support_count,
                "contradicting_evidence_count": contradicting_count,
                "distinct_host_count": len({
                    row["source_host"] for row in support_rows if row["source_host"]
                }),
                "distinct_content_count": len({
                    row["content_fingerprint"]
                    for row in support_rows
                    if row["content_fingerprint"]
                }),
                "source_origin_state": origin_state,
                "changed_source_count": changed_source_count,
                "status_changed_source_count": source_status_changed_count,
                "unextracted_source_count": unextracted_count,
                "evidence": claim_evidence,
                "issues": claim_issues,
            }
        )

        changed_evidence_ids = [
            row["evidence_id"]
            for row in claim_evidence
            if row["content_changed_since_assessment"] or row["source_version_changed"]
            or row["status_changed_since_assessment"]
        ]
        if changed_evidence_ids:
            add_action(
                "REVERIFY_CLAIM_AFTER_SOURCE_CHANGE",
                "HIGH",
                "CLAIM",
                claim.id,
                changed_evidence_ids,
                claim_id=claim.id,
                title="重新核验主张",
                rationale="主张核验后，关联证据的内容版本或核验状态发生变化；原有主张判断基于旧快照，需要重新核验。",
            )
        if contradicting_count and support_count:
            add_action(
                "REVIEW_CONTRADICTORY_EVIDENCE",
                "HIGH",
                "CLAIM",
                claim.id,
                [row["evidence_id"] for row in claim_evidence],
                claim_id=claim.id,
                title="处理相互矛盾的证据",
                rationale="同一主张同时关联支持和反驳证据；需要逐条比较范围、时间、主体和原文，而不是按来源数量投票。",
            )
        if origin_state in {
            "NO_SUPPORTING_EVIDENCE",
            "SINGLE_SOURCE_ONLY",
            "SAME_HOST_ONLY",
            "POSSIBLE_SYNDICATION",
        }:
            add_action(
                "SEEK_SEPARATELY_ORIGINATED_CORROBORATION",
                "MEDIUM",
                "CLAIM",
                claim.id,
                [row["evidence_id"] for row in support_rows],
                claim_id=claim.id,
                title="寻找独立产生的交叉证据",
                rationale="当前支持材料不足以证明有多个独立来源。应寻找来源归属、原始发布者和证据形成过程可分别核实的材料；不同域名本身不构成独立性证明。",
            )

    priority = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    actions.sort(key=lambda row: (
        priority[row["priority"]],
        row["action_type"],
        str(row["target_id"]),
    ))
    total_action_count = len(actions)
    actions_truncated = total_action_count > MAX_RETURNED_ACTIONS
    actions = actions[:MAX_RETURNED_ACTIONS]

    action_ids = [str(item["action_id"]) for item in actions]
    latest_question_by_action: dict[str, MarketResearchQuestion] = {}
    if action_ids:
        tracked_questions = session.scalars(
            select(MarketResearchQuestion)
            .join(
                AgentRun,
                (
                    AgentRun.organization_id == MarketResearchQuestion.organization_id
                )
                & (AgentRun.id == MarketResearchQuestion.generated_by_run_id),
            )
            .where(
                MarketResearchQuestion.organization_id == organization_id,
                MarketResearchQuestion.workspace_id == workspace_id,
                MarketResearchQuestion.planner_question_key.in_(action_ids),
                AgentRun.agent_type == "MARKET_SOURCE_VERIFICATION_PLANNER",
            )
            .order_by(
                MarketResearchQuestion.updated_at.desc(),
                MarketResearchQuestion.created_at.desc(),
                MarketResearchQuestion.id,
            )
        ).all()
        for question in tracked_questions:
            if question.planner_question_key:
                latest_question_by_action.setdefault(question.planner_question_key, question)
    for action in actions:
        question = latest_question_by_action.get(str(action["action_id"]))
        action["research_question_id"] = question.id if question else None
        action["research_question_status"] = question.status if question else None

    return {
        "workspace_id": workspace_id,
        "generated_at": datetime.now(UTC),
        "total_evidence_count": total_evidence_count,
        "analyzed_evidence_count": len(evidence_rows),
        "evidence_truncated": evidence_truncated,
        "total_claim_count": total_claim_count,
        "analyzed_claim_count": len(claim_rows),
        "claims_truncated": claims_truncated,
        "claims_needing_corroboration_count": claims_needing_corroboration,
        "claims_with_contradictions_count": claims_with_contradictions,
        "claims_with_changed_sources_count": claims_with_changed_sources,
        "action_count": total_action_count,
        "actions_truncated": actions_truncated,
        "claims": claim_assessments,
        "actions": actions,
        "limitations": [
            "本计划仅根据已存储的 Evidence、来源派生关系和主张核验快照生成，不会联网确认出版方身份或控制权。",
            "不同域名、不同 URL、不同内容指纹均不能单独证明出版方或信息来源相互独立。",
            "转载、联合发布、同一集团控制或原始发布者关系，不能仅靠网页元数据可靠确认。",
            "建议动作只是待执行的研究任务；本接口不会自动抓取网页、修改 Evidence、重跑主张核验或批准主张。",
            f"全局来源任务最多分析最近 {MAX_ANALYZED_EVIDENCE} 条 Evidence；主张任务最多分析最近 {MAX_ANALYZED_CLAIMS} 条未被替代的主张。",
        ],
    }
