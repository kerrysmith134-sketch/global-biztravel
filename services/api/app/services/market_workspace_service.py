"""市场 Workspace 基础用例。所有写操作与活动事件处于同一数据库事务。"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.errors import NotFound
from app.models.market import (
    MarketActivityEvent,
    MarketContextSnapshot,
    MarketResearchQuestion,
    MarketWorkspace,
)


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
    limit: int = 50,
    offset: int = 0,
) -> list[MarketWorkspace]:
    stmt = (
        select(MarketWorkspace)
        .where(MarketWorkspace.organization_id == organization_id)
        .order_by(MarketWorkspace.updated_at.desc(), MarketWorkspace.id)
        .limit(limit)
        .offset(offset)
    )
    return list(session.scalars(stmt).all())


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
) -> None:
    session.add(
        MarketActivityEvent(
            organization_id=organization_id,
            workspace_id=workspace_id,
            actor_type="USER" if actor_user_id is not None else "SYSTEM",
            actor_user_id=actor_user_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=payload or {},
        )
    )


def create_workspace(
    session: Session,
    organization_id: UUID | str,
    *,
    name: str,
    objective: str | None,
    settings: dict[str, Any],
    owner_user_id: UUID,
    created_by_user_id: UUID,
    initial_context: str | None = None,
) -> MarketWorkspace:
    workspace = MarketWorkspace(
        organization_id=organization_id,
        name=name.strip(),
        objective=objective.strip() if objective and objective.strip() else None,
        status="DRAFT",
        owner_user_id=owner_user_id,
        settings=settings,
        created_by_user_id=created_by_user_id,
    )
    session.add(workspace)
    session.flush()

    _record_activity(
        session,
        organization_id,
        workspace.id,
        actor_user_id=created_by_user_id,
        aggregate_type="market_workspace",
        aggregate_id=workspace.id,
        event_type="WORKSPACE_CREATED",
        payload={"name": workspace.name},
    )

    if initial_context is not None and initial_context.strip():
        snapshot = MarketContextSnapshot(
            organization_id=organization_id,
            workspace_id=workspace.id,
            version=1,
            raw_input=initial_context,
            extracted_context={},
            user_overrides={},
            unknowns=[],
            provenance={"raw_input": {"source": "USER"}},
            extraction_status="NOT_STARTED",
            is_current=True,
            created_by_user_id=created_by_user_id,
        )
        session.add(snapshot)
        workspace.current_context_version = 1
        session.flush()
        _record_activity(
            session,
            organization_id,
            workspace.id,
            actor_user_id=created_by_user_id,
            aggregate_type="market_context_snapshot",
            aggregate_id=snapshot.id,
            event_type="CONTEXT_SNAPSHOT_CREATED_AND_ADOPTED",
            payload={"version": 1},
        )

    session.flush()
    return workspace


def list_contexts(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
) -> list[MarketContextSnapshot]:
    return list(
        session.scalars(
            select(MarketContextSnapshot)
            .where(
                MarketContextSnapshot.organization_id == organization_id,
                MarketContextSnapshot.workspace_id == workspace_id,
            )
            .order_by(MarketContextSnapshot.version.desc())
        ).all()
    )


def create_context_snapshot(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    *,
    raw_input: str,
    adopt: bool,
    created_by_user_id: UUID,
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

    max_version = session.scalar(
        select(func.max(MarketContextSnapshot.version)).where(
            MarketContextSnapshot.organization_id == organization_id,
            MarketContextSnapshot.workspace_id == workspace_id,
        )
    )
    version = int(max_version or 0) + 1
    if adopt:
        session.execute(
            update(MarketContextSnapshot)
            .where(
                MarketContextSnapshot.organization_id == organization_id,
                MarketContextSnapshot.workspace_id == workspace_id,
                MarketContextSnapshot.is_current.is_(True),
            )
            .values(is_current=False)
        )

    snapshot = MarketContextSnapshot(
        organization_id=organization_id,
        workspace_id=workspace_id,
        version=version,
        raw_input=raw_input,
        extracted_context={},
        user_overrides={},
        unknowns=[],
        provenance={"raw_input": {"source": "USER"}},
        extraction_status="NOT_STARTED",
        is_current=adopt,
        created_by_user_id=created_by_user_id,
    )
    session.add(snapshot)
    if adopt:
        workspace.current_context_version = version
    session.flush()

    _record_activity(
        session,
        organization_id,
        workspace_id,
        actor_user_id=created_by_user_id,
        aggregate_type="market_context_snapshot",
        aggregate_id=snapshot.id,
        event_type="CONTEXT_SNAPSHOT_CREATED_AND_ADOPTED" if adopt else "CONTEXT_SNAPSHOT_CREATED",
        payload={"version": version},
    )
    session.flush()
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

    session.execute(
        update(MarketContextSnapshot)
        .where(
            MarketContextSnapshot.organization_id == organization_id,
            MarketContextSnapshot.workspace_id == workspace_id,
            MarketContextSnapshot.is_current.is_(True),
        )
        .values(is_current=False)
    )
    snapshot.is_current = True
    workspace.current_context_version = snapshot.version
    session.flush()

    _record_activity(
        session,
        organization_id,
        workspace_id,
        actor_user_id=actor_user_id,
        aggregate_type="market_context_snapshot",
        aggregate_id=snapshot.id,
        event_type="CONTEXT_SNAPSHOT_ADOPTED",
        payload={"version": snapshot.version},
    )
    session.flush()
    return snapshot


def create_research_question(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    *,
    question: str,
    rationale: str | None,
    priority: int,
    acceptance_criteria: list[Any],
    parent_question_id: UUID | None,
    created_by_user_id: UUID,
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
                "父研究问题不存在",
                details={"parent_question_id": str(parent_question_id)},
            )

    item = MarketResearchQuestion(
        organization_id=organization_id,
        workspace_id=workspace_id,
        parent_question_id=parent_question_id,
        question=question.strip(),
        rationale=rationale.strip() if rationale and rationale.strip() else None,
        source="USER",
        priority=priority,
        status="OPEN",
        acceptance_criteria=acceptance_criteria,
        created_by_user_id=created_by_user_id,
    )
    session.add(item)
    session.flush()

    _record_activity(
        session,
        organization_id,
        workspace_id,
        actor_user_id=created_by_user_id,
        aggregate_type="market_research_question",
        aggregate_id=item.id,
        event_type="RESEARCH_QUESTION_CREATED",
        payload={"priority": priority, "source": "USER"},
    )
    session.flush()
    return item


def list_research_questions(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[MarketResearchQuestion]:
    workspace = get_workspace(session, organization_id, workspace_id)
    if workspace is None:
        raise NotFound("市场工作空间不存在", details={"workspace_id": str(workspace_id)})
    stmt = (
        select(MarketResearchQuestion)
        .where(
            MarketResearchQuestion.organization_id == organization_id,
            MarketResearchQuestion.workspace_id == workspace_id,
        )
        .order_by(MarketResearchQuestion.priority, MarketResearchQuestion.created_at)
        .limit(limit)
        .offset(offset)
    )
    return list(session.scalars(stmt).all())


def list_activity_events(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    *,
    limit: int = 100,
) -> list[MarketActivityEvent]:
    workspace = get_workspace(session, organization_id, workspace_id)
    if workspace is None:
        raise NotFound("市场工作空间不存在", details={"workspace_id": str(workspace_id)})
    return list(
        session.scalars(
            select(MarketActivityEvent)
            .where(
                MarketActivityEvent.organization_id == organization_id,
                MarketActivityEvent.workspace_id == workspace_id,
            )
            .order_by(MarketActivityEvent.created_at.desc(), MarketActivityEvent.id.desc())
            .limit(limit)
        ).all()
    )
