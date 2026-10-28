"""确定性日程构造：给定访问顺序，逐日排入并产出精确到分钟的到达/离开时间。

本模块是**求解器与验证器共同的时间语义基准**：
- 求解器（OR-Tools 或启发式）只决定"访问顺序"；
- 时间与**日归属**由本模块按同一套几何模型物化，保证求解器输出与独立验证器的判据同源。

多天连续性语义（**冻结**，第 6.3 节 §时间语义）：
- 第 1 天从 `problem.origin` 出发；
- 第 k 天从**第 k-1 天最后一个已排停靠**的位置出发；若第 k-1 天没有停靠，则继续向前
  回溯，直到找到最近一个"有停靠的日子"；仍无则回到 `problem.origin`。
- 因此整个行程是一条**连续路径**：`origin → s1 → s2 → … → sn`。跨天不重置位置、
  也不模拟返程；`travel_minutes_total` 即这条路径上各段行程时间之和。
- 每天的**出发时刻**仍取该日区间起点（`DayWindow.open`）；同日续排则接续上一个停靠的
  `depart`。

为什么必须是连续路径（而不是"每天从 origin 重新起算"）：
设计文档强调"连续住宿 / 连续拜访同一城市 / 住宿是路线的一部分"，AT-001 也要求多国
行程能生成候选方案。若每天都从 origin 重新起算，跨洲行程（上海 → 欧洲，最省时的航空段
也要约 840 分钟）会被重复计入每一天，导致第 2 天之后的所有停靠都被算成"当天到不了"，
多国行程必然不可行——那是建模错误，不是现实约束。

贪心的单调性保证（与验证器复算口径一致的**前提**）：
- 天指针 `pointer` **只增不减**：某个停靠落在第 j 天之后，后续停靠只会在第 j 天及其
  之后的日子继续排。
- 由此可推出不变量：**`pointer` 之后的所有日子必然为空**。于是一个停靠落进第 j 天时，
  第 j+1 天（乃至更后）还没有任何停靠，第 j 天末位置的变化不会让"已物化的行程分钟"
  失效——这正是验证器按同一路径独立复算时能逐段对齐的原因。
- 空日的出发位置**每次现场回溯解析，不做缓存**：缓存会在"先前的空日后来被填上停靠"
  时失效（回溯结果取决于当时已排的停靠）。

独立验证器**不导入本模块**（由 `tests/test_no_io.py` 以 AST 断言强制），
它按规则名逐条独立复算。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from travel_planner.contracts import (
    DayPlan,
    PlanningProblem,
    StopInput,
    StopPlan,
    TravelLegPlan,
    UnplacedReason,
    UnplacedStop,
)
from travel_planner.days import DayWindow, enumerate_days
from travel_planner.geometry import haversine_km, travel_minutes


@dataclass
class _DayState:
    window: DayWindow
    stops: list[StopPlan] = field(default_factory=list)

    @property
    def full_at(self) -> int:
        return len(self.stops)


@dataclass(frozen=True)
class ScheduleOutcome:
    days: tuple[DayPlan, ...]
    legs: tuple[TravelLegPlan, ...]
    unplaced: tuple[UnplacedStop, ...]
    travel_minutes_total: int
    value_placed: float


def resolve_arrival(
    earliest: datetime, stop: StopInput, *, window_conflict: list[bool] | None = None
) -> datetime | None:
    """在 `earliest` 之后选择最早的可行到达时刻。

    时间窗语义：**到达时刻落在窗口区间内**（`start <= arrive <= end`）。
    无窗口 ⇒ 直接取 `earliest`。
    """
    if not stop.time_windows:
        return earliest
    for window in stop.time_windows:
        candidate = earliest if earliest > window.start else window.start
        if candidate <= window.end:
            return candidate
    if window_conflict is not None:
        window_conflict[0] = True
    return None


def build_schedule(
    problem: PlanningProblem,
    ordered_stop_ids: tuple[UUID, ...],
) -> ScheduleOutcome:
    """按给定顺序贪心排程：**最早可放日优先，同日按顺序追加**。

    位置在**天与天之间连续传递**（见模块 docstring 的"多天连续性语义"）。
    """
    by_id = {stop.visit_id: stop for stop in problem.stops}
    estimates = {
        (item.from_visit_id, item.to_visit_id, item.mode): item
        for item in problem.travel_estimates
    }
    day_windows = enumerate_days(problem.window, problem.timezone_home)

    states = [_DayState(window=day) for day in day_windows]

    max_per_day = problem.constraints.max_stops_per_day
    travel_total = 0
    value_placed = 0.0
    legs: list[TravelLegPlan] = []
    unplaced: list[UnplacedStop] = []
    seen: set[UUID] = set()

    def _coords(visit_id: UUID) -> tuple[float, float]:
        stop = by_id[visit_id]
        return stop.lat, stop.lon

    def _day_start(index: int) -> tuple[float, float, UUID | None]:
        """第 `index` 天的出发位置：回溯到最近一个"有停靠的日子"。

        空日不缓存结果——它取决于调用时刻已排入的停靠。
        """
        for earlier in range(index - 1, -1, -1):
            earlier_stops = states[earlier].stops
            if earlier_stops:
                last = earlier_stops[-1].visit_id
                lat, lon = _coords(last)
                return lat, lon, last
        return problem.origin.lat, problem.origin.lon, None

    def _departure(index: int) -> tuple[datetime, float, float, UUID | None]:
        """当天续排的出发时刻与位置。

        - 该日已有停靠：接续最后一个停靠的 `depart` 与坐标（同日链续）；
        - 该日尚无停靠：出发时刻取日区间起点，位置取该日的"起点位置"（跨天链续）。
        """
        placed = states[index].stops
        if placed:
            last = placed[-1]
            lat, lon = _coords(last.visit_id)
            return last.depart, lat, lon, last.visit_id
        lat, lon, from_visit = _day_start(index)
        return states[index].window.open, lat, lon, from_visit

    #: 天指针：只增不减，指向"最后一个已排入停靠的日子"。
    pointer = 0

    for visit_id in ordered_stop_ids:
        if visit_id in seen:
            continue
        stop = by_id[visit_id]

        window_conflict = [False]
        has_room = any(state.full_at < max_per_day for state in states)
        placed = False

        for index in range(pointer, len(states)):
            state = states[index]
            if state.full_at >= max_per_day:
                continue

            depart_time, lat, lon, from_visit = _departure(index)
            estimate = estimates.get((from_visit, stop.visit_id, stop.movement_mode))
            if estimate is not None:
                distance = estimate.distance_km
                travel = estimate.travel_minutes
                cost_amount = estimate.cost_amount
                cost_currency = estimate.cost_currency
                source = estimate.source
                stale = estimate.stale
            else:
                distance = haversine_km(lat, lon, stop.lat, stop.lon)
                travel = travel_minutes(lat, lon, stop.lat, stop.lon, stop.movement_mode)
                cost_amount = None
                cost_currency = None
                source = "haversine-estimate"
                stale = False
            earliest = depart_time + timedelta(minutes=travel)
            arrive = resolve_arrival(earliest, stop, window_conflict=window_conflict)
            if arrive is None:
                continue

            depart = arrive + timedelta(minutes=stop.service_minutes)
            if depart > state.window.close:
                continue

            state.stops.append(
                StopPlan(
                    visit_id=stop.visit_id,
                    sequence=len(state.stops) + 1,
                    arrive=arrive,
                    depart=depart,
                    travel_minutes=travel,
                )
            )
            legs.append(
                TravelLegPlan(
                    from_visit_id=from_visit,
                    to_visit_id=stop.visit_id,
                    day_index=index,
                    mode=stop.movement_mode,
                    travel_minutes=travel,
                    distance_km=round(distance, 3),
                    cost_amount=cost_amount,
                    cost_currency=cost_currency,
                    source=source,
                    stale=stale,
                )
            )
            travel_total += travel
            value_placed += stop.business_value
            seen.add(visit_id)
            placed = True
            pointer = index
            break

        if placed:
            continue

        if not has_room:
            reason = UnplacedReason.CAPACITY_EXCEEDED
        elif window_conflict[0]:
            reason = UnplacedReason.TIME_WINDOW_INFEASIBLE
        else:
            reason = UnplacedReason.NO_FEASIBLE_SLOT
        unplaced.append(UnplacedStop(visit_id=visit_id, reason=reason))

    days = tuple(DayPlan(date=state.window.date, stops=tuple(state.stops)) for state in states)
    return ScheduleOutcome(
        days=days,
        legs=tuple(legs),
        unplaced=tuple(unplaced),
        travel_minutes_total=travel_total,
        value_placed=value_placed,
    )


def make_objective_value(problem: PlanningProblem, travel_total: int, value_placed: float) -> float:
    """目标值定义（唯一口径）：越小越好。

    `objective = 总行程分钟 × weights.travel − 已排停靠商业价值 × weights.value`
    """
    weights = problem.objective.weights
    raw = travel_total * weights.travel - value_placed * weights.value
    return round(raw, 6)


def deferred_to_next_day(problem: PlanningProblem, stop: StopInput) -> bool:
    """辅助判定：该停靠是否可能被推到窗口之后的日期（供解释层使用）。"""
    days = enumerate_days(problem.window, problem.timezone_home)
    return len(days) > 1