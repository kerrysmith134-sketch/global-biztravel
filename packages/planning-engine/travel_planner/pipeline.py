"""规划流水线：把 `PlanningProblem` 变成**经过独立验证**的 `PlanningResult`。

对应设计文档第 6.1 节的六段式流水线：

1. Input Normalizer —— 输入已由 `PlanningProblem` 契约在构造时规范化；
2. Feasibility Checker —— `feasibility.check_feasibility` 剔除确定不可排的停靠；
3. Candidate Generator —— `candidates.candidate_orders` 提供确定性候选序列；
4. Optimization Solver —— OR-Tools 主选，自研确定性启发式兜底；
5. Independent Validator —— `validator.validate` 独立复算 9 条规则；
6. Explanation Generator —— 由上层用 `explanation_ref` 关联（引擎本身不生成自然语言）。

不变量 I1 的落地：`PlanningResult.validation.feasible` **只能**来自第 5 步。
求解器哪怕返回了"看起来排满"的顺序，只要验证器有一条规则 FAIL，结果就是不可行，
且 `degraded` 不会掩盖这一点。

本模块无 IO、不碰数据库、不读时钟、不生成随机数。
"""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from travel_planner.contracts import (
    PlanningProblem,
    PlanningResult,
    SolutionStatus,
    SolverKind,
)
from travel_planner.feasibility import check_feasibility
from travel_planner.outcome import SolveOutcome, SolverFailed, SolverUnavailable, merge_unplaced
from travel_planner.solvers import heuristic, is_available, ortools_solver
from travel_planner.validator import validate

#: 流水线阶段名（冻结顺序），供 API 层在任务进度与解释中稳定引用。
PIPELINE_STAGES: tuple[str, ...] = (
    "INPUT_NORMALIZER",
    "FEASIBILITY_CHECKER",
    "CANDIDATE_GENERATOR",
    "OPTIMIZATION_SOLVER",
    "INDEPENDENT_VALIDATOR",
    "EXPLANATION_GENERATOR",
)

#: 主求解器不可用时依次尝试的求解器（顺序冻结：主选 → 兜底）。
_FALLBACK_CHAIN: tuple[SolverKind, ...] = (SolverKind.ORTOOLS, SolverKind.HEURISTIC)


def preferred_solver() -> SolverKind:
    """当前环境下的默认主求解器：OR-Tools 可用则用之，否则直接用启发式。"""
    return SolverKind.ORTOOLS if is_available() else SolverKind.HEURISTIC


def resolve_solver(kind: SolverKind | None) -> SolverKind:
    """把调用方的"建议求解器"解析为实际要优先尝试的求解器。

    `kind=None` 表示交由引擎决定；显式传入 `ORTOOLS` 时也**保留兜底链**，
    因为"要求用 OR-Tools"是偏好而非硬约束（第 6.5 节：超预算或不可用时降级）。
    """
    if kind is None:
        return preferred_solver()
    return kind


def _attempt_chain(primary: SolverKind) -> tuple[SolverKind, ...]:
    chain = _FALLBACK_CHAIN if primary is SolverKind.ORTOOLS else (primary,)
    return chain


def _run_solver(kind: SolverKind, problem: PlanningProblem, eligible: tuple[UUID, ...]) -> SolveOutcome:
    if kind is SolverKind.ORTOOLS:
        return ortools_solver.solve(problem, eligible)
    return heuristic.solve(problem, eligible)


def plan_trip(
    problem: PlanningProblem,
    *,
    solver: SolverKind | None = None,
    explanation_ref: str | None = None,
) -> PlanningResult:
    """求解并**独立验证**一次行程规划。

    - 相同 `PlanningProblem` 必得相同 `PlanningResult`（含 `validation`）；
    - 主求解器不可用或未产出可行解时降级到启发式，并把 `degraded` 置为 `True`，
      但**绝不**因此把不可行结果标成可行；
    - 兜底求解器也失败时抛 `SolverFailed`（上层据此把任务标为 FAILED 并留错误信息）。
    """
    feasibility = check_feasibility(problem)
    eligible = feasibility.eligible_stop_ids

    primary = resolve_solver(solver)
    chain = _attempt_chain(primary)

    outcome: SolveOutcome | None = None
    failures: list[str] = []

    for kind in chain:
        try:
            candidate = _run_solver(kind, problem, eligible)
        except (SolverUnavailable, SolverFailed) as exc:
            failures.append(f"{kind.value}: {type(exc).__name__}: {exc}")
            continue
        # 覆盖不变量（第 6.4 节）：成功解必须覆盖**全部** eligible 停靠。物化后仍有人没排进去
        # ⇒ 视同求解失败，沿兜底链降级；否则会出现"计划看起来成功、实际什么都没排"的静默错误。
        placed = {stop.visit_id for day in candidate.days for stop in day.stops}
        missing = [visit_id for visit_id in eligible if visit_id not in placed]
        if missing:
            failures.append(
                f"{kind.value}: {len(missing)}/{len(eligible)} 个 eligible 停靠未被排入"
            )
            continue
        outcome = candidate
        break

    if outcome is None:  # 兜底求解器也拿不出完整解：按设计视为求解失败
        raise SolverFailed(
            "no solver produced a full-coverage result: " + "; ".join(failures)
        )

    if outcome.solver is not primary:
        outcome = replace(outcome, degraded=True)

    unplaced = merge_unplaced(problem, feasibility.pre_unplaced, outcome.unplaced)

    report = validate(
        problem=problem,
        days=outcome.days,
        unplaced=unplaced,
        objective_value=outcome.objective_value,
    )
    if not report.feasible:
        status = SolutionStatus.INFEASIBLE
    elif unplaced:
        status = SolutionStatus.PARTIAL
    else:
        status = SolutionStatus.FEASIBLE

    return PlanningResult(
        trip_id=problem.trip_id,
        problem_hash=problem.problem_hash(),
        solver=outcome.solver,
        days=outcome.days,
        legs=outcome.legs,
        objective_value=outcome.objective_value,
        validation=report,
        unplaced=unplaced,
        status=status,
        degraded=outcome.degraded,
        explanation_ref=explanation_ref,
    )


__all__ = ["PIPELINE_STAGES", "plan_trip", "preferred_solver", "resolve_solver"]