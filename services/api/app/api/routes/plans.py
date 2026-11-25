"""计划生成与查询接口。

这一层只做四件事：解析请求、校验权限、调用服务、组装响应。**求解不在这里发生**——
``plans:generate`` 只把任务放进队列并立刻返回 ``202``，真正的计算由 ``app.workers`` 在
后台完成。理由很直接：OR-Tools 求解耗时不可预期，把 HTTP 请求挂在那里等，既会打爆超时，
也会让"重复点按钮"变成"重复求解"。

三条设计取舍：

- **幂等检查必须在业务动作之前**（``IdempotentWrite.begin()`` 先于入队）。否则重放请求会
  真的再建一条任务，只是把首次响应回放给客户端。业务前置校验（如"已取消的出行不能规划"）
  刻意放在 ``begin()`` **之后**：重复提交同一请求应当拿到回放的 202，而不是一个 422。
- **生成计划不改 Trip 版本号**。它只把 ``DRAFT`` 推进为 ``PLANNING``（表示"正在算"），
  不动内容也不调 ``touch_trip``。若在这里递增版本号，计划生成完就永远无法被激活——
  激活时要比对 ``plan_versions.trip_version == trips.version``，对不上必然 409。
- **入队去重键 = 出行 + 版本**（``plan_trip:{trip_id}:{trip.version}``）。同一出行同一内容
  重复点按钮只会有一条在途任务；内容改过（版本号变了）则允许重新求解，因为那确实是个新问题。
"""

from __future__ import annotations

import dataclasses
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status
from sqlalchemy.orm import Session

from app.api.deps import (
    ROLE_TRAVELER,
    ROLE_VIEWER,
    ContextDep,
    IdempotencyKeyDep,
    IfMatchDep,
    SessionDep,
    require_role,
)
from app.api.errors import NotFound, Unprocessable
from app.api.idempotent import IdempotentWrite
from app.api.schemas import (
    JobRead,
    PlanActivationRead,
    PlanChangeCreate,
    PlanChangeRead,
    PlanGenerateRead,
    PlanVersionDetailRead,
    PlanVersionDiffRead,
    PlanVersionRead,
)
from app.services import (
    job_service,
    plan_change_request_service,
    plan_change_service,
    plan_service,
    trip_service,
)

router = APIRouter(tags=["plans"])

PLANS_GENERATE_ENDPOINT = "POST /v1/trips/{trip_id}/plans:generate"
PLANS_ACTIVATE_ENDPOINT = "POST /v1/trips/{trip_id}/plans/{plan_version_id}:activate"
PLANS_DIFF_ENDPOINT = "GET /v1/trips/{trip_id}/plans/{plan_version_id}:diff"
PLAN_CHANGES_ENDPOINT = "POST /v1/trips/{trip_id}/plan-changes"

#: 这两个状态是终态：出行已经收尾，不该再往里塞新计划。
_TERMINAL_TRIP_STATUSES = ("CANCELLED", "COMPLETED")


@router.post(
    "/trips/{trip_id}/plans:generate",
    response_model=PlanGenerateRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="生成计划（异步，立即返回 job_id）",
)
def generate_plan(
    trip_id: UUID,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    trip = trip_service.require_trip(session, context.organization_id, trip_id)

    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=PLANS_GENERATE_ENDPOINT,
        key=idempotency_key,
        payload={"trip_id": str(trip_id)},
    )
    if (replay := guard.begin()) is not None:
        return replay

    if trip.status in _TERMINAL_TRIP_STATUSES:
        raise Unprocessable(
            "已取消或已完成的出行不能再生成计划",
            code="TRIP_NOT_PLANNABLE",
            details={"trip_id": str(trip.id), "status": trip.status},
        )

    dedupe_key = f"plan_trip:{trip.id}:{trip.version}"
    # 先看一眼有没有在途任务，只为在响应里如实回答 reused；真正的去重由 enqueue 的
    # ON CONFLICT 保证（并发生效），因此这里允许"没看到"。
    in_flight = job_service.find_inflight(session, context.organization_id, dedupe_key)

    job = job_service.enqueue(
        session,
        context.organization_id,
        job_type=job_service.JOB_TYPE_PLAN_TRIP,
        payload={"trip_id": str(trip.id), "trip_version": trip.version},
        dedupe_key=dedupe_key,
        created_by_user_id=context.user_id,
    )

    if trip.status == "DRAFT":
        # 只是"正在算"这一状态标记，不是内容变更，因此不递增版本号（见模块文档）。
        trip.status = "PLANNING"
    session.flush()

    body = PlanGenerateRead(
        job_id=job.id,
        trip_id=trip.id,
        trip_version=trip.version,
        status=job.status,
        reused=in_flight is not None,
    )
    return guard.finish(status_code=status.HTTP_202_ACCEPTED, body=body)


