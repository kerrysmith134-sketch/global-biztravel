"""N3 求解器基准集回归：实例族的行为门禁与金目标基线。

对 `instance_family.py` 的 8 个具名实例锁三层性质：

1. **行为门禁**：可解实例必须全覆盖 + 独立验证器判可行；``min_solver="ortools"``
   的实例上启发式必须**诚实失败**（`SolverFailed`，不允许半成品计划）；不可行
   实例对两个求解器都必须失败。
2. **确定性**：同一实例重复求解，日程签名与 `problem_hash` 逐字节一致；
   生成器本身不含随机源，重复构建必须得到同一问题哈希。
3. **金目标基线**：`regression_baselines.json` 记录每个实例×求解器的目标值。
   求解器行为漂移（哪怕看起来是"改进"）都必须有意识地重新生成基线并在提交
   说明里写明原因——与契约样本门禁（ADR-0030）同一治理方式。

ortools 全族基准不进快速测试套件：大实例（16 停靠走廊、星形 12 站）按
"解数量预算"（`solver_budget_seconds`×40 个解）搜索，单次可达 15s+。全族
跑 `make bench`；这里只纳入本机 <600ms 的三个快实例（corridor_rhine_8 /
tight_windows_8 / dst_spring_berlin_6）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from instance_family import INFEASIBLE_INSTANCES, INSTANCES, SOLVABLE_INSTANCES
from travel_planner import SolverFailed, plan_trip
from travel_planner.contracts import SolutionStatus, SolverKind
from travel_planner.solvers import is_available

BASELINES_PATH = Path(__file__).parent / "regression_baselines.json"

#: 启发式可全覆盖求解的实例（``min_solver="heuristic"``）。
HEURISTIC_SOLVABLE = tuple(
    item for item in SOLVABLE_INSTANCES if item.min_solver == "heuristic"
)
#: 仅 ortools 可解的实例：启发式必须诚实失败。
ORTOOLS_ONLY = tuple(
    item for item in SOLVABLE_INSTANCES if item.min_solver == "ortools"
)
#: ortools 快实例（本机单次 <600ms），进入快速测试套件；慢实例走 `make bench`。
FAST_ORTOOLS_NAMES = ("corridor_rhine_8", "tight_windows_8", "dst_spring_berlin_6")
FAST_ORTOOLS = tuple(
    item for item in SOLVABLE_INSTANCES if item.name in FAST_ORTOOLS_NAMES
)

ORTOOLS_MARK = pytest.mark.skipif(not is_available(), reason="OR-Tools 未安装")


def _signature(result) -> tuple:
    return tuple(
        (str(stop.visit_id), stop.sequence, stop.arrive.isoformat())
        for day in result.days
        for stop in day.stops
    )


def _assert_feasible_plan(result, problem) -> None:
    """全覆盖 + 验证可行 + 状态 FEASIBLE + 行程非退化。"""
    assert result.validation.feasible is True
    assert result.unplaced == ()
    assert sorted(result.placed_visit_ids) == sorted(
        stop.visit_id for stop in problem.stops
    )
    assert result.status == SolutionStatus.FEASIBLE
    assert result.objective_value != 0.0
    assert sum(leg.travel_minutes for leg in result.legs) > 0


# ---------------------------------------------------------------------------
# 生成器确定性：无随机源，重复构建逐字节一致
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("instance", INSTANCES, ids=lambda item: item.name)
def test_instance_generator_is_byte_stable(instance):
    first = instance.problem
    second = instance.problem

    assert first.problem_hash() == second.problem_hash()


def test_baselines_cover_every_instance_and_solver():
    baselines = json.loads(BASELINES_PATH.read_text(encoding="utf-8"))
    entries = baselines["instances"]

    assert set(entries) == {item.name for item in INSTANCES}
    for name, entry in entries.items():
        assert set(entry) == {"heuristic", "ortools"}, name


# ---------------------------------------------------------------------------
# 启发式：可解实例全覆盖；"仅 ortools"实例诚实失败
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "instance", HEURISTIC_SOLVABLE, ids=lambda item: item.name
)
def test_heuristic_full_coverage(instance):
    problem = instance.problem
    result = plan_trip(problem, solver=SolverKind.HEURISTIC)

    _assert_feasible_plan(result, problem)


@pytest.mark.parametrize("instance", ORTOOLS_ONLY, ids=lambda item: item.name)
def test_heuristic_fails_honestly_on_ortools_only(instance):
    with pytest.raises(SolverFailed):
        plan_trip(instance.problem, solver=SolverKind.HEURISTIC)


@pytest.mark.parametrize(
    "instance", HEURISTIC_SOLVABLE, ids=lambda item: item.name
)
def test_heuristic_objectives_match_baselines(instance):
    baselines = json.loads(BASELINES_PATH.read_text(encoding="utf-8"))
    expected = baselines["instances"][instance.name]["heuristic"]

    result = plan_trip(instance.problem, solver=SolverKind.HEURISTIC)

    assert expected is not None
    assert round(result.objective_value, 6) == round(expected, 6)


@pytest.mark.parametrize(
    "instance", HEURISTIC_SOLVABLE, ids=lambda item: item.name
)
def test_heuristic_is_deterministic_across_repeated_solves(instance):
    problem = instance.problem
    first = plan_trip(problem, solver=SolverKind.HEURISTIC)
    second = plan_trip(problem, solver=SolverKind.HEURISTIC)

    assert _signature(first) == _signature(second)
    assert first.problem_hash == second.problem_hash


# ---------------------------------------------------------------------------
# ortools：快实例全覆盖（含"仅 ortools"成员）+ 确定性 + 金目标
# ---------------------------------------------------------------------------


@ORTOOLS_MARK
@pytest.mark.parametrize("instance", FAST_ORTOOLS, ids=lambda item: item.name)
def test_ortools_full_coverage(instance):
    problem = instance.problem
    result = plan_trip(problem, solver=SolverKind.ORTOOLS)

    _assert_feasible_plan(result, problem)


@ORTOOLS_MARK
@pytest.mark.parametrize("instance", FAST_ORTOOLS, ids=lambda item: item.name)
def test_ortools_is_deterministic_across_repeated_solves(instance):
    problem = instance.problem
    first = plan_trip(problem, solver=SolverKind.ORTOOLS)
    second = plan_trip(problem, solver=SolverKind.ORTOOLS)

    assert _signature(first) == _signature(second)
    assert first.problem_hash == second.problem_hash


@ORTOOLS_MARK
@pytest.mark.parametrize("instance", FAST_ORTOOLS, ids=lambda item: item.name)
def test_ortools_objectives_match_baselines(instance):
    baselines = json.loads(BASELINES_PATH.read_text(encoding="utf-8"))
    expected = baselines["instances"][instance.name]["ortools"]

    result = plan_trip(instance.problem, solver=SolverKind.ORTOOLS)

    assert expected is not None
    assert round(result.objective_value, 6) == round(expected, 6)


# ---------------------------------------------------------------------------
# 特定难度轴的语义断言
# ---------------------------------------------------------------------------


def test_locked_order_respected_across_days():
    instance = next(item for item in INSTANCES if item.name == "locked_order_10")
    problem = instance.problem
    locked_ids = problem.constraints.locked_order

    result = plan_trip(problem, solver=SolverKind.HEURISTIC)

    arrivals = {
        stop.visit_id: stop.arrive for day in result.days for stop in day.stops
    }
    first, second, third = (arrivals[vid] for vid in locked_ids)
    assert first < second < third


@ORTOOLS_MARK
def test_dst_instance_places_stops_within_windows_across_switch_day():
    """春跳日实例：所有到达时刻落进各自窗口，且跨 2026-03-29 切换日。"""
    instance = next(item for item in INSTANCES if item.name == "dst_spring_berlin_6")
    problem = instance.problem

    result = plan_trip(problem, solver=SolverKind.ORTOOLS)

    _assert_feasible_plan(result, problem)
    windows = {stop.visit_id: stop.time_windows for stop in problem.stops}
    for day in result.days:
        for stop in day.stops:
            window = windows[stop.visit_id][0]
            assert window.start <= stop.arrive <= window.end
    # 三个规划日都被用到（2+3+1 停靠），其中切换日 3-29 恰好三站。
    placed_days = sorted({day.date for day in result.days if day.stops})
    assert {day.isoformat() for day in placed_days} == {
        "2026-03-28",
        "2026-03-29",
        "2026-03-30",
    }
    stops_on_switch_day = [
        stop
        for day in result.days
        if day.date.isoformat() == "2026-03-29"
        for stop in day.stops
    ]
    assert len(stops_on_switch_day) == 3


@pytest.mark.parametrize("instance", INFEASIBLE_INSTANCES, ids=lambda item: item.name)
def test_infeasible_instance_fails_for_both_solvers(instance):
    problem = instance.problem

    with pytest.raises(SolverFailed):
        plan_trip(problem, solver=SolverKind.HEURISTIC)

    if is_available():
        with pytest.raises(SolverFailed):
            plan_trip(problem, solver=SolverKind.ORTOOLS)
