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

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import NotFound, PlanVersionConflict, Unprocessable
from app.api.schemas import VisitCreate, VisitUpdate
from app.models.flight_operations import FlightOperationalUpdate
from app.models.integrations import ExternalSignal
from app.models.records import VisitFollowUp
from app.models.trip import Trip
from app.models.workflow import AgentRun, Evidence, WorkflowInstance
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





def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _verified_follow_up_evidence(
    session: Session,
    organization_id: UUID,
    follow_up_id: UUID,
    *,
    kind: str,
    ref: str,
) -> Evidence:
    evidence = session.scalars(
        select(Evidence).where(
            Evidence.organization_id == organization_id,
            Evidence.subject_type == "visit_follow_up",
            Evidence.subject_id == follow_up_id,
            Evidence.kind == kind,
            Evidence.ref == ref,
        )
    ).one_or_none()
    if evidence is None or not isinstance(evidence.payload, dict):
        raise Unprocessable(
            "预约恢复的证据记录缺失或格式无效",
            details={"evidence_kind": kind, "evidence_ref": ref},
        )
    if evidence.content_hash != _canonical_sha256(evidence.payload):
        raise PlanVersionConflict(
            "预约恢复证据内容摘要不一致",
            details={"evidence_kind": kind, "evidence_ref": ref},
        )
    return evidence


def _aware_utc(value: Any, *, field: str, timezone: str | None = None) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise Unprocessable("预约恢复计划变更缺少有效时间", details={"field": field})
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise Unprocessable("预约恢复计划变更时间格式无效", details={"field": field}) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise Unprocessable("预约恢复计划变更时间必须包含时区偏移", details={"field": field})
    if timezone is not None:
        try:
            customer_zone = ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise Unprocessable("预约重评引用了无效的客户 IANA 时区") from None
        if parsed.astimezone(customer_zone).utcoffset() != parsed.utcoffset():
            raise PlanVersionConflict("提交时间的 UTC offset 与客户 IANA 时区不一致")
    return parsed.astimezone(UTC)


