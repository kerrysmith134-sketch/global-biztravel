"""OR-Tools 求解器（主选，第 6.5 节）。

职责边界（关键设计决定）：
- **OR-Tools 只负责"访问顺序"**，不决定日归属；
- 精确到分钟的到达/离开时间与**日归属**由 `travel_planner.scheduling.build_schedule`
  统一物化，这样求解器输出与独立验证器的判据同源，不可能各算一套。

为什么是"单车松弛"（模型形态，与多天连续性语义配套）：
`build_schedule` 的语义是**整条行程是一条连续路径**（第 1 天自 `origin` 出发，
第 k 天自第 k-1 天最后一个已排停靠出发）。因此求解器的模型也只放**一辆车**：
节点 0 = 起点车场（`origin`）、节点 1 = 终点车场（`origin`）、节点 `i+2` = 第 i 个停靠。
时间维 `CumulVar` 覆盖**整个窗口**，各停靠取其时间窗相对 `base` 的**凸包**并 clamp 到
`[0, horizon]`。时间维单调递增，因此凸包下界晚的停靠天然被排到后面——顺序由窗口
自身约束出来，日归属则完全交给物化器。

历史根因（**必须记住**）：早期实现给**每一天**各放一辆车（每天一个 start/end depot），
等价于"每天从 `origin` 重新起算、不模拟返程"。于是跨洲段（上海 → 柏林，最省时的航空
也要 840 分钟）被重复计入每一天，第 2 天之后的所有停靠都被算成"当天到不了"，
多国行程必然不可行。那是建模错误，不是现实约束——种子数据里的"2026 Q4 欧洲多国商务出行"
就是这样被误判为不可行的。

确定性策略（第 6.4 节）：
- 不使用 `random_seed`（OR-Tools 9.15 的 `RoutingSearchParameters` 无该字段）；
- 不使用墙钟 `time_limit`（跨机器不确定），改用 **解数量上限** `solution_limit`，
  其值由 `solver_budget_seconds` 线性推导，而该字段已计入 `problem_hash`；
- **规模护栏**（ADR-0031）：解数量预算管不住 DFS+GLS 单解成本随规模的指数增长
  （N3 基准集实测：n=12 走廊单次 39s、n=16 分钟级），故按问题内容封顶
  `ORTOOLS_MAX_ELIGIBLE`，超出在入口处拒绝（`SolverFailed`），流水线落到启发式。

出行模式口径（**本模块唯一的最高风险点**）：
- 行程分钟必须与 `scheduling.build_schedule` **逐段同源**：`build_schedule`
  用**目的停靠的 `movement_mode`**（`scheduling.py` 中 `travel_minutes(..., stop.movement_mode)`）。
  因此本模块 `duration(from, to)` 也必须取 **目的地** 的模式，而不是起点。
- 返程弧（进入终点车场，节点 1）计 0：与 `build_schedule` "不模拟返程"同口径。
- 反例（已修正，历史根因）：早期实现取 `from_mode`（起点的模式）。由于所有日从
  `origin`（GROUND）出发，`origin → 柏林` 被算成 **8418 分钟**（陆路，约 6 天），
  而共享调度器算的是 **840 分钟**（航空）。后果有两重：
  1) 时间维容量被撑爆，`SolveWithParameters` 直接返回 `None`（模型不可行）；
  2) 修复前带 `AddDisjunction` 的版本里，放一个停靠要付 8418 的行程成本，
     而丢弃它的商业价值折算只有 0.12 分钟量级，于是"什么都不排、目标值 0"成为最优解。
  两点叠加使 OR-Tools 输出退化解，而独立验证器的 `STOP_COVERAGE` 只检查
  "已排 ∪ 未排 = 全集"，仍判 `feasible=True`，形成"退化解也判可行"的静默错误。

时间轴量化方向（保守）：
- 求解器工作在**整数分钟**轴上，而调度器用真实 `datetime` 比较。两者必然存在
  亚分钟级量化差；方向必须是"求解器不宽于调度器"，否则会出现
  "求解器认为排得下、`build_schedule` 物化时排不下"的错配。
- 因此：区间**起点**（日开放、时间窗开始）向上取整，区间**终点**（日结束、
  时间窗结束）向下取整（见 `_ceil_minutes` / `_floor_minutes`）。

可访问性语义与覆盖不变量（重要）：
- `Feasibility Checker` 已保证"容量足够"（`eligible` 一定排得下），因此这里**不使用
  `AddDisjunction`**：所有 `eligible` 停靠都是**强制访问**的。若模型无解，
  `SolveWithParameters` 返回 `None`，本模块抛 `SolverFailed`，流水线沿既有兜底链
  落到自研确定性启发式并标记 `degraded=true`（第 6.5 节语义）。
- 更关键的一层：单车松弛后，模型**不认识"每天最多几个停靠"**，也不认识"某天结束前
  必须离场"，所以"模型内可排"不一定等于"物化后排得下"。因此
  `_require_full_coverage` 校验的对象**必须是 `build_schedule` 物化之后的停靠集合**，
  而不是 OR-Tools 返回的顺序：物化后仍有人没排进去 ⇒ `SolverFailed` ⇒ 兜底启发式。
  这条断言把"部分覆盖"永远限制在 `SolverFailed` 这一条出口上，
  "排 0 个停靠"再也不可能被当作成功解交给验证器。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from travel_planner.contracts import (
    PlanningProblem,
    SolverKind,
    UnplacedReason,
    UnplacedStop,
)
from travel_planner.days import minutes_between
from travel_planner.geometry import travel_minutes
from travel_planner.outcome import SolveOutcome, SolverFailed, SolverUnavailable, merge_unplaced
from travel_planner.scheduling import build_schedule, make_objective_value

SOLUTION_LIMIT_PER_BUDGET_SECOND = 40
MAX_SOLUTION_LIMIT = 5000

#: 确定性规模护栏（ADR-0031）：ortools 只接受 `len(eligible) <= ORTOOLS_MAX_ELIGIBLE`。
#: 依据（N3 基准集实测，见 docs/architecture/performance.md §3）：DFS+GUIDED_LOCAL_SEARCH
#: 的**单解**搜索成本随规模指数增长——n=8 ≈ 6ms/解、n=10 ≈ 150ms/解、n=12 ≈ 1s/解
#: （12 站走廊单次 39s、16 站分钟级）。`solver_budget_seconds` 只是解数量预算（线性），
#: 管不住这个指数因子；墙钟 `time_limit` 又会破坏跨机器确定性（第 6.4 节）。
#: 因此按**问题内容**封顶规模：超出即拒绝，流水线沿兜底链落到启发式（`degraded=true`）。
#: 溢出的质量损失实测有限（族内 7 实例仅 1 个 ortools 更优，差距 ~7%）。
ORTOOLS_MAX_ELIGIBLE = 10

#: 单车的车场节点：0 = 起点（`origin`），1 = 终点（`origin`，返程弧计 0）。
_START_DEPOT = 0
_END_DEPOT = 1
#: 停靠 i 的节点号（`i` 是 `eligible` 中的下标）。
_STOP_OFFSET = 2


def _ceil_minutes(base: datetime, moment: datetime) -> int:
    """**区间起点**映射到整数时间轴：向上取整（保守）。"""
    return minutes_between(base, moment)


def _floor_minutes(base: datetime, moment: datetime) -> int:
    """**区间终点**映射到整数时间轴：向下取整（保守）。

    `days.minutes_between` 统一向上取整，若用在"日结束/时间窗结束"上，
    会让求解器的可行区间比共享调度器**宽**最多 1 分钟，出现
    "求解器认为排得下、调度器物化时排不下"的错配。
    这里对终点单独向下取整，使求解器的模型严格**不宽于** `build_schedule`。
    """
    delta = (moment - base).total_seconds()
    if delta <= 0:
        return 0
    return int(delta // 60)


def is_available() -> bool:
    """OR-Tools Python 绑定是否可用（不抛异常）。"""
    try:
        from ortools.constraint_solver import pywrapcp, routing_enums_pb2  # noqa: F401
    except Exception:
        return False
    return True


def _require_full_coverage(placed: tuple[UUID, ...], eligible: tuple[UUID, ...]) -> None:
    """显式覆盖不变量：成功解必须覆盖**全部** `eligible` 停靠。

    `placed` 必须是 **`build_schedule` 物化之后**的停靠集合，而不是 OR-Tools 返回的顺序。
    单车松弛下模型不认识每日容量与日界，"模型内可排"不代表"物化后排得下"；
    只有物化后仍全覆盖，才允许把结果当作成功解。

    为什么需要它（历史根因）：早期版本用 `AddDisjunction` + 丢弃惩罚把停靠变成"可选"，
    而单次拜访的商业价值折算只有 0.12 分钟量级、远小于真实行程成本，
    于是"什么都不排、目标值 0"成了最优解。更糟的是独立验证器的 `STOP_COVERAGE`
    只检查"已排 ∪ 未排 = 全集"，丢弃全部停靠并不触发它，最终把退化解报成
    `solver=ortools, feasible=true`——一条静默错误。

    现在模型已不再允许丢弃任何 `eligible`（无 `AddDisjunction`），本条断言在结构上
    冗余；把它显式写出来是为了让"部分覆盖"永远只能走 `SolverFailed` 这一条出口，
    而不是把不满载的结果当成成功解交给验证器。
    """
    if set(placed) != set(eligible):
        missing = sorted(str(v) for v in set(eligible) - set(placed))
        raise SolverFailed(
            f"ortools assignment does not cover every eligible stop: missing={missing}"
        )


def solve(problem: PlanningProblem, eligible: tuple[UUID, ...]) -> SolveOutcome:
    try:
        from ortools.constraint_solver import pywrapcp, routing_enums_pb2
    except Exception as exc:  # pragma: no cover - 依赖缺失路径
        raise SolverUnavailable(f"ortools unavailable: {exc}") from exc

    # 确定性规模护栏（ADR-0031）：规模按问题内容判定，在建模之前拒绝，
    # 与"依赖缺失 ⇒ SolverUnavailable"一样属于求解器的能力边界。
    if len(eligible) > ORTOOLS_MAX_ELIGIBLE:
        raise SolverFailed(
            f"ortools refused: {len(eligible)} eligible stops exceed the "
            f"deterministic scale guard (ORTOOLS_MAX_ELIGIBLE="
            f"{ORTOOLS_MAX_ELIGIBLE}); the pipeline falls back to the heuristic"
        )

    if not eligible:
        empty = build_schedule(problem, ())
        return SolveOutcome(
            solver=SolverKind.ORTOOLS,
            days=empty.days,
            legs=empty.legs,
            unplaced=(),
            travel_minutes_total=0,
            value_placed=0.0,
            objective_value=make_objective_value(problem, 0, 0.0),
        )

    estimates = {
        (item.from_visit_id, item.to_visit_id, item.mode): item
        for item in problem.travel_estimates
    }
    stops_by_id = {stop.visit_id: stop for stop in problem.stops}
    node_stops = [stops_by_id[vid] for vid in eligible]
    stop_count = len(node_stops)
    total_nodes = stop_count + _STOP_OFFSET

    base = problem.window.start
    horizon = max(1, minutes_between(base, problem.window.end) + 1)

    def point(node: int) -> tuple[float, float]:
        """节点的坐标：车场都是 `origin`，其余是停靠坐标。"""
        if node >= _STOP_OFFSET:
            stop = node_stops[node - _STOP_OFFSET]
            return stop.lat, stop.lon
        return problem.origin.lat, problem.origin.lon

    def duration(from_node: int, to_node: int) -> int:
        """行程分钟：与 `build_schedule` 严格同口径。

        两条不可动摇的规则：
        1. **目的地是终点车场 ⇒ 返程弧计 0**（整条路径不模拟返程）。
        2. **模式取目的停靠的 `movement_mode`**，与 `scheduling.build_schedule`
           中 `travel_minutes(..., stop.movement_mode)` 一致；取起点模式会把
           `origin(GROUND) → 洲际停靠` 算成 8418 分钟（约 6 天），导致模型不可行
           且严重扭曲目标函数。
        """
        if to_node == _END_DEPOT:
            return 0
        to_lat, to_lon = point(to_node)
        from_lat, from_lon = point(from_node)
        if to_node < _STOP_OFFSET:
            return 0
        to_stop = node_stops[to_node - _STOP_OFFSET]
        from_visit = (
            node_stops[from_node - _STOP_OFFSET].visit_id
            if from_node >= _STOP_OFFSET
            else None
        )
        estimate = estimates.get((from_visit, to_stop.visit_id, to_stop.movement_mode))
        if estimate is not None:
            return estimate.travel_minutes
        return travel_minutes(from_lat, from_lon, to_lat, to_lon, to_stop.movement_mode)

    # 单车：起点车场 0、终点车场 1。`RoutingIndexManager` 的 `starts/ends` 形式
    # 才能让起点与终点落在不同节点上（depot 简写会强制二者相同）。
    manager = pywrapcp.RoutingIndexManager(total_nodes, 1, [_START_DEPOT], [_END_DEPOT])
    routing = pywrapcp.RoutingModel(manager)

    def service_minutes(node: int) -> int:
        if _STOP_OFFSET <= node < _STOP_OFFSET + stop_count:
            return node_stops[node - _STOP_OFFSET].service_minutes
        return 0

    def time_transit(from_index: int, to_index: int) -> int:
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return duration(from_node, to_node) + service_minutes(from_node)

    def cost_transit(from_index: int, to_index: int) -> int:
        """成本口径与 `make_objective_value` 对齐：**返程弧代价为 0**。"""
        return duration(manager.IndexToNode(from_index), manager.IndexToNode(to_index))

    time_callback = routing.RegisterTransitCallback(time_transit)
    cost_callback = routing.RegisterTransitCallback(cost_transit)
    routing.SetArcCostEvaluatorOfAllVehicles(cost_callback)

    # 时间维覆盖**整个窗口**：`slack` 取 `horizon`，等待（候窗）不花代价。
    routing.AddDimension(time_callback, horizon, horizon, False, "Time")
    time_dimension = routing.GetDimensionOrDie("Time")

    # 时间窗采取"凸包松弛"（[最小开始, 最大结束]）；真正的窗口判定由
    # 共享调度器与独立验证器负责，避免求解器与验证器出现两套窗口语义。
    # 凸包下界即模型内"最早可能到达"，时间维单调递增 ⇒ 窗口晚的停靠自然排在后面。
    for index, stop in enumerate(node_stops):
        node_index = manager.NodeToIndex(index + _STOP_OFFSET)
        if stop.time_windows:
            low = max(0, min(_ceil_minutes(base, w.start) for w in stop.time_windows))
            high = min(horizon, max(_floor_minutes(base, w.end) for w in stop.time_windows))
        else:
            low, high = 0, horizon
        if high < low:
            low, high = 0, horizon
        time_dimension.CumulVar(node_index).SetRange(low, high)

    # 端点索引必须用 `routing.Start/End`：`NodeToIndex` 对**结束节点**返回 -1，
    # 而结束节点的 CumulVar 也不存在，直接用会拿到 None。
    time_dimension.CumulVar(routing.Start(0)).SetRange(0, 0)
    time_dimension.CumulVar(routing.End(0)).SetRange(0, horizon)

    # 所有 `eligible` 停靠都是**强制访问**（不加 `AddDisjunction`）：
    # 预检已保证容量足够，OR-Tools 在此只扮演"访问顺序"的排序器；
    # 若模型无解则由 `SolveWithParameters` 返回 None ⇒ `SolverFailed` ⇒ 兜底启发式。
    position = {vid: index for index, vid in enumerate(eligible)}
    locked = problem.constraints.locked_order
    for first, second in zip(locked, locked[1:], strict=False):
        if first not in position or second not in position:
            continue
        first_index = manager.NodeToIndex(position[first] + _STOP_OFFSET)
        second_index = manager.NodeToIndex(position[second] + _STOP_OFFSET)
        service = node_stops[position[first]].service_minutes
        routing.solver().Add(
            time_dimension.CumulVar(first_index) + service <= time_dimension.CumulVar(second_index)
        )

    parameters = pywrapcp.DefaultRoutingSearchParameters()
    parameters.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )
    parameters.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    parameters.use_depth_first_search = True
    parameters.number_of_solutions_to_collect = 1
    parameters.solution_limit = max(
        1,
        min(problem.solver_budget_seconds * SOLUTION_LIMIT_PER_BUDGET_SECOND, MAX_SOLUTION_LIMIT),
    )

    assignment = routing.SolveWithParameters(parameters)
    if assignment is None:
        raise SolverFailed("ortools produced no solution within the deterministic search limit")

    placed_order: list[UUID] = []
    node_index = routing.Start(0)
    while not routing.IsEnd(node_index):
        node = manager.IndexToNode(node_index)
        if node >= _STOP_OFFSET:
            placed_order.append(node_stops[node - _STOP_OFFSET].visit_id)
        node_index = assignment.Value(routing.NextVar(node_index))

    # 日归属与分钟级时刻由共享调度器物化；覆盖判定必须基于**物化之后**的结果。
    schedule = build_schedule(problem, tuple(placed_order))
    materialized = tuple(stop.visit_id for day in schedule.days for stop in day.stops)
    _require_full_coverage(materialized, eligible)

    placed = set(materialized)
    dropped = tuple(
        UnplacedStop(visit_id=vid, reason=UnplacedReason.NO_FEASIBLE_SLOT)
        for vid in eligible
        if vid not in placed
    )
    unplaced = merge_unplaced(problem, schedule.unplaced, dropped)

    return SolveOutcome(
        solver=SolverKind.ORTOOLS,
        days=schedule.days,
        legs=schedule.legs,
        unplaced=unplaced,
        travel_minutes_total=schedule.travel_minutes_total,
        value_placed=schedule.value_placed,
        objective_value=make_objective_value(
            problem, schedule.travel_minutes_total, schedule.value_placed
        ),
    )