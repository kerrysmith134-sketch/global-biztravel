"""Evidence-qualified market opportunities and Workspace/Trip associations."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.errors import Conflict, NotFound, Unprocessable
from app.models.identity import Membership
from app.models.market import (
    MarketActivityEvent,
    MarketEntity,
    MarketOpportunity,
    MarketOpportunityAssessment,
    MarketOpportunityEntity,
    MarketOpportunityEvidence,
    MarketWorkspace,
    MarketWorkspaceEntity,
    MarketWorkspaceTripLink,
    MarketWorkspaceWorkflow,
)
from app.models.trip import Trip
from app.models.workflow import AgentRun, Evidence

TRANSITIONS: dict[str, set[str]] = {
    "DISCOVERED": {"RESEARCHING", "DISQUALIFIED"},
    "RESEARCHING": {"EVIDENCE_READY", "DISQUALIFIED"},
    "EVIDENCE_READY": {"QUALIFICATION_PENDING", "RESEARCHING", "DISQUALIFIED"},
    "QUALIFICATION_PENDING": {"RESEARCHING", "DISQUALIFIED"},
    "QUALIFIED": {"ACTION_PLANNED", "ON_HOLD"},
    "ACTION_PLANNED": {"ON_HOLD"},
    "ON_HOLD": {"RESEARCHING", "ACTION_PLANNED", "DISQUALIFIED"},
}


def _workspace(
    session: Session, organization_id: UUID | str, workspace_id: UUID, *, lock: bool = False
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


def _opportunity(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    opportunity_id: UUID,
    *,
    lock: bool = False,
) -> MarketOpportunity:
    query = select(MarketOpportunity).where(
        MarketOpportunity.organization_id == organization_id,
        MarketOpportunity.workspace_id == workspace_id,
        MarketOpportunity.id == opportunity_id,
    )
    if lock:
        query = query.with_for_update()
    row = session.scalars(query).one_or_none()
    if row is None:
        raise NotFound("商业机会不存在", details={"opportunity_id": str(opportunity_id)})
    return row


def _activity(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    actor_user_id: UUID | None,
    aggregate_type: str,
    aggregate_id: UUID,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    session.add(
        MarketActivityEvent(
            organization_id=organization_id,
            workspace_id=workspace_id,
            actor_type="USER" if actor_user_id else "SYSTEM",
            actor_user_id=actor_user_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=payload,
        )
    )


def _validate_run_for_workspace(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    run_id: UUID | None,
    *,
    field: str,
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
            f"{field} 必须属于当前 Workspace 关联的 ResearchPlan",
            details={"run_id": str(run_id)},
        )


def _evidence_scope(
    session: Session, organization_id: UUID | str, workspace_id: UUID
) -> tuple[set[UUID], set[UUID]]:
    workflow_ids = set(
        session.scalars(
            select(MarketWorkspaceWorkflow.workflow_id).where(
                MarketWorkspaceWorkflow.organization_id == organization_id,
                MarketWorkspaceWorkflow.workspace_id == workspace_id,
            )
        ).all()
    )
    evidence_ids = set(
        session.scalars(
            select(Evidence.id).where(
                Evidence.organization_id == organization_id,
                or_(
                    (Evidence.subject_type == "market_workspace")
                    & (Evidence.subject_id == workspace_id),
                    (Evidence.subject_type == "RESEARCH_PLAN")
                    & Evidence.subject_id.in_(workflow_ids),
                ),
            )
        ).all()
    )
    return evidence_ids, workflow_ids


def create_opportunity(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    *,
    title: str,
    description: str | None,
    next_action: str | None,
    owner_user_id: UUID | None,
    created_by_run_id: UUID | None,
    created_by_user_id: UUID,
) -> MarketOpportunity:
    _workspace(session, organization_id, workspace_id)
    _validate_run_for_workspace(
        session, organization_id, workspace_id, created_by_run_id, field="创建来源 AgentRun"
    )
    if owner_user_id is not None:
        member = session.scalars(
            select(Membership).where(
                Membership.organization_id == organization_id,
                Membership.user_id == owner_user_id,
            )
        ).one_or_none()
        if member is None:
            raise NotFound("指定负责人不是当前组织成员")
    row = MarketOpportunity(
        organization_id=organization_id,
        workspace_id=workspace_id,
        title=title.strip(),
        description=description.strip() if description and description.strip() else None,
        next_action=next_action.strip() if next_action and next_action.strip() else None,
        owner_user_id=owner_user_id,
        created_by_run_id=created_by_run_id,
        created_by_user_id=created_by_user_id,
        status="DISCOVERED",
    )
    session.add(row)
    session.flush()
    _activity(
        session, organization_id, workspace_id, created_by_user_id,
        "market_opportunity", row.id, "MARKET_OPPORTUNITY_CREATED",
        {"title": row.title, "status": row.status},
    )
    session.flush()
    return row


def list_opportunities(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    *,
    status_filter: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[MarketOpportunity]:
    _workspace(session, organization_id, workspace_id)
    query = select(MarketOpportunity).where(
        MarketOpportunity.organization_id == organization_id,
        MarketOpportunity.workspace_id == workspace_id,
    )
    if status_filter:
        query = query.where(MarketOpportunity.status == status_filter)
    return list(
        session.scalars(
            query.order_by(MarketOpportunity.updated_at.desc(), MarketOpportunity.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
    )


def list_opportunity_evidence(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    opportunity_id: UUID,
) -> list[dict[str, Any]]:
    _opportunity(session, organization_id, workspace_id, opportunity_id)
    rows = session.execute(
        select(MarketOpportunityEvidence, Evidence)
        .join(
            Evidence,
            (Evidence.organization_id == MarketOpportunityEvidence.organization_id)
            & (Evidence.id == MarketOpportunityEvidence.evidence_id),
        )
        .where(
            MarketOpportunityEvidence.organization_id == organization_id,
            MarketOpportunityEvidence.workspace_id == workspace_id,
            MarketOpportunityEvidence.opportunity_id == opportunity_id,
        )
        .order_by(MarketOpportunityEvidence.created_at, MarketOpportunityEvidence.id)
    ).all()
    return [
        {"relation_type": link.relation_type, "rationale": link.rationale, "evidence": evidence}
        for link, evidence in rows
    ]


def add_opportunity_evidence(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    opportunity_id: UUID,
    *,
    evidence_id: UUID,
    relation_type: str,
    rationale: str | None,
    actor_user_id: UUID,
) -> dict[str, Any]:
    _opportunity(session, organization_id, workspace_id, opportunity_id, lock=True)
    allowed_evidence, _ = _evidence_scope(session, organization_id, workspace_id)
    if evidence_id not in allowed_evidence:
        raise NotFound(
            "Evidence 不属于当前 Workspace 或其 ResearchPlan",
            details={"evidence_id": str(evidence_id)},
        )
    row = session.scalars(
        select(MarketOpportunityEvidence).where(
            MarketOpportunityEvidence.organization_id == organization_id,
            MarketOpportunityEvidence.workspace_id == workspace_id,
            MarketOpportunityEvidence.opportunity_id == opportunity_id,
            MarketOpportunityEvidence.evidence_id == evidence_id,
            MarketOpportunityEvidence.relation_type == relation_type,
        )
    ).one_or_none()
    if row is None:
        row = MarketOpportunityEvidence(
            organization_id=organization_id,
            workspace_id=workspace_id,
            opportunity_id=opportunity_id,
            evidence_id=evidence_id,
            relation_type=relation_type,
            rationale=rationale.strip() if rationale and rationale.strip() else None,
        )
        session.add(row)
        session.flush()
        _activity(
            session, organization_id, workspace_id, actor_user_id,
            "market_opportunity", opportunity_id, "MARKET_OPPORTUNITY_EVIDENCE_LINKED",
            {"evidence_id": str(evidence_id), "relation_type": relation_type},
        )
        session.flush()
    evidence = session.get(Evidence, evidence_id)
    return {"relation_type": row.relation_type, "rationale": row.rationale, "evidence": evidence}


def list_opportunity_entities(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    opportunity_id: UUID,
) -> list[MarketEntity]:
    _opportunity(session, organization_id, workspace_id, opportunity_id)
    return list(
        session.scalars(
            select(MarketEntity)
            .join(
                MarketOpportunityEntity,
                (MarketEntity.organization_id == MarketOpportunityEntity.organization_id)
                & (MarketEntity.id == MarketOpportunityEntity.entity_id),
            )
            .where(
                MarketOpportunityEntity.organization_id == organization_id,
                MarketOpportunityEntity.workspace_id == workspace_id,
                MarketOpportunityEntity.opportunity_id == opportunity_id,
            )
            .order_by(MarketEntity.display_name, MarketEntity.id)
        ).all()
    )


def attach_opportunity_entities(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    opportunity_id: UUID,
    *,
    entity_ids: list[UUID],
    relationship_note: str | None,
    actor_user_id: UUID,
) -> list[MarketEntity]:
    _opportunity(session, organization_id, workspace_id, opportunity_id, lock=True)
    if len(entity_ids) != len(set(entity_ids)):
        raise Unprocessable("MarketEntity ID 不得重复")
    if not entity_ids:
        return []
    workspace_entities = set(
        session.scalars(
            select(MarketWorkspaceEntity.entity_id).where(
                MarketWorkspaceEntity.organization_id == organization_id,
                MarketWorkspaceEntity.workspace_id == workspace_id,
                MarketWorkspaceEntity.entity_id.in_(entity_ids),
            )
        ).all()
    )
    if workspace_entities != set(entity_ids):
        raise NotFound("部分 MarketEntity 不属于当前 Workspace")
    existing = set(
        session.scalars(
            select(MarketOpportunityEntity.entity_id).where(
                MarketOpportunityEntity.organization_id == organization_id,
                MarketOpportunityEntity.workspace_id == workspace_id,
                MarketOpportunityEntity.opportunity_id == opportunity_id,
            )
        ).all()
    )
    new_ids = [entity_id for entity_id in entity_ids if entity_id not in existing]
    for entity_id in new_ids:
        session.add(
            MarketOpportunityEntity(
                organization_id=organization_id,
                workspace_id=workspace_id,
                opportunity_id=opportunity_id,
                entity_id=entity_id,
                relationship_note=(
                    relationship_note.strip()
                    if relationship_note and relationship_note.strip()
                    else None
                ),
            )
        )
    if new_ids:
        _activity(
            session, organization_id, workspace_id, actor_user_id,
            "market_opportunity", opportunity_id, "MARKET_OPPORTUNITY_ENTITIES_LINKED",
            {"entity_ids": [str(value) for value in new_ids]},
        )
    session.flush()
    return list_opportunity_entities(session, organization_id, workspace_id, opportunity_id)


def _validate_assessment_dimensions(
    dimensions: dict[str, dict[str, Any]], allowed_evidence_ids: set[UUID]
) -> dict[str, dict[str, Any]]:
    if not dimensions:
        raise Unprocessable("评估至少需要一个维度")
    normalized: dict[str, dict[str, Any]] = {}
    for raw_key, raw_value in dimensions.items():
        key = raw_key.strip()
        if not key or not isinstance(raw_value, dict):
            raise Unprocessable("评估维度必须是具名对象")
        if "value" not in raw_value:
            raise Unprocessable(f"评估维度 {key} 缺少 value；未知值请显式填写 UNKNOWN")
        value = raw_value["value"]
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise Unprocessable(f"评估维度 {key} 的 value 类型无效")
        if isinstance(value, float) and not (value == value and abs(value) != float("inf")):
            raise Unprocessable(f"评估维度 {key} 的 value 不能是 NaN/Infinity")
        rationale = raw_value.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            raise Unprocessable(f"评估维度 {key} 必须提供 rationale")
        refs = raw_value.get("evidence_refs", [])
        if not isinstance(refs, list):
            raise Unprocessable(f"评估维度 {key} 的 evidence_refs 必须是数组")
        parsed_refs: list[str] = []
        for ref in refs:
            try:
                evidence_id = UUID(str(ref))
            except (TypeError, ValueError) as exc:
                raise Unprocessable(f"评估维度 {key} 包含无效 Evidence ID") from exc
            if evidence_id not in allowed_evidence_ids:
                raise Conflict(
                    f"评估维度 {key} 引用了未关联到该机会的 Evidence",
                    details={"evidence_id": str(evidence_id)},
                )
            parsed_refs.append(str(evidence_id))
        unknowns = raw_value.get("unknowns", [])
        if not isinstance(unknowns, list):
            raise Unprocessable(f"评估维度 {key} 的 unknowns 必须是数组")
        normalized[key] = {
            "value": value,
            "rationale": rationale.strip(),
            "evidence_refs": parsed_refs,
            "unknowns": [str(item) for item in unknowns],
        }
    return normalized


def assess_opportunity(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    opportunity_id: UUID,
    *,
    dimensions: dict[str, dict[str, Any]],
    summary: str | None,
    confidence_level: str,
    assessed_by_run_id: UUID | None,
    assessed_by_user_id: UUID,
) -> dict[str, Any]:
    opportunity = _opportunity(
        session, organization_id, workspace_id, opportunity_id, lock=True
    )
    _validate_run_for_workspace(
        session, organization_id, workspace_id, assessed_by_run_id, field="评估来源 AgentRun"
    )
    linked_evidence_ids = set(
        session.scalars(
            select(MarketOpportunityEvidence.evidence_id).where(
                MarketOpportunityEvidence.organization_id == organization_id,
                MarketOpportunityEvidence.opportunity_id == opportunity_id,
            )
        ).all()
    )
    normalized = _validate_assessment_dimensions(dimensions, linked_evidence_ids)
    version = opportunity.assessment_version + 1
    row = MarketOpportunityAssessment(
        organization_id=organization_id,
        workspace_id=workspace_id,
        opportunity_id=opportunity_id,
        version=version,
        dimensions=normalized,
        summary=summary.strip() if summary and summary.strip() else None,
        confidence_level=confidence_level,
        assessed_by_run_id=assessed_by_run_id,
        assessed_by_user_id=assessed_by_user_id,
    )
    session.add(row)
    opportunity.assessment_version = version
    opportunity.assessment_dimensions = normalized
    opportunity.assessment_summary = row.summary
    opportunity.assessment_confidence = confidence_level
    session.flush()
    _activity(
        session, organization_id, workspace_id, assessed_by_user_id,
        "market_opportunity", opportunity_id, "MARKET_OPPORTUNITY_ASSESSED",
        {"assessment_version": version, "confidence_level": confidence_level},
    )
    session.flush()
    return {
        "id": row.id,
        "opportunity_id": row.opportunity_id,
        "version": row.version,
        "dimensions": row.dimensions,
        "summary": row.summary,
        "confidence_level": row.confidence_level,
        "assessed_by_run_id": row.assessed_by_run_id,
        "assessed_by_user_id": row.assessed_by_user_id,
        "created_at": row.created_at,
    }


def list_assessments(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    opportunity_id: UUID,
) -> list[MarketOpportunityAssessment]:
    _opportunity(session, organization_id, workspace_id, opportunity_id)
    return list(
        session.scalars(
            select(MarketOpportunityAssessment)
            .where(
                MarketOpportunityAssessment.organization_id == organization_id,
                MarketOpportunityAssessment.workspace_id == workspace_id,
                MarketOpportunityAssessment.opportunity_id == opportunity_id,
            )
            .order_by(MarketOpportunityAssessment.version)
        ).all()
    )


def transition_opportunity(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    opportunity_id: UUID,
    *,
    target_status: str,
    note: str | None,
    actor_user_id: UUID,
) -> MarketOpportunity:
    _workspace(session, organization_id, workspace_id, lock=True)
    row = _opportunity(session, organization_id, workspace_id, opportunity_id, lock=True)
    allowed = TRANSITIONS.get(row.status, set())
    if target_status not in allowed:
        raise Conflict(
            "不允许的商业机会状态迁移",
            details={"current_status": row.status, "target_status": target_status},
        )
    if target_status == "EVIDENCE_READY":
        relations = session.scalars(
            select(MarketOpportunityEvidence.relation_type).where(
                MarketOpportunityEvidence.organization_id == organization_id,
                MarketOpportunityEvidence.opportunity_id == opportunity_id,
            )
        ).all()
        if "SUPPORTS" not in relations:
            raise Conflict("证据就绪至少需要一条 SUPPORTS 证据")
    if target_status == "DISQUALIFIED":
        if not (note and note.strip()):
            raise Unprocessable("淘汰机会必须说明原因")
        row.disqualification_reason = note.strip()
    previous = row.status
    row.status = target_status
    row.updated_at = datetime.now(UTC)
    _activity(
        session, organization_id, workspace_id, actor_user_id,
        "market_opportunity", row.id, "MARKET_OPPORTUNITY_STATUS_CHANGED",
        {"from": previous, "to": target_status, "note": note},
    )
    session.flush()
    return row


def qualify_opportunity(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    opportunity_id: UUID,
    *,
    qualification_reason: str,
    actor_user_id: UUID,
) -> MarketOpportunity:
    _workspace(session, organization_id, workspace_id, lock=True)
    row = _opportunity(session, organization_id, workspace_id, opportunity_id, lock=True)
    if row.status != "QUALIFICATION_PENDING":
        raise Conflict(
            "只有处于 QUALIFICATION_PENDING 的机会才能确认资格",
            details={"status": row.status},
        )
    if not qualification_reason.strip():
        raise Unprocessable("确认机会资格必须提供理由")
    relations = session.scalars(
        select(MarketOpportunityEvidence.relation_type).where(
            MarketOpportunityEvidence.organization_id == organization_id,
            MarketOpportunityEvidence.opportunity_id == opportunity_id,
        )
    ).all()
    if "SUPPORTS" not in relations:
        raise Conflict("确认机会资格至少需要一条 SUPPORTS 证据")
    if "CONTRADICTS" in relations:
        raise Conflict("存在尚未解决的反驳证据，不能确认机会资格")
    row.status = "QUALIFIED"
    row.qualification_reason = qualification_reason.strip()
    row.qualified_by_user_id = actor_user_id
    row.qualified_at = datetime.now(UTC)
    _activity(
        session, organization_id, workspace_id, actor_user_id,
        "market_opportunity", row.id, "MARKET_OPPORTUNITY_QUALIFIED",
        {"qualification_reason": row.qualification_reason},
    )
    session.flush()
    return row


def record_opportunity_outcome(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    opportunity_id: UUID,
    *,
    outcome: str,
    outcome_note: str,
    actor_user_id: UUID,
) -> MarketOpportunity:
    _workspace(session, organization_id, workspace_id, lock=True)
    row = _opportunity(session, organization_id, workspace_id, opportunity_id, lock=True)
    if row.status != "ACTION_PLANNED" or outcome not in {"WON", "LOST"}:
        raise Conflict(
            "只有 ACTION_PLANNED 的机会可以记录 WON/LOST 结果",
            details={"status": row.status, "outcome": outcome},
        )
    if not outcome_note.strip():
        raise Unprocessable("记录 WON/LOST 必须提供业务事件或结果说明")
    row.status = outcome
    row.outcome_note = outcome_note.strip()
    row.outcome_recorded_by_user_id = actor_user_id
    row.outcome_recorded_at = datetime.now(UTC)
    _activity(
        session, organization_id, workspace_id, actor_user_id,
        "market_opportunity", row.id, f"MARKET_OPPORTUNITY_{outcome}",
        {"outcome_note": row.outcome_note},
    )
    session.flush()
    return row


def list_workspace_trips(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    *,
    linked_only: bool = True,
) -> list[dict[str, Any]]:
    _workspace(session, organization_id, workspace_id)
    query = (
        select(MarketWorkspaceTripLink, Trip)
        .join(
            Trip,
            (Trip.organization_id == MarketWorkspaceTripLink.organization_id)
            & (Trip.id == MarketWorkspaceTripLink.trip_id),
        )
        .where(
            MarketWorkspaceTripLink.organization_id == organization_id,
            MarketWorkspaceTripLink.workspace_id == workspace_id,
        )
    )
    if linked_only:
        query = query.where(MarketWorkspaceTripLink.link_status == "LINKED")
    rows = session.execute(
        query.order_by(MarketWorkspaceTripLink.linked_at.desc(), MarketWorkspaceTripLink.id)
    ).all()
    return [
        {
            "id": link.id,
            "workspace_id": link.workspace_id,
            "trip_id": link.trip_id,
            "link_status": link.link_status,
            "link_note": link.link_note,
            "linked_by_user_id": link.linked_by_user_id,
            "linked_at": link.linked_at,
            "unlinked_by_user_id": link.unlinked_by_user_id,
            "unlinked_at": link.unlinked_at,
            "trip_name": trip.name,
            "trip_status": trip.status,
            "window_start": trip.window_start,
            "window_end": trip.window_end,
            "countries": trip.countries,
        }
        for link, trip in rows
    ]


def link_trip_to_workspace(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    trip_id: UUID,
    *,
    link_note: str | None,
    actor_user_id: UUID,
) -> dict[str, Any]:
    _workspace(session, organization_id, workspace_id, lock=True)
    trip = session.scalars(
        select(Trip).where(
            Trip.organization_id == organization_id,
            Trip.id == trip_id,
        )
    ).one_or_none()
    if trip is None:
        raise NotFound("Trip 不存在或不属于当前组织", details={"trip_id": str(trip_id)})
    row = session.scalars(
        select(MarketWorkspaceTripLink)
        .where(
            MarketWorkspaceTripLink.organization_id == organization_id,
            MarketWorkspaceTripLink.workspace_id == workspace_id,
            MarketWorkspaceTripLink.trip_id == trip_id,
        )
        .with_for_update()
    ).one_or_none()
    event_type = "MARKET_WORKSPACE_TRIP_LINKED"
    if row is None:
        row = MarketWorkspaceTripLink(
            organization_id=organization_id,
            workspace_id=workspace_id,
            trip_id=trip_id,
            link_status="LINKED",
            link_note=link_note.strip() if link_note and link_note.strip() else None,
            linked_by_user_id=actor_user_id,
            linked_at=datetime.now(UTC),
        )
        session.add(row)
    elif row.link_status == "UNLINKED":
        row.link_status = "LINKED"
        row.link_note = link_note.strip() if link_note and link_note.strip() else None
        row.linked_by_user_id = actor_user_id
        row.linked_at = datetime.now(UTC)
        row.unlinked_by_user_id = None
        row.unlinked_at = None
    else:
        return next(
            item for item in list_workspace_trips(
                session, organization_id, workspace_id, linked_only=True
            ) if item["trip_id"] == trip_id
        )
    session.flush()
    _activity(
        session, organization_id, workspace_id, actor_user_id,
        "market_workspace_trip", row.id, event_type,
        {"trip_id": str(trip_id), "trip_name": trip.name},
    )
    session.flush()
    return next(
        item for item in list_workspace_trips(
            session, organization_id, workspace_id, linked_only=True
        ) if item["trip_id"] == trip_id
    )


def unlink_trip_from_workspace(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    trip_id: UUID,
    *,
    actor_user_id: UUID,
) -> dict[str, Any]:
    _workspace(session, organization_id, workspace_id, lock=True)
    row = session.scalars(
        select(MarketWorkspaceTripLink)
        .where(
            MarketWorkspaceTripLink.organization_id == organization_id,
            MarketWorkspaceTripLink.workspace_id == workspace_id,
            MarketWorkspaceTripLink.trip_id == trip_id,
        )
        .with_for_update()
    ).one_or_none()
    if row is None:
        raise NotFound("Workspace 与 Trip 的关联不存在")
    if row.link_status == "LINKED":
        row.link_status = "UNLINKED"
        row.unlinked_by_user_id = actor_user_id
        row.unlinked_at = datetime.now(UTC)
        _activity(
            session, organization_id, workspace_id, actor_user_id,
            "market_workspace_trip", row.id, "MARKET_WORKSPACE_TRIP_UNLINKED",
            {"trip_id": str(trip_id)},
        )
        session.flush()
    trip = session.scalars(
        select(Trip).where(
            Trip.organization_id == organization_id,
            Trip.id == trip_id,
        )
    ).one_or_none()
    return {
        "id": row.id,
        "workspace_id": row.workspace_id,
        "trip_id": row.trip_id,
        "link_status": row.link_status,
        "link_note": row.link_note,
        "linked_by_user_id": row.linked_by_user_id,
        "linked_at": row.linked_at,
        "unlinked_by_user_id": row.unlinked_by_user_id,
        "unlinked_at": row.unlinked_at,
        "trip_name": trip.name if trip else None,
        "trip_status": trip.status if trip else None,
        "window_start": trip.window_start if trip else None,
        "window_end": trip.window_end if trip else None,
        "countries": trip.countries if trip else [],
    }


def get_opportunity_detail(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    opportunity_id: UUID,
) -> dict[str, Any]:
    row = _opportunity(session, organization_id, workspace_id, opportunity_id)
    return {
        "opportunity": row,
        "evidence": list_opportunity_evidence(
            session, organization_id, workspace_id, opportunity_id
        ),
        "entities": list_opportunity_entities(
            session, organization_id, workspace_id, opportunity_id
        ),
        "assessments": list_assessments(
            session, organization_id, workspace_id, opportunity_id
        ),
    }
