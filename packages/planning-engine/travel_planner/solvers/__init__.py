"""求解器注册表。

两个实现具备相同的输入契约与输出语义，因此可以互换、可以互为对照：
- `ortools_solver`：主选（设计文档第 6.5 节），只决定"访问顺序 + 日归属"；
- `heuristic`：自研确定性兜底，主求解器不可用或未产出解时启用。

注意：`ortools` 依赖在 `ortools_solver` 内部**函数级懒导入**，因此本模块可在
未安装 OR-Tools 的环境中安全导入（这是 `tests/test_no_io.py` 断言的不变量之一）。
"""

from __future__ import annotations

from travel_planner.solvers import heuristic, ortools_solver
from travel_planner.solvers.ortools_solver import is_available

__all__ = ["heuristic", "is_available", "ortools_solver"]