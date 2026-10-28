"""可行性预检：在进入求解器之前剔除**确定不可排**的停靠。

预检只处理与排序无关的硬约束，不做任何"近似判断"：
1. 时间窗与出行窗口完全不相交 ⇒ `TIME_WINDOW_INFEASIBLE`；
2. 总容量（`max_stops_per_day × 天数`）不足，超出部分按"低优先级、低价值"顺序剔除
   ⇒ `CAPACITY_EXCEEDED`；不可动停靠优先占用容量。
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from travel_planner.contracts import (
    PlanningProblem,
    StopInput,
    UnplacedReason,
    UnplacedStop,
)
from travel_planner.days import enumerate_days


@dataclass(frozen=True)
class FeasibilityOutcome:
    """可进入求解的停靠，以及预检阶段已判定不可排的停靠。"""

    eligible_stop_ids: tuple[UUID, ...]
    pre_unplaced: tuple[UnplacedStop, ...]
    day_count: int


def rank_key(stop: StopInput) -> tuple[int, float, str]:
    """确定性排序键：优先级升序（1 最优先）→ 商业价值降序 → visit_id 字典序。

    字典序兜底让"完全同权"的停靠也有唯一顺序，避免集合迭代顺序引入不确定性。
    """
    return (stop.priority, -stop.business_value, str(stop.visit_id))


def _window_intersects_trip(problem: PlanningProblem, stop: StopInput) -> bool:
    if not stop.time_windows:
        return True
    trip_start = problem.window.start
    trip_end = problem.window.end
    for window in stop.time_windows:
        if window.end >= trip_start and window.start <= trip_end:
            return True
    return False


def check_feasibility(problem: PlanningProblem) -> FeasibilityOutcome:
    days = enumerate_days(problem.window, problem.timezone_home)
    capacity = problem.constraints.max_stops_per_day * len(days)

    pre_unplaced: list[UnplacedStop] = []
    eligible: list[StopInput] = []

    for stop in problem.stops:
        if _window_intersects_trip(problem, stop):
            eligible.append(stop)
        else:
            pre_unplaced.append(
                UnplacedStop(visit_id=stop.visit_id, reason=UnplacedReason.TIME_WINDOW_INFEASIBLE)
            )

    immovable = set(problem.constraints.immovable_stop_ids)
    required = [stop for stop in eligible if stop.visit_id in immovable]
    optional = [stop for stop in eligible if stop.visit_id not in immovable]

    if len(required) > capacity:
        # 不可动停靠本身已超容：按排序保留前 capacity 个，其余标记容量超限。
        ordered_required = sorted(required, key=rank_key)
        excess = ordered_required[capacity:]
        pre_unplaced.extend(
            UnplacedStop(visit_id=stop.visit_id, reason=UnplacedReason.CAPACITY_EXCEEDED)
            for stop in excess
        )
        kept_required = ordered_required[:capacity]
    else:
        kept_required = required

    # 可选停靠按排名键排序，前 `room` 个留在容量内，其余标记容量超限。
    room = max(0, capacity - len(kept_required))
    dropped_optional = sorted(optional, key=rank_key)[room:]

    pre_unplaced.extend(
        UnplacedStop(visit_id=stop.visit_id, reason=UnplacedReason.CAPACITY_EXCEEDED)
        for stop in dropped_optional
    )

    dropped_ids = {item.visit_id for item in pre_unplaced}
    eligible_ids = tuple(
        stop.visit_id for stop in problem.stops if stop.visit_id not in dropped_ids
    )

    # 未排原因按"原停靠顺序"输出，保证相同输入输出顺序一致。
    pre_unplaced.sort(key=lambda item: _stop_position(problem, item.visit_id))

    return FeasibilityOutcome(
        eligible_stop_ids=eligible_ids,
        pre_unplaced=tuple(pre_unplaced),
        day_count=len(days),
    )


def _stop_position(problem: PlanningProblem, visit_id: UUID) -> int:
    for index, stop in enumerate(problem.stops):
        if stop.visit_id == visit_id:
            return index
    return len(problem.stops)