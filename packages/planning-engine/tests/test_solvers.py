"""两个求解器的行为与确定性测试。

重点锁死的历史缺陷（**回归目标**）：
- 早期版本在 OR-Tools 里用 `AddDisjunction` 把停靠变成"可选"，因商业价值折算
  远小于行程成本，"排 0 个停靠、目标值 0"成为最优解，却仍被判 `feasible=true`。
- 现在的不变量是：**成功解必须覆盖全部 eligible 停靠**；覆盖不满或模型无解，
  只能走 `SolverFailed` ⇒ 流水线兜底。

本文件**不**断言 `ortools ≤ heuristic`：两个求解器都可自由选择搜索策略，
互相之间没有最优性保证关系。
"""

from __future__ import annotations

import pytest
from fixtures import air_stop, at, four_city_problem, make_problem, two_city_problem, vid
from travel_planner import plan_trip
from travel_planner.contracts import MovementMode, SolverKind, TimeWindow
from travel_planner.feasibility import check_feasibility
from travel_planner.geometry import haversine_km, travel_minutes
from travel_planner.outcome import SolverFailed
from travel_planner.solvers import heuristic, is_available, ortools_solver
from travel_planner.solvers.ortools_solver import _require_full_coverage

ORTOOLS_MARK = pytest.mark.skipif(not is_available(), reason="OR-Tools 未安装")

#: 到达时刻永远落不进自身时间窗的停靠（柏林需 840 分钟航空，窗口只给 1 小时）。
IMPOSSIBLE_WINDOW = (TimeWindow(start=at(10, 9, 0), end=at(10, 10, 0)),)


def _eligible(problem):
    return check_feasibility(problem).eligible_stop_ids


def _signature(outcome):
    return tuple(
        (str(stop.visit_id), stop.sequence, stop.arrive.isoformat(), stop.travel_minutes)
        for day in outcome.days
        for stop in day.stops
    )


def _placed_count(outcome) -> int:
    return sum(len(day.stops) for day in outcome.days)


def _impossible_problem():
    return make_problem(
        (air_stop(1, "berlin", service_minutes=90, windows=IMPOSSIBLE_WINDOW),)
    )


# ---------------------------------------------------------------------------
# 覆盖不变量（纯函数，与 OR-Tools 是否安装无关）
# ---------------------------------------------------------------------------


def test_coverage_guard_accepts_full_placement():
    _require_full_coverage((vid(1), vid(2)), (vid(1), vid(2)))


def test_coverage_guard_rejects_partial_placement():
    with pytest.raises(SolverFailed) as excinfo:
        _require_full_coverage((vid(1),), (vid(1), vid(2)))
    assert "00000000-0000-4000-8000-000000000002" in str(excinfo.value)


def test_coverage_guard_rejects_degenerate_zero_placement():
    """历史退化解的形状：一个都没排。它必须被显式拒绝。"""
    with pytest.raises(SolverFailed):
        _require_full_coverage((), (vid(1), vid(2), vid(3), vid(4)))


# ---------------------------------------------------------------------------
# OR-Tools
# ---------------------------------------------------------------------------


@ORTOOLS_MARK
def test_ortools_places_every_eligible_stop():
    problem = four_city_problem()
    eligible = _eligible(problem)

    outcome = ortools_solver.solve(problem, eligible)

    assert outcome.solver is SolverKind.ORTOOLS
    assert outcome.degraded is False
    assert _placed_count(outcome) == len(eligible) == 4
    assert outcome.unplaced == ()
    # 退化解（全不排、行程 0、目标 0）在数值上必须不可能出现。
    assert outcome.travel_minutes_total > 0
    assert outcome.objective_value != 0.0


@ORTOOLS_MARK
def test_ortools_is_deterministic():
    problem = four_city_problem()
    eligible = _eligible(problem)

    signatures = {_signature(ortools_solver.solve(problem, eligible)) for _ in range(5)}

    assert len(signatures) == 1


@ORTOOLS_MARK
def test_ortools_raises_instead_of_partial_placement():
    """到达时刻永远落不进时间窗 ⇒ 模型无解 ⇒ 必须抛 `SolverFailed`。

    绝不允许"排不进去就少排几个"这种静默降级——那是历史 bug 的同一条通路。
    """
    problem = _impossible_problem()
    eligible = _eligible(problem)
    assert eligible == (vid(1),)

    with pytest.raises(SolverFailed):
        ortools_solver.solve(problem, eligible)


@ORTOOLS_MARK
def test_ortools_empty_eligible_returns_empty_schedule():
    problem = make_problem(())
    outcome = ortools_solver.solve(problem, ())

    assert _placed_count(outcome) == 0
    assert outcome.unplaced == ()
    assert outcome.travel_minutes_total == 0
    assert outcome.objective_value == 0.0
    assert len(outcome.days) == 7


@ORTOOLS_MARK
def test_ortools_honours_locked_order():
    problem = four_city_problem(locked=(vid(1), vid(4), vid(3)))
    eligible = _eligible(problem)

    outcome = ortools_solver.solve(problem, eligible)

    arrivals = {stop.visit_id: stop.arrive for day in outcome.days for stop in day.stops}
    assert arrivals[vid(1)] < arrivals[vid(4)] < arrivals[vid(3)]


