"""统一计划变更入口：激活与版本差异比较（设计文档第 9 章 / 13.2 端点表 / RL-02）。

设计文档第 128 行把"业务逻辑不写在 API 路由中"写成了硬约束，并给出激活用例的骨架：
``HTTP Endpoint → ActivatePlanUseCase → Authorization → Domain → Transaction → Outbox → Audit``。
本模块就是那个用例层——路由只解析请求、判权限、组装响应。

与 :mod:`app.services.plan_service` 的分工：那个模块只读（把已经落库的计划读给人看），
本模块只写（唯一会改变计划生命周期状态的地方）。与 :mod:`app.services.planning_service`
的分工：那个模块负责"把一个规划问题算成一份新的 DRAFT 快照"，本模块负责"把某一份**已经存在**
的快照扶正或作废"，以及"把两版快照摊开来对比"。

三条与可靠性直接相关的取舍：

1. **先锁 Trip 行，再判版本。** 激活要一次性改三处（旧版本的 ``status``、目标版本的 ``status``、
   Trip 的 ``active_plan_version_id``/``status``），它们必须原子。锁的对象是 Trip 而不是
   PlanVersion：因为"同一出行至多一份 ACTIVE"这条不变量是**按出行**定义的，两个针对不同版本的
   并发激活只会在 Trip 行上相遇；锁某个具体 PlanVersion 则拦不住另一个版本。
2. **``If-Match`` 不符给 412，计划状态不符给 409。** 前者是"你手上的出行已经变了"，与
   ``PATCH /v1/trips/{id}`` 完全同义（同一条件、同一补救动作：重取再试），因此用同一个状态码；
   后者是"这份计划本身已经不能用了"（陈旧输入、已经生效、并发抢先），补救动作是换一份或重算。
   把两者压成一个状态码，客户端就无法区分"照原样重试即可"和"必须换掉手上的计划"。
3. **激活不改 ``trips.version``。** ``version`` 追踪的是**规划输入**的变化（时间窗口、拜访集合、
   坐标），激活只是把某一份已经算好的快照扶正，输入本身没变。若在这里递增版本号：所有既有计划的
   ``trip_version`` 会立刻全部失效，"用旧版本回滚"（RL-02 的立命之本）会变成不可能，
   而且下一次生成会凭空收到一个"输入变了"的假信号。并发安全由行锁 + ``trip_version`` 陈旧校验
   + 部分唯一索引 ``uq_plan_versions_trip_id_active`` 三者**共同**保证，不靠版本号递增。

关于"回滚"：``status='SUPERSEDED'`` 的版本**可以**被重新激活——这正是版本化的意义。只要该版本
生成时所依据的 ``trip_version`` 仍等于当前 ``trips.version``，就说明输入没变、旧快照仍然有效。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.errors import NotFound, PlanVersionConflict, PreconditionFailed, Unprocessable
from app.models.planning import PlanStop, PlanVersion
from app.models.trip import Trip
from app.services import memory_service, outbox_service, plan_service

#: 计划版本状态（取自 ``PLAN_VERSION_STATUSES``，这里只用到其中两个）。
STATUS_ACTIVE = "ACTIVE"
STATUS_SUPERSEDED = "SUPERSEDED"

#: 出行终态：已经收尾的出行不再接受激活。
TERMINAL_TRIP_STATUSES = ("CANCELLED", "COMPLETED")

#: 差异比较里单个停靠的变更类型。
CHANGE_ADDED = "ADDED"
CHANGE_REMOVED = "REMOVED"
CHANGE_MOVED = "MOVED"
CHANGE_RETIMED = "RETIMED"
CHANGE_UNCHANGED = "UNCHANGED"
STOP_CHANGES = (CHANGE_ADDED, CHANGE_REMOVED, CHANGE_MOVED, CHANGE_RETIMED, CHANGE_UNCHANGED)

#: 未排项的变化动作（只有增/减两个方向，没有"改了原因"这一说）。
UNPLACED_ACTIONS = (CHANGE_ADDED, CHANGE_REMOVED)


@dataclass(frozen=True)
class ActivationOutcome:
    """一次成功激活的完整结果，供路由直接序列化为响应。"""

    trip_id: UUID
    plan_version_id: UUID
    version_number: int
    status: str
    superseded_plan_version_id: UUID | None
    trip_status: str
    trip_version: int
    plan_trip_version: int
    activated_at: datetime


@dataclass(frozen=True)
class StopDelta:
    """一个拜访在两版计划里的对照。缺失的一侧留 ``None``，不塞哨兵值。"""

    visit_id: UUID
    change: str
    day_index_from: int | None
    day_index_to: int | None
    sequence_from: int | None
    sequence_to: int | None
    arrive_at_from: datetime | None
    arrive_at_to: datetime | None
    depart_at_from: datetime | None
    depart_at_to: datetime | None
    travel_minutes_from: int | None
    travel_minutes_to: int | None
    service_minutes_from: int | None
    service_minutes_to: int | None


@dataclass(frozen=True)
class UnplacedDelta:
    """一个未排拜访在基准版与目标版之间的进出。"""

    visit_id: UUID
    action: str
    reason: str | None
    source: str | None


@dataclass(frozen=True)
class PlanVersionDiff:
    """两版计划的差异。``base`` = 基准（旧），``target`` = 目标（新）。"""

    trip_id: UUID
    base_plan_version_id: UUID
    target_plan_version_id: UUID
    base_version_number: int
    target_version_number: int
    base_status: str
    target_status: str
    base_solver: str
    target_solver: str
    base_degraded: bool
    target_degraded: bool
    objective_from: float
    objective_to: float
    objective_delta: float
    travel_minutes_from: int
    travel_minutes_to: int
    travel_minutes_delta: int
    stops: list[StopDelta] = field(default_factory=list)
    unplaced: list[UnplacedDelta] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)


# ------------------------------------------------------------------ 激活


def activate_plan_version(
    session: Session,
    organization_id: UUID | str,
    trip_id: UUID | str,
    plan_version_id: UUID | str,
    *,
    expected_version: int | None = None,
    actor_user_id: UUID | str | None = None,
) -> ActivationOutcome:
    """把某个计划版本扶正为出行的生效计划（把旧的生效版本标为 ``SUPERSEDED``）。

    步骤顺序即事务语义，不可调换：先锁出行 → 锁定目标版本归属 → 乐观并发 → 计划可用性 →
    旧版本退位 → 新版本上位 → 出行指向新版本 → 发事件。本函数**不提交**，只 ``flush``，
    调用方（请求级会话）决定提交点，从而让"状态变更 + 事件"处在同一事务里。
    """
    org = str(organization_id)

    # ① 锁出行行：针对同一次出行的并发激活在这里串行化。
    trip = session.scalars(
        select(Trip).where(Trip.id == str(trip_id)).with_for_update()
    ).one_or_none()
    if trip is None:
        raise NotFound("出行不存在")

    if trip.status in TERMINAL_TRIP_STATUSES:
        raise Unprocessable(
            "已取消或已完成的出行不能再激活计划",
            code="TRIP_NOT_ACTIVATABLE",
            details={"trip_id": str(trip.id), "status": trip.status},
        )

    # ② 目标版本必须存在、属于本租户、且属于这次出行。跨出行的 id 不是"没找到"，
    #    而是"用错了"——这两件事的补救动作不同，所以分别给 404 与 422。
    target = session.scalars(
        select(PlanVersion).where(
            PlanVersion.organization_id == org,
            PlanVersion.id == str(plan_version_id),
        )
    ).one_or_none()
    if target is None:
        raise NotFound("计划版本不存在")
    if target.trip_id != trip.id:
        raise Unprocessable(
            "该计划版本不属于这次出行",
            code="PLAN_TRIP_MISMATCH",
            details={"trip_id": str(trip.id), "plan_version_id": str(target.id)},
        )

    # ③ 乐观并发：客户端手上的出行版本。与 PATCH /trips 同条件、同状态码。
    if expected_version is not None and expected_version != trip.version:
        raise PreconditionFailed(
            "出行已被修改，请重新获取后再激活计划",
            details={
                "expected_version": expected_version,
                "current_version": trip.version,
                "trip_id": str(trip.id),
            },
        )

    # ④ 重复激活先判：对"已经生效的那一版"再点一次，最准确的说法是"它已经生效了"，
    #    而不是让它掉进下面的陈旧输入分支（激活不改 version，那个分支不会触发，但顺序
    #    决定了将来若改成会触发时，客户端拿到的是哪条更可操作的提示）。
    if target.status == STATUS_ACTIVE:
        raise PlanVersionConflict(
            "该计划版本已经生效",
            details={
                "reason": "ALREADY_ACTIVE",
                "trip_id": str(trip.id),
                "plan_version_id": str(target.id),
                "version_number": target.version_number,
            },
        )

    # ⑤ 陈旧计划：生成它时所依据的出行输入已经不是当前版本了，扶正它等于用旧输入覆盖新输入。
    if target.trip_version != trip.version:
        raise PlanVersionConflict(
            "该计划基于已过期的出行输入生成，请重新生成计划",
            details={
                "reason": "STALE_PLAN_VERSION",
                "trip_id": str(trip.id),
                "plan_version_id": str(target.id),
                "plan_trip_version": target.trip_version,
                "current_trip_version": trip.version,
            },
        )

    # ⑥ 旧版本退位、新版本上位。**必须分两次 flush**：``uq_plan_versions_trip_id_active``
    #    是 ``WHERE status='ACTIVE'`` 的部分唯一索引，若把两次状态改动攒到一次 flush 里，
    #    PostgreSQL 在语句执行中途就可能先看到两行 ACTIVE 而报唯一冲突——不是逻辑错，
    #    是索引的可见性顺序问题，换成 ORM 的排序也无济于事，只能靠中间落一次盘。
    activated_at = datetime.now(UTC)
    previous = session.scalars(
        select(PlanVersion).where(
            PlanVersion.trip_id == trip.id,
            PlanVersion.status == STATUS_ACTIVE,
        )
    ).one_or_none()

    superseded_id: UUID | None = None
    try:
        if previous is not None:
            previous.status = STATUS_SUPERSEDED
            previous.superseded_at = activated_at
            superseded_id = previous.id
            session.flush()

        target.status = STATUS_ACTIVE
        target.activated_at = activated_at
        session.flush()
    except IntegrityError as exc:
        # 行锁已经把同出行的并发激活串行化了，走到这里说明有**绕过本入口**的写路径
        # （或行锁被跳过）。如实报成并发冲突，而不是让它变成 500。
        raise PlanVersionConflict(
            "并发的另一个激活请求已经抢先写入了生效计划",
            details={"reason": "CONCURRENT_ACTIVATION", "trip_id": str(trip.id)},
        ) from exc

    # ⑦ 出行指向新版本并进入 ACTIVE。``trips.version`` 刻意不变（见模块文档第 3 条）。
    trip.active_plan_version_id = target.id
    trip.status = STATUS_ACTIVE
    session.flush()

    # 过程记忆：激活与顶替都是"这次出行做过的决定"，下一次规划/复盘要读得到。
    if superseded_id is not None:
        memory_service.log_attempt(
            session,
            org,
            trip.id,
            attempt_type=memory_service.ATTEMPT_SUPERSEDED,
            outcome="SUPERSEDED",
            reason=f"计划 v{previous.version_number} 被 v{target.version_number} 替代",
            plan_version_id=superseded_id,
        )
    memory_service.log_attempt(
        session,
        org,
        trip.id,
        attempt_type=memory_service.ATTEMPT_ACTIVATED,
        outcome="ACTIVE",
        details={"version_number": target.version_number},
        plan_version_id=target.id,
    )

    # ⑧ 事件与业务写入同事务。主动式运行时是它的消费者：计划被激活意味着"未排项/缺坐标"
    #    这些状态规则的判断依据变了，值得立刻重扫一次（设计文档 §8.1 的 EVENT 触发源）。
    outbox_service.emit(
        session,
        org,
        aggregate_type=outbox_service.AGGREGATE_TRIP,
        aggregate_id=trip.id,
        event_type=outbox_service.EVENT_PLAN_VERSION_ACTIVATED,
        payload={
            "trip_id": str(trip.id),
            "plan_version_id": str(target.id),
            "version_number": target.version_number,
            "superseded_plan_version_id": str(superseded_id) if superseded_id else None,
            "activated_by_user_id": str(actor_user_id) if actor_user_id is not None else None,
        },
        consumers=(outbox_service.CONSUMER_PROACTIVE_TRIGGER,),
        # 这里**刻意不加去重键**。加过的版本用 ``{trip}:{target}`` 做键，结果是"回滚"被静默吞掉：
        # 把已 SUPERSEDED 的版本重新扶正时，键与它第一次激活时完全相同，``emit`` 判定命中便不再发事件
        # ——审计流里于是看不到"某版本被顶下去、旧的又回来了"这一整段。而本模块的立命之本恰恰是
        # 回滚可用（见模块文档末段）。真正会重复的那条路径也已被上游挡住：同一幂等键的重放在
        # ``IdempotentWrite.begin()`` 就回放了响应、根本走不到这里；对已生效版本再点一次则被上面的
        # ALREADY_ACTIVE 拦下。因此这里每一次 emit 都对应一次**真实的状态迁移**，都必须留痕。
    )

    return ActivationOutcome(
        trip_id=trip.id,
        plan_version_id=target.id,
        version_number=target.version_number,
        status=target.status,
        superseded_plan_version_id=superseded_id,
        trip_status=trip.status,
        trip_version=trip.version,
        plan_trip_version=target.trip_version,
        activated_at=activated_at,
    )


# ------------------------------------------------------------------ 差异


def diff_plan_versions(
    session: Session,
    organization_id: UUID | str,
    trip_id: UUID | str,
    plan_version_id: UUID | str,
    *,
    against: UUID | str,
) -> PlanVersionDiff:
    """把``plan_version_id``（目标/新）与``against``（基准/旧）摊开来逐拜访对照。

    ``against`` 是查询参数而不是路径参数，因为语义上"被比较的对象"是输入，而"要看的对象"
    是主体——这样 ``plans/{id}:diff`` 的返回值始终以路径里的那份计划为主语。
    """
    org = str(organization_id)

    trip = session.scalars(select(Trip).where(Trip.id == str(trip_id))).one_or_none()
    if trip is None:
        raise NotFound("出行不存在")

    target = _require_version(session, org, trip, plan_version_id, role="计划版本")
    base = _require_version(session, org, trip, against, role="对比基准计划版本")

    base_stops = {stop.visit_id: stop for stop in plan_service.list_plan_stops(session, org, base.id)}
    target_stops = {
        stop.visit_id: stop for stop in plan_service.list_plan_stops(session, org, target.id)
    }

    # 输出顺序刻意先走基准版（同序对照，读起来像"原计划改了什么"），再把只存在于目标版的
    # 新增项按目标版顺序追加。这样"删除/移动"不会因为排序而漂到新增项中间。
    stops: list[StopDelta] = []
    for stop in _ordered(base_stops.values()):
        stops.append(_stop_delta(stop, target_stops.get(stop.visit_id)))
    for stop in _ordered(target_stops.values()):
        if stop.visit_id not in base_stops:
            stops.append(_stop_delta(None, stop))

    base_unplaced = _index_unplaced(base.unplaced)
    target_unplaced = _index_unplaced(target.unplaced)
    unplaced: list[UnplacedDelta] = []
    for visit_id in sorted(set(base_unplaced) - set(target_unplaced)):
        item = base_unplaced[visit_id]
        unplaced.append(
            UnplacedDelta(visit_id, CHANGE_REMOVED, item.get("reason"), item.get("source"))
        )
    for visit_id in sorted(set(target_unplaced) - set(base_unplaced)):
        item = target_unplaced[visit_id]
        unplaced.append(UnplacedDelta(visit_id, CHANGE_ADDED, item.get("reason"), item.get("source")))

    summary = {change.lower(): 0 for change in STOP_CHANGES}
    for delta in stops:
        summary[delta.change.lower()] += 1
    summary["total"] = len(stops)

    base_travel = sum(stop.travel_minutes for stop in base_stops.values())
    target_travel = sum(stop.travel_minutes for stop in target_stops.values())
    # 浮点目标值直接相减会留下二进制尾数（1368.0000000000002 这种），客户端做前后对比时很刺眼，
    # 因此按验证器的容差量级（1e-6）收敛一次。
    objective_delta = round(target.objective_value - base.objective_value, 6)

    return PlanVersionDiff(
        trip_id=trip.id,
        base_plan_version_id=base.id,
        target_plan_version_id=target.id,
        base_version_number=base.version_number,
        target_version_number=target.version_number,
        base_status=base.status,
        target_status=target.status,
        base_solver=base.solver,
        target_solver=target.solver,
        base_degraded=base.degraded,
        target_degraded=target.degraded,
        objective_from=base.objective_value,
        objective_to=target.objective_value,
        objective_delta=objective_delta,
        travel_minutes_from=base_travel,
        travel_minutes_to=target_travel,
        travel_minutes_delta=target_travel - base_travel,
        stops=stops,
        unplaced=unplaced,
        summary=summary,
    )


def _require_version(
    session: Session, org: str, trip: Trip, plan_version_id: UUID | str, *, role: str
) -> PlanVersion:
    version = session.scalars(
        select(PlanVersion).where(
            PlanVersion.organization_id == org,
            PlanVersion.id == str(plan_version_id),
        )
    ).one_or_none()
    if version is None:
        raise NotFound(f"{role}不存在")
    if version.trip_id != trip.id:
        raise Unprocessable(
            f"{role}不属于这次出行",
            code="PLAN_TRIP_MISMATCH",
            details={"trip_id": str(trip.id), "plan_version_id": str(version.id)},
        )
    return version


def _ordered(stops) -> list[PlanStop]:
    return sorted(stops, key=lambda stop: (stop.day_index, stop.sequence))


def _stop_delta(before: PlanStop | None, after: PlanStop | None) -> StopDelta:
    """判定变更类型。优先级：新增/删除 > 移动 > 改时刻 > 未变。

    移动优先于改时刻：位置变了意味着计划结构变了，时刻随之变是**结果**而不是另一件事，
    单独再报一次 RETIMED 只会让同一件事在摘要里被数两遍。
    """
    if before is None and after is None:  # pragma: no cover —— 调用方保证至少一侧存在
        raise AssertionError("StopDelta 至少需要一侧存在")
    visit_id = before.visit_id if before is not None else after.visit_id  # type: ignore[union-attr]

    if before is None:
        change = CHANGE_ADDED
    elif after is None:
        change = CHANGE_REMOVED
    elif (before.day_index, before.sequence) != (after.day_index, after.sequence):
        change = CHANGE_MOVED
    elif _timing(before) != _timing(after):
        change = CHANGE_RETIMED
    else:
        change = CHANGE_UNCHANGED

    return StopDelta(
        visit_id=visit_id,
        change=change,
        day_index_from=before.day_index if before else None,
        day_index_to=after.day_index if after else None,
        sequence_from=before.sequence if before else None,
        sequence_to=after.sequence if after else None,
        arrive_at_from=before.arrive_at if before else None,
        arrive_at_to=after.arrive_at if after else None,
        depart_at_from=before.depart_at if before else None,
        depart_at_to=after.depart_at if after else None,
        travel_minutes_from=before.travel_minutes if before else None,
        travel_minutes_to=after.travel_minutes if after else None,
        service_minutes_from=before.service_minutes if before else None,
        service_minutes_to=after.service_minutes if after else None,
    )


def _timing(stop: PlanStop) -> tuple:
    return (stop.arrive_at, stop.depart_at, stop.travel_minutes, stop.service_minutes)


def _index_unplaced(payload: list | None) -> dict[UUID, dict]:
    """把 JSONB 里的未排项按 ``visit_id`` 建索引；跳过结构不完整的条目而不是崩掉。"""
    indexed: dict[UUID, dict] = {}
    for item in payload or []:
        if not isinstance(item, dict):
            continue
        raw = item.get("visit_id")
        if not raw:
            continue
        try:
            indexed[UUID(str(raw))] = item
        except ValueError:  # pragma: no cover —— 落库时一定是合法 UUID，这里只求不炸
            continue
    return indexed


__all__ = [
    "CHANGE_ADDED",
    "CHANGE_MOVED",
    "CHANGE_REMOVED",
    "CHANGE_RETIMED",
    "CHANGE_UNCHANGED",
    "STOP_CHANGES",
    "TERMINAL_TRIP_STATUSES",
    "UNPLACED_ACTIONS",
    "ActivationOutcome",
    "PlanVersionDiff",
    "StopDelta",
    "UnplacedDelta",
    "activate_plan_version",
    "diff_plan_versions",
]