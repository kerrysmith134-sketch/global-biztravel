"""几何与行程时间测试：确定性、单调性、模式差异、同名地点特例。"""

from __future__ import annotations

import pytest
from fixtures import CITIES, SHANGHAI
from travel_planner.contracts import MovementMode
from travel_planner.geometry import EARTH_RADIUS_KM, SAME_PLACE_KM, haversine_km, travel_minutes


def test_haversine_zero_and_symmetry() -> None:
    assert haversine_km(*SHANGHAI, *SHANGHAI) == 0.0
    forward = haversine_km(*SHANGHAI, *CITIES["berlin"])
    backward = haversine_km(*CITIES["berlin"], *SHANGHAI)
    assert forward == pytest.approx(backward, rel=0, abs=1e-9)


def test_haversine_matches_known_city_pairs() -> None:
    shanghai_beijing = haversine_km(31.2304, 121.4737, 39.9042, 116.4074)
    assert 1000.0 < shanghai_beijing < 1150.0

    # 大圆距离 ≈ 8397 km（经 108° 经差绕行），用宽容带避免把舍入误差当回归
    shanghai_berlin = haversine_km(*SHANGHAI, *CITIES["berlin"])
    assert 8300.0 < shanghai_berlin < 8600.0

    assert EARTH_RADIUS_KM == pytest.approx(6371.0088)


def test_same_place_is_zero_minutes() -> None:
    lat, lon = CITIES["berlin"]
    assert travel_minutes(lat, lon, lat, lon) == 0
    # 低于阈值（0.05 km）视为同一地点
    offset = SAME_PLACE_KM / 2.0 / 111.0
    assert travel_minutes(lat, lon, lat + offset, lon) == 0
    assert travel_minutes(lat, lon, lat, lon + offset * 4) > 0


def test_mode_speeds_are_ordered_for_long_hops() -> None:
    shanghai = (*SHANGHAI,)
    berlin = (*CITIES["berlin"],)
    ground = travel_minutes(*shanghai, *berlin, MovementMode.GROUND)
    rail = travel_minutes(*shanghai, *berlin, MovementMode.RAIL)
    air = travel_minutes(*shanghai, *berlin, MovementMode.AIR)

    assert 0 < air < rail < ground


def test_travel_minutes_is_deterministic_and_ceilinged() -> None:
    values = {travel_minutes(*SHANGHAI, *CITIES["munich"], MovementMode.GROUND) for _ in range(5)}
    assert len(values) == 1

    distance = haversine_km(*SHANGHAI, *CITIES["munich"])
    expected = int(distance / 60.0 * 60.0) + 20
    assert travel_minutes(*SHANGHAI, *CITIES["munich"], MovementMode.GROUND) in {expected, expected + 1}


def test_travel_minutes_is_monotone_in_distance() -> None:
    near = travel_minutes(*SHANGHAI, *CITIES["tokyo"], MovementMode.GROUND)
    far = travel_minutes(*SHANGHAI, *CITIES["amsterdam"], MovementMode.GROUND)
    assert far > near


def test_every_city_pair_is_reachable_with_positive_minutes() -> None:
    names = sorted(CITIES)
    for left in names:
        for right in names:
            if left == right:
                continue
            minutes = travel_minutes(*CITIES[left], *CITIES[right], MovementMode.GROUND)
            assert minutes > 0