#!/usr/bin/env python
"""规划引擎基准（S2 性能预算 / N3 求解器基准集与回归）。

跑 `instance_family.py` 的 8 个具名实例（覆盖规模/地理形态/时间窗紧度/每日容量/
锁定顺序/夏令时/不可行七个难度轴），分别测量启发式与 OR-Tools 的耗时，输出
p50/p95/max 与目标值，并做四类门禁：

- **覆盖**：成功解必须覆盖全部 eligible 停靠（成功解不允许丢停靠）；
- **能力声明**：``min_solver="ortools"`` 的实例上启发式必须诚实失败；
  ``kind="infeasible"`` 的实例两个求解器都必须 `SolverFailed`；
- **确定性**：两次运行的 ``problem_hash`` 一致；
- **金目标**：目标值与 `regression_baselines.json` 一致（漂移即失败，与
  `gen_regression_baselines.py --check` 同一口径）。

预算语义提醒：``solver_budget_seconds`` 是**解数量预算**（×40 个解，见
`ortools_solver.SOLUTION_LIMIT_PER_BUDGET_SECOND`），不是墙钟——大实例单次
ortools 可达分钟级，属确定性设计而非泄漏。可用 ``--skip-ortools`` 快跑。

用法::

    .venv/bin/python scripts/bench_planning.py [--rounds 20] [--ortools-rounds 1]
        [--skip-ortools]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "planning-engine"))
sys.path.insert(0, str(ROOT / "packages" / "planning-engine" / "tests"))

import instance_family  # noqa: E402
from travel_planner import SolverFailed, plan_trip  # noqa: E402
from travel_planner.contracts import SolverKind  # noqa: E402

BASELINES_PATH = ROOT / "packages" / "planning-engine" / "tests" / "regression_baselines.json"

KIND_HEURISTIC = SolverKind.HEURISTIC
KIND_ORTOOLS = SolverKind.ORTOOLS


def p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
    return ordered[index]


def measure(problem, solver: SolverKind, rounds: int) -> dict | None:
    """跑 N 轮返回耗时统计与终局结果；`SolverFailed` 返回 None。"""
    latencies: list[float] = []
    hashes: set[str] = set()
    last = None
    for _ in range(rounds):
        started = time.perf_counter()
        try:
            last = plan_trip(problem, solver=solver)
        except SolverFailed:
            return None
        latencies.append((time.perf_counter() - started) * 1000)
        hashes.add(last.problem_hash)
    assert last is not None
    return {
        "latencies": latencies,
        "result": last,
        "objective": last.objective_value,
        "placed": len(last.placed_visit_ids),
        "feasible": last.validation.feasible,
        "stable_hash": len(hashes) == 1,
    }


def golden_objective(name: str, solver: SolverKind) -> float | None:
    baselines = json.loads(BASELINES_PATH.read_text(encoding="utf-8"))
    key = "heuristic" if solver is KIND_HEURISTIC else "ortools"
    return baselines["instances"][name][key]


def run_instance(item, heuristic_rounds: int, ortools_rounds: int, skip_ortools: bool) -> bool:
    """测一个实例的两轨，打印行；返回是否全部通过门禁。"""
    problem = item.problem
    total = len(problem.stops)
    ok = True
    cells: list[str] = []

    for solver, rounds in (
        (KIND_HEURISTIC, heuristic_rounds),
        (KIND_ORTOOLS, 0 if skip_ortools else ortools_rounds),
    ):
        if rounds <= 0:
            cells.append(f"{solver.value}=skip")
            continue
        stats = measure(problem, solver, rounds)
        expected_fail = (
            (solver is KIND_HEURISTIC and item.min_solver == "ortools")
            or item.kind == "infeasible"
        )
        if expected_fail:
            if stats is not None:
                print(
                    f"[FAIL] {item.name}/{solver.value}: 按能力声明应当失败，"
                    f"却得到了 objective={stats['objective']}"
                )
                ok = False
            else:
                cells.append(f"{solver.value}=诚实失败✓")
            continue

        if stats is None:
            print(f"[FAIL] {item.name}/{solver.value}: 意外求解失败（应全覆盖）")
            ok = False
            continue

        problems: list[str] = []
        if not stats["feasible"]:
            problems.append("被独立验证器判为不可行")
        if stats["placed"] != total:
            problems.append(f"覆盖不完整 {stats['placed']}/{total}")
        if not stats["stable_hash"]:
            problems.append("problem_hash 跨轮不稳定")
        golden = golden_objective(item.name, solver)
        if golden is None:
            problems.append("基线缺失（gen_regression_baselines.py 生成）")
        elif round(stats["objective"], 6) != round(golden, 6):
            problems.append(f"目标值漂移：基线 {golden} 实测 {stats['objective']}")

        if problems:
            print(f"[FAIL] {item.name}/{solver.value}: " + "；".join(problems))
            ok = False

        latencies = stats["latencies"]
        cells.append(
            f"{solver.value}: p50={statistics.median(latencies):.1f}ms "
            f"p95={p95(latencies):.1f}ms max={max(latencies):.1f}ms "
            f"obj={stats['objective']:.1f} 覆盖={stats['placed']}/{total}"
        )

    print(f"[{item.name}] 轴={item.axis}  " + "  |  ".join(cells))
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="规划引擎基准（N3 实例族）")
    parser.add_argument("--rounds", type=int, default=20, help="启发式轮数（默认 20）")
    parser.add_argument(
        "--ortools-rounds", type=int, default=1, help="OR-Tools 轮数（默认 1；大实例单次可达分钟级）"
    )
    parser.add_argument("--skip-ortools", action="store_true", help="只跑启发式（快）")
    args = parser.parse_args(argv)
    heuristic_rounds = max(args.rounds, 1)
    ortools_rounds = max(args.ortools_rounds, 0 if args.skip_ortools else 1)

    failures = 0
    for item in instance_family.INSTANCES:
        if not run_instance(item, heuristic_rounds, ortools_rounds, args.skip_ortools):
            failures += 1

    if failures:
        print(f"\n{failures} 个实例未通过门禁")
        return 1
    print("\n全部实例通过：覆盖 / 能力声明 / 确定性 / 金目标基线")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
