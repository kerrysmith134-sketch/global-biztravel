"""流水线测试：六段式流程、兜底链、降级语义与确定性。

冻结的行为约定（第 6.5 节）：
- `degraded=True` **只**表示"没用上主选求解器"，绝不表示"结果不可行"；
- `validation.feasible` **只**由独立验证器写入；
- 主求解器不可用、未产出解、或产出覆盖不满的解，一律落到兜底求解器；
- 任一验证规则 FAIL ⇒ `feasible=False`，且不被 `degraded` 掩盖。
"""

from __future__ import annotations

import pytest
from fixtures import CITIES, air_stop, at, four_city_problem, make_problem, vid
from travel_planner.contracts import (
    HASH_PREFIX,
    RULE_STOP_COVERAGE,
    VALIDATION_RULES,
    CheckStatus,
    SolutionStatus,
    SolverKind,
    TimeWindow,
    UnplacedReason,
)
from travel_planner.outcome import SolverFailed, SolverUnavailable
from travel_planner.pipeline import (
    PIPELINE_STAGES,
    plan_trip,
    preferred_solver,
    resolve_solver,
)
from travel_planner.solvers import heuristic, ortools_solver

#: 到达时刻永远落不进时间窗（柏林需 840 分钟航空，窗口只给 1 小时）。
IMPOSSIBLE_WINDOW = (TimeWindow(start=at(10, 9, 0), end=at(10, 10, 0)),)


def _impossible_problem(**overrides):
    return make_problem(
        (air_stop(1, "berlin", service_minutes=90, windows=IMPOSSIBLE_WINDOW),),
        **overrides,
    )


def _rule_status(result, rule: str) -> CheckStatus:
    for check in result.validation.checks:
        if check.rule == rule:
            return check.status
    raise AssertionError(f"rule {rule} missing")





# ---------------------------------------------------------------------------
# 求解器选择
# ---------------------------------------------------------------------------


def test_pipeline_stages_are_frozen():
    assert PIPELINE_STAGES == (
        "INPUT_NORMALIZER",
        "FEASIBILITY_CHECKER",
        "CANDIDATE_GENERATOR",
        "OPTIMIZATION_SOLVER",
        "INDEPENDENT_VALIDATOR",
        "EXPLANATION_GENERATOR",
    )


def test_resolve_solver_defaults_to_preferred():
    assert resolve_solver(None) is preferred_solver()
    assert resolve_solver(None) is (
        SolverKind.ORTOOLS if ortools_solver.is_available() else SolverKind.HEURISTIC
    )


def test_resolve_solver_keeps_explicit_choice():
    assert resolve_solver(SolverKind.HEURISTIC) is SolverKind.HEURISTIC
    assert resolve_solver(SolverKind.ORTOOLS) is SolverKind.ORTOOLS


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_produces_validated_result():
    problem = four_city_problem()

    result = plan_trip(problem)

    assert result.trip_id == problem.trip_id
    assert result.problem_hash == problem.problem_hash()
    assert result.problem_hash.startswith(HASH_PREFIX)
    assert result.solver is preferred_solver()
    assert result.degraded is False
    assert result.validation.feasible is True
    assert result.validation.failures == ()
    assert [check.rule for check in result.validation.checks] == list(VALIDATION_RULES)
    assert len(result.placed_visit_ids) == 4
    assert result.unplaced == ()
    assert result.objective_value != 0.0


def test_explicit_heuristic_is_not_degraded():
    problem = four_city_problem()

    result = plan_trip(problem, solver=SolverKind.HEURISTIC)

    assert result.solver is SolverKind.HEURISTIC
    assert result.degraded is False
    assert len(result.placed_visit_ids) == 4


def test_result_exposes_status_and_travel_legs() -> None:
    result = plan_trip(four_city_problem())
    assert result.status is SolutionStatus.FEASIBLE
    assert len(result.legs) == 4
    assert result.legs[0].from_visit_id is None
    assert all(leg.distance_km >= 0 and leg.travel_minutes >= 0 for leg in result.legs)
    assert all(leg.cost_amount is None for leg in result.legs)  # 未知费用不是 0


def test_precheck_removals_mark_result_partial() -> None:
    problem = make_problem(
        four_city_problem().stops,
        start=at(9, 9, 0),
        end=at(9, 18, 0),
        max_stops_per_day=1,
        origin=CITIES["amsterdam"],
    )
    result = plan_trip(problem)
    assert result.status is SolutionStatus.PARTIAL


def test_empty_stop_set_is_feasible_with_no_unplaced():
    result = plan_trip(make_problem(()))

    assert result.validation.feasible is True
    assert result.placed_visit_ids == ()
    assert result.unplaced == ()
    assert result.objective_value == 0.0