@ORTOOLS_MARK
def test_ortools_travel_minutes_follow_shared_geometry():
    """求解器输出的行程分钟必须逐段等于共享几何模型的取值（取目的地模式）。"""
    problem = two_city_problem()
    eligible = _eligible(problem)

    outcome = ortools_solver.solve(problem, eligible)
    stops = [stop for day in outcome.days for stop in day.stops]

    assert len(stops) == 2
    lat, lon = problem.origin.lat, problem.origin.lon
    for stop in stops:
        source = problem.stop_by_id(stop.visit_id)
        assert stop.travel_minutes == travel_minutes(
            lat, lon, source.lat, source.lon, source.movement_mode
        )
        lat, lon = source.lat, source.lon


# ---------------------------------------------------------------------------
# 启发式兜底
# ---------------------------------------------------------------------------


def test_heuristic_places_every_eligible_stop():
    problem = four_city_problem()
    eligible = _eligible(problem)

    outcome = heuristic.solve(problem, eligible)

    assert outcome.solver is SolverKind.HEURISTIC
    assert _placed_count(outcome) == len(eligible)
    assert outcome.unplaced == ()
    assert outcome.travel_minutes_total > 0


def test_heuristic_is_deterministic():
    problem = four_city_problem()
    eligible = _eligible(problem)

    signatures = {_signature(heuristic.solve(problem, eligible)) for _ in range(5)}

    assert len(signatures) == 1


def test_heuristic_reports_unplaced_for_unreachable_window():
    problem = _impossible_problem()
    outcome = heuristic.solve(problem, _eligible(problem))

    assert _placed_count(outcome) == 0
    assert [item.reason.value for item in outcome.unplaced] == ["TIME_WINDOW_INFEASIBLE"]


def test_heuristic_empty_eligible():
    outcome = heuristic.solve(make_problem(()), ())
    assert outcome.days
    assert outcome.objective_value == 0.0


# ---------------------------------------------------------------------------
# 确定性规模护栏（ADR-0031）：ortools 只服务小规模实例
# ---------------------------------------------------------------------------


@ORTOOLS_MARK
def test_ortools_refuses_above_scale_guard():
    """n > `ORTOOLS_MAX_ELIGIBLE` 时 ortools 必须入口处拒绝（`SolverFailed`）。

    依据：DFS+GUIDED_LOCAL_SEARCH 的单解成本随规模指数增长（N3 基准集实测，
    12 站走廊单次 39s、16 站分钟级），而 `solver_budget_seconds` 是解数量
    预算、不是墙钟。护栏按**问题内容**判定，跨机器一致，不破坏确定性。
    """
    from instance_family import corridor_rhine_12

    problem = corridor_rhine_12()
    assert len(problem.stops) > ortools_solver.ORTOOLS_MAX_ELIGIBLE

    with pytest.raises(SolverFailed) as excinfo:
        ortools_solver.solve(problem, _eligible(problem))

    assert "scale guard" in str(excinfo.value)


@ORTOOLS_MARK
def test_ortools_refusal_falls_back_to_heuristic_degraded():
    """护栏拒绝后流水线沿既有兜底链落到启发式：结果可行、全覆盖、`degraded=True`。"""
    from instance_family import corridor_rhine_12

    problem = corridor_rhine_12()

    result = plan_trip(problem, solver=SolverKind.ORTOOLS)

    assert result.solver is SolverKind.HEURISTIC
    assert result.degraded is True
    assert result.validation.feasible is True
    assert sorted(result.placed_visit_ids) == sorted(
        stop.visit_id for stop in problem.stops
    )


@ORTOOLS_MARK
def test_ortools_scale_guard_accepts_boundary_size():
    """恰好 `ORTOOLS_MAX_ELIGIBLE` 个 eligible 的实例仍由 ortools 求解（边界不收缩）。"""
    from instance_family import global_air_10

    problem = global_air_10()
    eligible = _eligible(problem)
    assert len(eligible) == ortools_solver.ORTOOLS_MAX_ELIGIBLE

    outcome = ortools_solver.solve(problem, eligible)

    assert _placed_count(outcome) == len(eligible)


# ---------------------------------------------------------------------------
# 两个求解器共同遵守的口径
# ---------------------------------------------------------------------------


def test_long_haul_segments_are_never_faster_than_great_circle():
    """洲际段行程分钟不可能短于大圆航线的直飞时间（几何模型上界）。"""
    problem = four_city_problem()
    eligible = _eligible(problem)

    for solver in (heuristic, ortools_solver):
        if solver is ortools_solver and not is_available():
            continue
        outcome = solver.solve(problem, eligible)
        for day in outcome.days:
            for stop in day.stops:
                source = problem.stop_by_id(stop.visit_id)
                great_circle = haversine_km(
                    problem.origin.lat, problem.origin.lon, source.lat, source.lon
                )
                assert great_circle > 8000
                # 航空 700 km/h + 120 分钟地面超额 ⇒ 必然超过 120 分钟。
                assert stop.travel_minutes > 120


def test_movement_mode_does_not_change_eligibility():
    ground = make_problem((air_stop(1, "berlin", movement_mode=MovementMode.GROUND),))
    air = make_problem((air_stop(1, "berlin", movement_mode=MovementMode.AIR),))

    assert check_feasibility(ground).eligible_stop_ids == (vid(1),)
    assert check_feasibility(air).eligible_stop_ids == (vid(1),)