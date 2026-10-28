"""离线地名/坐标解析（geocoding）。

设计文档 v2.0 第 4.4 章："坐标缺失是合法输入，但必须显式暴露"。实现顺序刻意是：

1. 导入表里**已经有** lat/lon 且合法 → 直接用，``source = "spreadsheet"``；
2. 表里只有城市名 → 查本仓库自带的 ``app/data/cities.json`` 离线城市索引，
   ``source = "offline-city-index"``，``confidence`` 按"是否命中主名/别名、是否多国歧义"给分；
3. 都没有 → 返回 ``None``，落库为 ``geocode_status = 'MISSING'``，并在导入报告的
   ``COORDINATES_MISSING`` 里计数，规划引擎把该拜访归入 ``unplaced``。

这样做是刻意的取舍：**绝不为了"让数据好看"而编造坐标**。一个假坐标会让行程看起来
可行，实际却在另一个大洲——这违反 I3（LLM 不得仅凭自身判断宣布可行）。生产环境应把
``OfflineCityGeocoder`` 换成真实地理编码提供方，但接口不变。
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Protocol

DEFAULT_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "cities.json"

#: 命中主名给满分，命中别名打折——别名更容易是"同城不同写法"或误配。
_CONFIDENCE_PRIMARY = 0.95
_CONFIDENCE_ALIAS = 0.80
#: 未提供国家代码时命中城市名 → 存在同名城市跨国的风险，降低置信度并保留告警。
_CONFIDENCE_NO_COUNTRY = 0.55

_NOISE_SUFFIXES = ("市", " City", " city", "市辖区")
_NOISE_RE = re.compile(r"[\s\-_,.()（）·]+")


def normalize_city(value: str) -> str:
    """把城市名规整成索引键：全角转半角、去重音、去空白与标点、去常见后缀。"""
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold().strip()
    for suffix in _NOISE_SUFFIXES:
        suffix_norm = suffix.casefold().strip()
        if suffix_norm and text.endswith(suffix_norm) and len(text) > len(suffix_norm):
            text = text[: -len(suffix_norm)]
    return _NOISE_RE.sub("", text)


@dataclass(frozen=True)
class GeocodeResult:
    """一次地名解析的结果。``source`` 会落到 ``places.geocode_source`` 供审计。"""

    lat: float
    lon: float
    timezone: str | None
    source: str
    confidence: float
    matched_city: str
    country_code: str | None


class Geocoder(Protocol):
    """地理编码接口。换提供方只换实现，不换调用方。"""

    def resolve(self, *, city: str | None, country_code: str | None) -> GeocodeResult | None: ...


class OfflineCityGeocoder:
    """基于仓库内离线城市索引的确定性地理编码器（零网络、零外部依赖）。"""

    SOURCE = "offline-city-index"

    def __init__(self, data_path: Path | str = DEFAULT_DATA_PATH) -> None:
        self.data_path = Path(data_path)
        self._by_country: dict[tuple[str, str], dict] = {}
        self._by_city: dict[str, list[dict]] = {}
        self.version = "unknown"
        self._load()

    def _load(self) -> None:
        payload = json.loads(self.data_path.read_text(encoding="utf-8"))
        self.version = str(payload.get("version", "unknown"))
        for entry in payload.get("cities", []):
            country = (entry.get("country_code") or "").upper() or None
            names = [entry["city"], *entry.get("aliases", [])]
            for index, name in enumerate(names):
                key = normalize_city(name)
                if not key:
                    continue
                record = {
                    "entry": entry,
                    "is_alias": index > 0,
                    "country_code": country,
                }
                self._by_city.setdefault(key, []).append(record)
                if country:
                    self._by_country.setdefault((key, country), record)

    def resolve(self, *, city: str | None, country_code: str | None) -> GeocodeResult | None:
        key = normalize_city(city or "")
        if not key:
            return None
        country = (country_code or "").upper() or None

        record = self._by_country.get((key, country)) if country else None
        if record is not None:
            confidence = _CONFIDENCE_ALIAS if record["is_alias"] else _CONFIDENCE_PRIMARY
        else:
            candidates = self._by_city.get(key, [])
            if not candidates:
                return None
            if len({c["country_code"] for c in candidates}) > 1 and country is None:
                # 同名跨多国且调用方没给国家：可以给结果，但必须把不确定性写进置信度。
                record = candidates[0]
                confidence = _CONFIDENCE_NO_COUNTRY
            else:
                record = candidates[0]
                confidence = _CONFIDENCE_ALIAS if record["is_alias"] else _CONFIDENCE_PRIMARY
                if country is None:
                    confidence = min(confidence, _CONFIDENCE_NO_COUNTRY)

        entry = record["entry"]
        return GeocodeResult(
            lat=float(entry["lat"]),
            lon=float(entry["lon"]),
            timezone=entry.get("timezone"),
            source=self.SOURCE,
            confidence=round(confidence, 3),
            matched_city=entry["city"],
            country_code=record["country_code"],
        )

    def known_cities(self) -> int:
        return len({id(rec["entry"]) for recs in self._by_city.values() for rec in recs})


@lru_cache
def get_geocoder() -> Geocoder:
    """进程级单例。索引是只读的，换来每行导入不再重复解析 JSON。"""
    return OfflineCityGeocoder()