def test_determinism_repeated_calls_are_byte_identical():
    problem = four_city_problem()

    first = plan_trip(problem).model_dump(mode="json")
    second = plan_trip(problem).model_dump(mode="json")

    assert first == second


def test_explanation_ref_is_passed_through_untouched():
    result = plan_trip(four_city_problem(), explanation_ref="explain://plan/42")

    assert result.explanation_ref == "explain://plan/42"


# ---------------------------------------------------------------------------
# 兜底链与 degraded 语义
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("error", [SolverUnavailable("missing binding"), SolverFailed("no solution")])
def test_primary_failure_falls_back_to_heuristic(monkeypatch, error):
    def boom(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(ortools_solver, "solve", boom)

    result = plan_trip(four_city_problem(), solver=SolverKind.ORTOOLS)

    assert result.solver is SolverKind.HEURISTIC
    assert result.degraded is True
    # 降级不等于不可行：兜底解仍须通过独立验证。
    assert result.validation.feasible is True
    assert len(result.placed_visit_ids) == 4


def test_no_solver_can_serve_raises_solver_failed(monkeypatch):
    def boom(*_args, **_kwargs):
        raise SolverFailed("both solvers down")

    monkeypatch.setattr(ortools_solver, "solve", boom)
    monkeypatch.setattr(heuristic, "solve", boom)

    with pytest.raises(SolverFailed):
        plan_trip(four_city_problem(), solver=SolverKind.ORTOOLS)


def test_unreachable_window_fails_instead_of_partial_plan():
    """eligible 停靠排不进去 ⇒ 覆盖不变量不成立 ⇒ 求解失败。

    设计 §6.4：物化后仍有人没排进去就视同求解失败。绝不能返回"只排了一部分/一个都没排"
    却标 feasible 的计划——那正是历史上出现过的静默错误。
    """
    with pytest.raises(SolverFailed):
        plan_trip(_impossible_problem())


def test_degenerate_zero_placement_fails_solving():
    """历史 bug 的回归锁：不允许出现 "solver=ortools + degraded=False + 什么都没排"。

    现在的出口不是"降级后交一份空计划"，而是两层求解器都无法覆盖 eligible ⇒ SolverFailed。
    """
    with pytest.raises(SolverFailed):
        plan_trip(_impossible_problem(), solver=SolverKind.ORTOOLS)


# ---------------------------------------------------------------------------
# 预检剔除与验证器联动
# ---------------------------------------------------------------------------


def test_capacity_exceeded_drops_lowest_value_stops():
    """1 天窗口 + 每日上限 1 ⇒ 容量 1；按排名键保留价值最高的 amsterdam 停靠。

    出发地也设在阿姆斯特丹：被保留的停靠行程时间为 0，因此本用例只检验"容量剔除"
    这一条不变量。若沿用"上海出发 + 9 小时窗口"的组合，跨洲航段（约 840 分钟）
    本身排不进当天窗口，被保留的停靠会转成 `NO_FEASIBLE_SLOT`，把容量语义掩盖掉。
    """
    stops = (
        air_stop(1, "munich", service_minutes=90, business_value=120.0),
        air_stop(2, "frankfurt", service_minutes=90, business_value=80.0),
        air_stop(3, "amsterdam", service_minutes=90, business_value=150.0),
        air_stop(4, "rotterdam", service_minutes=90, business_value=60.0),
    )
    problem = make_problem(
        stops,
        start=at(9, 9, 0),
        end=at(9, 18, 0),
        max_stops_per_day=1,
        origin=CITIES["amsterdam"],
    )

    result = plan_trip(problem)

    dropped = {item.visit_id for item in result.unplaced}
    assert dropped == {vid(1), vid(2), vid(4)}
    assert all(item.reason is UnplacedReason.CAPACITY_EXCEEDED for item in result.unplaced)
    assert result.placed_visit_ids == (vid(3),)
    assert result.validation.feasible is True
    assert _rule_status(result, RULE_STOP_COVERAGE) is CheckStatus.PASS


def test_immovable_stop_left_unplaced_fails_solving():
    """不可动停靠排不进去 ⇒ 覆盖不变量失败 ⇒ 求解失败（不会交出一份不可行计划）。"""
    problem = _impossible_problem(immovable=(vid(1),))

    with pytest.raises(SolverFailed):
        plan_trip(problem)


def test_precheck_removals_are_merged_into_unplaced_in_original_order():
    problem = make_problem(
        four_city_problem().stops,
        start=at(9, 9, 0),
        end=at(9, 18, 0),
        max_stops_per_day=1,
        # 出发地放在被容量保留的城市，避免跨洲航段本身排不进当天窗口。
        origin=CITIES["amsterdam"],
    )

    result = plan_trip(problem)

    order = {stop.visit_id: index for index, stop in enumerate(problem.stops)}
    positions = [order[item.visit_id] for item in result.unplaced]
    assert positions == sorted(positions)