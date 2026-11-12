#!/usr/bin/env python
"""N3 金目标基线：生成 / 校验 `regression_baselines.json`。

求解器行为回归的治理方式与契约样本门禁（ADR-0030）相同：

- 默认模式：求解实例族，把每个实例×求解器的目标值写入基线文件；
- ``--check`` 模式：重算并与已提交的基线比对，漂移即退出 1
  （`make bench` 与 `test_benchmark_family.py` 都用同一口径做门禁）。

目标值漂移意味着求解器行为变了——**哪怕看起来是改进**也必须有意识地重新
生成基线，并在提交说明里写明原因。

用法::

    .venv/bin/python scripts/gen_regression_baselines.py           # 重新生成
    .venv/bin/python scripts/gen_regression_baselines.py --check   # 校验漂移
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "planning-engine"))
sys.path.insert(0, str(ROOT / "packages" / "planning-engine" / "tests"))

from instance_family import INSTANCES  # noqa: E402
from travel_planner import SolverFailed, plan_trip  # noqa: E402
from travel_planner.contracts import SolverKind  # noqa: E402
from travel_planner.solvers.ortools_solver import ORTOOLS_MAX_ELIGIBLE  # noqa: E402

BASELINES_PATH = ROOT / "packages" / "planning-engine" / "tests" / "regression_baselines.json"

NOTE = (
    "N3 金目标基线：由 scripts/gen_regression_baselines.py 生成，"
    "求解器行为漂移（含改进）必须重新生成并在提交说明写明原因。"
    "值为 null 表示该求解器按能力声明（min_solver / kind / ADR-0031 规模护栏）不求解此实例。"
)


def solve_objective(problem, solver: SolverKind) -> float | None:
    # ADR-0031：超出规模护栏的实例 ortools 入口即拒绝；必须显式跳过，
    # 否则 plan_trip 会静默兜底到启发式，把启发式目标值误记成 ortools 基线。
    if solver is SolverKind.ORTOOLS and len(problem.stops) > ORTOOLS_MAX_ELIGIBLE:
        return None
    try:
        return round(plan_trip(problem, solver=solver).objective_value, 6)
    except SolverFailed:
        return None


def build_baselines() -> dict:
    instances: dict[str, dict[str, float | None]] = {}
    for item in INSTANCES:
        problem = item.problem
        heuristic = solve_objective(problem, SolverKind.HEURISTIC)
        ortools = solve_objective(problem, SolverKind.ORTOOLS)
        instances[item.name] = {"heuristic": heuristic, "ortools": ortools}
    return {"note": NOTE, "instances": instances}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="N3 金目标基线生成/校验")
    parser.add_argument("--check", action="store_true", help="校验模式：漂移即失败")
    args = parser.parse_args(argv)

    computed = build_baselines()

    if not args.check:
        BASELINES_PATH.write_text(
            json.dumps(computed, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        for name, entry in computed["instances"].items():
            print(f"{name}: heuristic={entry['heuristic']} ortools={entry['ortools']}")
        print(f"已写入 {BASELINES_PATH}")
        return 0

    if not BASELINES_PATH.exists():
        print(f"缺少基线文件：{BASELINES_PATH}（先运行生成模式）")
        return 1

    committed = json.loads(BASELINES_PATH.read_text(encoding="utf-8"))
    drift = [
        f"{name}.{solver}: committed={committed['instances'][name][solver]} "
        f"computed={entry[solver]}"
        for name, entry in computed["instances"].items()
        for solver in ("heuristic", "ortools")
        if committed["instances"][name][solver] != entry[solver]
    ]
    if drift:
        print("基线漂移（求解器行为变化？先确认是否有意变更）：")
        for line in drift:
            print(f"  {line}")
        return 1
    print("金目标基线一致。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
