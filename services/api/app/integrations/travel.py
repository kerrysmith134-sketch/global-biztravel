"""交通 Provider 边界（PRD §5.1 / AT-003）。

规划引擎不直接调用供应商；API 侧把供应商估算转成 ``TravelEstimate`` 注入问题。默认离线
估算用大圆距离，费用显式为 ``None``（未知不是 0）。供应商不可用/超时：

- 重试一次；
- 仍失败 ⇒ 回退离线估算，并把所有段的 ``stale=True``、``source=fallback-...``；
- 计划照常生成，但 UI/API 必须展示 stale 与未知费用，不能伪装成实时数据。

真实供应商只需实现 ``TravelProvider.estimate``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from travel_planner import Origin, StopInput, TravelEstimate
from travel_planner.geometry import haversine_km, travel_minutes

from app.config import get_settings


class TravelProviderUnavailable(RuntimeError):
    pass


class TravelProvider(Protocol):
    name: str

    def estimate(self, origin: Origin, stops: list[StopInput]) -> list[TravelEstimate]: ...


@dataclass
class TravelProviderResult:
    estimates: list[TravelEstimate] = field(default_factory=list)
    degraded: bool = False
    errors: list[str] = field(default_factory=list)


class OfflineTravelProvider:
    """无外部依赖的确定性估算；费用未知。"""

    name = "offline-haversine"

    def estimate(self, origin: Origin, stops: list[StopInput]) -> list[TravelEstimate]:
        return _estimate_pairs(origin, stops, source=self.name, stale=False)


class UnavailableTravelProvider:
    """用于验证降级路径/配置缺失的 provider。"""

    name = "unavailable"

    def estimate(self, origin: Origin, stops: list[StopInput]) -> list[TravelEstimate]:  # noqa: ARG002
        raise TravelProviderUnavailable("交通供应商不可用")


def _estimate_pairs(
    origin: Origin, stops: list[StopInput], *, source: str, stale: bool
) -> list[TravelEstimate]:
    estimates: list[TravelEstimate] = []
    for stop in stops:
        distance = haversine_km(origin.lat, origin.lon, stop.lat, stop.lon)
        estimates.append(
            TravelEstimate(
                from_visit_id=None,
                to_visit_id=stop.visit_id,
                mode=stop.movement_mode,
                travel_minutes=travel_minutes(
                    origin.lat, origin.lon, stop.lat, stop.lon, stop.movement_mode
                ),
                distance_km=round(distance, 3),
                cost_amount=None,
                cost_currency=None,
                source=source,
                stale=stale,
            )
        )
    for source_stop in stops:
        for target in stops:
            if source_stop.visit_id == target.visit_id:
                continue
            distance = haversine_km(source_stop.lat, source_stop.lon, target.lat, target.lon)
            estimates.append(
                TravelEstimate(
                    from_visit_id=source_stop.visit_id,
                    to_visit_id=target.visit_id,
                    mode=target.movement_mode,
                    travel_minutes=travel_minutes(
                        source_stop.lat,
                        source_stop.lon,
                        target.lat,
                        target.lon,
                        target.movement_mode,
                    ),
                    distance_km=round(distance, 3),
                    cost_amount=None,
                    cost_currency=None,
                    source=source,
                    stale=stale,
                )
            )
    return estimates


def get_travel_provider() -> TravelProvider:
    settings = get_settings()
    if settings.travel_provider == "unavailable":
        return UnavailableTravelProvider()
    if settings.travel_provider == "osrm":
        from app.integrations.roads import OSRM_BASE_URL, OsrmRoadProvider

        return OsrmRoadProvider(base_url=settings.osrm_base_url or OSRM_BASE_URL)
    return OfflineTravelProvider()


def estimate_with_fallback(
    provider: TravelProvider, origin: Origin, stops: list[StopInput]
) -> TravelProviderResult:
    errors: list[str] = []
    for _ in range(2):
        try:
            return TravelProviderResult(estimates=provider.estimate(origin, stops))
        except Exception as exc:  # noqa: BLE001 —— 外部失败统一降级
            errors.append(f"{type(exc).__name__}: {exc}")
    fallback = _estimate_pairs(origin, stops, source="fallback-haversine", stale=True)
    return TravelProviderResult(estimates=fallback, degraded=True, errors=errors)


__all__ = [
    "OfflineTravelProvider",
    "TravelProvider",
    "TravelProviderResult",
    "TravelProviderUnavailable",
    "UnavailableTravelProvider",
    "estimate_with_fallback",
    "get_travel_provider",
]
