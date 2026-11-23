"""Read-only change-impact analysis for market claim consumers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.errors import NotFound
from app.models.market import (
    MarketClaim,
    MarketClaimEvidence,
    MarketInsight,
    MarketInsightEvidence,
    MarketOpportunity,
    MarketOpportunityEvidence,
    MarketWorkspace,
)

MAX_ANALYZED_LINEAGES = 200
MAX_HISTORY_CLAIMS = 5000
MAX_RETURNED_IMPACTS = 250
_PENDING_CLAIM_STATUSES = {"NEEDS_REVIEW", "CONFLICTED", "INSUFFICIENT"}
_CLOSED_OPPORTUNITY_STATUSES = {"DISQUALIFIED", "WON", "LOST"}


def build_report(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
) -> dict[str, Any]:
    """Find Insights and Opportunities whose linked source snapshot may now be stale."""
    workspace = session.scalars(
        select(MarketWorkspace).where(
            MarketWorkspace.organization_id == organization_id,
            MarketWorkspace.id == workspace_id,
        )
    ).one_or_none()
    if workspace is None:
        raise NotFound("市场工作空间不存在", details={"workspace_id": str(workspace_id)})

    total_lineages = int(
        session.scalar(
            select(func.count(func.distinct(MarketClaim.claim_lineage_id))).where(
                MarketClaim.organization_id == organization_id,
                MarketClaim.workspace_id == workspace_id,
            )
        )
        or 0
    )
    ranked_latest = (
        select(
            MarketClaim.id.label("claim_id"),
            func.row_number()
            .over(
                partition_by=MarketClaim.claim_lineage_id,
                order_by=(
                    MarketClaim.version.desc(),
                    MarketClaim.created_at.desc(),
                    MarketClaim.id.desc(),
                ),
            )
            .label("lineage_rank"),
        )
        .where(
            MarketClaim.organization_id == organization_id,
            MarketClaim.workspace_id == workspace_id,
        )
        .subquery()
    )
    latest_rows = list(
        session.scalars(
            select(MarketClaim)
            .join(ranked_latest, ranked_latest.c.claim_id == MarketClaim.id)
            .where(ranked_latest.c.lineage_rank == 1)
            .order_by(MarketClaim.created_at.desc(), MarketClaim.id.desc())
            .limit(MAX_ANALYZED_LINEAGES + 1)
        ).all()
    )
    truncated = (
        total_lineages > MAX_ANALYZED_LINEAGES
        or len(latest_rows) > MAX_ANALYZED_LINEAGES
    )
    latest_rows = latest_rows[:MAX_ANALYZED_LINEAGES]
    if not latest_rows:
        return {
            "workspace_id": workspace_id,
            "generated_at": datetime.now(UTC),
            "analyzed_lineage_count": 0,
            "total_lineage_count": total_lineages,
            "affected_consumer_count": 0,
            "impact_count": 0,
            "truncated": truncated,
            "impacts": [],
        }

    lineage_ids = [row.claim_lineage_id for row in latest_rows]
    latest_by_lineage = {row.claim_lineage_id: row for row in latest_rows}
    history_rows = list(
        session.scalars(
            select(MarketClaim)
            .where(
                MarketClaim.organization_id == organization_id,
                MarketClaim.workspace_id == workspace_id,
                MarketClaim.claim_lineage_id.in_(lineage_ids),
            )
            .order_by(
                MarketClaim.version.desc(),
                MarketClaim.created_at.desc(),
                MarketClaim.id.desc(),
            )
            .limit(MAX_HISTORY_CLAIMS + 1)
        ).all()
    )
    if len(history_rows) > MAX_HISTORY_CLAIMS:
        truncated = True
        history_rows = history_rows[:MAX_HISTORY_CLAIMS]

    claims_by_id = {row.id: row for row in history_rows}
    claims_by_id.update({row.id: row for row in latest_rows})
    claim_ids = list(claims_by_id)
    claim_links = list(
        session.scalars(
            select(MarketClaimEvidence)
            .where(
                MarketClaimEvidence.organization_id == organization_id,
                MarketClaimEvidence.workspace_id == workspace_id,
                MarketClaimEvidence.claim_id.in_(claim_ids),
            )
            .order_by(MarketClaimEvidence.created_at, MarketClaimEvidence.id)
        ).all()
    )
    if not claim_links:
        return {
            "workspace_id": workspace_id,
            "generated_at": datetime.now(UTC),
            "analyzed_lineage_count": len(latest_rows),
            "total_lineage_count": total_lineages,
            "affected_consumer_count": 0,
            "impact_count": 0,
            "truncated": truncated,
            "impacts": [],
        }

    lineage_evidence_versions: dict[UUID, dict[UUID, set[int]]] = {}
    evidence_ids: set[UUID] = set()
    for link in claim_links:
        claim = claims_by_id.get(link.claim_id)
        if claim is None:
            continue
        evidence_ids.add(link.evidence_id)
        lineage_evidence_versions.setdefault(claim.claim_lineage_id, {}).setdefault(
            link.evidence_id, set()
        ).add(claim.version)

    evidence_ids_list = list(evidence_ids)
    insight_links = session.execute(
        select(MarketInsight, MarketInsightEvidence)
        .join(
            MarketInsightEvidence,
            (MarketInsightEvidence.organization_id == MarketInsight.organization_id)
            & (MarketInsightEvidence.insight_id == MarketInsight.id),
        )
        .where(
            MarketInsight.organization_id == organization_id,
            MarketInsight.workspace_id == workspace_id,
            MarketInsightEvidence.evidence_id.in_(evidence_ids_list),
            MarketInsight.status.not_in({"REJECTED", "SUPERSEDED"}),
        )
    ).all()
    opportunity_links = session.execute(
        select(MarketOpportunity, MarketOpportunityEvidence)
        .join(
            MarketOpportunityEvidence,
            (MarketOpportunityEvidence.organization_id == MarketOpportunity.organization_id)
            & (MarketOpportunityEvidence.workspace_id == MarketOpportunity.workspace_id)
            & (MarketOpportunityEvidence.opportunity_id == MarketOpportunity.id),
        )
        .where(
            MarketOpportunity.organization_id == organization_id,
            MarketOpportunity.workspace_id == workspace_id,
            MarketOpportunityEvidence.evidence_id.in_(evidence_ids_list),
            MarketOpportunity.status.not_in(_CLOSED_OPPORTUNITY_STATUSES),
        )
    ).all()

    consumers_by_evidence: dict[UUID, list[tuple[str, Any, str]]] = {}
    for insight, link in insight_links:
        consumers_by_evidence.setdefault(link.evidence_id, []).append(
            ("INSIGHT", insight, link.relation_type)
        )
    for opportunity, link in opportunity_links:
        consumers_by_evidence.setdefault(link.evidence_id, []).append(
            ("OPPORTUNITY", opportunity, link.relation_type)
        )

    grouped: dict[tuple[UUID, str, UUID], dict[str, Any]] = {}
    for claim_link in claim_links:
        claim = claims_by_id.get(claim_link.claim_id)
        if claim is None:
            continue
        for consumer_type, consumer, relation_type in consumers_by_evidence.get(
            claim_link.evidence_id, []
        ):
            key = (claim.claim_lineage_id, consumer_type, consumer.id)
            item = grouped.setdefault(
                key,
                {
                    "consumer_type": consumer_type,
                    "consumer_id": consumer.id,
                    "consumer_title": (
                        consumer.statement if consumer_type == "INSIGHT" else consumer.title
                    ),
                    "consumer_status": consumer.status,
                    "relation_types": set(),
                    "matched_evidence_ids": set(),
                    "referenced_claim_versions": set(),
                },
            )
            item["relation_types"].add(relation_type)
            item["matched_evidence_ids"].add(claim_link.evidence_id)
            item["referenced_claim_versions"].add(claim.version)

    impacts: list[dict[str, Any]] = []
    for (lineage_id, _consumer_type, _consumer_id), consumer in grouped.items():
        latest = latest_by_lineage[lineage_id]
        version_evidence = lineage_evidence_versions.get(lineage_id, {})
        latest_evidence_ids = {
            evidence_id
            for evidence_id, versions in version_evidence.items()
            if latest.version in versions
        }
        matched_evidence_ids = consumer["matched_evidence_ids"]
        referenced_versions = consumer["referenced_claim_versions"]
        references_older_version = any(
            version < latest.version for version in referenced_versions
        )
        stale_evidence_ids = matched_evidence_ids - latest_evidence_ids
        accepted_rows = [
            row for row in claims_by_id.values()
            if row.claim_lineage_id == lineage_id and row.status == "ACCEPTED"
        ]
        current_accepted = max(accepted_rows, key=lambda row: row.version) if accepted_rows else None

        reason: str | None = None
        severity = "MEDIUM"
        if latest.status in _PENDING_CLAIM_STATUSES:
            if latest.verification_verdict in {"POTENTIAL_CONTRADICTION", "CONFLICTED"}:
                reason = "LATEST_VERSION_CONTRADICTORY"
                severity = "HIGH"
            elif latest.verification_verdict == "INSUFFICIENT" or latest.status == "INSUFFICIENT":
                reason = "LATEST_VERSION_INSUFFICIENT"
                severity = "HIGH"
            else:
                reason = "LATEST_VERSION_PENDING_REVIEW"
        elif (
            latest.status == "ACCEPTED"
            and references_older_version
            and stale_evidence_ids
        ):
            reason = "CONSUMER_USES_OLDER_EVIDENCE_SNAPSHOT"
        elif latest.status == "REJECTED" and current_accepted is None:
            reason = "NO_ACCEPTED_CLAIM_VERSION"
            severity = "HIGH"

        if reason is None:
            continue

        impacts.append(
            {
                "claim_lineage_id": lineage_id,
                "latest_claim_id": latest.id,
                "latest_claim_version": latest.version,
                "latest_claim_status": latest.status,
                "latest_verification_verdict": latest.verification_verdict,
                "current_accepted_claim_id": (
                    current_accepted.id if current_accepted else None
                ),
                "consumer_type": consumer["consumer_type"],
                "consumer_id": consumer["consumer_id"],
                "consumer_title": consumer["consumer_title"],
                "consumer_status": consumer["consumer_status"],
                "relation_types": sorted(consumer["relation_types"]),
                "matched_evidence_count": len(matched_evidence_ids),
                "stale_evidence_count": len(stale_evidence_ids),
                "referenced_claim_versions": sorted(referenced_versions),
                "severity": severity,
                "reason_code": reason,
            }
        )

    severity_order = {"HIGH": 0, "MEDIUM": 1}
    impacts.sort(
        key=lambda item: (
            severity_order.get(item["severity"], 2),
            item["consumer_type"],
            str(item["consumer_title"]).casefold(),
            str(item["claim_lineage_id"]),
        )
    )
    if len(impacts) > MAX_RETURNED_IMPACTS:
        truncated = True
        impacts = impacts[:MAX_RETURNED_IMPACTS]

    affected_consumers = {
        (item["consumer_type"], item["consumer_id"]) for item in impacts
    }
    return {
        "workspace_id": workspace_id,
        "generated_at": datetime.now(UTC),
        "analyzed_lineage_count": len(latest_rows),
        "total_lineage_count": total_lineages,
        "affected_consumer_count": len(affected_consumers),
        "impact_count": len(impacts),
        "truncated": truncated,
        "impacts": impacts,
    }


__all__ = ["build_report"]
