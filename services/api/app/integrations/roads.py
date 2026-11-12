"""地面交通 Provider（OSRM，开源路由引擎，ADR-0034）。

OSRM（Open Source Routing Machine）是开源项目：公共 demo 服务器
``router.project-osrm.org`` 免 key 可用（限速，仅供轻量使用），生产可
自托管（``GBT_OSRM_BASE_URL`` 指向私有实例，数据来自 OpenStreetMap）。

它只服务 **GROUND（驾车）** 模式——OSRM 的世界模型是公路网；RAIL/AIR/SEA
没有全球免费时刻表源，保持共享几何模型的大圆估算（诚实标注来源）。

provider 边界（ADR-0012 同源）：一次 table 调用拿全矩阵（origin + 各停靠），
失败按 ``TravelProviderUnavailable`` 抛出，由 planning_service 重试一次后
回退离线估算并标 ``stale=True``；费用不估算（未知不是 0）。
"""

from __future__ import annotations

from typing import Any

import httpx
from travel_planner import Origin, StopInput, TravelEstimate

from app.integrations.travel import TravelProviderUnavailable

OSRM_BASE_URL = "https://router.project-osrm.org"
_HTTP_TIMEOUT_SECONDS = 15.0


def parse_table(payload: dict[str, Any]) -> tuple[list[list[float]], list[list[float]]]:
    """解析 OSRM /table 响应，返回 (durations 秒, distances 米) 矩阵。"""
    durations = payload.get("durations")
    distances = payload.get("distances")
    if not isinstance(durations, list) or not durations:
        raise TravelProviderUnavailable("OSRM 响应缺少 durations 矩阵")
    if not isinstance(distances, list) or not distances:
        raise TravelProviderUnavailable("OSRM 响应缺少 distances 矩阵")
    return durations, distances


class OsrmRoadProvider:
    """OSRM 驾车估算。``base_url`` 可指向自托管实例。"""

    name = "osrm-road"

    def __init__(self, base_url: str = OSRM_BASE_URL) -> None:
        self.base_url = base_url.rstrip("/")

    def estimate(self, origin: Origin, stops: list[StopInput]) -> list[TravelEstimate]:
        coords = [(origin.lon, origin.lat)] + [(stop.lon, stop.lat) for stop in stops]
        coord_path = ";".join(f"{lon:.6f},{lat:.6f}" for lon, lat in coords)
        url = f"{self.base_url}/table/v1/driving/{coord_path}"
        try:
            response = httpx.get(
                url,
                params={"annotations": "duration,distance"},
                timeout=_HTTP_TIMEOUT_SECONDS,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise TravelProviderUnavailable(f"OSRM 请求失败：{exc}") from exc
        if payload.get("code") not in (None, "Ok"):
            raise TravelProviderUnavailable(f"OSRM 返回错误码：{payload.get('code')}")
        durations, distances = parse_table(payload)

        estimates: list[TravelEstimate] = []
        for i, stop in enumerate(stops, start=1):
            if stop.movement_mode.value != "GROUND":
                estimates.append(
                    _haversine_estimate(origin, stops[i - 1], source="haversine-estimate")
                )
                continue
            duration = durations[0][i] if i < len(durations[0]) else None
            distance = distances[0][i] if i < len(distances[0]) else None
            if duration is None or distance is None:
                raise TravelProviderUnavailable("OSRM 矩阵缺格")
            estimates.append(
                TravelEstimate(
                    from_visit_id=None,
                    to_visit_id=stop.visit_id,
                    mode=stop.movement_mode,
                    travel_minutes=-(-int(duration) // 60),  # ceil，秒 → 分钟
                    distance_km=round(distance / 1000.0, 3),
                    cost_amount=None,
                    cost_currency=None,
                    source=self.name,
                    stale=False,
                )
            )
        for i, source_stop in enumerate(stops):
            for j, target_stop in enumerate(stops):
                if i == j:
                    continue
                if target_stop.movement_mode.value != "GROUND":
                    estimates.append(
                        _pair_haversine_estimate(
                            source_stop, target_stop, source="haversine-estimate"
                        )
                    )
                    continue
                duration = durations[i + 1][j + 1]
                distance = distances[i + 1][j + 1]
                if duration is None or distance is None:
                    raise TravelProviderUnavailable("OSRM 矩阵缺格")
                estimates.append(
                    TravelEstimate(
                        from_visit_id=source_stop.visit_id,
                        to_visit_id=target_stop.visit_id,
                        mode=target_stop.movement_mode,
                        travel_minutes=-(-int(duration) // 60),
                        distance_km=round(distance / 1000.0, 3),
                        cost_amount=None,
                        cost_currency=None,
                        source=self.name,
                        stale=False,
                    )
                )
        return estimates


def _haversine_estimate(origin: Origin, stop: StopInput, *, source: str) -> TravelEstimate:
    from travel_planner.geometry import haversine_km, travel_minutes

    return TravelEstimate(
        from_visit_id=None,
        to_visit_id=stop.visit_id,
        mode=stop.movement_mode,
        travel_minutes=travel_minutes(origin.lat, origin.lon, stop.lat, stop.lon, stop.movement_mode),
        distance_km=round(haversine_km(origin.lat, origin.lon, stop.lat, stop.lon), 3),
        cost_amount=None,
        cost_currency=None,
        source=source,
        stale=False,
    )


def _pair_haversine_estimate(
    source_stop: StopInput, target_stop: StopInput, *, source: str
) -> TravelEstimate:
    from travel_planner.geometry import haversine_km, travel_minutes

    return TravelEstimate(
        from_visit_id=source_stop.visit_id,
        to_visit_id=target_stop.visit_id,
        mode=target_stop.movement_mode,
        travel_minutes=travel_minutes(
            source_stop.lat,
            source_stop.lon,
            target_stop.lat,
            target_stop.lon,
            target_stop.movement_mode,
        ),
        distance_km=round(
            haversine_km(source_stop.lat, source_stop.lon, target_stop.lat, target_stop.lon), 3
        ),
        cost_amount=None,
        cost_currency=None,
        source=source,
        stale=False,
    )


__all__ = ["OSRM_BASE_URL", "OsrmRoadProvider", "parse_table"]
