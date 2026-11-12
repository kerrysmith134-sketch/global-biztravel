"""航班状态 Provider（OpenSky Network，免钥公开源，ADR-0034）。

OpenSky Network 是开源的全球 ADS-B 航迹网络，匿名访问可用（低速率配额），
注册（仍免费）可提高配额。它填补 ``FLIGHT_STATUS`` 外部信号的免费接入源。

语义红线：匿名配额耗尽/网络失败按"不可用"抛出，不伪造航班状态。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

OPEN_SKY_BASE_URL = "https://opensky-network.org/api"
_HTTP_TIMEOUT_SECONDS = 15.0


class FlightDataUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class FlightState:
    """一条航迹快照（OpenSky ``states`` 数组元素的子集）。"""

    icao24: str
    callsign: str
    origin_country: str
    latitude: float | None
    longitude: float | None
    baro_altitude_m: float | None
    velocity_ms: float | None
    on_ground: bool
    last_contact: datetime


def parse_states(payload: dict[str, Any]) -> list[FlightState]:
    """解析 OpenSky /states/all 响应。

    状态向量下标（OpenSky 文档冻结）：0 icao24、1 callsign、2 origin_country、
    4 last_contact(epoch)、5 longitude、6 latitude、7 baro_altitude、8 on_ground、
    9 velocity。``time`` 与 ``states`` 是唯二必需字段。
    """
    states = payload.get("states")
    if not isinstance(states, list):
        raise FlightDataUnavailable("OpenSky 响应缺少 states 数组")
    result: list[FlightState] = []
    for row in states:
        if not isinstance(row, list) or len(row) < 10:
            continue
        try:
            icao24 = str(row[0])
            callsign = str(row[1] or "").strip()
            last_contact = float(row[4])
        except (TypeError, ValueError) as exc:
            raise FlightDataUnavailable(f"OpenSky 状态行不可解析：{row[:5]!r}") from exc
        result.append(
            FlightState(
                icao24=icao24,
                callsign=callsign,
                origin_country=str(row[2] or ""),
                longitude=row[5] if isinstance(row[5], (int, float)) else None,
                latitude=row[6] if isinstance(row[6], (int, float)) else None,
                baro_altitude_m=row[7] if isinstance(row[7], (int, float)) else None,
                on_ground=bool(row[8]),
                velocity_ms=row[9] if isinstance(row[9], (int, float)) else None,
                last_contact=datetime.fromtimestamp(last_contact, tz=UTC),
            )
        )
    return result


class OpenSkyFlightProvider:
    name = "opensky"

    def fetch_by_callsign(self, callsign: str) -> list[FlightState]:
        normalized = callsign.strip().upper()
        if not normalized:
            raise FlightDataUnavailable("callsign 不能为空")
        try:
            response = httpx.get(
                f"{OPEN_SKY_BASE_URL}/states/all",
                params={"callsign": normalized},
                timeout=_HTTP_TIMEOUT_SECONDS,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise FlightDataUnavailable(f"OpenSky 请求失败：{exc}") from exc
        return [
            state
            for state in parse_states(payload)
            if state.callsign.upper() == normalized
        ]


class UnavailableFlightProvider:
    name = "unavailable"

    def fetch_by_callsign(self, callsign: str) -> list[FlightState]:  # noqa: ARG002
        raise FlightDataUnavailable("航班源未配置（GBT_FLIGHT_PROVIDER=unavailable）")


def get_flight_provider():
    from app.config import get_settings

    if get_settings().flight_provider == "opensky":
        return OpenSkyFlightProvider()
    return UnavailableFlightProvider()


__all__ = [
    "OPEN_SKY_BASE_URL",
    "FlightDataUnavailable",
    "FlightState",
    "OpenSkyFlightProvider",
    "UnavailableFlightProvider",
    "get_flight_provider",
    "parse_states",
]
