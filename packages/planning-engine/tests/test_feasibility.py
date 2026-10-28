"""可行性预检测试：时间窗不可行、容量超限、不可动优先占容、输出顺序确定。"""

from __future__ import annotations

from datetime import timedelta

from fixtures import at, make_problem, make_stop, vid
from travel_planner.contracts import TimeWindow, UnplacedReason
from travel_planner.feasibility import check_feasibility, rank_key


def test_window_intersection_is_required() -> None:
    window = TimeWindow(start=at(9, 8), end=at(9, 10))
    inside = make_stop(1, "berlin", windows=(window,))
    outside = make_stop(2, "munich", windows=(TimeWindow(start=at(1, 8), end=at(1, 10)),))
    no_window = make_stop(3, "amsterdam")

    outcome = check_feasibility(make_problem((inside, outside, no_window), start=at(9, 9), end=at(15, 18)))

    assert set(outcome.eligible_stop_ids) == {vid(1), vid(3)}
    assert [(item.visit_id, item.reason) for item in outcome.pre_unplaced] == [
        (vid(2), UnplacedReason.TIME_WINDOW_INFEASIBLE)
    ]
    assert outcome.day_count == 7


def test_capacity_drops_lowest_ranked_optional_stops() -> None:
    stops = (
        make_stop(1, "berlin", priority=1, business_value=10.0),
        make_stop(2, "munich", priority=3, business_value=999.0),
        make_stop(3, "amsterdam", priority=2, business_value=5.0),
        make_stop(4, "frankfurt", priority=2, business_value=500.0),
    )
    # 2 天 × 每天 1 站 = 容量 2
    outcome = check_feasibility(
        make_problem(stops, start=at(9, 9), end=at(10, 18), max_stops_per_day=1)
    )

    assert set(outcome.eligible_stop_ids) == {vid(1), vid(4)}
    dropped = {item.visit_id: item.reason for item in outcome.pre_unplaced}
    assert dropped == {vid(2): UnplacedReason.CAPACITY_EXCEEDED, vid(3): UnplacedReason.CAPACITY_EXCEEDED}


def test_immovable_stops_occupy_capacity_first() -> None:
    stops = (
        make_stop(1, "berlin", priority=3, business_value=0.0),
        make_stop(2, "munich", priority=1, business_value=999.0),
    )
    outcome = check_feasibility(
        make_problem(
            stops, start=at(9, 9), end=at(9, 18), max_stops_per_day=1, immovable=(vid(1),)
        )
    )

    assert outcome.eligible_stop_ids == (vid(1),)
    assert [(item.visit_id, item.reason) for item in outcome.pre_unplaced] == [
        (vid(2), UnplacedReason.CAPACITY_EXCEEDED)
    ]


def test_real_capacity_excess_over_immovable_stops_keeps_ranked_prefix() -> None:
    stops = (
        make_stop(1, "berlin", priority=3),
        make_stop(2, "munich", priority=1),
        make_stop(3, "amsterdam", priority=2),
    )
    outcome = check_feasibility(
        make_problem(
            stops,
            start=at(9, 9),
            end=at(10, 18),
            max_stops_per_day=1,
            immovable=(vid(1), vid(2), vid(3)),
        )
    )

    # 容量 2，三个不可动停靠：按 rank_key（优先级 1 最优先）保留前两个
    assert set(outcome.eligible_stop_ids) == {vid(2), vid(3)}
    assert [(item.visit_id, item.reason) for item in outcome.pre_unplaced] == [
        (vid(1), UnplacedReason.CAPACITY_EXCEEDED)
    ]


def test_pre_unplaced_keeps_original_stop_order() -> None:
    stops = tuple(
        make_stop(n, city)
        for n, city in ((1, "berlin"), (2, "munich"), (3, "amsterdam"), (4, "frankfurt"))
    )
    outcome = check_feasibility(
        make_problem(stops, start=at(9, 9), end=at(9, 18), max_stops_per_day=2)
    )
    positions = [stop.visit_id for stop in stops]
    indexes = [positions.index(item.visit_id) for item in outcome.pre_unplaced]
    assert indexes == sorted(indexes)
    assert len(outcome.pre_unplaced) == 2


def test_rank_key_is_total_and_deterministic() -> None:
    high = make_stop(1, "berlin", priority=1, business_value=0.0)
    low = make_stop(2, "munich", priority=1, business_value=0.0)

    assert rank_key(high) < rank_key(low)  # 同优先级同价值时按 visit_id 兜底
    assert rank_key(high) == rank_key(
        make_stop(1, "berlin", priority=1, business_value=0.0)
    )


def test_precheck_ignores_travel_time_losses() -> None:
    """预检只做与排序无关的硬约束判断，不因"路上要很久"而剔除停靠。"""
    outcome = check_feasibility(make_problem((make_stop(1, "tokyo"),)))
    assert outcome.eligible_stop_ids == (vid(1),)
    assert outcome.pre_unplaced == ()


def test_day_count_reflects_window() -> None:
    one_day = check_feasibility(make_problem((make_stop(1, "berlin"),), start=at(9, 9), end=at(9, 18)))
    assert one_day.day_count == 1
    assert one_day.eligible_stop_ids == (vid(1),)
    assert one_day.pre_unplaced == ()


def test_window_boundary_touching_counts_as_intersecting() -> None:
    """窗口与出行窗口仅端点相接也算相交（闭区间语义），交由调度器与验证器裁决。"""
    touching = make_stop(1, "berlin", windows=(TimeWindow(start=at(9, 8), end=at(9, 9)),))
    outcome = check_feasibility(make_problem((touching,), start=at(9, 9), end=at(15, 18)))
    assert outcome.eligible_stop_ids == (vid(1),)

    # 时间窗整体早于出行窗口 → 不可行
    earlier = make_stop(2, "munich", windows=(TimeWindow(start=at(9, 8), end=at(9, 8, 59)),))
    outcome2 = check_feasibility(make_problem((earlier,), start=at(9, 9), end=at(15, 18)))
    assert outcome2.eligible_stop_ids == ()
    assert outcome2.pre_unplaced[0].reason is UnplacedReason.TIME_WINDOW_INFEASIBLE


def test_time_window_longer_than_window_is_still_eligible() -> None:
    spanning = make_stop(
        1, "berlin", windows=(TimeWindow(start=at(1, 0), end=at(30, 0)),)
    )
    outcome = check_feasibility(make_problem((spanning,)))
    assert outcome.eligible_stop_ids == (vid(1),)
    assert outcome.pre_unplaced == ()


def test_window_close_is_inclusive_endpoint() -> None:
    problem = make_problem((make_stop(1, "berlin"),), start=at(9, 9), end=at(9, 18))
    assert problem.window.end - problem.window.start == timedelta(hours=9)