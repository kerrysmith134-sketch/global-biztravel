"""任务处理器：``job_type`` → 可执行函数。

处理器契约（与 ``job_service`` 的分工）：

- 收到一个**已经处于 ``RUNNING``、且租约属于当前 Worker** 的任务（由 ``main`` 负责抢占）。
- 只在当前会话里写数据，**不提交事务**——提交点由 ``main`` 决定，这样"处理器留下的副作用"
  与"任务状态结算"能在一个事务里一起落库。
- 拿不出结果时抛 ``TaskFailed``。``retryable=True`` 表示"这次是环境问题，重试有意义"
  （``main`` 会回滚本事务）；``retryable=False`` 表示"重试必然重复同一个错误"
  （``main`` 会**保留**本事务已经写的副作用再结算，用于"先把失败事件发出去，再把任务标终态"）。

为什么要区分是否保留副作用：计划不可行时我们**必须**把 ``plan.not_feasible`` 事件留在库里，
否则消费者永远不会把 Trip 从 ``PLANNING`` 拨回 ``DRAFT``，用户界面上那次出行就永远停在
"正在规划中"。既然结果本身是确定的失败，就没必要为了回滚而丢掉这条通知。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session
from travel_planner import SolverFailed, plan_trip

from app.api.errors import ApiError
from app.config import get_settings
from app.models.reliability import BackgroundJob
from app.models.trip import Trip
from app.services import job_service, memory_service, outbox_service, planning_service


class TaskFailed(RuntimeError):
    """任务处理失败。``retryable`` 决定是"换个时间再试"还是"到此为止"。"""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.message = message
        self.retryable = retryable


TaskHandler = Callable[[Session, BackgroundJob], dict[str, Any]]


def handle_plan_trip(session: Session, job: BackgroundJob) -> dict[str, Any]:
    """为一次出行求解并落一份候选计划版本。

    失败路径统一走 ``_reject``：先发 ``plan.not_feasible`` 事件（并把 Trip 拨回 ``DRAFT``
    的责任交给消费者），再抛 ``TaskFailed(retryable=False)``。三种失败共用一条路径：
    输入不可用（缺坐标、窗口冲突）、求解器双双失败、引擎判定不可行。
    """
    settings = get_settings()
    raw_trip_id = (job.payload or {}).get("trip_id")
    if not raw_trip_id:
        raise TaskFailed("任务载荷缺少 trip_id", retryable=False)
    try:
        trip_id = UUID(str(raw_trip_id))
    except ValueError as exc:
        raise TaskFailed(f"任务载荷中的 trip_id 不是合法 UUID：{raw_trip_id}", retryable=False) from exc

    trip = session.get(Trip, trip_id)
    if trip is None:
        raise TaskFailed("出行不存在（可能已被删除）", retryable=False)

    try:
        build = planning_service.build_problem(session, job.organization_id, trip)
    except ApiError as exc:
        _reject(session, trip, reason=exc.message, code=exc.code)
        raise TaskFailed(exc.message, retryable=False) from exc

    try:
        result = plan_trip(
            build.problem,
            solver=planning_service.solver_kind_for(settings.planning_solver),
            explanation_ref=f"job:{job.id}",
        )
    except SolverFailed as exc:
        _reject(session, trip, reason=str(exc), code="SOLVER_FAILED")
        raise TaskFailed(str(exc), retryable=False) from exc

    if not result.validation.feasible:
        failures = ", ".join(f"{check.rule}: {check.detail}" for check in result.validation.failures)
        _reject(session, trip, reason=f"独立验证器判定计划不可行（{failures}）", code="PLAN_NOT_FEASIBLE")
        raise TaskFailed("独立验证器判定计划不可行", retryable=False)

    version = planning_service.persist_plan(
        session,
        job.organization_id,
        trip,
        build,
        result,
        created_by_user_id=job.created_by_user_id,
        job_id=job.id,
    )

    # 已排入计划的拜访转为 SCHEDULED，表示"这次行程里它有了确定的位置"。
    planning_service.mark_visit_status(session, result.placed_visit_ids, status="SCHEDULED")

    # 过程记忆：记下"这次算过、结果如何"。失败路径由 _reject 记另一条。
    memory_service.log_attempt(
        session,
        job.organization_id,
        trip.id,
        attempt_type=memory_service.ATTEMPT_GENERATED,
        outcome="FEASIBLE",
        details={
            "solver": result.solver.value,
            "degraded": result.degraded,
            "objective_value": result.objective_value,
            "placed": len(result.placed_visit_ids),
            "unplaced": len(result.unplaced),
            "job_id": str(job.id),
        },
        plan_version_id=version.id,
    )

    # 事件带上 plan_version_id：消费者据此判断"这份计划是否已经被激活过"。
    # dedupe_key 用 job.id：同一个任务被重跑（例如租约过期后重排）不会产生第二条同义事件。
    outbox_service.emit(
        session,
        job.organization_id,
        aggregate_type=outbox_service.AGGREGATE_TRIP,
        aggregate_id=trip.id,
        event_type=outbox_service.EVENT_PLAN_VERSION_CREATED,
        payload={
            "trip_id": str(trip.id),
            "plan_version_id": str(version.id),
            "version_number": version.version_number,
            "solver": result.solver.value,
            "degraded": result.degraded,
            "objective_value": result.objective_value,
            "job_id": str(job.id),
        },
        consumers=(outbox_service.CONSUMER_TRIP_STATUS_SYNC,),
        dedupe_key=f"plan-version-created:{job.id}",
    )
    session.flush()

    return {
        "plan_version_id": str(version.id),
        "version_number": version.version_number,
        "solver": result.solver.value,
        "objective_value": result.objective_value,
        "degraded": result.degraded,
        "placed_visits": [str(vid) for vid in result.placed_visit_ids],
        "unplaced": [
            {"visit_id": str(item["visit_id"]), "reason": str(item["reason"]), "source": str(item["source"])}
            for item in version.unplaced
        ],
    }


def _reject(session: Session, trip: Trip, *, reason: str, code: str) -> None:
    """记下"这次规划没能产出计划"，并立刻 flush（``retryable=False`` 时不回滚，见模块文档）。"""
    memory_service.log_attempt(
        session,
        trip.organization_id,
        trip.id,
        attempt_type=memory_service.ATTEMPT_REJECTED,
        outcome=code,
        reason=reason,
    )
    outbox_service.emit(
        session,
        trip.organization_id,
        aggregate_type=outbox_service.AGGREGATE_TRIP,
        aggregate_id=trip.id,
        event_type=outbox_service.EVENT_PLAN_NOT_FEASIBLE,
        payload={"trip_id": str(trip.id), "code": code, "reason": reason},
        consumers=(outbox_service.CONSUMER_TRIP_STATUS_SYNC,),
        dedupe_key=f"plan-not-feasible:{trip.id}:{trip.version}",
    )
    session.flush()


#: 任务处理器注册表。新增 ``job_type`` 时必须同时在这里登记，否则任务会以
#: "没有注册处理器"直接终态失败（这是刻意的：静默丢弃任务比报错更糟）。
TASK_HANDLERS: Mapping[str, TaskHandler] = {
    job_service.JOB_TYPE_PLAN_TRIP: handle_plan_trip,
}


__all__ = ["TASK_HANDLERS", "TaskFailed", "TaskHandler", "handle_plan_trip"]