@router.get("/jobs/{job_id}", response_model=JobRead, summary="任务详情")
def get_job(job_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_VIEWER)
    return job_service.get_job(session, context.organization_id, job_id)


@router.get(
    "/trips/{trip_id}/plans",
    response_model=list[PlanVersionRead],
    summary="出行的计划版本列表（最新在前）",
)
def list_plan_versions(trip_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_VIEWER)
    trip_service.require_trip(session, context.organization_id, trip_id)
    versions = plan_service.list_plan_versions(session, context.organization_id, trip_id)
    stats = plan_service.stop_stats(
        session, context.organization_id, [version.id for version in versions]
    )
    items: list[PlanVersionRead] = []
    for version in versions:
        read = PlanVersionRead.model_validate(version)
        read.stop_count, read.day_count = stats.get(version.id, (0, 0))
        items.append(read)
    return items



@router.get(
    "/plan-versions/{plan_version_id}",
    response_model=PlanVersionDetailRead,
    summary="计划版本详情（含停靠时间表）",
)
def get_plan_version(plan_version_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_VIEWER)
    version = plan_service.require_plan_version(
        session, context.organization_id, plan_version_id
    )
    return plan_service.plan_version_detail(session, context.organization_id, version)



@router.post(
    "/trips/{trip_id}/plans/{plan_version_id}:activate",
    response_model=PlanActivationRead,
    status_code=status.HTTP_200_OK,
    summary="激活某一版计划（幂等；If-Match: version）",
)
def activate_plan(
    trip_id: UUID,
    plan_version_id: UUID,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
    if_match: IfMatchDep,
):
    """把某一份计划版本扶正为出行生效计划（把旧的生效版本标为 ``SUPERSEDED``）。

    与 ``plans:generate`` 的差别：**激活是同步事务，不是异步任务**。改的是状态而非算结果，
    没有不可预期的耗时，因此不排队、不返回 202，用户点完立刻看到出行进入 ``ACTIVE``。
    全部判定（``If-Match`` 乐观并发、计划陈旧、重复激活、并发抢先）都在
    :func:`plan_change_service.activate_plan_version` 里，路由只做权限与组装。

    ``If-Match`` 建议带上：不带则退化为"最后写入者胜"，并发的两个激活会被行锁串行化，
    后者拿到 409。带上则两者之一必然先拿到 412 而重取，语义更清晰。
    """
    require_role(context, ROLE_TRAVELER)
    trip_service.require_trip(session, context.organization_id, trip_id)

    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=PLANS_ACTIVATE_ENDPOINT,
        key=idempotency_key,
        # ``if_match`` 计入指纹：同一 key 配不同版本号是**误用 key**（换了请求体），不是重试。
        payload={
            "trip_id": str(trip_id),
            "plan_version_id": str(plan_version_id),
            "if_match": if_match,
        },
    )
    if (replay := guard.begin()) is not None:
        return replay

    outcome = plan_change_service.activate_plan_version(
        session,
        context.organization_id,
        trip_id,
        plan_version_id,
        expected_version=if_match,
        actor_user_id=context.user_id,
    )
    body = PlanActivationRead.model_validate(dataclasses.asdict(outcome))
    return guard.finish(status_code=status.HTTP_200_OK, body=body)


