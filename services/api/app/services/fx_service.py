"""汇率录入与查询。

只做"取最新已录入汇率"这一件事。没有任何外部 API 时，组织可以手工录入；没有记录就是
未知，不生成默认值。``rate`` 语义固定为 1 base = rate quote，反向使用同一条记录时取倒数。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import Unprocessable
from app.models.fx import FxRate


@dataclass(frozen=True)
class FxQuote:
    """把一个 ``currency`` 金额换算成 ``base`` 所需的有效因子。"""

    currency: str
    base_currency: str
    rate: Decimal
    as_of: datetime | None
    source: str | None
    via_inverse: bool


def normalize_currency(currency: str) -> str:
    """3 位字母代码规范化（大写）；非法输入显式 422。行情同步（ADR-0033）共用。"""
    value = currency.strip().upper()
    if len(value) != 3 or not value.isalpha():
        raise Unprocessable("币种必须是 3 位字母代码", details={"currency": currency})
    return value


#: 历史内部名，模块内既有调用点沿用。
_normalize = normalize_currency


def upsert_rate(
    session: Session,
    organization_id: UUID | str,
    *,
    base_currency: str,
    quote_currency: str,
    rate: Decimal,
    as_of: datetime | None = None,
    source: str | None = None,
    actor_user_id: UUID | str | None = None,
) -> FxRate:
    base = _normalize(base_currency)
    quote = _normalize(quote_currency)
    if base == quote:
        raise Unprocessable("基准币种与报价币种不能相同", details={"currency": base})
    if rate <= 0:
        raise Unprocessable("汇率必须大于 0")

    moment = as_of or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)

    existing = session.scalars(
        select(FxRate).where(
            FxRate.organization_id == str(organization_id),
            FxRate.base_currency == base,
            FxRate.quote_currency == quote,
            FxRate.as_of == moment,
        )
    ).one_or_none()
    if existing is not None:
        existing.rate = rate
        existing.source = source
        existing.created_by_user_id = (
            None if actor_user_id is None else str(actor_user_id)
        )
        session.flush()
        return existing

    row = FxRate(
        organization_id=str(organization_id),
        base_currency=base,
        quote_currency=quote,
        rate=rate,
        as_of=moment,
        source=source,
        created_by_user_id=None if actor_user_id is None else str(actor_user_id),
    )
    session.add(row)
    session.flush()
    return row


def latest_rates(
    session: Session,
    organization_id: UUID | str,
    *,
    base_currency: str | None = None,
    quote_currency: str | None = None,
    effective_at: datetime | None = None,
) -> list[FxRate]:
    """每个币种对只返回最新一条；``effective_at`` 用于排除尚未生效的预约汇率。"""
    query = select(FxRate).where(FxRate.organization_id == str(organization_id))
    if base_currency:
        query = query.where(FxRate.base_currency == _normalize(base_currency))
    if quote_currency:
        query = query.where(FxRate.quote_currency == _normalize(quote_currency))
    if effective_at is not None:
        query = query.where(FxRate.as_of <= effective_at)
    rows = session.scalars(
        query.order_by(FxRate.as_of.desc(), FxRate.created_at.desc())
    ).all()
    seen: set[tuple[str, str]] = set()
    result: list[FxRate] = []
    for row in rows:
        key = (row.base_currency, row.quote_currency)
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def conversion_for(
    session: Session, organization_id: UUID | str, *, base: str, currency: str
) -> FxQuote | None:
    """返回 1 单位 ``currency`` 值多少 ``base``；直接/反向记录都支持，缺记录返回 ``None``。"""
    base = _normalize(base)
    currency = _normalize(currency)
    if base == currency:
        return FxQuote(
            currency=currency,
            base_currency=base,
            rate=Decimal("1"),
            as_of=None,
            source=None,
            via_inverse=False,
        )

    now = datetime.now(UTC)
    direct = latest_rates(
        session,
        organization_id,
        base_currency=base,
        quote_currency=currency,
        effective_at=now,
    )
    if direct:
        row = direct[0]
        return FxQuote(
            currency=currency,
            base_currency=base,
            rate=(Decimal("1") / row.rate),
            as_of=row.as_of,
            source=row.source,
            via_inverse=False,
        )

    inverse = latest_rates(
        session,
        organization_id,
        base_currency=currency,
        quote_currency=base,
        effective_at=now,
    )
    if inverse:
        row = inverse[0]
        return FxQuote(
            currency=currency,
            base_currency=base,
            rate=row.rate,
            as_of=row.as_of,
            source=row.source,
            via_inverse=True,
        )
    return None


__all__ = ["FxQuote", "conversion_for", "latest_rates", "normalize_currency", "upsert_rate"]
