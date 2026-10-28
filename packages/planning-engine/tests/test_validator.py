"""独立验证器测试：每条规则都必须能**单独**识别其对应的违规。

手法：先用共享调度器造一份**合法**日程，再对日程做单点破坏，
断言"恰好目标规则 FAIL"，从而证明验证器不是靠整体印象判分。
"""

from __future__ import annotations

from datetime import timedelta

from fixtures import air_stop, at, four_city_problem, make_problem, objective_of, vid
from travel_planner.contracts import (
    RULE_DAY_WITHIN_WINDOW,
    RULE_LOCKED_ORDER,
    RULE_MAX_STOPS_PER_DAY,
    RULE_NO_DUPLICATE_STOP,
    RULE_OBJECTIVE_RECOMPUTED,
    RULE_SERVICE_DURATION,
    RULE_STOP_COVERAGE,
    RULE_TIME_WINDOW,
    RULE_TRAVEL_TIME_CONSISTENCY,
    VALIDATION_RULES,
    CheckStatus,
    DayPlan,
    TimeWindow,
    UnplacedReason,
    UnplacedStop,
)
from travel_planner.days import enumerate_days
from travel_planner.geometry import travel_minutes
from travel_planner.scheduling import build_schedule
from travel_planner.validator import validate


def _schedule_days(problem, ordered_ids):
    return build_schedule(problem, tuple(ordered_ids)).days


def _report(problem, days, unplaced: tuple[UnplacedStop, ...] = ()):
    return validate(
        problem=problem, days=days, unplaced=unplaced, objective_value=objective_of(problem, days)
    )


def _status(report, rule: str) -> CheckStatus:
    for check in report.checks:
        if check.rule == rule:
            return check.status
    raise AssertionError(f"rule {rule} missing from report")


def _failures(report) -> list[str]:
    return [check.rule for check in report.failures]


def _replace_day(days, index: int, stops):
    return tuple(
        day if position != index else day.model_copy(update={"stops": stops})
        for position, day in enumerate(days)
    )


def _first_day_with_stops(days) -> int:
    for index, day in enumerate(days):
        if day.stops:
            return index
    raise AssertionError("schedule has no stops")


def first_service(problem, stop_plan) -> int:
    return problem.stop_by_id(stop_plan.visit_id).service_minutes


def _base_case():
    problem = four_city_problem()
    ordered = [stop.visit_id for stop in problem.stops]
    days = _schedule_days(problem, ordered)
    return problem, days


def test_valid_schedule_passes_every_rule() -> None:
    problem, days = _base_case()
    report = _report(problem, days)

    assert [check.rule for check in report.checks] == list(VALIDATION_RULES)
    assert all(check.status is CheckStatus.PASS for check in report.checks)
    assert report.feasible is True
    assert report.failures == ()


def test_all_stops_are_placed_in_the_base_case() -> None:
    """夹具必须真的能在 7 天内排完 4 个停靠，否则后面的单点破坏会失真。"""
    _, days = _base_case()
    placed = [stop.visit_id for day in days for stop in day.stops]
    assert len(placed) == 4
    assert len(set(placed)) == 4


def test_missing_stop_fails_coverage() -> None:
    problem, days = _base_case()
    empty_days = tuple(day.model_copy(update={"stops": ()}) for day in days)
    report = _report(problem, empty_days)

    assert report.feasible is False
    assert _status(report, RULE_STOP_COVERAGE) is CheckStatus.FAIL
    assert _failures(report) == [RULE_STOP_COVERAGE]


def test_immovable_stop_left_unplaced_fails_coverage() -> None:
    problem = make_problem(
        (air_stop(1, "berlin"), air_stop(2, "munich")), immovable=(vid(1),)
    )
    days = _schedule_days(problem, [vid(2)])
    unplaced = (UnplacedStop(visit_id=vid(1), reason=UnplacedReason.NO_FEASIBLE_SLOT),)
    report = _report(problem, days, unplaced)

    assert report.feasible is False
    assert _status(report, RULE_STOP_COVERAGE) is CheckStatus.FAIL
    assert "immovable_unplaced" in (report.checks[0].detail or "")


def test_unknown_placed_stop_fails_coverage() -> None:
    problem, days = _base_case()
    index = _first_day_with_stops(days)
    stops = list(days[index].stops)
    stops[0] = stops[0].model_copy(update={"visit_id": vid(404)})
    report = _report(problem, _replace_day(days, index, tuple(stops)))

    assert _status(report, RULE_STOP_COVERAGE) is CheckStatus.FAIL
    assert report.feasible is False


