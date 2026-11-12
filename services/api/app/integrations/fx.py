"""FX 行情源适配器边界（§7-8 / ADR-0033）。

``frankfurter`` 适配器接 Frankfurter（ECB 欧央行参考汇率，公开免钥），语义与 CRM 适配器
一致：未配置时 ``unavailable`` 显式失败，不伪造汇率；适配器只负责"取行情"，写库统一走
`fx_market_service.sync_rates`（与 ADR-0019 的手工录入共用同一张 `fx_rates` 表，用
``source`` 区分来源）。
"""

from __future__ import annotations

from typing import Protocol

import httpx

from app.config import get_settings

FRANKFURTER_BASE_URL = "https://api.frankfurter.dev/v1"
#: 行情源是单次外部调用，10 秒足够；超时按不可用处理，不做部分结果。
_HTTP_TIMEOUT_SECONDS = 10.0


class FxMarketUnavailable(RuntimeError):
    pass


class FxMarketProvider(Protocol):
    name: str

    def fetch_latest(self, base: str) -> dict: ...


class FrankfurterFxProvider:
    """Frankfurter（ECB 参考汇率）。响应形如
    ``{"amount":1,"base":"EUR","date":"2026-11-06","rates":{"USD":1.0812,...}}``，
    工作日更新；解析交给 `fx_market_service.parse_frankfurter`（有独立单测）。"""

    name = "frankfurter"

    def fetch_latest(self, base: str) -> dict:
        try:
            response = httpx.get(
                f"{FRANKFURTER_BASE_URL}/latest",
                params={"base": base},
                timeout=_HTTP_TIMEOUT_SECONDS,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise FxMarketUnavailable(f"Frankfurter 行情获取失败：{exc}") from exc
        if not isinstance(payload, dict) or payload.get("base") != base:
            raise FxMarketUnavailable("Frankfurter 响应与请求币种不一致")
        return payload


class UnavailableFxProvider:
    name = "unavailable"

    def fetch_latest(self, base: str) -> dict:  # noqa: ARG002
        raise FxMarketUnavailable("FX 行情源未配置（GBT_FX_PROVIDER=unavailable）")


def get_fx_provider() -> FxMarketProvider:
    if get_settings().fx_provider == "frankfurter":
        return FrankfurterFxProvider()
    return UnavailableFxProvider()


__all__ = [
    "FRANKFURTER_BASE_URL",
    "FrankfurterFxProvider",
    "FxMarketProvider",
    "FxMarketUnavailable",
    "UnavailableFxProvider",
    "get_fx_provider",
]