def _validate_appointment_recovery_change(
    session: Session,
    organization_id: UUID,
    trip: Trip,
    base_plan_id: UUID,
    base_trip_version: int,
    payload: dict[str, Any],
) -> UUID:
    """Bind the write to the latest successful reassessment proposal for this FollowUp."""
    try:
        follow_up_id = UUID(str(_require(payload, "recovery_follow_up_id")))
        visit_id = UUID(str(_require(payload, "visit_id")))
    except ValueError:
        raise Unprocessable("预约恢复计划变更必须引用有效的 FollowUp 和 Visit") from None

    follow_up = session.scalars(
        select(VisitFollowUp).where(
            VisitFollowUp.organization_id == organization_id,
            VisitFollowUp.id == follow_up_id,
            VisitFollowUp.trip_id == trip.id,
            VisitFollowUp.visit_id == visit_id,
            VisitFollowUp.source_type == "APPOINTMENT_RECOVERY",
        )
    ).one_or_none()
    if follow_up is None:
        raise NotFound("预约恢复 FollowUp 与当前行程/拜访不匹配")
    if follow_up.status not in {"OPEN", "IN_PROGRESS"}:
        raise PlanVersionConflict("预约恢复 FollowUp 已关闭，不能再提交计划变更")

    run = session.scalars(
        select(AgentRun)
        .where(
            AgentRun.organization_id == organization_id,
            AgentRun.agent_type == "APPOINTMENT_RECOVERY_REASSESSMENT",
            AgentRun.status == "SUCCEEDED",
            AgentRun.input["follow_up_id"].as_string() == str(follow_up.id),
        )
        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        .limit(1)
    ).one_or_none()
    if run is None or run.workflow_id is None:
        raise Unprocessable("该 FollowUp 尚无成功的预约恢复重评记录")

    run_input = run.input if isinstance(run.input, dict) else {}
    run_output = run.output if isinstance(run.output, dict) else {}
    if (
        run_input.get("trip_id") != str(trip.id)
        or run_input.get("visit_id") != str(visit_id)
        or run_output.get("decision") != "PLAN_CHANGE_RECOMMENDATION_READY"
        or run_output.get("data_gaps") not in ([], None)
        or run_output.get("customer_window_verification_status") != "PARTIALLY_VERIFIED"
    ):
        raise PlanVersionConflict("最新预约重评未提供可提交的计划变更建议")

    workflow = session.scalars(
        select(WorkflowInstance).where(
            WorkflowInstance.organization_id == organization_id,
            WorkflowInstance.id == run.workflow_id,
            WorkflowInstance.trip_id == trip.id,
            WorkflowInstance.workflow_type == "APPOINTMENT_RECOVERY_REASSESSMENT",
            WorkflowInstance.status == "COMPLETED",
        )
    ).one_or_none()
    workflow_payload = (
        workflow.payload
        if workflow is not None and isinstance(workflow.payload, dict)
        else {}
    )
    if (
        workflow is None
        or workflow_payload.get("agent_run_id") != str(run.id)
        or workflow_payload.get("follow_up_id") != str(follow_up.id)
    ):
        raise Unprocessable("预约恢复重评的 Workflow/AgentRun 血缘校验失败")

    # Reassessment is a point-in-time decision. Do not accept it after its source
    # provider signal expires or a newer operational update supersedes it.
    input_snapshot = run_output.get("input_snapshot")
    if not isinstance(input_snapshot, dict):
        raise Unprocessable("预约重评缺少可验证的输入快照")
    snapshot_sha256 = _canonical_sha256(input_snapshot)
    if (
        run_input.get("input_sha256") != snapshot_sha256
        or run_output.get("input_sha256") != snapshot_sha256
    ):
        raise PlanVersionConflict("预约重评 AgentRun 输入快照 SHA-256 校验失败")
    customer_window_attestation = input_snapshot.get("customer_window_attestation")
    if not isinstance(customer_window_attestation, dict):
        raise Unprocessable("预约重评缺少客户窗口及其时区陈述")
    attested_timezone = customer_window_attestation.get("timezone")
    if not isinstance(attested_timezone, str) or not attested_timezone.strip():
        raise Unprocessable("预约重评缺少客户预约窗口 IANA 时区")
    attestation_evidence = _verified_follow_up_evidence(
        session,
        organization_id,
        follow_up.id,
        kind="APPOINTMENT_WINDOW_ATTESTATION",
        ref=f"customer-window-attestation:{run.id}",
    )
    attestation_quality = (
        attestation_evidence.quality
        if isinstance(attestation_evidence.quality, dict)
        else {}
    )
    if (
        attestation_evidence.verification_status != "PARTIALLY_VERIFIED"
        or attestation_quality.get("externally_verified") is not False
        or attestation_evidence.payload.get("customer_window") != customer_window_attestation
    ):
        raise PlanVersionConflict("客户预约窗口陈述证据与 AgentRun 输入快照不一致")
    reassessment_evidence = _verified_follow_up_evidence(
        session,
        organization_id,
        follow_up.id,
        kind="APPOINTMENT_RECOVERY_REASSESSMENT",
        ref=f"appointment-reassessment:{run.id}",
    )
    reassessment_payload = reassessment_evidence.payload
    if (
        reassessment_payload.get("agent_run_id") != str(run.id)
        or reassessment_payload.get("workflow_id") != str(workflow.id)
        or reassessment_payload.get("input_sha256") != snapshot_sha256
        or reassessment_payload.get("decision") != run_output.get("decision")
        or reassessment_payload.get("plan_change_recommendation")
        != run_output.get("plan_change_recommendation")
    ):
        raise PlanVersionConflict("预约重评结果 Evidence 与 AgentRun 输出不一致")
    try:
        expected_update_id = UUID(str(input_snapshot.get("latest_operational_update_id")))
        expected_signal_id = UUID(str(input_snapshot.get("latest_signal_id")))
        flight_segment_id = UUID(str(input_snapshot.get("flight_segment_id")))
    except (TypeError, ValueError):
        raise Unprocessable("预约重评缺少有效的航班证据引用") from None

    latest_update = session.scalars(
        select(FlightOperationalUpdate)
        .where(
            FlightOperationalUpdate.organization_id == organization_id,
            FlightOperationalUpdate.trip_id == trip.id,
            FlightOperationalUpdate.flight_segment_id == flight_segment_id,
        )
        .order_by(
            FlightOperationalUpdate.observed_at.desc(),
            FlightOperationalUpdate.id.desc(),
        )
        .limit(1)
    ).one_or_none()
    if (
        latest_update is None
        or latest_update.id != expected_update_id
        or latest_update.content_sha256 != input_snapshot.get("latest_operational_update_sha256")
    ):
        raise PlanVersionConflict("重评所依据的航班更新已被更新事件取代或内容摘要不一致")

    signal = session.scalars(
        select(ExternalSignal).where(
            ExternalSignal.organization_id == organization_id,
            ExternalSignal.id == expected_signal_id,
            ExternalSignal.provider == latest_update.provider,
            ExternalSignal.signal_type == "FLIGHT_STATUS",
            ExternalSignal.subject_type == "TRIP",
            ExternalSignal.subject_ref == str(trip.id),
            ExternalSignal.dedupe_key == f"flight_ops:{latest_update.provider_event_id}",
        )
    ).one_or_none()
    now = datetime.now(UTC)
    if (
        signal is None
        or signal.valid_until is None
        or signal.valid_until <= now
        or signal.observed_at > now + timedelta(minutes=5)
    ):
        raise PlanVersionConflict("重评所依据的航班信号已过期或无效，请重新评估")

    recommendation = run_output.get("plan_change_recommendation")
    if (
        not isinstance(recommendation, dict)
        or recommendation.get("requires_human_submission") is not True
    ):
        raise PlanVersionConflict("最新预约重评没有可供人工提交的计划变更建议")
    if recommendation.get("timezone") != attested_timezone:
        raise PlanVersionConflict("预约计划建议的客户时区与原始预约窗口陈述不一致")
    if (
        recommendation.get("operation_type") != CHANGE_VISIT_TIME
        or str(recommendation.get("visit_id") or "") != str(visit_id)
        or str(recommendation.get("base_plan_version_id") or "") != str(base_plan_id)
        or recommendation.get("base_trip_version") != base_trip_version
        or trip.active_plan_version_id != base_plan_id
        or trip.version != base_trip_version
    ):
        raise PlanVersionConflict("预约恢复建议所依据的计划或行程版本已过期")

    requested_start = _aware_utc(
        payload.get("new_window_start"), field="new_window_start", timezone=attested_timezone
    )
    requested_end = _aware_utc(
        payload.get("new_window_end"), field="new_window_end", timezone=attested_timezone
    )
    proposed_start = _aware_utc(
        recommendation.get("new_window_start"),
        field="proposal.new_window_start",
        timezone=attested_timezone,
    )
    proposed_end = _aware_utc(
        recommendation.get("new_window_end"),
        field="proposal.new_window_end",
        timezone=attested_timezone,
    )
    if (
        requested_end <= requested_start
        or requested_start != proposed_start
        or requested_end != proposed_end
    ):
        raise PlanVersionConflict("提交的预约窗口与最新重评建议不一致")

    return follow_up.id


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
    if trip.active_plan_version_id != base_plan.id or base_plan.status != "ACTIVE":
        raise PlanVersionConflict(
            "只能在当前生效计划的基础上提出变更",
            details={
                "base_plan_version_id": str(base_plan.id),
                "active_plan_version_id": (
                    str(trip.active_plan_version_id) if trip.active_plan_version_id else None
                ),
                "base_plan_status": base_plan.status,
            },
        )
    if base_plan.trip_version != trip.version:
        raise PlanVersionConflict(
            "当前生效计划基于旧的行程输入，必须先重新生成并激活计划",
            details={
                "base_plan_trip_version": base_plan.trip_version,
                "current_trip_version": trip.version,
            },
        )
    if trip.version != base_trip_version:
        raise PlanVersionConflict(
            "出行已被修改，请重新获取后再提出变更",
            details={"base_trip_version": base_trip_version, "current_trip_version": trip.version},
        )

    recovery_follow_up_id = None
    if source == "APPOINTMENT_RECOVERY":
        if operation_type != CHANGE_VISIT_TIME:
            raise Unprocessable("预约恢复来源当前只允许提交拜访时间窗变更")
        recovery_follow_up_id = _validate_appointment_recovery_change(
            session,
            org_id,
            trip,
            base_plan.id,
            base_trip_version,
            payload,
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
            "result_trip_version": changed_trip.version,
            "recovery_follow_up_id": (
                str(recovery_follow_up_id) if recovery_follow_up_id is not None else None
            ),
        },
        job_id=job.id,
        created_by_user_id=None if actor_user_id is None else str(actor_user_id),
    )
    session.add(workflow)
    session.flush()
    return PlanChangeOutcome(
        workflow=workflow, job_id=job.id, trip_version=changed_trip.version
    )


