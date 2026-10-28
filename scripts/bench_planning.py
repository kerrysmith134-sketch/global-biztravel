#!/usr/bin/env python
"""规划引擎基准（S2 性能预算 / N3 求解器回归）。

跑一个确定性的多国行程问题，分别测量 OR-Tools 与自研启发式的耗时，输出 p50/p95 与
目标值。它不设硬性阈值（机器差异大），但要求：

- 两次运行的 ``problem_hash`` 一致；
- 结果 ``validation.feasible`` 为 True；
- 覆盖全部停靠（成功解不允许丢停靠）。

用法：
    .venv/bin/python scripts/bench_planning.py [--rounds 20]
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "planning-engine"))
sys.path.insert(0, str(ROOT / "packages" / "planning-engine" / "tests"))

import fixtures  # noqa: E402
from travel_planner import SolverFailed, plan_trip  # noqa: E402


def build_problem():
    stops = (
        fixtures.air_stop(1, "berlin", service_minutes=90, business_value=50000),
        fixtures.air_stop(2, "munich", service_minutes=120, business_value=30000),
        fixtures.air_stop(3, "amsterdam", service_minutes=90, business_value=40000),
        fixtures.air_stop(4, "rotterdam", service_minutes=60, business_value=20000),
        fixtures.air_stop(5, "frankfurt", service_minutes=90, business_value=35000),
        fixtures.air_stop(6, "hamburg", service_minutes=60, business_value=15000),
    )
    return fixtures.make_problem(
        stops,
        start=fixtures.at(3, 0),
        end=fixtures.at(9, 23),
        budget_seconds=1,
        max_stops_per_day=4,
    )


def p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
    return ordered[index]


def run(solver: str, rounds: int) -> int:
    problem = build_problem()
    latencies: list[float] = []
    hashes: set[str] = set()
    last = None
    for _ in range(rounds):
        started = time.perf_counter()
        try:
            result = plan_trip(problem, solver=solver)
        except SolverFailed as exc:
            print(f"[{solver}] 求解失败：{exc}")
            return 1
        latencies.append((time.perf_counter() - started) * 1000)
        hashes.add(result.problem_hash)
        last = result

    assert last is not None
    if not last.validation.feasible:
        print(f"[{solver}] 结果被独立验证器判为不可行")
        return 1
    if len(last.placed_visit_ids) != len(problem.stops):
        print(f"[{solver}] 覆盖不完整：{len(last.placed_visit_ids)}/{len(problem.stops)}")
        return 1
    if len(hashes) != 1:
        print(f"[{solver}] problem_hash 不稳定：{hashes}")
        return 1

    print(
        f"[{solver}] rounds={rounds} "
        f"p50={statistics.median(latencies):.1f}ms "
        f"p95={p95(latencies):.1f}ms "
        f"max={max(latencies):.1f}ms "
        f"objective={last.objective_value:.2f} "
        f"stops={len(last.placed_visit_ids)} "
        f"hash={next(iter(hashes))}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="规划引擎基准")
    parser.add_argument("--rounds", type=int, default=20)
    args = parser.parse_args(argv)
    rounds = max(args.rounds, 1)
    failures = 0
    for solver in ("heuristic", "ortools"):
        failures += run(solver, rounds)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
