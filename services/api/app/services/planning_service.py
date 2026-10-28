"""规划服务：领域数据 ⇄ 规划引擎契约的**唯一**转换点（设计文档 v2.0 第 6 章）。

职责边界（对应引擎侧的不变量 I1）：

- ``build_problem``：把 ``Trip``/``Visit``/``Place`` 规范化成 ``PlanningProblem``。
  **它必须把"为什么某个拜访进不了这次规划"讲清楚**——排除项不是静默丢弃，而是带原因返回，
  最终一路传到 API 响应里，让用户知道"少了什么、为什么少"。
- ``persist_plan``：把引擎产出、**已通过独立验证器**的 ``PlanningResult`` 落成一份
  **不可变** ``PlanVersion`` + 若干 ``PlanStop``。

两条硬约束贯穿全模块：

1. **引擎只会算，不会替我们判断数据是否齐备**。缺坐标、窗口落在出行之外这类"输入不可用"，
   必须在这里拦住并给出结构化原因，绝不能塞一个 ``lat=0, lon=0`` 进引擎——那会让引擎
   在一张错误的世界上算出"可行"的行程。
2. **计划版本号必须在锁住 Trip 行之后才算**（``SELECT ... FOR UPDATE``）。否则并发生成会
   算出同一个 ``version_number``，撞上唯一约束后一侧直接 500，而不是得到一个正常的后续版本。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from travel_planner import (
    Constraints,
    MovementMode,
    Origin,
    PlanningProblem,
    PlanningResult,
    ProblemWindow,
    SolverKind,
    StopInput,
    TimeWindow,
)

# `enumerate_days` 属于引擎的日期工具子模块，未在包顶层重导出。
from travel_planner.days import enumerate_days

from app.api.errors import Unprocessable
from app.config import get_settings
from app.integrations.travel import (
    TravelProvider,
    estimate_with_fallback,
    get_travel_provider,
)
from app.models.geo import Place
from app.models.planning import PlanStop, PlanTravelLeg, PlanVersion
from app.models.trip import Trip, Visit

#: 能进入规划的拜访状态。``CANCELLED`` 不在其中（用户已取消）；``DONE``/``MISSED`` 也不在
#: ——它们是**已经发生**的现场事实，重排计划不能把它们再排一遍。
PLANNABLE_VISIT_STATUSES: tuple[str, ...] = ("PLANNED", "SCHEDULED")

#: 排除原因。与引擎的 ``UnplacedReason`` 是**两套不同的东西**，刻意不复用同一个枚举：
#: ``UnplacedReason`` 回答"求解器为什么没排进去"，这里回答"我们为什么压根没交给求解器"。
#: 混在一起会让前端无法区分"算法尽力了但排不下"和"这条数据根本用不了"。
EXCLUDE_SOURCE_INPUT = "input_guard"


class ExclusionReason(StrEnum):
    """输入侧排除原因（不是求解结果）。"""

    EXCLUDED_BY_STATUS = "EXCLUDED_BY_STATUS"
    MISSING_COORDINATES = "MISSING_COORDINATES"
    WINDOW_OUTSIDE_TRIP = "WINDOW_OUTSIDE_TRIP"
    NOT_ATTACHED_TO_TRIP = "NOT_ATTACHED_TO_TRIP"


@dataclass(frozen=True)
class ExcludedVisit:
    visit_id: UUID
    reason: ExclusionReason
    detail: str


@dataclass
class PlanningBuild:
    """``build_problem`` 的产物：引擎输入 + 上下文 + 排除项。"""

    trip_id: UUID
    problem: PlanningProblem
    visits_by_id: dict[UUID, Visit] = field(default_factory=dict)
    places_by_id: dict[UUID, Place] = field(default_factory=dict)
    excluded: list[ExcludedVisit] = field(default_factory=list)
    travel_degraded: bool = False
    travel_errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 输入规范化
# ---------------------------------------------------------------------------
def build_problem(
    session: Session,
    organization_id: UUID | str,
    trip: Trip,
    *,
    travel_provider: TravelProvider | None = None,
) -> PlanningBuild:
    """把一次出行规范化为 ``PlanningProblem``。

    抛 ``Unprocessable``（422）的两种情况，都带机器可读 ``code``：

    - ``ORIGIN_COORDINATES_MISSING``：出发点没有坐标，算不出任何行程时间；
    - ``NO_PLANNABLE_VISIT``：一个可用停靠都没有，规划无意义（``details.excluded`` 列出原因）。
    """
    settings = get_settings()

    origin_place = _load_origin_place(session, organization_id, trip)
    if origin_place.lat is None or origin_place.lon is None:
        raise Unprocessable(
            "出行出发点缺少坐标，无法计算行程时间",
            code="ORIGIN_COORDINATES_MISSING",
            details={"trip_id": str(trip.id), "origin_place_id": str(origin_place.id)},
        )

    window = ProblemWindow(start=trip.window_start, end=trip.window_end)

    visits, excluded = _select_stops(session, organization_id, trip, window=window)
    if not visits:
        raise Unprocessable(
            "该出行没有任何可规划的有效拜访",
            code="NO_PLANNABLE_VISIT",
            details={
                "trip_id": str(trip.id),
                "excluded": [
                    {"visit_id": str(item.visit_id), "reason": item.reason.value, "detail": item.detail}
                    for item in excluded
                ],
            },
        )

    places = _load_places(session, organization_id, [visit.place_id for visit in visits])
    stops: list[StopInput] = []
    visits_by_id: dict[UUID, Visit] = {}
    for visit in visits:
        place = places[visit.place_id]
        stops.append(
            StopInput(
                visit_id=visit.id,
                place_id=place.id,
                lat=place.lat,
                lon=place.lon,
                service_minutes=visit.service_minutes,
                time_windows=_time_windows(visit, window=window),
                priority=visit.priority,
                business_value=float(visit.business_value or 0),
                movement_mode=MovementMode(visit.movement_mode),
            )
        )
        visits_by_id[visit.id] = visit

    origin = Origin(place_id=origin_place.id, lat=origin_place.lat, lon=origin_place.lon)
    travel = estimate_with_fallback(travel_provider or get_travel_provider(), origin, stops)

    problem = PlanningProblem(
        trip_id=trip.id,
        organization_id=UUID(str(organization_id)),
        timezone_home=trip.home_timezone,
        origin=origin,
        window=window,
        stops=tuple(stops),
        travel_estimates=tuple(travel.estimates),
        constraints=Constraints(
            max_stops_per_day=settings.max_stops_per_day,
            # ``is_immovable`` 表达的就是"这条拜访不能因为容量或价值被优化掉"，
            # 与契约里的语义一一对应。
            immovable_stop_ids=tuple(visit.id for visit in visits if visit.is_immovable),
            # ``locked_order`` 留空：MVP 没有"用户显式锁定顺序"的输入面（访单上没有这个字段），
            # 契约保留该字段供后续的"手动拖动后重新生成"使用。这里不猜一个顺序出来——
            # 猜错会变成一条用户看不见、却真实生效的硬约束。
            locked_order=(),
        ),
        solver_budget_seconds=settings.solver_budget_seconds,
    )
    return PlanningBuild(
        trip_id=trip.id,
        problem=problem,
        visits_by_id=visits_by_id,
        places_by_id=places,
        excluded=excluded,
        travel_degraded=travel.degraded,
        travel_errors=travel.errors,
    )


def solver_kind_for(configured: str) -> SolverKind | None:
    """把配置里的求解器名解析为引擎枚举；无法识别则返回 ``None``（交给引擎自选）。"""
    try:
        return SolverKind(configured)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 持久化
# ---------------------------------------------------------------------------
def persist_plan(
    session: Session,
    organization_id: UUID | str,
    trip: Trip,
    build: PlanningBuild,
    result: PlanningResult,
    *,
    created_by_user_id: UUID | str | None = None,
    job_id: UUID | str | None = None,
) -> PlanVersion:
    """落一份不可变计划版本（``status='DRAFT'``，激活是另一步，见计划版本闭环）。

    调用方负责在合适的位置提交；本函数只 ``flush``。
    """
    org = str(organization_id)

    # 先锁住 Trip 行：版本号要与"同一个 Trip 的其它并发生成"串行化。
    locked = session.scalars(
        select(Trip).where(Trip.id == trip.id).with_for_update()
    ).one_or_none()
    if locked is None:  # pragma: no cover —— 调用方刚拿到 trip，正常不可能消失
        raise Unprocessable("出行不存在", code="TRIP_NOT_FOUND", details={"trip_id": str(trip.id)})

    next_number = (
        session.scalar(
            select(func.coalesce(func.max(PlanVersion.version_number), 0)).where(
                PlanVersion.trip_id == trip.id
            )
        )
        or 0
    ) + 1

    known_costs = [leg.cost_amount for leg in result.legs if leg.cost_amount is not None]
    cost_currencies = {leg.cost_currency for leg in result.legs if leg.cost_currency}
    version = PlanVersion(
        organization_id=org,
        trip_id=trip.id,
        version_number=next_number,
        status="DRAFT",
        problem_hash=result.problem_hash,
        solver=result.solver.value,
        objective_value=result.objective_value,
        solution_status=result.status.value,
        cost_total=round(sum(known_costs), 2) if known_costs else None,
        cost_currency=(
            next(iter(cost_currencies))
            if len(cost_currencies) == 1
            else (trip.currency if known_costs else None)
        ),
        cost_unknown_count=sum(1 for leg in result.legs if leg.cost_amount is None),
        degraded=result.degraded or build.travel_degraded or any(leg.stale for leg in result.legs),
        trip_version=locked.version,
        validation=result.validation.model_dump(mode="json"),
        unplaced=_unplaced_payload(build, result),
        explanation_ref=result.explanation_ref or f"problem:{result.problem_hash}",
        job_id=str(job_id) if job_id is not None else None,
        created_by_user_id=str(created_by_user_id) if created_by_user_id is not None else None,
    )
    session.add(version)
    session.flush()

    day_index_by_date = _day_index_by_date(build.problem, trip)
    legs_by_target = {leg.to_visit_id: leg for leg in result.legs}
    stop_rows: dict[UUID, PlanStop] = {}
    for day in result.days:
        for stop in day.stops:
            visit = build.visits_by_id.get(stop.visit_id)
            leg = legs_by_target.get(stop.visit_id)
            row = PlanStop(
                organization_id=org,
                plan_version_id=version.id,
                visit_id=stop.visit_id,
                day_index=_resolve_day_index(day_index_by_date, day.date),
                sequence=stop.sequence,
                day_date=day.date,
                arrive_at=stop.arrive,
                depart_at=stop.depart,
                travel_minutes=stop.travel_minutes,
                service_minutes=visit.service_minutes if visit is not None else 0,
                distance_km=leg.distance_km if leg is not None else None,
                cost_amount=leg.cost_amount if leg is not None else None,
                cost_currency=leg.cost_currency if leg is not None else None,
                travel_source=leg.source if leg is not None else "haversine-estimate",
                travel_stale=leg.stale if leg is not None else False,
            )
            session.add(row)
            stop_rows[stop.visit_id] = row
    session.flush()

    for leg in result.legs:
        target = stop_rows.get(leg.to_visit_id)
        if target is None:
            continue
        session.add(
            PlanTravelLeg(
                organization_id=org,
                plan_version_id=version.id,
                from_stop_id=(
                    stop_rows[leg.from_visit_id].id
                    if leg.from_visit_id is not None and leg.from_visit_id in stop_rows
                    else None
                ),
                to_stop_id=target.id,
                day_index=target.day_index,
                mode=leg.mode.value,
                travel_minutes=leg.travel_minutes,
                distance_km=leg.distance_km,
                cost_amount=leg.cost_amount,
                cost_currency=leg.cost_currency,
                source=leg.source,
                stale=leg.stale,
            )
        )
    session.flush()
    return version


def mark_visit_status(session: Session, planned_visit_ids: Iterable[UUID], *, status: str) -> None:
    """把已排入计划的拜访标成 ``SCHEDULED``。

    刻意**不**调用 ``trip_service.touch_trip``：``trips.version`` 是激活计划时的比对基准
    （``plan_versions.trip_version == trips.version``），若生成计划顺手把令牌加一，
    刚生成的计划永远比不过、激活必然 409。
    """
    ids = [str(vid) for vid in planned_visit_ids]
    if not ids:
        return
    session.execute(
        Visit.__table__.update().where(Visit.id.in_(ids)).values(status=status)
    )
    session.flush()


# ---------------------------------------------------------------------------
# 内部助手
# ---------------------------------------------------------------------------
def _load_origin_place(session: Session, organization_id: UUID | str, trip: Trip) -> Place:
    if trip.origin_place_id is None:
        raise Unprocessable(
            "出行未设置出发点，无法规划",
            code="ORIGIN_COORDINATES_MISSING",
            details={"trip_id": str(trip.id)},
        )
    place = session.get(Place, trip.origin_place_id)
    if place is None or str(place.organization_id) != str(organization_id):
        raise Unprocessable(
            "出行出发点不存在于当前组织",
            code="ORIGIN_COORDINATES_MISSING",
            details={"trip_id": str(trip.id), "origin_place_id": str(trip.origin_place_id)},
        )
    return place


def _select_stops(
    session: Session, organization_id: UUID | str, trip: Trip, *, window: ProblemWindow
) -> tuple[list[Visit], list[ExcludedVisit]]:
    """挑出可进规划的拜访，并记录被排除的原因。

    坐标缺失判定放在这里而不是引擎里：引擎只接受带坐标的 ``StopInput``，
    而"哪个拜访被丢了"必须由知道``Place`` 的服务层来解释。
    """
    all_visits = session.scalars(
        select(Visit).where(Visit.trip_id == trip.id).order_by(Visit.created_at, Visit.id)
    ).all()

    excluded: list[ExcludedVisit] = []
    candidates: list[Visit] = []
    for visit in all_visits:
        if str(visit.organization_id) != str(organization_id):
            # RLS 正常不会让这种情况出现；显式判定是为了不依赖"环境一定正确"。
            excluded.append(
                ExcludedVisit(visit.id, ExclusionReason.NOT_ATTACHED_TO_TRIP, "拜访不属于当前组织")
            )
            continue
        if visit.status not in PLANNABLE_VISIT_STATUSES:
            excluded.append(
                ExcludedVisit(
                    visit.id,
                    ExclusionReason.EXCLUDED_BY_STATUS,
                    f"拜访状态为 {visit.status}，不参与重新规划",
                )
            )
            continue
        if _window_conflict(visit, window=window) is not None:
            conflict = _window_conflict(visit, window=window)
            assert conflict is not None
            excluded.append(ExcludedVisit(visit.id, ExclusionReason.WINDOW_OUTSIDE_TRIP, conflict))
            continue
        candidates.append(visit)

    if not candidates:
        return [], excluded

    places = _load_places(session, organization_id, [visit.place_id for visit in candidates])
    usable: list[Visit] = []
    for visit in candidates:
        place = places.get(visit.place_id)
        if place is None or place.lat is None or place.lon is None:
            excluded.append(
                ExcludedVisit(
                    visit.id,
                    ExclusionReason.MISSING_COORDINATES,
                    "地点不存在或缺少经纬度",
                )
            )
            continue
        usable.append(visit)
    return usable, excluded


def _load_places(
    session: Session, organization_id: UUID | str, place_ids: Sequence[UUID]
) -> dict[UUID, Place]:
    unique = list(set(place_ids))
    if not unique:
        return {}
    rows = session.scalars(select(Place).where(Place.id.in_(unique))).all()
    return {place.id: place for place in rows if str(place.organization_id) == str(organization_id)}


def _window_conflict(visit: Visit, *, window: ProblemWindow) -> str | None:
    """拜访时间窗与出行窗口是否完全不相交。

    单边窗（只有 start 或只有 end）不算冲突——它表达的是"从这个方向起不受限"，
    在 ``_time_windows`` 里会用出行窗口把缺的那一边补上。
    """
    start = visit.time_window_start
    end = visit.time_window_end
    if start is not None and start > window.end:
        return f"拜访时间窗起点 {start.isoformat()} 晚于出行窗口结束 {window.end.isoformat()}"
    if end is not None and end < window.start:
        return f"拜访时间窗终点 {end.isoformat()} 早于出行窗口开始 {window.start.isoformat()}"
    return None


def _time_windows(visit: Visit, *, window: ProblemWindow) -> tuple[TimeWindow, ...]:
    """把可选的单边/双边时间窗规范化为引擎的 ``TimeWindow`` 元组。

    补边后若 ``start >= end`` 说明这条窗在出行窗口内已经塌缩成一个空区间——此时**不抛异常**：
    它在 ``_window_conflict`` 已被排除，这里只是兜住不可能路径。
    """
    start = visit.time_window_start
    end = visit.time_window_end
    if start is None and end is None:
        return ()
    lo = start if start is not None else window.start
    hi = end if end is not None else window.end
    if hi < lo:
        return ()
    return (TimeWindow(start=lo, end=hi),)


def _day_index_by_date(problem: PlanningProblem, trip: Trip) -> dict[object, int]:
    """按 ``enumerate_days`` 的结果把日期映射到 0 基 ``day_index``。

    不自己算日期差：``enumerate_days`` 是按 ``home_timezone`` 切日界的，用 UTC 天数相减会在
    跨时区时算错一天。
    """
    windows = enumerate_days(problem.window, trip.home_timezone)
    return {day.date: index for index, day in enumerate(windows)}


def _resolve_day_index(index_by_date: dict[object, int], day) -> int:
    index = index_by_date.get(day)
    if index is not None:
        return index
    # 兜底：引擎返回了 enumerate_days 覆盖不到的日期，说明两侧对"一个出行有几天"的理解不一致。
    # 不静默取 0（那会把所有停靠挤到第一天，看起来像排期崩了），而是显式报错。
    raise Unprocessable(
        "规划结果中的日期不在出行窗口内，无法映射到日程",
        code="PLAN_DAY_OUT_OF_WINDOW",
        details={"day_date": day.isoformat()},
    )


def _unplaced_payload(build: PlanningBuild, result: PlanningResult) -> list[dict[str, object]]:
    """合并"求解器没排下"与"我们没交给求解器"两类未排项，并标出各自来源。"""
    payload: list[dict[str, object]] = [
        {"visit_id": str(item.visit_id), "reason": item.reason.value, "source": "solver"}
        for item in result.unplaced
    ]
    payload.extend(
        {
            "visit_id": str(item.visit_id),
            "reason": item.reason.value,
            "source": EXCLUDE_SOURCE_INPUT,
            "detail": item.detail,
        }
        for item in build.excluded
    )
    return payload


__all__ = [
    "EXCLUDE_SOURCE_INPUT",
    "PLANNABLE_VISIT_STATUSES",
    "ExcludedVisit",
    "ExclusionReason",
    "PlanningBuild",
    "build_problem",
    "mark_visit_status",
    "persist_plan",
    "solver_kind_for",
]