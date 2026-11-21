"""Market Workspace、上下文版本与研究问题的领域服务。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.errors import NotFound, Unprocessable
from app.models.market import (
    MarketActivityEvent,
    MarketContextSnapshot,
    MarketResearchQuestion,
    MarketWorkspace,
)

_INITIAL_EXTRACTED_CONTEXT: dict[str, Any] = {
    "offerings": [],
    "business_models": [],
    "industries": [],
    "customer_segments": [],
    "value_propositions": [],
    "target_markets": [],
    "goals": [],
    "constraints": [],
    "assumptions": [],
    "extensions": {},
}


def _record_activity(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    *,
    actor_user_id: UUID | None,
    aggregate_type: str,
    aggregate_id: UUID,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> MarketActivityEvent:
    event = MarketActivityEvent(
        organization_id=organization_id,
        workspace_id=workspace_id,
        actor_type="USER" if actor_user_id else "SYSTEM",
        actor_user_id=actor_user_id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type=event_type,
        payload=payload or {},
    )
    session.add(event)
    session.flush()
    return event


def get_workspace(
    session: Session, organization_id: UUID | str, workspace_id: UUID
) -> MarketWorkspace | None:
    return session.scalars(
        select(MarketWorkspace).where(
            MarketWorkspace.organization_id == organization_id,
            MarketWorkspace.id == workspace_id,
        )
    ).one_or_none()


def list_workspaces(
    session: Session,
    organization_id: UUID | str,
    *,
    limit: int,
    offset: int,
) -> list[MarketWorkspace]:
    return list(
        session.scalars(
            select(MarketWorkspace)
            .where(MarketWorkspace.organization_id == organization_id)
            .order_by(MarketWorkspace.updated_at.desc(), MarketWorkspace.id)
            .offset(offset)
            .limit(limit)
        )
    )


def create_workspace(
    session: Session,
    organization_id: UUID | str,
    *,
    owner_user_id: UUID,
    name: str,
    objective: str | None,
    raw_input: str,
    settings: dict[str, Any] | None = None,
) -> MarketWorkspace:
    workspace = MarketWorkspace(
        organization_id=organization_id,
        owner_user_id=owner_user_id,
        name=name.strip(),
        objective=objective,
        settings=settings or {},
        status="DRAFT",
        current_context_version=1,
    )
    session.add(workspace)
    session.flush()

    snapshot = MarketContextSnapshot(
        organization_id=organization_id,
        workspace_id=workspace.id,
        version=1,
        raw_input=raw_input,
        extracted_context=deepcopy(_INITIAL_EXTRACTED_CONTEXT),
        user_overrides={},
        unknowns={},
        provenance={"raw_input": {"source": "USER"}},
        extraction_status="NOT_STARTED",
        is_current=True,
        created_by_user_id=owner_user_id,
    )
    session.add(snapshot)
    session.flush()
    _record_activity(
        session,
        organization_id,
        workspace.id,
        actor_user_id=owner_user_id,
        aggregate_type="MARKET_WORKSPACE",
        aggregate_id=workspace.id,
        event_type="WORKSPACE_CREATED",
        payload={"context_version": 1},
    )
    return workspace


def update_workspace(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    *,
    actor_user_id: UUID,
    changes: dict[str, Any],
) -> MarketWorkspace:
    workspace = get_workspace(session, organization_id, workspace_id)
    if workspace is None:
        raise NotFound("市场工作空间不存在", details={"workspace_id": str(workspace_id)})
    allowed = {"name", "objective", "settings"}
    unexpected = set(changes) - allowed
    if unexpected:
        raise Unprocessable(
            "工作空间只允许修改名称、目标和设置",
            details={"fields": sorted(unexpected)},
        )
    for field, value in changes.items():
        if field == "name":
            if value is None or not str(value).strip():
                raise Unprocessable("工作空间名称不能为空")
            setattr(workspace, field, str(value).strip())
        elif field == "settings" and value is None:
            workspace.settings = {}
        else:
            setattr(workspace, field, value)
    session.flush()
    _record_activity(
        session,
        organization_id,
        workspace.id,
        actor_user_id=actor_user_id,
        aggregate_type="MARKET_WORKSPACE",
        aggregate_id=workspace.id,
        event_type="WORKSPACE_UPDATED",
        payload={"changed_fields": sorted(changes)},
    )
    return workspace


def list_contexts(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
) -> list[MarketContextSnapshot]:
    if get_workspace(session, organization_id, workspace_id) is None:
        raise NotFound("市场工作空间不存在", details={"workspace_id": str(workspace_id)})
    return list(
        session.scalars(
            select(MarketContextSnapshot)
            .where(
                MarketContextSnapshot.organization_id == organization_id,
                MarketContextSnapshot.workspace_id == workspace_id,
            )
            .order_by(MarketContextSnapshot.version.desc())
        )
    )


def create_context_snapshot(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    *,
    actor_user_id: UUID,
    raw_input: str,
    user_overrides: dict[str, Any] | None = None,
) -> MarketContextSnapshot:
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

    current = session.scalars(
        select(MarketContextSnapshot).where(
            MarketContextSnapshot.organization_id == organization_id,
            MarketContextSnapshot.workspace_id == workspace_id,
            MarketContextSnapshot.is_current.is_(True),
        )
    ).one_or_none()
    latest_version = session.scalar(
        select(func.max(MarketContextSnapshot.version)).where(
            MarketContextSnapshot.organization_id == organization_id,
            MarketContextSnapshot.workspace_id == workspace_id,
        )
    ) or 0
    snapshot = MarketContextSnapshot(
        organization_id=organization_id,
        workspace_id=workspace_id,
        version=latest_version + 1,
        raw_input=raw_input,
        extracted_context=dict(_INITIAL_EXTRACTED_CONTEXT),
        user_overrides=(
            user_overrides if user_overrides is not None
            else (deepcopy(current.user_overrides) if current else {})
        ),
        unknowns={},
        provenance={
            "raw_input": {"source": "USER"},
            "user_overrides": {"source": "USER_EDIT"} if user_overrides is not None else {},
        },
        extraction_status="NOT_STARTED",
        is_current=False,
        created_by_user_id=actor_user_id,
    )
    session.add(snapshot)
    session.flush()
    _record_activity(
        session,
        organization_id,
        workspace.id,
        actor_user_id=actor_user_id,
        aggregate_type="MARKET_CONTEXT",
        aggregate_id=snapshot.id,
        event_type="CONTEXT_VERSION_CREATED",
        payload={"version": snapshot.version, "previous_current_version": workspace.current_context_version},
    )
    return snapshot


def adopt_context_snapshot(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    context_id: UUID,
    *,
    actor_user_id: UUID,
) -> MarketContextSnapshot:
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
    snapshot = session.scalars(
        select(MarketContextSnapshot).where(
            MarketContextSnapshot.organization_id == organization_id,
            MarketContextSnapshot.workspace_id == workspace_id,
            MarketContextSnapshot.id == context_id,
        )
    ).one_or_none()
    if snapshot is None:
        raise NotFound("上下文版本不存在", details={"context_id": str(context_id)})

    if not snapshot.is_current:
        session.query(MarketContextSnapshot).filter(
            MarketContextSnapshot.organization_id == organization_id,
            MarketContextSnapshot.workspace_id == workspace_id,
            MarketContextSnapshot.is_current.is_(True),
        ).update({"is_current": False}, synchronize_session=False)
        snapshot.is_current = True
        workspace.current_context_version = snapshot.version
        session.flush()
        _record_activity(
            session,
            organization_id,
            workspace.id,
            actor_user_id=actor_user_id,
            aggregate_type="MARKET_CONTEXT",
            aggregate_id=snapshot.id,
            event_type="CONTEXT_VERSION_ADOPTED",
            payload={"version": snapshot.version},
        )
    return snapshot


def list_questions(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    *,
    limit: int,
    offset: int,
) -> list[MarketResearchQuestion]:
    if get_workspace(session, organization_id, workspace_id) is None:
        raise NotFound("市场工作空间不存在", details={"workspace_id": str(workspace_id)})
    return list(
        session.scalars(
            select(MarketResearchQuestion)
            .where(
                MarketResearchQuestion.organization_id == organization_id,
                MarketResearchQuestion.workspace_id == workspace_id,
            )
            .order_by(
                MarketResearchQuestion.priority,
                MarketResearchQuestion.created_at,
                MarketResearchQuestion.id,
            )
            .offset(offset)
            .limit(limit)
        )
    )


def create_question(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    *,
    actor_user_id: UUID,
    question: str,
    rationale: str | None,
    priority: int,
    parent_question_id: UUID | None,
    acceptance_criteria: dict[str, Any] | None,
) -> MarketResearchQuestion:
    workspace = get_workspace(session, organization_id, workspace_id)
    if workspace is None:
        raise NotFound("市场工作空间不存在", details={"workspace_id": str(workspace_id)})
    if parent_question_id is not None:
        parent = session.scalars(
            select(MarketResearchQuestion).where(
                MarketResearchQuestion.organization_id == organization_id,
                MarketResearchQuestion.workspace_id == workspace_id,
                MarketResearchQuestion.id == parent_question_id,
            )
        ).one_or_none()
        if parent is None:
            raise NotFound(
                "父研究问题不存在或不属于此工作空间",
                details={"parent_question_id": str(parent_question_id)},
            )
    row = MarketResearchQuestion(
        organization_id=organization_id,
        workspace_id=workspace_id,
        parent_question_id=parent_question_id,
        question=question.strip(),
        rationale=rationale,
        source="USER",
        priority=priority,
        status="OPEN",
        acceptance_criteria=acceptance_criteria or {},
        created_by_user_id=actor_user_id,
    )
    session.add(row)
    session.flush()
    _record_activity(
        session,
        organization_id,
        workspace_id,
        actor_user_id=actor_user_id,
        aggregate_type="MARKET_RESEARCH_QUESTION",
        aggregate_id=row.id,
        event_type="RESEARCH_QUESTION_CREATED",
        payload={"priority": row.priority},
    )
    return row


def get_question(
    session: Session, organization_id: UUID | str, question_id: UUID
) -> MarketResearchQuestion | None:
    return session.scalars(
        select(MarketResearchQuestion).where(
            MarketResearchQuestion.organization_id == organization_id,
            MarketResearchQuestion.id == question_id,
        )
    ).one_or_none()


def update_question(
    session: Session,
    organization_id: UUID | str,
    question_id: UUID,
    *,
    actor_user_id: UUID,
    changes: dict[str, Any],
) -> MarketResearchQuestion:
    row = get_question(session, organization_id, question_id)
    if row is None:
        raise NotFound("研究问题不存在", details={"question_id": str(question_id)})
    allowed = {"question", "rationale", "priority", "acceptance_criteria"}
    unexpected = set(changes) - allowed
    if unexpected:
        raise Unprocessable(
            "研究问题只允许修改问题、原因、优先级和验收条件",
            details={"fields": sorted(unexpected)},
        )
    for field, value in changes.items():
        if field == "question":
            if value is None or not str(value).strip():
                raise Unprocessable("研究问题不能为空")
            setattr(row, field, str(value).strip())
        elif field == "priority" and value is None:
            raise Unprocessable("研究问题优先级不能为空")
        elif field == "acceptance_criteria" and value is None:
            row.acceptance_criteria = {}
        else:
            setattr(row, field, value)
    session.flush()
    _record_activity(
        session,
        organization_id,
        row.workspace_id,
        actor_user_id=actor_user_id,
        aggregate_type="MARKET_RESEARCH_QUESTION",
        aggregate_id=row.id,
        event_type="RESEARCH_QUESTION_UPDATED",
        payload={"changed_fields": sorted(changes)},
    )
    return row


def list_activity(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    *,
    limit: int,
    offset: int,
) -> list[MarketActivityEvent]:
    if get_workspace(session, organization_id, workspace_id) is None:
        raise NotFound("市场工作空间不存在", details={"workspace_id": str(workspace_id)})
    return list(
        session.scalars(
            select(MarketActivityEvent)
            .where(
                MarketActivityEvent.organization_id == organization_id,
                MarketActivityEvent.workspace_id == workspace_id,
            )
            .order_by(MarketActivityEvent.created_at.desc(), MarketActivityEvent.id)
            .offset(offset)
            .limit(limit)
        )
    )
