"""候选序列生成：为构造式求解器提供**确定性**的初始排序种子。

生成多个互不相同的候选顺序，由求解器全部评估后取目标函数最优者；
候选集合与顺序都只依赖问题本身，不依赖时间、随机数或集合迭代顺序。
"""

from __future__ import annotations

from uuid import UUID

from travel_planner.contracts import PlanningProblem
from travel_planner.feasibility import rank_key
from travel_planner.geometry import travel_minutes


def lock_and_rank_order(problem: PlanningProblem, eligible: tuple[UUID, ...]) -> tuple[UUID, ...]:
    """锁定顺序优先 → 不可动停靠 → 其余按"优先级/价值/ID"排序。"""
    eligible_set = set(eligible)
    by_id = {stop.visit_id: stop for stop in problem.stops}

    locked = [vid for vid in problem.constraints.locked_order if vid in eligible_set]
    locked_set = set(locked)
    immovable = set(problem.constraints.immovable_stop_ids)

    second = [
        by_id[vid]
        for vid in eligible
        if vid in immovable and vid not in locked_set
    ]
    third = [
        by_id[vid]
        for vid in eligible
        if vid not in immovable and vid not in locked_set
    ]

    return (
        *locked,
        *(stop.visit_id for stop in sorted(second, key=rank_key)),
        *(stop.visit_id for stop in sorted(third, key=rank_key)),
    )


def nearest_neighbour_order(problem: PlanningProblem, eligible: tuple[UUID, ...]) -> tuple[UUID, ...]:
    """最近邻构造：始终从当前位置选择行程时间最短的剩余停靠。

    平局按 `rank_key` 决定，因此结果唯一。
    """
    by_id = {stop.visit_id: stop for stop in problem.stops}
    remaining = list(eligible)
    ordered: list[UUID] = []
    lat, lon = problem.origin.lat, problem.origin.lon

    while remaining:
        best = min(
            remaining,
            key=lambda vid: (
                travel_minutes(lat, lon, by_id[vid].lat, by_id[vid].lon, by_id[vid].movement_mode),
                rank_key(by_id[vid]),
            ),
        )
        ordered.append(best)
        lat, lon = by_id[best].lat, by_id[best].lon
        remaining.remove(best)
    return tuple(ordered)


def _dedupe(orders: list[tuple[UUID, ...]]) -> tuple[tuple[UUID, ...], ...]:
    seen: set[tuple[UUID, ...]] = set()
    result: list[tuple[UUID, ...]] = []
    for order in orders:
        if order in seen:
            continue
        seen.add(order)
        result.append(order)
    return tuple(result)


def candidate_orders(problem: PlanningProblem, eligible: tuple[UUID, ...]) -> tuple[tuple[UUID, ...], ...]:
    """返回去重后的候选顺序集合（顺序本身也被冻结）。"""
    if not eligible:
        return ((),)

    locked_prefix = lock_and_rank_order(problem, eligible)
    nn_all = nearest_neighbour_order(problem, eligible)

    locked_part = tuple(vid for vid in locked_prefix if vid in set(problem.constraints.locked_order))
    if locked_part:
        remaining = tuple(vid for vid in eligible if vid not in set(locked_part))
        nn_tail = nearest_neighbour_order(problem, remaining) if remaining else ()
        nn_locked_first = (*locked_part, *nn_tail)
    else:
        nn_locked_first = nn_all

    return _dedupe([locked_prefix, nn_all, nn_locked_first])


__all__ = [
    "candidate_orders",
    "lock_and_rank_order",
    "nearest_neighbour_order",
]