def test_duplicate_stop_fails_duplicate_rule() -> None:
    problem, days = _base_case()
    index = _first_day_with_stops(days)
    stops = list(days[index].stops)
    stops.append(stops[0])
    report = _report(problem, _replace_day(days, index, tuple(stops)))

    assert _status(report, RULE_NO_DUPLICATE_STOP) is CheckStatus.FAIL
    assert report.feasible is False


def test_arrive_before_day_open_fails_day_within_window() -> None:
    problem, days = _base_case()
    index = _first_day_with_stops(days)
    day_open = enumerate_days(problem.window, problem.timezone_home)[index].open
    stops = list(days[index].stops)
    first = stops[0]
    arrive = day_open - timedelta(minutes=1)
    stops[0] = first.model_copy(
        update={"arrive": arrive, "depart": arrive + timedelta(minutes=90)}
    )
    report = _report(problem, _replace_day(days, index, tuple(stops)))

    assert _status(report, RULE_DAY_WITHIN_WINDOW) is CheckStatus.FAIL
    assert _failures(report) == [RULE_DAY_WITHIN_WINDOW]


def test_depart_before_arrive_fails_day_within_window() -> None:
    problem, days = _base_case()
    index = _first_day_with_stops(days)
    stops = list(days[index].stops)
    stops[0] = stops[0].model_copy(
        update={"depart": stops[0].arrive - timedelta(minutes=1)}
    )
    report = _report(problem, _replace_day(days, index, tuple(stops)))

    assert _status(report, RULE_DAY_WITHIN_WINDOW) is CheckStatus.FAIL


def test_day_outside_trip_window_fails_day_within_window() -> None:
    problem, days = _base_case()
    stray = DayPlan(date=at(1, 0).date(), stops=())
    report = _report(problem, (*days, stray))

    assert _status(report, RULE_DAY_WITHIN_WINDOW) is CheckStatus.FAIL
    assert _status(report, RULE_STOP_COVERAGE) is CheckStatus.PASS


def test_arrival_outside_visit_time_window_fails() -> None:
    # 时间窗放在出行窗口的**第二天**：上海→柏林航空约 14 小时，
    # 首日 09:00 出发最早 23:00 到，只有第二天 14:00 能被命中。
    window = TimeWindow(start=at(10, 14), end=at(10, 16))
    stops = (
        air_stop(1, "berlin", windows=(window,)),
        air_stop(2, "munich"),
        air_stop(3, "amsterdam"),
    )
    problem = make_problem(stops)
    days = _schedule_days(problem, [vid(1), vid(2), vid(3)])
    index = _first_day_with_stops(days)
    day_stops = list(days[index].stops)
    first = day_stops[0]
    arrive = first.arrive - timedelta(minutes=60)
    day_stops[0] = first.model_copy(
        update={"arrive": arrive, "depart": arrive + timedelta(minutes=first_service(problem, first))}
    )
    report = _report(problem, _replace_day(days, index, tuple(day_stops)))

    assert _status(report, RULE_TIME_WINDOW) is CheckStatus.FAIL


def test_service_duration_mismatch_fails() -> None:
    problem, days = _base_case()
    index = _first_day_with_stops(days)
    stops = list(days[index].stops)
    stops[0] = stops[0].model_copy(update={"depart": stops[0].depart + timedelta(minutes=5)})
    report = _report(problem, _replace_day(days, index, tuple(stops)))

    assert _status(report, RULE_SERVICE_DURATION) is CheckStatus.FAIL
    assert _failures(report) == [RULE_SERVICE_DURATION]


def test_travel_time_mismatch_fails() -> None:
    problem, days = _base_case()
    index = _first_day_with_stops(days)
    stops = list(days[index].stops)
    stops[0] = stops[0].model_copy(update={"travel_minutes": stops[0].travel_minutes + 7})
    report = _report(problem, _replace_day(days, index, tuple(stops)))

    assert _status(report, RULE_TRAVEL_TIME_CONSISTENCY) is CheckStatus.FAIL
    assert _failures(report) == [RULE_TRAVEL_TIME_CONSISTENCY]


def test_travel_time_chains_within_day_from_previous_stop() -> None:
    """同日第二段应从**同日上一个停靠**起算——把它当作首段（自起点）复算会被判不一致。"""
    problem, days = _base_case()
    multi_stop_day = next((index for index, day in enumerate(days) if len(day.stops) > 1), None)
    assert multi_stop_day is not None, "夹具应至少有一天安排两个停靠"

    stops = list(days[multi_stop_day].stops)
    stops[1] = stops[1].model_copy(update={"travel_minutes": stops[0].travel_minutes})
    report = _report(problem, _replace_day(days, multi_stop_day, tuple(stops)))

    assert _status(report, RULE_TRAVEL_TIME_CONSISTENCY) is CheckStatus.FAIL


