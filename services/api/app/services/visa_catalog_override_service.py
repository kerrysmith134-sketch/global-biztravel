"""签证目录维护（ADR-0034 网页形式兜底）。

签证官方数据源没有免费 API（Timatic 收费），目录用「离线基线 + 组织级覆盖」两层：

- 基线：``app/data/visa_catalog.json``（只读、进版本库、可评审）；
- 覆盖：``visa_catalog_overrides`` 表（ADMIN 经维护页修改，整条按国别覆盖基线）。

读取合并时 ``manual_confirmation_required`` 强制为真——无论来源如何，
签证要求都只是"出发前需人核验"的提醒，不给确定性结论（ADR-0027）。
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import NotFound, Unprocessable
from app.models.visa_override import VisaCatalogOverride
from app.services import visa_catalog

_ENTRY_KEYS = ("label", "business_entry", "review_hint")


def _normalize_code(code: str) -> str:
    value = code.strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", value):
        raise Unprocessable("国家代码必须是 2 位字母（ISO 3166-1 alpha-2）", details={"country_code": code})
    return value


def _validate_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """覆盖条目必须给 label + business_entry；其余键自由（JSONB）。"""
    if not isinstance(entry, dict):
        raise Unprocessable("条目必须是对象")
    for key in _ENTRY_KEYS[:2]:
        value = entry.get(key)
        if not isinstance(value, str) or not value.strip():
            raise Unprocessable(f"条目缺少 {key}（网页表单必填项）", details={"field": key})
    return entry


def upsert_override(
    session: Session,
    organization_id: UUID | str,
    *,
    country_code: str,
    entry: dict[str, Any],
    actor_user_id: UUID | str | None = None,
) -> VisaCatalogOverride:
    code = _normalize_code(country_code)
    validated = _validate_entry(entry)
    # 覆盖条目一律保持人工确认语义（ADR-0027），网页维护也不例外。
    validated = dict(validated)
    validated["manual_confirmation_required"] = True

    existing = session.scalars(
        select(VisaCatalogOverride).where(
            VisaCatalogOverride.organization_id == str(organization_id),
            VisaCatalogOverride.country_code == code,
        )
    ).one_or_none()
    if existing is not None:
        existing.entry = validated
        existing.updated_by_user_id = None if actor_user_id is None else str(actor_user_id)
        session.flush()
        return existing

    row = VisaCatalogOverride(
        organization_id=str(organization_id),
        country_code=code,
        entry=validated,
        updated_by_user_id=None if actor_user_id is None else str(actor_user_id),
    )
    session.add(row)
    session.flush()
    return row


def delete_override(session: Session, organization_id: UUID | str, *, country_code: str) -> None:
    code = _normalize_code(country_code)
    existing = session.scalars(
        select(VisaCatalogOverride).where(
            VisaCatalogOverride.organization_id == str(organization_id),
            VisaCatalogOverride.country_code == code,
        )
    ).one_or_none()
    if existing is None:
        raise NotFound("该国家没有组织级覆盖")
    session.delete(existing)
    session.flush()


def list_overrides(session: Session, organization_id: UUID | str) -> dict[str, dict[str, Any]]:
    rows = session.scalars(
        select(VisaCatalogOverride).where(
            VisaCatalogOverride.organization_id == str(organization_id)
        )
    ).all()
    return {row.country_code: dict(row.entry) for row in rows}


def effective_catalog(session: Session, organization_id: UUID | str) -> dict[str, Any]:
    """离线基线 + 组织覆盖合并后的目录（覆盖整条替换，但人工确认强制为真）。"""
    baseline = dict(visa_catalog.load_catalog())
    countries = dict(baseline.get("countries", {}))
    for code, entry in list_overrides(session, organization_id).items():
        merged = dict(entry)
        merged["manual_confirmation_required"] = True
        countries[code] = merged
    baseline["countries"] = countries
    baseline["effective_at"] = datetime.now(UTC).isoformat()
    return baseline


__all__ = [
    "delete_override",
    "effective_catalog",
    "list_overrides",
    "upsert_override",
]
