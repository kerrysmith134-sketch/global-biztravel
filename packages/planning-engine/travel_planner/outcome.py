"""求解器内部结果载体（非公开契约，不跨 API 边界）。"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from travel_planner.contracts import DayPlan, PlanningProblem, SolverKind, UnplacedStop


class SolverUnavailable(RuntimeError):
    """主求解器不可用（依赖缺失）——流水线据此降级到启发式。"""


class SolverFailed(RuntimeError):
    """主求解器可用但未产出可行解——流水线据此降级到启发式。"""


@dataclass(frozen=True)
class SolveOutcome:
    """一次求解的原始产出；`objective_value` 由目标函数定义统一计算。"""

    solver: SolverKind
    days: tuple[DayPlan, ...]
    unplaced: tuple[UnplacedStop, ...]
    travel_minutes_total: int
    value_placed: float
    objective_value: float
    degraded: bool = False


def merge_unplaced(
    problem: PlanningProblem, *groups: tuple[UnplacedStop, ...]
) -> tuple[UnplacedStop, ...]:
    """合并多来源的"未排入"结果，去重并按**原停靠顺序**输出。

    顺序只取决于问题本身，因此相同输入必得相同输出（第 6.4 节确定性要求）。
    """
    order: dict[UUID, int] = {stop.visit_id: index for index, stop in enumerate(problem.stops)}
    seen: set[UUID] = set()
    merged: list[UnplacedStop] = []
    for group in groups:
        for item in group:
            if item.visit_id in seen or item.visit_id not in order:
                continue
            seen.add(item.visit_id)
            merged.append(item)
    merged.sort(key=lambda item: order[item.visit_id])
    return tuple(merged)