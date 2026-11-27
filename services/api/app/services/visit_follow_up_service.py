"""Human confirmation and lifecycle management for visit follow-ups."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import Conflict, NotFound, Unprocessable
from app.models.proactive import ProactiveSuggestion
from app.models.records import VisitFollowUp, VisitFollowUpEvent, VisitRecord
from app.models.workflow import AgentRun, WorkflowInstance
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


_RECOVERY_ACTION_TITLES = {
    "REQUEST_CUSTOMER_RESCHEDULE_WINDOW": "确认客户改期窗口",
    "VERIFY_REPLACEMENT_FLIGHT_AND_RECONFIRM": "核实替代航班并重新确认客户拜访",
    "REVALIDATE_TRAVEL_CHAIN": "补齐交通链证据并重新评估",
    "CONFIRM_CUSTOMER_APPOINTMENT_WINDOW": "确认客户预约时间窗口",
    "RECONFIRM_ARRIVAL_AND_KEEP_APPOINTMENT_PROVISIONAL": "复核到达时间并确认预约",
}


def _load_recovery_run(
    session: Session,
    organization_id: UUID,
    trip_id: UUID,
    run_id: UUID,
    *,
    lock_run: bool = False,
) -> tuple[AgentRun, list]:
    statement = select(AgentRun).where(
        AgentRun.organization_id == organization_id,
        AgentRun.id == run_id,
        AgentRun.agent_type == "APPOINTMENT_RECOVERY",
        AgentRun.status == "SUCCEEDED",
    )
    if lock_run:
        statement = statement.with_for_update()
    run = session.scalars(statement).one_or_none()
    if run is None:
        raise NotFound("成功的预约恢复 AgentRun 不存在")
    run_input = run.input if isinstance(run.input, dict) else {}
    if str(run_input.get("trip_id") or "") != str(trip_id) or run.workflow_id is None:
        raise NotFound("预约恢复 AgentRun 不属于当前出行")

    workflow = session.scalars(
        select(WorkflowInstance).where(
            WorkflowInstance.organization_id == organization_id,
            WorkflowInstance.id == run.workflow_id,
            WorkflowInstance.trip_id == trip_id,
            WorkflowInstance.workflow_type == "APPOINTMENT_RECOVERY",
            WorkflowInstance.status == "COMPLETED",
        )
    ).one_or_none()
    if workflow is None:
        raise NotFound("预约恢复 Workflow 不属于当前出行或尚未完成")

    try:
        suggestion_id = UUID(str(run_input.get("suggestion_id")))
    except (TypeError, ValueError):
        raise NotFound("预约恢复运行缺少有效的来源建议") from None
    suggestion = session.scalars(
        select(ProactiveSuggestion).where(
            ProactiveSuggestion.organization_id == organization_id,
            ProactiveSuggestion.trip_id == trip_id,
            ProactiveSuggestion.id == suggestion_id,
        )
    ).one_or_none()
    if suggestion is None:
        raise NotFound("预约恢复运行的来源建议不存在")

    params = (suggestion.proposed_action or {}).get("params") or {}
    workflow_payload = workflow.payload if isinstance(workflow.payload, dict) else {}
    if (
        params.get("recovery_agent_run_id") != str(run.id)
        or params.get("recovery_workflow_id") != str(workflow.id)
        or workflow_payload.get("agent_run_id") != str(run.id)
        or workflow_payload.get("source_suggestion_id") != str(suggestion.id)
    ):
        raise NotFound("预约恢复 AgentRun 的建议、工作流血缘不一致")

    output = run.output if isinstance(run.output, dict) else {}
    actions = output.get("actions")
    if not isinstance(actions, list):
        raise NotFound("预约恢复 AgentRun 缺少结构化动作清单")
    return run, actions


def load_recovery_action(
    session: Session,
    organization_id: UUID | str,
    follow_up: VisitFollowUp,
    *,
    lock_run: bool = False,
) -> tuple[AgentRun, dict]:
    """Resolve the immutable source action for one accepted recovery follow-up."""
    org = UUID(str(organization_id))
    if (
        follow_up.organization_id != org
        or follow_up.source_type != "APPOINTMENT_RECOVERY"
        or follow_up.source_agent_run_id is None
        or follow_up.source_index is None
    ):
        raise Unprocessable("跟进项不是有效的预约恢复来源")
    run, actions = _load_recovery_run(
        session, org, follow_up.trip_id, follow_up.source_agent_run_id, lock_run=lock_run
    )
    return run, _recovery_action_for_visit(actions, follow_up.visit_id, follow_up.source_index)


def _recovery_action_for_visit(actions: list, visit_id: UUID, index: int) -> dict:
    if index < 0 or index >= len(actions):
        raise Unprocessable("恢复动作索引超出 AgentRun 范围", details={"source_index": index})
    action = actions[index]
    if not isinstance(action, dict):
        raise NotFound("预约恢复动作不是有效对象")
    scope = action.get("scope") or ("VISIT" if action.get("visit_id") else "TRIP")
    if scope != "VISIT" or str(action.get("visit_id") or "") != str(visit_id):
        raise NotFound("预约恢复动作不属于当前拜访")
    action_code = action.get("action_code")
    if action_code not in _RECOVERY_ACTION_TITLES:
        raise Unprocessable("预约恢复动作类型不支持转为拜访跟进项")
    return action


def _recovery_action_context(action: dict) -> dict:
    return {
        "integrity_status": "VERIFIED",
        "action_code": action.get("action_code"),
        "priority": action.get("priority"),
        "decision_status": action.get("decision_status"),
        "appointment_status": action.get("appointment_status"),
        "rationale": action.get("rationale"),
        "required_inputs": [
            item for item in action.get("required_inputs", []) if isinstance(item, str)
        ] if isinstance(action.get("required_inputs"), list) else [],
        "recommended_steps": [
            item for item in action.get("recommended_steps", []) if isinstance(item, str)
        ] if isinstance(action.get("recommended_steps"), list) else [],
    }


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
    session: Session, organization_id: UUID, visit_id: UUID, trip_id: UUID, existing_keys: set[str]
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
    recovery_runs = session.scalars(
        select(AgentRun)
        .where(
            AgentRun.organization_id == organization_id,
            AgentRun.agent_type == "APPOINTMENT_RECOVERY",
            AgentRun.status == "SUCCEEDED",
            AgentRun.input["trip_id"].as_string() == str(trip_id),
        )
        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        .limit(30)
    )
    for candidate_run in recovery_runs:
        try:
            run, actions = _load_recovery_run(
                session, organization_id, trip_id, candidate_run.id
            )
        except NotFound:
            continue
        if len(actions) > 100:
            continue
        for index, action in enumerate(actions):
            if not isinstance(action, dict):
                continue
            scope = action.get("scope") or ("VISIT" if action.get("visit_id") else "TRIP")
            if scope != "VISIT" or str(action.get("visit_id") or "") != str(visit_id):
                continue
            source_key = _source_key("APPOINTMENT_RECOVERY", run.id, index)
            if source_key in existing_keys:
                continue
            try:
                _recovery_action_for_visit(actions, visit_id, index)
            except (NotFound, Unprocessable):
                continue
            action_code = action["action_code"]
            suggestions.append(
                {
                    "source_type": "APPOINTMENT_RECOVERY",
                    "source_record_id": None,
                    "source_agent_run_id": run.id,
                    "source_index": index,
                    "title": _RECOVERY_ACTION_TITLES[action_code],
                    "due_date": None,
                    "source_created_at": run.created_at,
                    **_recovery_action_context(action),
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
    source_action_context = None
    if (
        row.source_type == "APPOINTMENT_RECOVERY"
        and row.source_agent_run_id is not None
        and row.source_index is not None
    ):
        try:
            _run, actions = _load_recovery_run(
                session, org, row.trip_id, row.source_agent_run_id
            )
            action = _recovery_action_for_visit(actions, row.visit_id, row.source_index)
            source_action_context = _recovery_action_context(action)
        except (NotFound, Unprocessable):
            source_action_context = {"integrity_status": "SOURCE_LINK_UNVERIFIED"}
    latest_reassessment = None
    if row.source_type == "APPOINTMENT_RECOVERY":
        candidates = session.scalars(
            select(AgentRun)
            .where(
                AgentRun.organization_id == org,
                AgentRun.agent_type == "APPOINTMENT_RECOVERY_REASSESSMENT",
                AgentRun.status == "SUCCEEDED",
                AgentRun.input["follow_up_id"].as_string() == str(row.id),
            )
            .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
            .limit(5)
        )
        for candidate in candidates:
            workflow = session.scalars(
                select(WorkflowInstance).where(
                    WorkflowInstance.organization_id == org,
                    WorkflowInstance.id == candidate.workflow_id,
                    WorkflowInstance.trip_id == row.trip_id,
                    WorkflowInstance.workflow_type == "APPOINTMENT_RECOVERY_REASSESSMENT",
                    WorkflowInstance.status == "COMPLETED",
                )
            ).one_or_none()
            run_input = candidate.input if isinstance(candidate.input, dict) else {}
            workflow_payload = workflow.payload if workflow is not None and isinstance(workflow.payload, dict) else {}
            if (
                workflow is None
                or run_input.get("trip_id") != str(row.trip_id)
                or workflow_payload.get("agent_run_id") != str(candidate.id)
                or workflow_payload.get("follow_up_id") != str(row.id)
            ):
                continue
            output_value: Any = candidate.output
            output: dict[str, Any] = output_value if isinstance(output_value, dict) else {}
            after_value: Any = output.get("after")
            after: dict[str, Any] = after_value if isinstance(after_value, dict) else {}
            latest_reassessment = {
                "workflow_id": str(workflow.id),
                "agent_run_id": str(candidate.id),
                "outcome": output.get("outcome"),
                "decision": output.get("decision"),
                "created_at": candidate.finished_at or candidate.created_at,
                "input_sha256": run_input.get("input_sha256"),
                "current_plan_version_id": output.get("current_plan_version_id"),
                "appointment_status": after.get("appointment_status"),
                "projected_arrival_at": after.get("projected_arrival_at"),
                "delay_minutes": after.get("delay_minutes"),
                "customer_window_verification_status": output.get(
                    "customer_window_verification_status", "NOT_PROVIDED"
                ),
                "evidence_ids": output.get("evidence_ids") or [],
                "plan_change_recommendation": output.get("plan_change_recommendation"),
            }
            break
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
        "source_action_context": source_action_context,
        "latest_reassessment": latest_reassessment,
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
            session, org, visit_uuid, visit.trip_id, existing_keys
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


def _task_from_recovery(
    session: Session,
    organization_id: UUID,
    visit_id: UUID,
    trip_id: UUID,
    run_id: UUID,
    index: int,
) -> dict[str, str | None]:
    _run, actions = _load_recovery_run(
        session, organization_id, trip_id, run_id, lock_run=True
    )
    action = _recovery_action_for_visit(actions, visit_id, index)
    return {
        "title": _RECOVERY_ACTION_TITLES[action["action_code"]],
        "due_date": None,
    }


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
        elif source_type == "MINUTES_DRAFT":
            if source_agent_run_id is None or source_record_id is not None:
                raise Unprocessable("会议纪要来源必须提供 source_agent_run_id")
            task = _task_from_minutes(
                session, org, visit_uuid, source_agent_run_id, source_index
            )
            source_key = _source_key(source_type, source_agent_run_id, source_index)
        elif source_type == "APPOINTMENT_RECOVERY":
            if source_agent_run_id is None or source_record_id is not None:
                raise Unprocessable("预约恢复来源必须提供 source_agent_run_id")
            task = _task_from_recovery(
                session, org, visit_uuid, visit.trip_id, source_agent_run_id, source_index
            )
            source_key = _source_key(source_type, source_agent_run_id, source_index)
        else:
            raise Unprocessable("不支持的跟进来源类型")
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