def retry_plan_change(
    session: Session,
    organization_id: UUID | str,
    workflow_id: UUID | str,
    *,
    actor_user_id: UUID | str,
) -> PlanChangeOutcome:
    """Re-enqueue the existing Plan Change job without reapplying its Visit mutation."""
    org_id = UUID(str(organization_id))
    resolved_workflow_id = UUID(str(workflow_id))
    reference = session.scalars(
        select(WorkflowInstance).where(
            WorkflowInstance.organization_id == org_id,
            WorkflowInstance.id == resolved_workflow_id,
            WorkflowInstance.workflow_type == "PLAN_CHANGE",
        )
    ).one_or_none()
    if reference is None or reference.trip_id is None:
        raise NotFound("计划变更工作流不存在")

    # Keep the same Trip → Workflow lock order used by Plan Change creation.
    trip = session.scalars(
        select(Trip)
        .where(Trip.organization_id == org_id, Trip.id == reference.trip_id)
        .with_for_update()
    ).one_or_none()
    if trip is None:
        raise NotFound("计划变更所属出行不存在")

    workflow = session.scalars(
        select(WorkflowInstance)
        .where(
            WorkflowInstance.organization_id == org_id,
            WorkflowInstance.id == resolved_workflow_id,
            WorkflowInstance.trip_id == trip.id,
            WorkflowInstance.workflow_type == "PLAN_CHANGE",
        )
        .with_for_update()
    ).one_or_none()
    if workflow is None:
        raise NotFound("计划变更工作流不存在")

    workflow_payload = workflow.payload if isinstance(workflow.payload, dict) else {}
    try:
        result_trip_version = int(workflow_payload.get("result_trip_version"))
        base_plan_id = UUID(str(workflow_payload.get("base_plan_version_id")))
    except (TypeError, ValueError):
        raise Unprocessable("计划变更工作流缺少基础版本或结果版本记录") from None

    if workflow.status not in {"ACCEPTED", "FAILED"}:
        raise PlanVersionConflict(
            "当前计划变更工作流状态不允许重试",
            details={"workflow_id": str(workflow.id), "status": workflow.status},
        )
    base_plan = plan_service.require_plan_version(session, org_id, base_plan_id)
    if str(base_plan.trip_id) != str(trip.id) or base_plan.status != "ACTIVE":
        raise PlanVersionConflict("计划变更工作流的基础计划不再是当前 ACTIVE 计划")
    if trip.status in {"CANCELLED", "COMPLETED"}:
        raise Unprocessable("已取消或已完成的出行不能重新规划")
    if trip.version != result_trip_version:
        raise PlanVersionConflict(
            "计划变更后的行程输入再次发生变化，不能重试旧工作流",
            details={
                "workflow_trip_version": result_trip_version,
                "current_trip_version": trip.version,
            },
        )
    if trip.active_plan_version_id != base_plan_id:
        raise PlanVersionConflict("生效计划已变化，不能重试基于旧计划的变更工作流")
    if workflow.job_id is None:
        raise Unprocessable("计划变更工作流没有关联规划任务")

    previous_job = job_service.get_job(session, org_id, workflow.job_id)
    if (
        previous_job.job_type != job_service.JOB_TYPE_PLAN_TRIP
        or str((previous_job.payload or {}).get("trip_id") or "") != str(trip.id)
        or (previous_job.payload or {}).get("trip_version") != result_trip_version
    ):
        raise Unprocessable("计划变更关联的任务与当前行程版本不匹配")

    if previous_job.status in {"PENDING", "RUNNING"}:
        return PlanChangeOutcome(
            workflow=workflow,
            job_id=previous_job.id,
            trip_version=trip.version,
        )
    if previous_job.status not in {"FAILED", "DEAD", "CANCELLED"}:
        raise PlanVersionConflict(
            "当前规划任务不是可重试的失败或取消状态",
            details={"job_id": str(previous_job.id), "status": previous_job.status},
        )

    dedupe_key = f"plan_trip:{trip.id}:{trip.version}"
    retried_job = job_service.enqueue(
        session,
        org_id,
        job_type=job_service.JOB_TYPE_PLAN_TRIP,
        payload={"trip_id": str(trip.id), "trip_version": trip.version},
        dedupe_key=dedupe_key,
        created_by_user_id=actor_user_id,
    )
    if retried_job.status not in {"PENDING", "RUNNING"}:
        raise Unprocessable("重新入队未能创建或复用在途规划任务")

    retry_history = workflow_payload.get("retry_history")
    history = list(retry_history) if isinstance(retry_history, list) else []
    history.append(
        {
            "previous_job_id": str(previous_job.id),
            "previous_job_status": previous_job.status,
            "job_id": str(retried_job.id),
            "queued_at": datetime.now(UTC).isoformat(),
            "actor_user_id": str(actor_user_id),
        }
    )
    workflow_payload["retry_history"] = history[-20:]
    retry_count = workflow_payload.get("retry_count")
    retry_count = retry_count if isinstance(retry_count, int) and retry_count >= 0 else 0
    workflow_payload["retry_count"] = retry_count + 1
    workflow_payload["latest_retry_job_id"] = str(retried_job.id)
    workflow.job_id = retried_job.id
    workflow.status = "ACCEPTED"
    workflow.payload = workflow_payload
    session.flush()

    return PlanChangeOutcome(
        workflow=workflow,
        job_id=retried_job.id,
        trip_version=trip.version,
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
    "retry_plan_change",
]
