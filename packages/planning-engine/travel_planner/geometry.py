"""几何与行程时间估算。纯函数、无 IO、无随机。

速度与固定开销是**引擎常量**（不是配置），以保证相同输入在任何机器上得到相同的
`PlanningResult`——这是第 6.4 节"确定性要求"的前提。
"""

from __future__ import annotations

import math

from travel_planner.contracts import MovementMode

EARTH_RADIUS_KM = 6371.0088

#: 视为"同一地点"的距离阈值（公里），低于该值不产生行程时间。
SAME_PLACE_KM = 0.05

_SPEED_KMH: dict[MovementMode, float] = {
    MovementMode.GROUND: 60.0,
    MovementMode.RAIL: 160.0,
    MovementMode.AIR: 700.0,
    MovementMode.SEA: 35.0,
}

#: 固定开销（分钟）：安检、候车、登机、装卸等与距离无关的部分。
_OVERHEAD_MINUTES: dict[MovementMode, int] = {
    MovementMode.GROUND: 20,
    MovementMode.RAIL: 45,
    MovementMode.AIR: 120,
    MovementMode.SEA: 60,
}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """两点大圆距离（公里）。"""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    a = min(1.0, max(0.0, a))
    return 2.0 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def travel_minutes(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
    mode: MovementMode = MovementMode.GROUND,
) -> int:
    """行程时间（分钟，向上取整）。

    同名地点（距离 < `SAME_PLACE_KM`）返回 0；否则 `ceil(距离 / 速度 × 60) + 固定开销`。
    """
    distance = haversine_km(lat1, lon1, lat2, lon2)
    if distance < SAME_PLACE_KM:
        return 0
    speed = _SPEED_KMH[mode]
    minutes = distance / speed * 60.0
    return math.ceil(minutes) + _OVERHEAD_MINUTES[mode]