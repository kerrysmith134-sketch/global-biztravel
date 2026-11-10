"""N3 基准集：确定性的实例族生成器（求解器基准与回归共用）。

设计约束（与 fixtures.py 同一血脉，但面向**规模化回归**）：

- 坐标一律由真实城市锚点做**算术插值**得出——不引入随机数，任何机器、任何时间
  生成的实例逐字节相同（这是金目标基线可比较的前提）；
- 每个实例沿一个"难度轴"取值：规模、地理形态、时间窗紧度、每日容量、锁定顺序、
  夏令时切换日、不可行窗口；
- ``kind="infeasible"`` 的实例专锁"诚实失败"路径：eligible 排不进 ⇒ `SolverFailed`，
  绝不允许静默半成品计划。

实例族（共 8 个，名字在基线 JSON 与 bench 输出里都是稳定键）：

=========================  ====================================================
名字                        难度轴
=========================  ====================================================
``corridor_rhine_8``        走廊簇拥（RAIL），小规模基线
``corridor_rhine_12``       走廊 12 站 + 每日容量收紧（多日摊开）
``star_china_10``           星形放射（上海出发 RAIL），中规模
``global_air_10``           洲际 AIR 混合欧亚，长窗口
``tight_windows_8``         窄时间窗阶梯排布，顺序强约束
``locked_order_10``         3 个停靠锁定先后，其余自由
``dst_spring_berlin_6``     柏林春跳日（2026-03-29）跨日窗口
``infeasible_window_4``     一个停靠窗口不可达 ⇒ 必须求解失败
=========================  ====================================================
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from travel_planner.contracts import (
    Constraints,
    MovementMode,
    Objective,
    Origin,
    PlanningProblem,
    ProblemWindow,
    StopInput,
    TimeWindow,
)

TZ_CN = ZoneInfo("Asia/Shanghai")
TZ_DE = ZoneInfo("Europe/Berlin")

TRIP_ID = UUID("88888888-8888-4888-8888-888888888888")
ORG_ID = UUID("44444444-4444-4444-4444-444444444444")
ORIGIN_PLACE = UUID("50000000-0000-4000-8000-000000000000")

#: 真实城市锚点（纬度, 经度）。插值只发生在锚点连线之间，不引入任何随机源。
ANCHORS: dict[str, tuple[float, float]] = {
    "amsterdam": (52.3676, 4.9041),
    "rotterdam": (51.9244, 4.4777),
    "eindhoven": (51.4416, 5.4697),
    "duisburg": (51.4344, 6.7623),
    "cologne": (50.9375, 6.9603),
    "bonn": (50.7374, 7.0982),
    "frankfurt": (50.1109, 8.6821),
    "mannheim": (49.4875, 8.4660),
    "karlsruhe": (49.0069, 8.4037),
    "stuttgart": (48.7758, 9.1829),
    "ulm": (48.4011, 9.9876),
    "munich": (48.1351, 11.5820),
    "shanghai": (31.2304, 121.4737),
    "nanjing": (32.0603, 118.7969),
    "hefei": (31.8209, 117.2272),
    "xuzhou": (34.2044, 117.2857),
    "jinan": (36.6512, 117.1201),
    "beijing": (39.9042, 116.4074),
    "hangzhou": (30.2741, 120.1551),
    "suzhou": (31.2989, 120.5853),
    "wuhan": (30.5928, 114.3055),
    "changsha": (28.2282, 112.9388),
    "singapore": (1.3521, 103.8198),
    "tokyo": (35.6762, 139.6503),
    "berlin": (52.5200, 13.4050),
    "hamburg": (53.5511, 9.9937),
    "leipzig": (51.3397, 12.3731),
    "dresden": (51.0504, 13.7373),
    "hannover": (52.3759, 9.7320),
}


def _visit(n: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{n:012d}")


def _place(n: int) -> UUID:
    return UUID(f"10000000-0000-4000-8000-{n:012d}")


def _interpolate(
    a: tuple[float, float], b: tuple[float, float], count: int
) -> tuple[tuple[float, float], ...]:
    """线段 [a, b] 上取 count 个等分点（含两端）。"""
    step = 1.0 / (count - 1)
    return tuple(
        (a[0] + (b[0] - a[0]) * step * i, a[1] + (b[1] - a[1]) * step * i)
        for i in range(count)
    )


def _corridor_coordinates(
    route: tuple[str, ...], per_segment: int
) -> tuple[tuple[float, float], ...]:
    """沿走廊锚点路线生成等分坐标：每段插值 per_segment 个点（相邻段共享端点）。

    总点数 = ``(len(route)-1) * (per_segment-1) + 1``。
    """
    coordinates: list[tuple[float, float]] = []
    for i in range(len(route) - 1):
        segment = _interpolate(ANCHORS[route[i]], ANCHORS[route[i + 1]], per_segment)
        coordinates.extend(segment if i == 0 else segment[1:])
    return tuple(coordinates)


def _corridor_stops(
    route: tuple[str, ...],
    per_segment: int,
    *,
    service_minutes: int,
    mode: MovementMode,
    business_value: float,
) -> tuple[StopInput, ...]:
    coords = _corridor_coordinates(route, per_segment)
    return tuple(
        StopInput(
            visit_id=_visit(i),
            place_id=_place(i),
            lat=lat,
            lon=lon,
            service_minutes=service_minutes,
            priority=2,
            business_value=business_value,
            movement_mode=mode,
        )
        for i, (lat, lon) in enumerate(coords, start=1)
    )


def _with_windows(
    stops: tuple[StopInput, ...],
    windows: dict[int, TimeWindow],
) -> tuple[StopInput, ...]:
    """按 1 起始的停靠序号给各站补时间窗（未列出的站保持无窗）。"""
    return tuple(
        StopInput(
            visit_id=stop.visit_id,
            place_id=stop.place_id,
            lat=stop.lat,
            lon=stop.lon,
            service_minutes=stop.service_minutes,
            time_windows=(windows[i],) if i in windows else (),
            priority=stop.priority,
            business_value=stop.business_value,
            movement_mode=stop.movement_mode,
        )
        for i, stop in enumerate(stops, start=1)
    )


def _dt_cn(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=TZ_CN)


def _dt_de(month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=TZ_DE)


def _problem(
    stops: tuple[StopInput, ...],
    *,
    window: tuple[datetime, datetime],
    max_stops_per_day: int = 4,
    locked: tuple[UUID, ...] = (),
    immovable: tuple[UUID, ...] = (),
    timezone_home: str = "Asia/Shanghai",
    origin: tuple[float, float] = ANCHORS["shanghai"],
    budget_seconds: int = 2,
) -> PlanningProblem:
    return PlanningProblem(
        trip_id=TRIP_ID,
        organization_id=ORG_ID,
        timezone_home=timezone_home,
        origin=Origin(place_id=ORIGIN_PLACE, lat=origin[0], lon=origin[1]),
        window=ProblemWindow(start=window[0], end=window[1]),
        stops=stops,
        objective=Objective(),
        constraints=Constraints(
            max_stops_per_day=max_stops_per_day,
            immovable_stop_ids=immovable,
            locked_order=locked,
        ),
        solver_budget_seconds=budget_seconds,
    )


# ---------------------------------------------------------------------------
# 各实例构造器（每个只依赖算术，不依赖时钟/随机/IO）
# ---------------------------------------------------------------------------


def corridor_rhine_8() -> PlanningProblem:
    """阿姆斯特丹→科隆走廊等分 8 站（约 440km），RAIL，3 天窗口，松时间窗。"""
    stops = _corridor_stops(
        ("amsterdam", "cologne"),
        per_segment=8,
        service_minutes=60,
        mode=MovementMode.RAIL,
        business_value=500.0,
    )
    assert len(stops) == 8
    return _problem(
        stops,
        window=(_dt_de(11, 9, 8, 30), _dt_de(11, 11, 19, 0)),
        max_stops_per_day=4,
        timezone_home="Europe/Berlin",
        origin=ANCHORS["amsterdam"],
    )


def corridor_rhine_12() -> PlanningProblem:
    """阿姆斯特丹→慕尼黑走廊等分 12 站（约 780km），每日上限 4 ⇒ 至少摊 3 天。

    规模封顶在 12：ortools 的 DFS+GLS 在 n≥14 后墙钟呈指数级（见
    docs/architecture/performance.md §3），实例族必须保持基准可负担。
    """
    stops = _corridor_stops(
        ("amsterdam", "munich"),
        per_segment=12,
        service_minutes=60,
        mode=MovementMode.RAIL,
        business_value=400.0,
    )
    assert len(stops) == 12
    return _problem(
        stops,
        window=(_dt_de(11, 9, 8, 0), _dt_de(11, 12, 19, 30)),
        max_stops_per_day=4,
        timezone_home="Europe/Berlin",
        origin=ANCHORS["amsterdam"],
        budget_seconds=1,
    )


def star_china_10() -> PlanningProblem:
    """上海原点的华东—华北 RAIL 星形网络，10 停靠、4 天。"""
    names = (
        "suzhou", "hangzhou", "nanjing", "hefei", "xuzhou",
        "beijing", "wuhan", "changsha", "nanjing", "jinan",
    )
    stops: list[StopInput] = []
    for i, name in enumerate(names, start=1):
        lat, lon = ANCHORS[name]
        stops.append(
            StopInput(
                visit_id=_visit(i),
                place_id=_place(i),
                # 同名锚点重复出现时做确定性微移，保证坐标互异、行程段非零。
                lat=lat + (0.08 if i % 2 else -0.08),
                lon=lon + (0.08 if i % 3 else -0.08),
                service_minutes=60,
                priority=2,
                business_value=300.0 + i * 10.0,
                movement_mode=MovementMode.RAIL,
            )
        )
    return _problem(
        tuple(stops),
        window=(_dt_cn(9, 8, 0), _dt_cn(12, 20, 0)),
        max_stops_per_day=4,
        budget_seconds=1,
    )


def global_air_10() -> PlanningProblem:
    """欧亚混排的洲际 AIR 实例：10 停靠、10 天窗口、每日上限 3。"""
    eu_asia: tuple[tuple[float, float], ...] = (
        ANCHORS["berlin"],
        ANCHORS["hamburg"],
        ANCHORS["amsterdam"],
        ANCHORS["frankfurt"],
        (48.2082, 16.3738),  # vienna
        (47.3769, 8.5417),  # zurich
        ANCHORS["munich"],
        ANCHORS["singapore"],
        ANCHORS["tokyo"],
        ANCHORS["shanghai"],
    )
    stops = tuple(
        StopInput(
            visit_id=_visit(i),
            place_id=_place(i),
            lat=lat,
            lon=lon,
            service_minutes=90,
            priority=2,
            business_value=1000.0,
            movement_mode=MovementMode.AIR,
        )
        for i, (lat, lon) in enumerate(eu_asia, start=1)
    )
    assert len(stops) == 10
    return _problem(
        stops,
        window=(_dt_cn(9, 6, 0), _dt_cn(18, 22, 0)),
        max_stops_per_day=3,
    )


def tight_windows_8() -> PlanningProblem:
    """走廊 8 站全部压进阶梯式窄窗（90–120 分钟），顺序强约束。"""
    stops = _corridor_stops(
        ("amsterdam", "cologne"),
        per_segment=8,
        service_minutes=60,
        mode=MovementMode.RAIL,
        business_value=600.0,
    )
    plan = {
        1: (9, 13, 0, 15, 0),
        2: (9, 15, 30, 17, 30),
        3: (9, 18, 0, 19, 30),
        4: (10, 9, 30, 11, 30),
        5: (10, 12, 0, 14, 0),
        6: (10, 14, 30, 16, 30),
        7: (10, 17, 0, 18, 30),
        8: (11, 9, 30, 11, 30),
    }
    windows = {
        n: TimeWindow(start=_dt_de(11, day, h1, m1), end=_dt_de(11, day, h2, m2))
        for n, (day, h1, m1, h2, m2) in plan.items()
    }
    return _problem(
        _with_windows(stops, windows),
        window=(_dt_de(11, 9, 8, 30), _dt_de(11, 11, 19, 0)),
        max_stops_per_day=4,
        timezone_home="Europe/Berlin",
        origin=ANCHORS["amsterdam"],
    )


def locked_order_10() -> PlanningProblem:
    """阿姆斯特丹→波恩走廊等分 10 站，锁定第 1 → 6 → 10 站先后（LOCKED_ORDER 轴）。"""
    stops = _corridor_stops(
        ("amsterdam", "bonn"),
        per_segment=10,
        service_minutes=60,
        mode=MovementMode.RAIL,
        business_value=450.0,
    )
    assert len(stops) == 10
    return _problem(
        stops,
        window=(_dt_de(11, 9, 8, 0), _dt_de(11, 11, 19, 0)),
        max_stops_per_day=4,
        locked=(_visit(1), _visit(6), _visit(10)),
        timezone_home="Europe/Berlin",
        origin=ANCHORS["amsterdam"],
    )


def dst_spring_berlin_6() -> PlanningProblem:
    """柏林春跳日实例：2026-03-29 本地 02:00→03:00 跳变，窗口跨切换日。

    ``timezone_home=Europe/Berlin``：日界与窗口全按柏林墙上时间；验证器用 UTC
    差值独立复核服务时长。切换日的墙上窗口仍是 8 小时，真实经过时间只有 7 小时。
    """
    spots: tuple[tuple[float, float], ...] = (
        ANCHORS["berlin"],
        (52.4600, 13.3200),
        (52.4300, 13.1800),
        (52.3900, 13.0600),
        (52.4100, 12.9800),
        (52.4700, 13.2800),
    )
    stops = tuple(
        StopInput(
            visit_id=_visit(i),
            place_id=_place(i),
            lat=lat,
            lon=lon,
            service_minutes=60,
            priority=2,
            business_value=800.0,
            movement_mode=MovementMode.GROUND,
        )
        for i, (lat, lon) in enumerate(spots, start=1)
    )
    # 窗口特意盖住切换日：3-28 两站、3-29（跳变日）三站、3-30 一站。
    windows = {
        1: TimeWindow(start=_dt_de(3, 28, 10, 0), end=_dt_de(3, 28, 17, 0)),
        2: TimeWindow(start=_dt_de(3, 28, 12, 0), end=_dt_de(3, 28, 19, 0)),
        3: TimeWindow(start=_dt_de(3, 29, 9, 0), end=_dt_de(3, 29, 16, 0)),
        4: TimeWindow(start=_dt_de(3, 29, 10, 0), end=_dt_de(3, 29, 17, 0)),
        5: TimeWindow(start=_dt_de(3, 29, 11, 0), end=_dt_de(3, 29, 18, 0)),
        6: TimeWindow(start=_dt_de(3, 30, 9, 30), end=_dt_de(3, 30, 16, 0)),
    }
    return _problem(
        _with_windows(stops, windows),
        window=(_dt_de(3, 28, 8, 0), _dt_de(3, 30, 19, 0)),
        max_stops_per_day=3,
        timezone_home="Europe/Berlin",
        origin=ANCHORS["berlin"],
    )


def infeasible_window_4() -> PlanningProblem:
    """上海→欧洲 AIR，但第 1 站的窗口短于洲际飞行 ⇒ eligible 排不进。

    专锁"诚实失败"：两个求解器都必须走 `SolverFailed`，不允许半成品计划。
    """
    base = _corridor_coordinates(("berlin", "frankfurt", "munich"), 2)
    coords = (ANCHORS["berlin"], ANCHORS["amsterdam"], base[1], ANCHORS["munich"])
    stops = tuple(
        StopInput(
            visit_id=_visit(i),
            place_id=_place(i),
            lat=lat,
            lon=lon,
            service_minutes=90,
            priority=2,
            business_value=900.0,
            movement_mode=MovementMode.AIR,
        )
        for i, (lat, lon) in enumerate(coords, start=1)
    )
    # 出发 09:00（上海本地），柏林窗口 10:00–11:00 结束——洲际 840+ 分钟赶不上。
    return _problem(
        _with_windows(
            stops, {1: TimeWindow(start=_dt_cn(9, 10, 0), end=_dt_cn(9, 11, 0))}
        ),
        window=(_dt_cn(9, 9, 0), _dt_cn(15, 18, 0)),
        max_stops_per_day=4,
    )


FamilyKind = Literal["solvable", "infeasible"]

#: ``min_solver`` 是对该实例的**能力声明**，也是回归门禁的一部分：
#: - ``"heuristic"``：两个求解器都必须全覆盖求解；
#: - ``"ortools"``：ortools 必须求解，而启发式**必须诚实失败**
#:   （`SolverFailed`，不允许半成品计划）——锁定两求解器的能力边界。
MinSolver = Literal["heuristic", "ortools"]


@dataclass(frozen=True)
class FamilyInstance:
    """基准集的一个具名实例。"""

    name: str
    kind: FamilyKind
    axis: str
    build: Callable[[], PlanningProblem]
    min_solver: MinSolver = "heuristic"

    @property
    def problem(self) -> PlanningProblem:
        return self.build()


INSTANCES: tuple[FamilyInstance, ...] = (
    FamilyInstance("corridor_rhine_8", "solvable", "规模/走廊", corridor_rhine_8),
    FamilyInstance("corridor_rhine_12", "solvable", "规模×容量", corridor_rhine_12),
    FamilyInstance("star_china_10", "solvable", "地理/星形", star_china_10),
    FamilyInstance("global_air_10", "solvable", "洲际/长窗", global_air_10),
    FamilyInstance("tight_windows_8", "solvable", "时间窗紧度", tight_windows_8, "ortools"),
    FamilyInstance("locked_order_10", "solvable", "锁定顺序", locked_order_10),
    FamilyInstance("dst_spring_berlin_6", "solvable", "夏令时", dst_spring_berlin_6, "ortools"),
    FamilyInstance("infeasible_window_4", "infeasible", "诚实失败", infeasible_window_4),
)

SOLVABLE_INSTANCES = tuple(item for item in INSTANCES if item.kind == "solvable")
INFEASIBLE_INSTANCES = tuple(item for item in INSTANCES if item.kind == "infeasible")
