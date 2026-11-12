"""FX 行情源同步（§7-8 / ADR-0033）。

把公开行情源的快照写进组织已有的 `fx_rates` 表（ADR-0019），与手工录入共用同一
张表、同一套折算逻辑；区别只在 ``source``（``fx-<provider 名>``）与
``created_by_user_id``（行情行为 NULL，不归到管理员名下）。行情没覆盖的币种进
``missing`` 列表显式暴露，绝不伪造；provider 未配置/网络失败按不可用抛出，
由任务层结算为可见的 FAILED。
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.api.errors import Unprocessable
from app.integrations.fx import FxMarketUnavailable, get_fx_provider
from app.services import fx_service


class FxMarketQuote:
    """一次行情快照：``1 base = rates[currency] currency``，``as_of`` 为行情日期。"""

    __slots__ = ("base", "as_of", "rates")

    def __init__(
        self, *, base: str, as_of: datetime, rates: dict[str, Decimal]
    ) -> None:
        self.base = base
        self.as_of = as_of
        self.rates = rates


def parse_frankfurter(payload: dict[str, Any]) -> FxMarketQuote:
    """解析 Frankfurter 响应。缺日期/缺 rates/结构不对都按不可用处理。"""
    base = payload.get("base")
    raw_date = payload.get("date")
    raw_rates = payload.get("rates")
    if not isinstance(base, str) or not isinstance(raw_date, str) or not isinstance(raw_rates, dict):
        raise FxMarketUnavailable("Frankfurter 响应结构不完整")
    try:
        moment = datetime.fromisoformat(f"{raw_date}T00:00:00").replace(tzinfo=UTC)
    except ValueError as exc:
        raise FxMarketUnavailable(f"Frankfurter 行情日期不可解析：{raw_date}") from exc
    rates: dict[str, Decimal] = {}
    for currency, value in raw_rates.items():
        try:
            rate = Decimal(str(value))
        except Exception as exc:  # noqa: BLE001 —— 单个坏值按不可用处理，不做部分结果
            raise FxMarketUnavailable(f"Frankfurter 汇率不可解析：{currency}={value!r}") from exc
        if rate <= 0:
            raise FxMarketUnavailable(f"Frankfurter 汇率必须为正：{currency}={value!r}")
        rates[currency.upper()] = rate
    return FxMarketQuote(base=base.upper(), as_of=moment, rates=rates)


def sync_rates(
    session,
    organization_id: UUID | str,
    *,
    base_currency: str,
    symbols: list[str],
    provider=None,
    actor_user_id: UUID | str | None = None,
) -> dict[str, Any]:
    """取最新行情并把请求币种写入 `fx_rates`；返回同步摘要（含诚实缺口）。

    ``provider`` 供测试注入；生产路径经 `get_fx_provider()` 按配置解析。
    """
    base = fx_service.normalize_currency(base_currency)
    normalized: list[str] = []
    for symbol in symbols:
        code = fx_service.normalize_currency(symbol)
        if code == base:
            raise Unprocessable(
                "行情币种不能与基准币种相同",
                details={"base_currency": base, "symbol": symbol},
            )
        if code not in normalized:
            normalized.append(code)

    if provider is None:
        provider = get_fx_provider()
    try:
        quote = provider.fetch_latest(base)
    except FxMarketUnavailable:
        raise
    quote = (
        quote
        if isinstance(quote, FxMarketQuote)
        else parse_frankfurter(quote)
    )
    if quote.base != base:
        raise FxMarketUnavailable(
            f"行情基准币种不符：请求 {base}，响应 {quote.base}"
        )

    synced: list[str] = []
    missing: list[str] = []
    for code in normalized:
        rate = quote.rates.get(code)
        if rate is None:
            missing.append(code)
            continue
        fx_service.upsert_rate(
            session,
            organization_id,
            base_currency=base,
            quote_currency=code,
            rate=rate,
            as_of=quote.as_of,
            source=f"fx-{provider.name}",
            actor_user_id=actor_user_id,
        )
        synced.append(code)

    return {
        "synced": len(synced),
        "currencies": synced,
        "missing": missing,
        "base_currency": base,
        "source": f"fx-{provider.name}",
        "as_of": quote.as_of.isoformat(),
    }


__all__ = ["FxMarketQuote", "FxMarketUnavailable", "parse_frankfurter", "sync_rates"]
