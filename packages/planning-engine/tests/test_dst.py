"""DST 专项：日界长度、跨日连续性与日程日期归属。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fixtures import CITIES, make_problem, make_stop
from travel_planner.contracts import ProblemWindow, TimeWindow
from travel_planner.days import enumerate_days
from travel_planner.pipeline import plan_trip
from travel_planner.scheduling import build_schedule

BERLIN = ZoneInfo("Europe/Berlin")
TZ_HOME = "Europe/Berlin"


def _window(start: datetime, end: datetime) -> ProblemWindow:
    return ProblemWindow(start=start, end=end)


def test_spring_forward_day_is_23_hours() -> None:
    days = enumerate_days(
        _window(datetime(2026, 3, 29, 0, 0, tzinfo=BERLIN), datetime(2026, 3, 30, 0, 0, tzinfo=BERLIN)),
        TZ_HOME,
    )
    assert [day.date.isoformat() for day in days] == ["2026-03-29", "2026-03-30"]
    # 排程按**墙上时间**工作：本地 00:00–23:59 仍是 1440 个可用分钟；
    # 但真实经过的瞬时只有 23 小时，这一点必须能被测试看见。
    assert days[0].minutes == 24 * 60
    assert (
        days[0].close.astimezone(UTC) - days[0].open.astimezone(UTC)
        == timedelta(hours=23, seconds=-1)
    )
    assert days[1].open.astimezone(UTC) - days[0].close.astimezone(UTC) == timedelta(seconds=1)


def test_fall_back_day_is_25_hours() -> None:
    days = enumerate_days(
        _window(datetime(2026, 10, 25, 0, 0, tzinfo=BERLIN), datetime(2026, 10, 26, 0, 0, tzinfo=BERLIN)),
        TZ_HOME,
    )
    assert days[0].minutes == 24 * 60
    assert (
        days[0].close.astimezone(UTC) - days[0].open.astimezone(UTC)
        == timedelta(hours=25, seconds=-1)
    )


def test_schedule_keeps_local_dates_across_spring_forward() -> None:
    start = datetime(2026, 3, 27, 0, 0, tzinfo=BERLIN)
    end = datetime(2026, 3, 29, 23, 59, tzinfo=BERLIN)
    stop = make_stop(
        1,
        "berlin",
        windows=(
            TimeWindow(
                start=datetime(2026, 3, 28, 0, 30, tzinfo=BERLIN),
                end=datetime(2026, 3, 28, 1, 30, tzinfo=BERLIN),
            ),
        ),
    )
    problem = make_problem(
        (stop,),
        start=start,
        end=end,
        timezone_home=TZ_HOME,
        origin=CITIES["berlin"],
    )
    outcome = build_schedule(problem, (stop.visit_id,))

    assert len(outcome.unplaced) == 0
    assert len(outcome.days) == 3
    assert [day.date.isoformat() for day in outcome.days] == [
        "2026-03-27",
        "2026-03-28",
        "2026-03-29",
    ]
    placed = outcome.days[1].stops[0]
    assert placed.arrive.date().isoformat() == "2026-03-28"
    assert placed.arrive.hour == 0


def test_service_duration_uses_elapsed_time_across_spring_gap() -> None:
    """01:30 + 60 分钟必须得到 03:30：夏令时跳过的 02:00–03:00 不能被算作一小时。"""
    start = datetime(2026, 3, 29, 0, 0, tzinfo=BERLIN)
    end = datetime(2026, 3, 29, 23, 59, tzinfo=BERLIN)
    moment = datetime(2026, 3, 29, 1, 30, tzinfo=BERLIN)
    stop = make_stop(1, "berlin", service_minutes=60, windows=(TimeWindow(start=moment, end=moment),))
    problem = make_problem(
        (stop,), start=start, end=end, timezone_home=TZ_HOME, origin=CITIES["berlin"]
    )

    result = plan_trip(problem, solver="heuristic")
    assert result.validation.feasible
    placed = result.days[0].stops[0]
    assert placed.arrive.hour == 1 and placed.arrive.minute == 30
    assert placed.depart.hour == 3 and placed.depart.minute == 30
    assert placed.depart.utcoffset() == timedelta(hours=2)


def test_service_duration_uses_elapsed_time_across_fall_overlap() -> None:
    """02:30（第一次）+ 60 分钟 = 02:30（第二次），墙上时间相同但真实经过 60 分钟。"""
    start = datetime(2026, 10, 25, 0, 0, tzinfo=BERLIN)
    end = datetime(2026, 10, 25, 23, 59, tzinfo=BERLIN)
    moment = datetime(2026, 10, 25, 2, 30, fold=0, tzinfo=BERLIN)
    stop = make_stop(1, "berlin", service_minutes=60, windows=(TimeWindow(start=moment, end=moment),))
    problem = make_problem(
        (stop,), start=start, end=end, timezone_home=TZ_HOME, origin=CITIES["berlin"]
    )

    result = plan_trip(problem, solver="heuristic")
    assert result.validation.feasible
    placed = result.days[0].stops[0]
    assert placed.arrive.hour == 2 and placed.arrive.minute == 30
    assert placed.depart.hour == 2 and placed.depart.minute == 30
    assert placed.depart.utcoffset() == timedelta(hours=1)
