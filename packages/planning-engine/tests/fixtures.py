"""测试夹具：构造确定性的 `PlanningProblem` 与基于共享调度器的日程。

练习要点：
- 所有 UUID 与坐标都是**硬编码常量**，不依赖随机数或集合迭代顺序；
- 时间一律带 `Asia/Shanghai` 时区（日界按 `timezone_home` 切分）；
- 这里构造的是"问题"与"候选日程"，不构造求解器——求解器由被测代码驱动。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from travel_planner.contracts import (
    Constraints,
    MovementMode,
    Objective,
    ObjectiveWeights,
    Origin,
    PlanningProblem,
    ProblemWindow,
    StopInput,
    TimeWindow,
)

TZ_HOME = "Asia/Shanghai"
TZ = ZoneInfo(TZ_HOME)

TRIP_ID = UUID("77777777-7777-4777-8777-777777777777")
ORG_ID = UUID("33333333-3333-3333-3333-333333333333")

#: 出发地：上海人民广场附近
SHANGHAI = (31.2304, 121.4737)

#: 目的地坐标（纬度, 经度）
CITIES: dict[str, tuple[float, float]] = {
    "berlin": (52.5200, 13.4050),
    "munich": (48.1351, 11.5820),
    "amsterdam": (52.3676, 4.9041),
    "frankfurt": (50.1109, 8.6821),
    "rotterdam": (51.9244, 4.4777),
    "hamburg": (53.5511, 9.9937),
    "singapore": (1.3521, 103.8198),
    "tokyo": (35.6762, 139.6503),
}


def vid(n: int) -> UUID:
    """第 n 个 visit_id（稳定、可读）。"""
    return UUID(f"00000000-0000-4000-8000-{n:012d}")


def place_id(n: int) -> UUID:
    """第 n 个 place_id。"""
    return UUID(f"10000000-0000-4000-8000-{n:012d}")


def at(day: int, hour: int, minute: int = 0, *, month: int = 11, year: int = 2026) -> datetime:
    """2026 年 11 月的某个上海本地时刻。"""
    return datetime(year, month, day, hour, minute, tzinfo=TZ)


def make_stop(
    n: int,
    city: str,
    *,
    service_minutes: int = 60,
    windows: tuple[TimeWindow, ...] = (),
    priority: int = 2,
    business_value: float = 0.0,
    movement_mode: MovementMode = MovementMode.GROUND,
) -> StopInput:
    lat, lon = CITIES[city]
    return StopInput(
        visit_id=vid(n),
        place_id=place_id(n),
        lat=lat,
        lon=lon,
        service_minutes=service_minutes,
        time_windows=windows,
        priority=priority,
        business_value=business_value,
        movement_mode=movement_mode,
    )


def air_stop(n: int, city: str, **kwargs) -> StopInput:
    """航空模式的停靠。

    上海出发的洲际段在陆路模式下需要约 6 天，7 天窗口里根本排不进，
    因此夹具默认用航空模式，让"4 城 7 天"成为一个真正可排的场景。
    """
    kwargs.setdefault("movement_mode", MovementMode.AIR)
    return make_stop(n, city, **kwargs)


def make_problem(
    stops: tuple[StopInput, ...],
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    max_stops_per_day: int = 4,
    immovable: tuple[UUID, ...] = (),
    locked: tuple[UUID, ...] = (),
    budget_seconds: int = 1,
    weights: tuple[float, float] | None = None,
    timezone_home: str = TZ_HOME,
    origin: tuple[float, float] = SHANGHAI,
) -> PlanningProblem:
    objective = (
        Objective()
        if weights is None
        else Objective(weights=ObjectiveWeights(travel=weights[0], value=weights[1]))
    )
    return PlanningProblem(
        trip_id=TRIP_ID,
        organization_id=ORG_ID,
        timezone_home=timezone_home,
        origin=Origin(place_id=place_id(900), lat=origin[0], lon=origin[1]),
        window=ProblemWindow(
            start=start if start is not None else at(9, 9, 0),
            end=end if end is not None else at(15, 18, 0),
        ),
        stops=stops,
        objective=objective,
        constraints=Constraints(
            max_stops_per_day=max_stops_per_day,
            immovable_stop_ids=immovable,
            locked_order=locked,
        ),
        solver_budget_seconds=budget_seconds,
    )


def four_city_problem(**overrides) -> PlanningProblem:
    """默认场景：欧洲四城，7 天窗口，每停靠 90 分钟。"""
    stops = (
        air_stop(1, "berlin", service_minutes=90, business_value=120.0),
        air_stop(2, "munich", service_minutes=90, business_value=80.0),
        air_stop(3, "amsterdam", service_minutes=90, business_value=150.0),
        air_stop(4, "frankfurt", service_minutes=90, business_value=60.0),
    )
    return make_problem(stops, **overrides)


def two_city_problem(**overrides) -> PlanningProblem:
    stops = (air_stop(1, "berlin"), air_stop(2, "munich"))
    return make_problem(stops, **overrides)


def objective_of(problem: PlanningProblem, days) -> float:
    """按目标函数定义独立复算（供测试断言，不用于生产路径）。

    校验器在被注入"未知停靠"时只把它算进行程时间、不计商业价值，
    这里保持同一口径，便于构造破坏性用例。
    """
    weights = problem.objective.weights
    by_id = {stop.visit_id: stop for stop in problem.stops}
    travel = sum(stop.travel_minutes for day in days for stop in day.stops)
    value = sum(
        by_id[stop.visit_id].business_value
        for day in days
        for stop in day.stops
        if stop.visit_id in by_id
    )
    return round(travel * weights.travel - value * weights.value, 6)