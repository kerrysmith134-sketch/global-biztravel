"""确定性启发式求解器：候选序列枚举 + 有界相邻交换改进。

- 无随机数、无墙钟：所有循环以**迭代次数**为界，跨机器结果一致。
- 作为 OR-Tools 不可用时的兜底（第 6.5 节），以及 OR-Tools 结果的对照下界。
"""

from __future__ import annotations

from uuid import UUID

from travel_planner.candidates import candidate_orders
from travel_planner.contracts import PlanningProblem, SolverKind
from travel_planner.outcome import SolveOutcome
from travel_planner.scheduling import ScheduleOutcome, build_schedule, make_objective_value

#: 每次相邻交换遍历次数上限 = `solver_budget_seconds × 该系数`
PASSES_PER_BUDGET_SECOND = 5
MAX_PASSES = 50


def _score(problem: PlanningProblem, outcome: ScheduleOutcome, order: tuple[UUID, ...]) -> tuple:
    """目标值优先，其次按顺序串字典序 —— 保证最优点唯一。"""
    objective = make_objective_value(problem, outcome.travel_minutes_total, outcome.value_placed)
    return (objective, tuple(str(vid) for vid in order))


def _improve(
    problem: PlanningProblem,
    order: tuple[UUID, ...],
    outcome: ScheduleOutcome,
) -> tuple[tuple[UUID, ...], ScheduleOutcome]:
    """相邻交换局部改进，**不交换两个被锁定顺序的停靠**，因而不会破坏 LOCKED_ORDER。"""
    locked = set(problem.constraints.locked_order)
    size = len(order)
    if size < 2:
        return order, outcome

    passes = max(1, min(problem.solver_budget_seconds * PASSES_PER_BUDGET_SECOND, MAX_PASSES))
    best_order = order
    best_outcome = outcome
    best_score = _score(problem, outcome, order)

    for _ in range(passes):
        improved = False
        for index in range(size - 1):
            left, right = best_order[index], best_order[index + 1]
            if left in locked and right in locked:
                continue
            candidate = list(best_order)
            candidate[index], candidate[index + 1] = candidate[index + 1], candidate[index]
            candidate_order = tuple(candidate)
            candidate_outcome = build_schedule(problem, candidate_order)
            candidate_score = _score(problem, candidate_outcome, candidate_order)
            if candidate_score < best_score:
                best_score = candidate_score
                best_order = candidate_order
                best_outcome = candidate_outcome
                improved = True
        if not improved:
            break

    return best_order, best_outcome


def solve(problem: PlanningProblem, eligible: tuple[UUID, ...]) -> SolveOutcome:
    orders = candidate_orders(problem, eligible)

    best_order: tuple[UUID, ...] = ()
    best_outcome: ScheduleOutcome | None = None
    best_score: tuple | None = None

    for order in orders:
        outcome = build_schedule(problem, order)
        score = _score(problem, outcome, order)
        if best_score is None or score < best_score:
            best_score = score
            best_order = order
            best_outcome = outcome

    if best_outcome is None:  # pragma: no cover - candidate_orders 至少返回一个顺序
        best_outcome = build_schedule(problem, eligible)

    best_order, best_outcome = _improve(problem, best_order, best_outcome)

    return SolveOutcome(
        solver=SolverKind.HEURISTIC,
        days=best_outcome.days,
        legs=best_outcome.legs,
        unplaced=best_outcome.unplaced,
        travel_minutes_total=best_outcome.travel_minutes_total,
        value_placed=best_outcome.value_placed,
        objective_value=make_objective_value(
            problem, best_outcome.travel_minutes_total, best_outcome.value_placed
        ),
        degraded=False,
    )