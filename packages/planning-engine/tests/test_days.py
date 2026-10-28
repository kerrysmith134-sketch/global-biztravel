"""日界切分测试：按 `timezone_home` 切日、首末日部分窗口、防御上限、确定性。"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fixtures import TZ, TZ_HOME, at, four_city_problem
from travel_planner.days import MAX_DAYS, as_aware, enumerate_days, local_zone, minutes_between


def test_enumerate_days_splits_first_and_last_day() -> None:
    problem = four_city_problem()
    days = enumerate_days(problem.window, problem.timezone_home)

    assert len(days) == 7
    assert days[0].date.isoformat() == "2026-11-09"
    assert days[-1].date.isoformat() == "2026-11-15"

    # 首日从窗口开始时刻起，末日到窗口结束时刻止
    assert days[0].open == problem.window.start
    assert days[-1].close == problem.window.end

    # 中间日是完整的本地日
    for day in days[1:-1]:
        assert day.open.hour == 0 and day.open.minute == 0 and day.open.second == 0
        assert day.close.hour == 23 and day.close.minute == 59 and day.close.second == 59


def test_days_are_contiguous_and_ordered() -> None:
    days = enumerate_days(four_city_problem().window, TZ_HOME)
    for previous, following in zip(days, days[1:], strict=False):
        assert following.open - previous.close == timedelta(seconds=1)
        assert previous.date < following.date


def test_enumerate_days_is_deterministic() -> None:
    problem = four_city_problem()
    assert enumerate_days(problem.window, problem.timezone_home) == enumerate_days(
        problem.window, problem.timezone_home
    )


def test_timezone_changes_day_boundaries() -> None:
    """同一瞬时在不同 `timezone_home` 下切出的日界不同——日界只由时区决定。"""
    problem = four_city_problem()
    shanghai = enumerate_days(problem.window, "Asia/Shanghai")
    berlin = enumerate_days(problem.window, "Europe/Berlin")

    assert shanghai[0].open == berlin[0].open  # 绝对瞬时相同
    assert shanghai[1].open != berlin[1].open  # 本地午夜不同
    assert shanghai[-1].close == berlin[-1].close


def test_too_long_window_is_rejected() -> None:
    window = four_city_problem().window
    long_window = window.model_copy(
        update={"start": at(1, 0, 0, month=1, year=2026), "end": at(1, 0, 0, month=1, year=2028)}
    )
    with pytest.raises(ValueError, match=f"more than {MAX_DAYS} days"):
        enumerate_days(long_window, TZ_HOME)


def test_single_day_window_is_valid() -> None:
    window = four_city_problem().window.model_copy(update={"start": at(9, 9), "end": at(9, 18)})
    days = enumerate_days(window, TZ_HOME)
    assert len(days) == 1
    assert days[0].open == at(9, 9)
    assert days[0].close == at(9, 18)


def test_as_aware_and_local_zone() -> None:
    zone = local_zone(TZ_HOME)
    assert zone is TZ or str(zone) == TZ_HOME
    naive = at(9, 9).replace(tzinfo=None)
    assert as_aware(naive, zone) == at(9, 9)
    assert as_aware(at(9, 9), zone) == at(9, 9)


def test_minutes_between_ceils_and_clamps_negative() -> None:
    base = at(9, 9)
    assert minutes_between(base, base) == 0
    assert minutes_between(base, at(9, 8)) == 0
    assert minutes_between(base, at(9, 10)) == 60
    assert minutes_between(base, at(9, 9, 1)) == 1
    assert minutes_between(base, at(10, 9)) == 24 * 60