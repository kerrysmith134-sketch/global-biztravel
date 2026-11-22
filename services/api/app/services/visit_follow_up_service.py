"""Human confirmation and lifecycle management for visit follow-ups."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import Conflict, NotFound, Unprocessable
from app.models.records import VisitFollowUp, VisitFollowUpEvent, VisitRecord
from app.models.workflow import AgentRun
from app.services import visit_service


def _normalise_task(value: object) -> dict[str, str | None] | None:
    if isinstance(value, str):
        title = value.strip()
        return {"title": title[:500], "due_date": None} if title else None
    if not isinstance(value, dict):
        return None
    title = str(value.get("title") or "").strip()
    if not title:
        return None
    due_date = value.get("due_date")
    return {"title": title[:500], "due_date": str(due_date)[:40] if due_date else None}


def _source_key(source_type: str, source_id: UUID, source_index: int) -> str:
    return f"{source_type}:{source_id}:{source_index}"


def _append_event(
    session: Session,
    organization_id: UUID,
    row: VisitFollowUp,
    *,
    event_type: str,
    from_status: str | None,
    actor_user_id: UUID,
    note: str | None = None,
) -> None:
    session.add(
        VisitFollowUpEvent(
            organization_id=organization_id,
            follow_up_id=row.id,
            event_type=event_type,
            from_status=from_status,
            to_status=row.status,
            actor_user_id=actor_user_id,
            note=note,
        )
    )


def _candidate_suggestions(
    session: Session, organization_id: UUID, visit_id: UUID, existing_keys: set[str]
) -> list[dict]:
    suggestions: list[dict] = []
    records = session.scalars(
        select(VisitRecord)
        .where(
            VisitRecord.organization_id == organization_id,
            VisitRecord.visit_id == visit_id,
            VisitRecord.applied.is_(True),
        )
        .order_by(VisitRecord.created_at.desc(), VisitRecord.id.desc())
        .limit(30)
    )
    for record in records:
        for index, raw in enumerate(record.follow_up_tasks or []):
            task = _normalise_task(raw)
            if task is None:
                continue
            key = _source_key("RECORD", record.id, index)
            if key in existing_keys:
                continue
            suggestions.append(
                {
                    "source_type": "RECORD",
                    "source_record_id": record.id,
                    "source_agent_run_id": None,
                    "source_index": index,
                    "title": task["title"],
                    "due_date": task["due_date"],
                    "source_created_at": record.created_at,
                }
            )

    runs = session.scalars(
        select(AgentRun)
        .where(
            AgentRun.organization_id == organization_id,
            AgentRun.agent_type == "MEETING_MINUTES_DRAFT",
            AgentRun.status == "SUCCEEDED",
            AgentRun.input["visit_id"].as_string() == str(visit_id),
        )
        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        .limit(30)
    )
    for run in runs:
        run_input = run.input if isinstance(run.input, dict) else {}
        if str(run_input.get("visit_id") or "") != str(visit_id):
            continue
        output = run.output if isinstance(run.output, dict) else {}
        tasks = output.get("follow_up_tasks")
        if not isinstance(tasks, list):
            continue
        for index, raw in enumerate(tasks):
            task = _normalise_task(raw)
            if task is None:
                continue
            key = _source_key("MINUTES_DRAFT", run.id, index)
            if key in existing_keys:
                continue
            suggestions.append(
                {
                    "source_type": "MINUTES_DRAFT",
                    "source_record_id": None,
                    "source_agent_run_id": run.id,
                    "source_index": index,
                    "title": task["title"],
                    "due_date": task["due_date"],
                    "source_created_at": run.created_at,
                }
            )
    suggestions.sort(key=lambda item: item["source_created_at"], reverse=True)
    return suggestions


def _event_reads(session: Session, organization_id: UUID, follow_up_id: UUID) -> list[dict]:
    rows = session.scalars(
        select(VisitFollowUpEvent)
        .where(
            VisitFollowUpEvent.organization_id == organization_id,
            VisitFollowUpEvent.follow_up_id == follow_up_id,
        )
        .order_by(VisitFollowUpEvent.created_at.asc(), VisitFollowUpEvent.id.asc())
    )
    return [
        {
            "id": event.id,
            "event_type": event.event_type,
            "from_status": event.from_status,
            "to_status": event.to_status,
            "actor_user_id": event.actor_user_id,
            "note": event.note,
            "created_at": event.created_at,
        }
        for event in rows
    ]


def read_follow_up(
    session: Session, organization_id: UUID | str, row: VisitFollowUp
) -> dict:
    org = UUID(str(organization_id))
    return {
        "id": row.id,
        "visit_id": row.visit_id,
        "trip_id": row.trip_id,
        "source_type": row.source_type,
        "source_record_id": row.source_record_id,
        "source_agent_run_id": row.source_agent_run_id,
        "source_index": row.source_index,
        "source_title": row.source_title,
        "source_due_date": row.source_due_date,
        "title": row.title,
        "due_date": row.due_date,
        "status": row.status,
        "created_by_user_id": row.created_by_user_id,
        "confirmed_by_user_id": row.confirmed_by_user_id,
        "confirmed_at": row.confirmed_at,
        "completed_by_user_id": row.completed_by_user_id,
        "completed_at": row.completed_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "events": _event_reads(session, org, row.id),
    }


def list_follow_ups(session: Session, organization_id: UUID | str, visit_id: UUID | str) -> dict:
    org = UUID(str(organization_id))
    visit_uuid = UUID(str(visit_id))
    visit = visit_service.require_visit(session, org, visit_uuid)
    rows = list(
        session.scalars(
            select(VisitFollowUp)
            .where(
                VisitFollowUp.organization_id == org,
                VisitFollowUp.visit_id == visit_uuid,
            )
            .order_by(VisitFollowUp.created_at.desc(), VisitFollowUp.id.desc())
        )
    )
    existing_keys = {row.source_key for row in rows}
    return {
        "visit_id": visit.id,
        "trip_id": visit.trip_id,
        "items": [read_follow_up(session, org, row) for row in rows],
        "suggestions": _candidate_suggestions(
            session, org, visit_uuid, existing_keys
        ),
    }


def _task_from_record(
    session: Session, organization_id: UUID, visit_id: UUID, record_id: UUID, index: int
) -> dict[str, str | None]:
    record = session.scalars(
        select(VisitRecord).where(
            VisitRecord.organization_id == organization_id,
            VisitRecord.visit_id == visit_id,
            VisitRecord.id == record_id,
            VisitRecord.applied.is_(True),
        )
    ).one_or_none()
    if record is None:
        raise NotFound("现场记录不存在或不是当前生效版本")
    tasks = record.follow_up_tasks or []
    if index >= len(tasks):
        raise Unprocessable("跟进项索引超出现场记录范围", details={"source_index": index})
    task = _normalise_task(tasks[index])
    if task is None:
        raise Unprocessable("来源跟进项缺少有效标题")
    return task


def _task_from_minutes(
    session: Session, organization_id: UUID, visit_id: UUID, run_id: UUID, index: int
) -> dict[str, str | None]:
    run = session.scalars(
        select(AgentRun).where(
            AgentRun.organization_id == organization_id,
            AgentRun.id == run_id,
            AgentRun.agent_type == "MEETING_MINUTES_DRAFT",
            AgentRun.status == "SUCCEEDED",
        )
    ).one_or_none()
    if run is None:
        raise NotFound("成功的会议纪要 AgentRun 不存在")
    run_input = run.input if isinstance(run.input, dict) else {}
    if str(run_input.get("visit_id") or "") != str(visit_id):
        raise NotFound("会议纪要草稿不属于当前拜访")
    output = run.output if isinstance(run.output, dict) else {}
    tasks = output.get("follow_up_tasks")
    if not isinstance(tasks, list) or index >= len(tasks):
        raise Unprocessable("跟进项索引超出会议纪要范围", details={"source_index": index})
    task = _normalise_task(tasks[index])
    if task is None:
        raise Unprocessable("来源跟进项缺少有效标题")
    return task


def create_follow_up(
    session: Session,
    organization_id: UUID | str,
    visit_id: UUID | str,
    payload,
    *,
    actor_user_id: UUID | str,
) -> VisitFollowUp:
    org = UUID(str(organization_id))
    actor = UUID(str(actor_user_id))
    visit_uuid = UUID(str(visit_id))
    visit = visit_service.require_visit(session, org, visit_uuid)

    source_type = payload.source_type
    decision = payload.decision
    source_record_id = payload.source_record_id
    source_agent_run_id = payload.source_agent_run_id
    source_index = payload.source_index
    source_title: str | None = None
    source_due_date: str | None = None

    if source_type == "MANUAL":
        if decision != "CREATE" or not payload.title or not payload.title.strip():
            raise Unprocessable("手工跟进必须使用 CREATE 决策并提供标题")
        if source_record_id or source_agent_run_id or source_index is not None:
            raise Unprocessable("手工跟进不能声明 AI 来源")
        source_key = f"MANUAL:{uuid4().hex}"
        source_title = payload.title.strip()
    else:
        if decision not in ("ACCEPT", "DISMISS") or source_index is None:
            raise Unprocessable("来源跟进项只能显式 ACCEPT 或 DISMISS")
        if source_type == "RECORD":
            if source_record_id is None or source_agent_run_id is not None:
                raise Unprocessable("现场记录来源必须提供 source_record_id")
            task = _task_from_record(session, org, visit_uuid, source_record_id, source_index)
            source_key = _source_key(source_type, source_record_id, source_index)
        else:
            if source_agent_run_id is None or source_record_id is not None:
                raise Unprocessable("会议纪要来源必须提供 source_agent_run_id")
            task = _task_from_minutes(session, org, visit_uuid, source_agent_run_id, source_index)
            source_key = _source_key(source_type, source_agent_run_id, source_index)
        source_title = task["title"]
        source_due_date = task["due_date"]
        duplicate = session.scalars(
            select(VisitFollowUp).where(
                VisitFollowUp.organization_id == org,
                VisitFollowUp.visit_id == visit_uuid,
                VisitFollowUp.source_key == source_key,
            )
        ).one_or_none()
        if duplicate is not None:
            raise Conflict(
                "该来源跟进项已由用户处理",
                details={"follow_up_id": str(duplicate.id), "status": duplicate.status},
            )

    title = (payload.title or source_title or "").strip()
    if not title:
        raise Unprocessable("跟进标题不能为空")
    now = datetime.now(UTC)
    status = "DISMISSED" if decision == "DISMISS" else "OPEN"
    row = VisitFollowUp(
        organization_id=org,
        trip_id=visit.trip_id,
        visit_id=visit_uuid,
        source_type=source_type,
        source_key=source_key,
        source_record_id=source_record_id,
        source_agent_run_id=source_agent_run_id,
        source_index=source_index,
        source_title=source_title,
        source_due_date=source_due_date,
        title=title[:500],
        due_date=payload.due_date if payload.due_date is not None else source_due_date,
        status=status,
        created_by_user_id=actor,
        confirmed_by_user_id=actor if source_type != "MANUAL" else None,
        confirmed_at=now if source_type != "MANUAL" else None,
    )
    session.add(row)
    session.flush()
    event_type = (
        "DISMISSED" if status == "DISMISSED" else
        "CREATED_MANUAL" if source_type == "MANUAL" else "ACCEPTED"
    )
    _append_event(
        session, org, row, event_type=event_type, from_status=None,
        actor_user_id=actor, note=payload.note,
    )
    session.flush()
    return row


def update_follow_up(
    session: Session,
    organization_id: UUID | str,
    follow_up_id: UUID | str,
    payload,
    *,
    actor_user_id: UUID | str,
) -> VisitFollowUp:
    org = UUID(str(organization_id))
    actor = UUID(str(actor_user_id))
    row = session.scalars(
        select(VisitFollowUp).where(
            VisitFollowUp.organization_id == org,
            VisitFollowUp.id == UUID(str(follow_up_id)),
        )
        .with_for_update()
    ).one_or_none()
    if row is None:
        raise NotFound("拜访跟进项不存在")

    changes = payload.model_dump(exclude_unset=True)
    note = changes.pop("note", None)
    desired_status = changes.pop("status", None)
    if not changes and desired_status is None:
        raise Unprocessable("至少需要更新状态、标题或截止日期")

    old_status = row.status
    if desired_status is not None and desired_status != old_status:
        allowed = {
            "OPEN": {"IN_PROGRESS", "COMPLETED", "DISMISSED"},
            "IN_PROGRESS": {"OPEN", "COMPLETED", "DISMISSED"},
            "COMPLETED": {"OPEN"},
            "DISMISSED": {"OPEN"},
        }
        if desired_status not in allowed.get(old_status, set()):
            raise Conflict(
                "不允许该跟进状态转换",
                details={"from_status": old_status, "to_status": desired_status},
            )
        if old_status in ("COMPLETED", "DISMISSED") and desired_status == "OPEN":
            if not note or not note.strip():
                raise Unprocessable("重新打开已完成或已驳回事项时必须填写原因")
        row.status = desired_status
        if desired_status == "COMPLETED":
            row.completed_by_user_id = actor
            row.completed_at = datetime.now(UTC)
        elif desired_status == "OPEN":
            row.completed_by_user_id = None
            row.completed_at = None

    if "title" in changes:
        title = changes["title"]
        if title is None or not str(title).strip():
            raise Unprocessable("跟进标题不能为空")
        row.title = str(title).strip()[:500]
    if "due_date" in changes:
        row.due_date = changes["due_date"]

    session.flush()
    event_type = None
    if desired_status is not None and desired_status != old_status:
        event_type = {
            "IN_PROGRESS": "STARTED",
            "COMPLETED": "COMPLETED",
            "OPEN": "REOPENED",
            "DISMISSED": "DISMISSED",
        }[desired_status]
    elif changes:
        event_type = "UPDATED"
    if event_type:
        _append_event(
            session, org, row, event_type=event_type, from_status=old_status,
            actor_user_id=actor, note=note,
        )
    session.flush()
    return row


__all__ = ["create_follow_up", "list_follow_ups", "read_follow_up", "update_follow_up"]
