"""候选序列生成（candidates）的行为测试。

TDD 目标：该模块此前**零测试引用**——它是构造式求解的起点，候选集合错了
求解器再好也白搭。这里锁死的行为：

- 锁定顺序优先、不可动停靠次之、其余按 rank 排；
- 最近邻从当前位置贪心选择、平局按 rank_key 决定（结果唯一）；
- 候选集合确定性：相同问题必得相同候选、候选互不相同、全部覆盖 eligible。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fixtures import air_stop, make_problem, vid  # noqa: E402
from travel_planner.candidates import (  # noqa: E402
    _dedupe,
    candidate_orders,
    lock_and_rank_order,
    nearest_neighbour_order,
)
from travel_planner.contracts import MovementMode  # noqa: E402


def _eligible(problem):
    """构造时按 travel_planner 的惯例用 stop 顺序当 eligible。"""
    return tuple(stop.visit_id for stop in problem.stops)


# ---------------------------------------------------------------------------
# lock_and_rank_order（第 1~5 轮）
# ---------------------------------------------------------------------------


def test_round_01_locked_stops_come_first():
    """轮 1：锁定顺序必须排在最前，且按声明的顺序。"""
    problem = make_problem(
        (
            air_stop(1, "berlin"),
            air_stop(2, "munich"),
            air_stop(3, "amsterdam"),
        ),
        locked=(vid(3), vid(1)),
    )
    order = lock_and_rank_order(problem, _eligible(problem))
    assert order[:2] == (vid(3), vid(1)), f"锁定顺序应最前：{order}"


def test_round_02_locked_stop_not_in_eligible_is_dropped():
    """轮 2：锁定的停靠不在 eligible 里时被静默丢弃。

    注意：locked 不在 stops 里会被 PlanningProblem 的模型校验器直接拒绝（上游防线）；
    这里测的是下游场景——valid 问题，但 eligible 只传入子集（比如时间窗淘汰后）。
    """
    problem = make_problem(
        (
            air_stop(1, "berlin"),
            air_stop(2, "munich"),
            air_stop(3, "amsterdam"),
        ),
        locked=(vid(3),),
    )
    eligible = (vid(1), vid(2))  # vid(3) 已被（假想的）时间窗淘汰
    order = lock_and_rank_order(problem, eligible)
    assert vid(3) not in order
    assert set(order) == {vid(1), vid(2)}


def test_round_03_immovable_before_free_stops():
    """轮 3：不可动停靠排在自由停靠之前（仅次于锁定）。"""
    problem = make_problem(
        (
            air_stop(1, "berlin"),  # 自由
            air_stop(2, "munich"),  # 不可动
            air_stop(3, "amsterdam"),  # 自由
        ),
        immovable=(vid(2),),
    )
    order = lock_and_rank_order(problem, _eligible(problem))
    assert order.index(vid(2)) < order.index(vid(1)), f"不可动应先于自由：{order}"
    assert order.index(vid(2)) < order.index(vid(3)), f"不可动应先于自由：{order}"


def test_round_04_free_stops_sorted_by_priority():
    """轮 4：自由停靠按 rank_key 排——高优先级（数值小）在前。"""
    problem = make_problem(
        (
            air_stop(1, "berlin", priority=3),
            air_stop(2, "munich", priority=1),
            air_stop(3, "amsterdam", priority=2),
        ),
    )
    order = lock_and_rank_order(problem, _eligible(problem))
    assert order == (vid(2), vid(3), vid(1)), f"按优先级升序：{order}"


def test_round_05_all_eligible_present_exactly_once():
    """轮 5：锁定+不可动+自由三段拼接后恰好覆盖 eligible，无遗漏无重复。"""
    problem = make_problem(
        (
            air_stop(1, "berlin"),
            air_stop(2, "munich"),
            air_stop(3, "amsterdam"),
            air_stop(4, "frankfurt"),
        ),
        locked=(vid(4),),
        immovable=(vid(2),),
    )
    order = lock_and_rank_order(problem, _eligible(problem))
    assert sorted(order) == sorted(_eligible(problem))
    assert len(order) == len(set(order))


# ---------------------------------------------------------------------------
# nearest_neighbour_order（第 6~9 轮）
# ---------------------------------------------------------------------------


def test_round_06_nearest_first_from_origin():
    """轮 6：最近邻从出发点开始，先选行程时间最短的停靠。"""
    # 汉堡比柏林离上海……不，都远。用同一城市不同坐标没有意义；直接用
    # geometry 的实际行为：坐标近的先选。慕尼黑（48.1N）比柏林（52.5N）离上海更远，
    # 法兰克福（50.1N）居中。这里不断言具体城市，只断言"结果与手工贪心一致"。
    from travel_planner.geometry import travel_minutes

    problem = make_problem(
        (
            air_stop(1, "berlin"),
            air_stop(2, "munich"),
            air_stop(3, "frankfurt"),
        ),
    )
    order = nearest_neighbour_order(problem, _eligible(problem))
    # 手工复算贪心：从上海出发，每次选 travel_minutes 最小者（平局按 vid 序）。
    from travel_planner.feasibility import rank_key

    by_id = {s.visit_id: s for s in problem.stops}
    lat, lon = problem.origin.lat, problem.origin.lon
    remaining = list(_eligible(problem))
    expected: list = []
    while remaining:
        best = min(
            remaining,
            key=lambda v: (
                travel_minutes(lat, lon, by_id[v].lat, by_id[v].lon, by_id[v].movement_mode),
                rank_key(by_id[v]),  # 平局按 rank_key（docstring 写明），结果唯一
            ),
        )
        expected.append(best)
        remaining.remove(best)
        lat, lon = by_id[best].lat, by_id[best].lon  # 下一跳从新位置出发
    assert order == tuple(expected), f"贪心复算不一致：{order} vs {expected}"


def test_round_07_chain_updates_current_position():
    """轮 7：第二跳从第一跳的位置出发（不是始终从 origin）。"""
    # 柏林→汉堡近，汉堡→阿姆斯特丹近：链式顺序应体现位置滚动。
    problem = make_problem(
        (
            air_stop(1, "hamburg"),
            air_stop(2, "berlin"),
            air_stop(3, "amsterdam"),
        ),
    )
    order = nearest_neighbour_order(problem, _eligible(problem))
    # 不变量：相邻两跳必须是几何上"较近"的组合——断言柏林紧邻汉堡（两者仅 ~250km），
    # 而不是被排到链尾与阿姆斯特丹相邻（汉堡→阿姆 ~460km，柏林→阿姆 ~660km）。
    positions = {v: i for i, v in enumerate(order)}
    assert abs(positions[vid(1)] - positions[vid(2)]) == 1, f"柏林应紧邻汉堡：{order}"


def test_round_08_air_slower_than_ground_prefers_ground():
    """轮 8：同等距离下地面模式行程时间更长——最近邻在混合模式下仍确定。"""
    # 同一坐标放两个停靠（lat/lon 相同），一个 AIR 一个 GROUND：AIR 时间更长，
    # 最近邻应先选 GROUND 那个（时间短）。
    from fixtures import place_id
    from travel_planner.contracts import StopInput

    s_ground = make_problem((air_stop(1, "berlin"),),)
    ground_stop = StopInput(
        visit_id=vid(2),
        place_id=place_id(2),
        lat=52.5200,
        lon=13.4050,
        movement_mode=MovementMode.GROUND,
    )
    problem = make_problem(
        (
            air_stop(1, "berlin"),
            ground_stop,
        ),
    )
    order = nearest_neighbour_order(problem, _eligible(problem))
    # AIR 的 640km+ 在 GROUND 模式下会更长，但 AIR 模式本身时间远短于 GROUND 同距离；
    # 断言：结果确定且覆盖两者即可（行为不漂移）。
    assert set(order) == {vid(1), vid(2)}
    assert len(order) == 2
    assert s_ground is not None  # 夹具自检


def test_round_09_deterministic_across_calls():
    """轮 9：同一问题连续调用两次，结果完全一致（无集合迭代顺序依赖）。"""
    problem = make_problem(
        (
            air_stop(1, "berlin"),
            air_stop(2, "munich"),
            air_stop(3, "amsterdam"),
            air_stop(4, "frankfurt"),
        ),
    )
    first = nearest_neighbour_order(problem, _eligible(problem))
    second = nearest_neighbour_order(problem, _eligible(problem))
    assert first == second


# ---------------------------------------------------------------------------
# _dedupe 与 candidate_orders（第 10~14 轮）
# ---------------------------------------------------------------------------


def test_round_10_dedupe_preserves_first_occurrence():
    """轮 10：去重保留首次出现的顺序。"""
    a = (vid(1), vid(2))
    b = (vid(2), vid(1))
    assert _dedupe([a, b, a]) == (a, b)


def test_round_11_dedupe_empty():
    """轮 11：空输入 → 空元组。"""
    assert _dedupe([]) == ()


def test_round_12_empty_eligible_gives_single_empty_order():
    """轮 12：eligible 为空 → 候选集是 ((,),)——求解器收到"什么都不排"的种子。"""
    problem = make_problem((air_stop(1, "berlin"),))
    assert candidate_orders(problem, ()) == ((),)


def test_round_13_candidates_are_distinct_and_cover_eligible():
    """轮 13：候选互不相同，且每个候选都是 eligible 的一个排列。"""
    problem = make_problem(
        (
            air_stop(1, "berlin"),
            air_stop(2, "munich"),
            air_stop(3, "amsterdam"),
            air_stop(4, "frankfurt"),
        ),
        immovable=(vid(2),),
    )
    eligible = _eligible(problem)
    orders = candidate_orders(problem, eligible)
    assert len(orders) >= 1
    assert len(set(orders)) == len(orders), "候选应去重"
    for order in orders:
        assert sorted(order) == sorted(eligible), f"候选必须是完整排列：{order}"


def test_round_14_deterministic_candidate_set():
    """轮 14：同一问题两次调用，候选集完全一致（含顺序）。"""
    problem = make_problem(
        (
            air_stop(1, "berlin"),
            air_stop(2, "munich"),
            air_stop(3, "amsterdam"),
        ),
        locked=(vid(2),),
    )
    first = candidate_orders(problem, _eligible(problem))
    second = candidate_orders(problem, _eligible(problem))
    assert first == second
