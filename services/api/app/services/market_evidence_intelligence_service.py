"""Deterministic, read-only source and content diagnostics for one Market Workspace.

The report deliberately does not score truthfulness, freshness, or publisher independence.
It surfaces observable metadata and exact-match relationships for a human/research agent
to interpret in the context of the actual question.
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

from app.api.errors import NotFound
from app.models.market import MarketEvidenceDerivation, MarketWorkspace, MarketWorkspaceWorkflow
from app.models.workflow import Evidence

MAX_ANALYZED_EVIDENCE = 500
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


def _content_fingerprint(row: Evidence) -> tuple[str | None, str]:
    stored = (row.content_hash or "").strip().casefold()
    if _SHA256.fullmatch(stored):
        return stored, "STORED_HASH"
    content = (row.payload or {}).get("content")
    if isinstance(content, str) and content.strip():
        # The fallback is explicitly marked: it is a digest of the stored body, not
        # proof that the original publisher's full document was captured.
        normalized = " ".join(content.split())
        return sha256(normalized.encode("utf-8")).hexdigest(), "BODY_SHA256"
    return None, "UNAVAILABLE"


def _age_days(published_at: datetime | None, now: datetime) -> tuple[int | None, bool]:
    if published_at is None:
        return None, False
    value = published_at.replace(tzinfo=UTC) if published_at.tzinfo is None else published_at.astimezone(UTC)
    age = (now - value).total_seconds()
    return int(age // 86400), age < 0


def build_report(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
) -> dict[str, Any]:
    """Return exact content/source relationships without mutating any Evidence row."""
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
    filters = (Evidence.organization_id == organization_id, or_(*scope))
    total = int(
        session.scalar(select(func.count()).select_from(Evidence).where(*filters)) or 0
    )
    rows = list(
        session.scalars(
            select(Evidence)
            .where(*filters)
            .order_by(Evidence.retrieved_at.desc().nullslast(), Evidence.created_at.desc(), Evidence.id)
            .limit(MAX_ANALYZED_EVIDENCE + 1)
        ).all()
    )
    truncated = total > MAX_ANALYZED_EVIDENCE or len(rows) > MAX_ANALYZED_EVIDENCE
    rows = rows[:MAX_ANALYZED_EVIDENCE]
    evidence_ids = [row.id for row in rows]
    now = datetime.now(UTC)

    derivations_by_source: dict[UUID, list[UUID]] = {}
    if evidence_ids:
        derivations = session.scalars(
            select(MarketEvidenceDerivation).where(
                MarketEvidenceDerivation.organization_id == organization_id,
                MarketEvidenceDerivation.workspace_id == workspace_id,
                MarketEvidenceDerivation.source_evidence_id.in_(evidence_ids),
            )
        ).all()
        for derivation in derivations:
            derivations_by_source.setdefault(derivation.source_evidence_id, []).append(
                derivation.derived_evidence_id
            )

    hosts_by_evidence: dict[UUID, str | None] = {
        row.id: _source_host(row) for row in rows
    }
    fingerprints: dict[UUID, str | None] = {}
    fingerprint_sources: dict[UUID, str] = {}
    for row in rows:
        fingerprints[row.id], fingerprint_sources[row.id] = _content_fingerprint(row)

    ids_by_fingerprint: dict[str, list[UUID]] = {}
    ids_by_host: dict[str, list[UUID]] = {}
    ids_by_canonical_ref: dict[str, list[UUID]] = {}
    for row in rows:
        fingerprint = fingerprints[row.id]
        if fingerprint:
            ids_by_fingerprint.setdefault(fingerprint, []).append(row.id)
        host = hosts_by_evidence[row.id]
        if host:
            ids_by_host.setdefault(host, []).append(row.id)
        canonical_ref = (row.canonical_ref or "").strip()
        if canonical_ref:
            ids_by_canonical_ref.setdefault(canonical_ref, []).append(row.id)

    duplicate_ids_by_evidence: dict[UUID, list[UUID]] = {}
    duplicate_groups = 0
    duplicate_member_count = 0
    for members in ids_by_fingerprint.values():
        if len(members) < 2:
            continue
        duplicate_groups += 1
        duplicate_member_count += len(members)
        for evidence_id in members:
            duplicate_ids_by_evidence[evidence_id] = [
                other_id for other_id in members if other_id != evidence_id
            ]

    version_ids_by_evidence: dict[UUID, list[UUID]] = {}
    multi_version_source_count = 0
    multi_version_evidence_count = 0
    for canonical_ref, members in ids_by_canonical_ref.items():
        distinct_fingerprints = {
            fingerprints[evidence_id]
            for evidence_id in members
            if fingerprints[evidence_id] is not None
        }
        if len(distinct_fingerprints) < 2:
            continue
        multi_version_source_count += 1
        multi_version_evidence_count += len(members)
        for evidence_id in members:
            version_ids_by_evidence[evidence_id] = [
                other_id
                for other_id in members
                if other_id != evidence_id
                and fingerprints[other_id] != fingerprints[evidence_id]
            ]

    items: list[dict[str, Any]] = []
    status_counts = {
        "UNVERIFIED": 0,
        "PARTIALLY_VERIFIED": 0,
        "VERIFIED": 0,
        "DISPUTED": 0,
        "REJECTED": 0,
    }
    missing_publication_date_count = 0
    missing_content_hash_count = 0
    unextracted_search_result_count = 0
    host_set = set(ids_by_host)

    for row in rows:
        status = (row.verification_status or "UNVERIFIED").upper()
        if status not in status_counts:
            status = "UNVERIFIED"
        status_counts[status] += 1
        host = hosts_by_evidence[row.id]
        same_host_ids = [
            other_id for other_id in ids_by_host.get(host, []) if other_id != row.id
        ] if host else []
        duplicate_ids = duplicate_ids_by_evidence.get(row.id, [])
        source_version_ids = version_ids_by_evidence.get(row.id, [])
        derived_page_ids = derivations_by_source.get(row.id, [])
        fingerprint = fingerprints[row.id]
        age_days, publication_in_future = _age_days(row.published_at, now)

        payload = row.payload or {}
        is_search_result = (
            row.kind.upper() in _SEARCH_RESULT_KINDS
            or payload.get("evidence_state") == "UNVERIFIED_SEARCH_RESULT"
        )
        has_extracted_page = bool(derived_page_ids)
        if row.published_at is None:
            missing_publication_date_count += 1
        if not row.content_hash:
            missing_content_hash_count += 1
        if is_search_result and not has_extracted_page:
            unextracted_search_result_count += 1

        issues: list[str] = []
        if status == "UNVERIFIED":
            issues.append("UNVERIFIED_SOURCE")
        elif status == "PARTIALLY_VERIFIED":
            issues.append("PARTIALLY_VERIFIED_SOURCE")
        elif status == "DISPUTED":
            issues.append("DISPUTED_SOURCE")
        elif status == "REJECTED":
            issues.append("REJECTED_SOURCE")
        if row.published_at is None:
            issues.append("MISSING_PUBLICATION_DATE")
        if not row.content_hash:
            issues.append("MISSING_STORED_CONTENT_HASH")
        if not host:
            issues.append("SOURCE_HOST_UNRESOLVED")
        if duplicate_ids:
            issues.append("DUPLICATE_CONTENT")
        if source_version_ids:
            issues.append("SAME_SOURCE_MULTIPLE_CONTENT_VERSIONS")
        if is_search_result and not has_extracted_page:
            issues.append("SEARCH_RESULT_NOT_PAGE_EXTRACTED")
        if publication_in_future:
            issues.append("PUBLICATION_DATE_IN_FUTURE")

        if duplicate_ids:
            origin_assessment = "DUPLICATE_CONTENT"
        elif same_host_ids:
            origin_assessment = "SAME_HOST"
        elif host:
            origin_assessment = "HOST_IDENTIFIED"
        else:
            origin_assessment = "HOST_UNKNOWN"

        items.append(
            {
                "evidence_id": row.id,
                "kind": row.kind,
                "source": row.source,
                "canonical_ref": row.canonical_ref,
                "source_host": host,
                "content_fingerprint": fingerprint,
                "fingerprint_source": fingerprint_sources[row.id],
                "verification_status": status,
                "published_at": row.published_at,
                "retrieved_at": row.retrieved_at,
                "age_days": age_days,
                "origin_assessment": origin_assessment,
                "duplicate_content_evidence_ids": duplicate_ids,
                "same_host_evidence_ids": same_host_ids,
                "same_source_version_evidence_ids": source_version_ids,
                "derived_page_evidence_ids": derived_page_ids,
                "issues": issues,
            }
        )

    return {
        "workspace_id": workspace_id,
        "generated_at": now,
        "total_evidence_count": total,
        "analyzed_evidence_count": len(rows),
        "is_truncated": truncated,
        "distinct_host_count": len(host_set),
        "duplicate_content_group_count": duplicate_groups,
        "duplicate_content_evidence_count": duplicate_member_count,
        "multi_version_source_count": multi_version_source_count,
        "multi_version_evidence_count": multi_version_evidence_count,
        "unverified_count": status_counts["UNVERIFIED"],
        "partially_verified_count": status_counts["PARTIALLY_VERIFIED"],
        "verified_count": status_counts["VERIFIED"],
        "disputed_count": status_counts["DISPUTED"],
        "rejected_count": status_counts["REJECTED"],
        "missing_publication_date_count": missing_publication_date_count,
        "missing_content_hash_count": missing_content_hash_count,
        "unextracted_search_result_count": unextracted_search_result_count,
        "items": items,
        "caveats": [
            "不同 host 仅代表不同主机名，不证明出版方相互独立、没有共同所有权或不存在转载关系。",
            "相同内容指纹表示精确内容重复候选，不证明哪一个来源是原创发布方。",
            "发布时间与距今天数仅作时间元数据展示；是否过期必须结合具体研究问题和领域时效要求判断。",
            "报告是只读快照，最多分析最近 500 条证据；超过上限时 is_truncated=true，分组计数只覆盖已分析部分。",
            "BODY_SHA256 是对本地存储正文的回退指纹，只有 STORED_HASH 才来自 Evidence.content_hash 字段。",
        ],
    }


__all__ = ["MAX_ANALYZED_EVIDENCE", "build_report"]