def test_travel_time_chains_across_days_from_previous_stop() -> None:
    """跨天首段应从**前一天最后一个停靠**起算，而不是每天回到 `origin` 重新起算。

    这条用例覆盖的是"多天连续性语义"在验证器侧的独立复算：验证器不导入调度器，
    必须自己按同一条连续路径推进 `prev`，空天不重置。
    """
    problem, days = _base_case()
    ordered = sorted(days, key=lambda day: day.date)
    first_index = next(index for index, day in enumerate(ordered) if day.stops)
    second_index = next(
        (index for index in range(first_index + 1, len(ordered)) if ordered[index].stops), None
    )
    assert second_index is not None, "夹具应至少跨两天安排停靠"

    prev_source = problem.stop_by_id(ordered[first_index].stops[-1].visit_id)
    head = ordered[second_index].stops[0]
    head_source = problem.stop_by_id(head.visit_id)

    from_previous = travel_minutes(
        prev_source.lat, prev_source.lon, head_source.lat, head_source.lon, head_source.movement_mode
    )
    from_origin = travel_minutes(
        problem.origin.lat,
        problem.origin.lon,
        head_source.lat,
        head_source.lon,
        head_source.movement_mode,
    )
    assert from_previous != from_origin, "夹具应让跨天首段的两种口径算出不同值，否则覆盖不到"
    assert head.travel_minutes == from_previous

    stops = list(ordered[second_index].stops)
    stops[0] = stops[0].model_copy(update={"travel_minutes": from_origin})
    report = _report(problem, _replace_day(days, second_index, tuple(stops)))

    assert _status(report, RULE_TRAVEL_TIME_CONSISTENCY) is CheckStatus.FAIL


def test_max_stops_per_day_exceeded_fails() -> None:
    problem = make_problem(
        (air_stop(1, "berlin"), air_stop(2, "munich")), max_stops_per_day=1
    )
    days = _schedule_days(problem, [vid(1), vid(2)])
    index = _first_day_with_stops(days)
    stops = list(days[index].stops)
    stops.append(stops[0].model_copy(update={"depart": stops[0].depart + timedelta(minutes=200)}))
    report = _report(problem, _replace_day(days, index, tuple(stops)))

    assert _status(report, RULE_MAX_STOPS_PER_DAY) is CheckStatus.FAIL
    assert report.feasible is False


def test_locked_order_violation_is_caught() -> None:
    problem = make_problem(
        (air_stop(1, "berlin"), air_stop(2, "munich")), locked=(vid(1), vid(2))
    )
    correct = _report(problem, _schedule_days(problem, [vid(1), vid(2)]))
    reversed_report = _report(problem, _schedule_days(problem, [vid(2), vid(1)]))

    assert _status(correct, RULE_LOCKED_ORDER) is CheckStatus.PASS
    assert _status(reversed_report, RULE_LOCKED_ORDER) is CheckStatus.FAIL
    assert _failures(reversed_report) == [RULE_LOCKED_ORDER]


def test_locked_successor_without_predecessor_fails() -> None:
    problem = make_problem(
        (air_stop(1, "berlin"), air_stop(2, "munich")), locked=(vid(1), vid(2))
    )
    days = _schedule_days(problem, [vid(2)])
    unplaced = (UnplacedStop(visit_id=vid(1), reason=UnplacedReason.NO_FEASIBLE_SLOT),)
    report = _report(problem, days, unplaced)

    assert _status(report, RULE_LOCKED_ORDER) is CheckStatus.FAIL
    assert "placed-before" in (report.checks[7].detail or "")
    assert report.feasible is False


def test_objective_recomputation_tolerance() -> None:
    problem, days = _base_case()
    exact = objective_of(problem, days)

    ok = validate(problem=problem, days=days, unplaced=(), objective_value=exact + 1e-9)
    off = validate(problem=problem, days=days, unplaced=(), objective_value=exact + 0.001)

    assert _status(ok, RULE_OBJECTIVE_RECOMPUTED) is CheckStatus.PASS
    assert ok.feasible is True
    assert _status(off, RULE_OBJECTIVE_RECOMPUTED) is CheckStatus.FAIL
    assert off.feasible is False


def test_validator_reports_every_rule_even_when_all_pass() -> None:
    problem, days = _base_case()
    report = _report(problem, days)
    assert len(report.checks) == len(VALIDATION_RULES)
    assert {check.status for check in report.checks} == {CheckStatus.PASS}


def test_empty_problem_is_valid() -> None:
    problem = make_problem(())
    report = _report(problem, build_schedule(problem, ()).days)

    assert report.feasible is True
    assert all(check.status is CheckStatus.PASS for check in report.checks)