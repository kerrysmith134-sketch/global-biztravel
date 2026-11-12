"""天气 Provider（Open-Meteo，免钥公开源，ADR-0034）。

Open-Meteo 是开源项目、公开 API 免 API key，数据源为各国气象机构开放数据。
它填补两处既有缺口：主动式 EXTERNAL 触发的 ``WEATHER_DISRUPTION`` 信号、
当地指南的 ``WEATHER_NOT_CONNECTED`` data gap。

语义红线（ADR-0005 同源）：获取失败/响应结构不对都按"不可用"抛出，
调用方保持既有 data_gaps 行为，绝不编造天气。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

OPEN_METEO_BASE_URL = "https://api.open-meteo.com/v1"
_HTTP_TIMEOUT_SECONDS = 10.0

#: 恶劣天气阈值（商务出行受影响的量级，写入 ADR-0034）：
#: 持续风速 ≥ 50 km/h（约 7 级风）或小时降水 ≥ 10 mm（暴雨）。
SEVERE_WIND_KMH = 50.0
SEVERE_PRECIPITATION_MM = 10.0


class WeatherUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class WeatherObservation:
    """一次当前天气观测（Open-Meteo ``current`` 块）。"""

    latitude: float
    longitude: float
    temperature_c: float | None
    wind_kmh: float | None
    precipitation_mm: float | None
    weather_code: int | None
    observed_at: datetime | None


def parse_current(payload: dict[str, Any]) -> WeatherObservation:
    """解析 Open-Meteo forecast 响应。结构缺失按不可用处理，不猜字段。"""
    current = payload.get("current")
    if not isinstance(current, dict):
        raise WeatherUnavailable("Open-Meteo 响应缺少 current 块")
    latitude = payload.get("latitude")
    longitude = payload.get("longitude")
    if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
        raise WeatherUnavailable("Open-Meteo 响应缺少坐标回显")

    observed_raw = current.get("time")
    observed_at: datetime | None = None
    if isinstance(observed_raw, str):
        try:
            observed_at = datetime.fromisoformat(observed_raw).replace(tzinfo=UTC)
        except ValueError as exc:
            raise WeatherUnavailable(f"Open-Meteo 观测时间不可解析：{observed_raw}") from exc

    def _number(key: str) -> float | None:
        value = current.get(key)
        return value if isinstance(value, (int, float)) else None

    return WeatherObservation(
        latitude=float(latitude),
        longitude=float(longitude),
        temperature_c=_number("temperature_2m"),
        wind_kmh=_number("wind_speed_10m"),
        precipitation_mm=_number("precipitation"),
        weather_code=(
            current["weather_code"] if isinstance(current.get("weather_code"), int) else None
        ),
        observed_at=observed_at,
    )


def is_severe(observation: WeatherObservation) -> bool:
    """是否达到"可能影响商务出行"的恶劣天气阈值。字段缺失视为不判定。"""
    if observation.wind_kmh is not None and observation.wind_kmh >= SEVERE_WIND_KMH:
        return True
    return (
        observation.precipitation_mm is not None
        and observation.precipitation_mm >= SEVERE_PRECIPITATION_MM
    )


class OpenMeteoWeatherProvider:
    name = "open-meteo"

    def fetch_current(self, lat: float, lon: float) -> WeatherObservation:
        try:
            response = httpx.get(
                f"{OPEN_METEO_BASE_URL}/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current": "temperature_2m,wind_speed_10m,precipitation,weather_code",
                    "wind_speed_unit": "kmh",
                    "timezone": "UTC",
                },
                timeout=_HTTP_TIMEOUT_SECONDS,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise WeatherUnavailable(f"Open-Meteo 请求失败：{exc}") from exc
        return parse_current(payload)


class UnavailableWeatherProvider:
    name = "unavailable"

    def fetch_current(self, lat: float, lon: float) -> WeatherObservation:  # noqa: ARG002
        raise WeatherUnavailable("天气源未配置（GBT_WEATHER_PROVIDER=unavailable）")


def get_weather_provider():
    from app.config import get_settings

    if get_settings().weather_provider == "open-meteo":
        return OpenMeteoWeatherProvider()
    return UnavailableWeatherProvider()


__all__ = [
    "OPEN_METEO_BASE_URL",
    "OpenMeteoWeatherProvider",
    "SEVERE_PRECIPITATION_MM",
    "SEVERE_WIND_KMH",
    "UnavailableWeatherProvider",
    "WeatherObservation",
    "WeatherUnavailable",
    "get_weather_provider",
    "is_severe",
    "parse_current",
]
