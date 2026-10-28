"""离线签证规则目录（共享读取）。"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

CATALOG_PATH = Path(__file__).resolve().parents[1] / "data" / "visa_catalog.json"

DEFAULT_DOCUMENTS: tuple[tuple[str, str], ...] = (
    ("passport", "护照（有效期通常需 ≥ 6 个月）"),
    ("photo", "近期证件照"),
    ("application_form", "签证申请表"),
    ("invitation_letter", "商务邀请函 / 邀请方信息"),
    ("itinerary", "行程与住宿证明"),
    ("insurance", "旅行保险"),
    ("financial_proof", "资金或在职证明"),
)


@lru_cache
def load_catalog() -> dict[str, Any]:
    with CATALOG_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def country_entry(code: str) -> dict[str, Any] | None:
    return load_catalog()["countries"].get(code.upper())


def default_checklist() -> list[dict[str, str]]:
    return [
        {"key": key, "label": label, "status": "MISSING"}
        for key, label in DEFAULT_DOCUMENTS
    ]


__all__ = ["DEFAULT_DOCUMENTS", "country_entry", "default_checklist", "load_catalog"]
