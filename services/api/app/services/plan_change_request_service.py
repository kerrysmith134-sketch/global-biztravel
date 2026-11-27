"""统一计划变更入口（PRD §6 / AT-010）。

所有页面（地图、日历、AI）修改计划都必须经过这里：校验基础版本 → 持久化变更请求 →
修改规划输入 → 入队新的求解任务。它**不直接改激活计划**；新计划仍要经过生成、独立验证、
差异比较和再次激活。

支持的变更类型先覆盖 MVP-1 的高频操作：

- ``CHANGE_VISIT_TIME``：改拜访时间窗；
- ``CHANGE_VISIT_DURATION``：改停留时长；
- ``CANCEL_VISIT``：取消拜访；
- ``ADD_VISIT`` / ``REMOVE_VISIT``：增删拜访。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import NotFound, PlanVersionConflict, Unprocessable
from app.api.schemas import VisitCreate, VisitUpdate
from app.models.trip import Trip
from app.models.workflow import WorkflowInstance
from app.services import job_service, plan_service, trip_service, visit_service

CHANGE_VISIT_TIME = "CHANGE_VISIT_TIME"
CHANGE_VISIT_DURATION = "CHANGE_VISIT_DURATION"
CANCEL_VISIT = "CANCEL_VISIT"
ADD_VISIT = "ADD_VISIT"
REMOVE_VISIT = "REMOVE_VISIT"

SUPPORTED_OPERATIONS = (
    CHANGE_VISIT_TIME,
    CHANGE_VISIT_DURATION,
    CANCEL_VISIT,
    ADD_VISIT,
    REMOVE_VISIT,
)


@dataclass
class PlanChangeOutcome:
    workflow: WorkflowInstance
    job_id: UUID
    trip_version: int


def _require(payload: dict[str, Any], key: str) -> Any:
    value = payload.get(key)
    if value is None or value == "":
        raise Unprocessable("计划变更缺少字段", details={"missing": key})
    return value


def _apply_change(
    session: Session,
    organization_id: UUID | str,
    trip_id: UUID,
    operation_type: str,
    payload: dict[str, Any],
) -> Trip:
    trip = trip_service.require_trip(session, organization_id, trip_id)
    if operation_type == CHANGE_VISIT_TIME:
        visit_id = UUID(str(_require(payload, "visit_id")))
        start = payload.get("new_window_start")
        end = payload.get("new_window_end")
        if start is None and end is None:
            raise Unprocessable("计划变更缺少 new_window_start/new_window_end")
        update = VisitUpdate(
            time_window_start=datetime.fromisoformat(start) if start else None,
            time_window_end=datetime.fromisoformat(end) if end else None,
        )
        visit_service.update_visit(session, organization_id, visit_id, update)
    elif operation_type == CHANGE_VISIT_DURATION:
        visit_id = UUID(str(_require(payload, "visit_id")))
        minutes = int(_require(payload, "service_minutes"))
        visit_service.update_visit(
            session,
            organization_id,
            visit_id,
            VisitUpdate(service_minutes=minutes),
        )
    elif operation_type == CANCEL_VISIT:
        visit_id = UUID(str(_require(payload, "visit_id")))
        visit_service.update_visit(
            session,
            organization_id,
            visit_id,
            VisitUpdate(status="CANCELLED"),
        )
    elif operation_type == ADD_VISIT:
        visit_service.create_visit(
            session,
            organization_id,
            trip_id,
            VisitCreate.model_validate(payload),
        )
    elif operation_type == REMOVE_VISIT:
        visit_id = UUID(str(_require(payload, "visit_id")))
        visit_service.delete_visit(session, organization_id, visit_id)
    else:
        raise Unprocessable(
            "不支持的计划变更类型",
            details={"operation_type": operation_type, "supported": list(SUPPORTED_OPERATIONS)},
        )
    session.flush()
    session.refresh(trip)
    return trip


def create_change_request(
    session: Session,
    organization_id: UUID | str,
    trip_id: UUID | str,
    *,
    base_plan_version_id: UUID | str,
    base_trip_version: int,
    source: str,
    operation_type: str,
    payload: dict[str, Any],
    reason: str | None,
    actor_user_id: UUID | str | None,
) -> PlanChangeOutcome:
    org_id = UUID(str(organization_id))
    resolved_trip_id = UUID(str(trip_id))
    # Serialize plan changes for one Trip. Without this lock, two requests with different
    # idempotency keys can both observe the same base version and mutate the Visit twice.
    trip = session.scalars(
        select(Trip)
        .where(Trip.organization_id == org_id, Trip.id == resolved_trip_id)
        .with_for_update()
    ).one_or_none()
    if trip is None:
        raise NotFound("出行不存在", details={"trip_id": str(trip_id)})

    base_plan = plan_service.require_plan_version(session, org_id, base_plan_version_id)
    if str(base_plan.trip_id) != str(trip.id):
        raise NotFound("计划版本不属于该出行")
    if trip.active_plan_version_id != base_plan.id:
        raise PlanVersionConflict(
            "只能在当前生效计划的基础上提出变更",
            details={
                "base_plan_version_id": str(base_plan.id),
                "active_plan_version_id": (
                    str(trip.active_plan_version_id) if trip.active_plan_version_id else None
                ),
            },
        )
    if trip.version != base_trip_version:
        raise PlanVersionConflict(
            "出行已被修改，请重新获取后再提出变更",
            details={"base_trip_version": base_trip_version, "current_trip_version": trip.version},
        )

    changed_trip = _apply_change(
        session, organization_id, trip.id, operation_type, payload
    )
    job = job_service.enqueue(
        session,
        organization_id,
        job_type=job_service.JOB_TYPE_PLAN_TRIP,
        payload={"trip_id": str(trip.id), "trip_version": changed_trip.version},
        dedupe_key=f"plan_trip:{trip.id}:{changed_trip.version}",
        created_by_user_id=actor_user_id,
    )
    workflow = WorkflowInstance(
        organization_id=str(organization_id),
        trip_id=trip.id,
        workflow_type="PLAN_CHANGE",
        status="ACCEPTED",
        payload={
            "source": source,
            "operation_type": operation_type,
            "operation_payload": payload,
            "reason": reason,
            "base_plan_version_id": str(base_plan.id),
            "base_trip_version": base_trip_version,
        },
        job_id=job.id,
        created_by_user_id=None if actor_user_id is None else str(actor_user_id),
    )
    session.add(workflow)
    session.flush()
    return PlanChangeOutcome(
        workflow=workflow, job_id=job.id, trip_version=changed_trip.version
    )


__all__ = [
    "ADD_VISIT",
    "CANCEL_VISIT",
    "CHANGE_VISIT_DURATION",
    "CHANGE_VISIT_TIME",
    "PlanChangeOutcome",
    "REMOVE_VISIT",
    "SUPPORTED_OPERATIONS",
    "create_change_request",
]