@router.get(
    "/trips/{trip_id}/plans/{plan_version_id}:diff",
    response_model=PlanVersionDiffRead,
    summary="两版计划差异对比（只读）",
)
def diff_plan(
    trip_id: UUID,
    plan_version_id: UUID,
    context: ContextDep,
    session: SessionDep,
    against: Annotated[UUID, Query(description="对比基准（旧）计划版本 id")],
):
    """把路径里那一版（target）与 ``against``（base）摊开来逐拜访对照。

    ``against`` 是查询参数而不是路径参数：被比较的对象是输入，要看的是主体，
    这样返回值的主语始终是路径里的那份计划。
    """
    require_role(context, ROLE_VIEWER)
    diff = plan_change_service.diff_plan_versions(
        session,
        context.organization_id,
        trip_id,
        plan_version_id,
        against=against,
    )
    # 用 ``dataclasses.asdict`` 递归展开（嵌套的 ``StopDelta``/``UnplacedDelta`` 一并变 dict）：
    # 数据类不是 ORM 对象，没有 ``from_attributes`` 可依，而嵌套 dataclass 混进 Pydantic
    # 会在校验期报"不是 dict"。
    return PlanVersionDiffRead.model_validate(dataclasses.asdict(diff))

@router.get(
    "/trips/{trip_id}/plans/{plan_version_id}",
    response_model=PlanVersionDetailRead,
    summary="出行下的计划版本详情（契约 §13.2 的嵌套路径）",
)
def get_trip_plan_version(
    trip_id: UUID,
    plan_version_id: UUID,
    context: ContextDep,
    session: SessionDep,
):
    require_role(context, ROLE_VIEWER)
    trip_service.require_trip(session, context.organization_id, trip_id)
    version = plan_service.require_plan_version(
        session, context.organization_id, plan_version_id
    )
    if str(version.trip_id) != str(trip_id):
        raise NotFound("计划版本不属于该出行")
    return plan_service.plan_version_detail(session, context.organization_id, version)


@router.post(
    "/trips/{trip_id}/plan-changes",
    response_model=PlanChangeRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="统一计划变更入口（校验基础版本 → 改输入 → 入队新计划）",
)
def create_plan_change(
    trip_id: UUID,
    payload: PlanChangeCreate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=PLAN_CHANGES_ENDPOINT,
        key=idempotency_key,
        payload={"trip_id": str(trip_id), **payload.model_dump(mode="json")},
    )
    if (replay := guard.begin()) is not None:
        return replay

    outcome = plan_change_request_service.create_change_request(
        session,
        context.organization_id,
        trip_id,
        base_plan_version_id=payload.base_plan_version_id,
        base_trip_version=payload.base_trip_version,
        source=payload.source,
        operation_type=payload.operation_type,
        payload=payload.payload,
        reason=payload.reason,
        actor_user_id=context.user_id,
    )
    body = PlanChangeRead(
        change_request_id=outcome.workflow.id,
        status=outcome.workflow.status,
        optimization_job_id=outcome.job_id,
        trip_version=outcome.trip_version,
    )
    return guard.finish(status_code=status.HTTP_202_ACCEPTED, body=body)


@router.post(
    "/jobs/{job_id}:cancel",
    response_model=JobRead,
    summary="取消排队中的规划任务",
)
def cancel_job(
    job_id: UUID,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint="POST /v1/jobs/{job_id}:cancel",
        key=idempotency_key,
        payload={"job_id": str(job_id)},
    )
    if (replay := guard.begin()) is not None:
        return replay
    job = job_service.cancel(session, context.organization_id, job_id)
    return guard.finish(status_code=status.HTTP_200_OK, body=JobRead.model_validate(job))
