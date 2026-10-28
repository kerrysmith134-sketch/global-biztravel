"""Excel 两阶段导入（设计文档 v2.0 第 4.4 章）。

客户名单是**脏数据**：名字写法不一、只有城市没有坐标、时间窗格式随手、同一客户在不同行
出现、有人把"服务时长"填成"2 小时"。如果导入器一边读一边写库，用户第一次看到结果时
数据库已经被污染，而且**无法回退到"导入前"**。

因此这里拆成两个明确阶段：

- **阶段一 `parse`**：只读，把每一行的原始值、规整值、问题清单写进 ``import_rows``，
  外加一份按问题码聚合的**净空报告**。**一个字节的业务数据都不落库。**
- **阶段二 `confirm`**：用户看到报告后勾选行号，服务端才真正创建客户/地点/拜访，并把
  每一行的去向（创建 / 复用 / 拒绝 / 跳过）回写到行记录。重复确认会被拒绝。

三个阶段设计要点：

1. **问题分级**：``ERROR`` 让整行不可接受（缺客户名），``WARNING`` 让行可接受但必须知情
   （缺坐标、时长异常）。导入器**从不**替用户猜测缺失的关键字段。
2. **重复既不静默丢弃也不静默入库**：批内重复与"该出行已有拜访"都标出来，交由用户勾选。
3. **落库前去重**：按 ``external_ref`` / ``(客户名, 国家)`` 复用客户，按 ``(地点名, 城市, 国家)``
   复用地点，避免一次导入把客户表翻倍。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import Conflict, NotFound, Unprocessable, UnsupportedMedia
from app.models.geo import Customer, Place
from app.models.trip import ImportBatch, ImportRow, Trip, Visit
from app.services import customer_service, place_service, trip_service, visit_service
from app.services.geocoding import Geocoder, get_geocoder

MAX_ROWS = 2000
ALLOWED_SUFFIXES = (".xlsx", ".xlsm")
SCHEMA_VERSION = "1"

#: 表头别名 → 规范字段名。中英文都收，因为客户名单常是双语混排。
COLUMN_ALIASES: dict[str, str] = {
    "customer_name": "customer_name",
    "customer": "customer_name",
    "company": "customer_name",
    "客户": "customer_name",
    "客户名称": "customer_name",
    "公司": "customer_name",
    "公司名称": "customer_name",
    "visit_title": "visit_title",
    "title": "visit_title",
    "topic": "visit_title",
    "拜访主题": "visit_title",
    "主题": "visit_title",
    "external_ref": "external_ref",
    "customer_code": "external_ref",
    "客户编号": "external_ref",
    "tier": "tier",
    "grade": "tier",
    "客户等级": "tier",
    "country_code": "country_code",
    "country": "country_code",
    "国家": "country_code",
    "国家代码": "country_code",
    "city": "city",
    "城市": "city",
    "address_line": "address_line",
    "address": "address_line",
    "地址": "address_line",
    "详细地址": "address_line",
    "region": "region",
    "state": "region",
    "省": "region",
    "省份": "region",
    "latitude": "latitude",
    "lat": "latitude",
    "纬度": "latitude",
    "longitude": "longitude",
    "lon": "longitude",
    "lng": "longitude",
    "经度": "longitude",
    "timezone": "timezone",
    "tz": "timezone",
    "时区": "timezone",
    "service_minutes": "service_minutes",
    "duration_minutes": "service_minutes",
    "visit_minutes": "service_minutes",
    "服务时长": "service_minutes",
    "停留时长": "service_minutes",
    "priority": "priority",
    "优先级": "priority",
    "business_value": "business_value",
    "value": "business_value",
    "商业价值": "business_value",
    "movement_mode": "movement_mode",
    "transport": "movement_mode",
    "交通方式": "movement_mode",
    "is_immovable": "is_immovable",
    "fixed": "is_immovable",
    "时间固定": "is_immovable",
    "time_window_start": "time_window_start",
    "window_start": "time_window_start",
    "earliest": "time_window_start",
    "最早到达": "time_window_start",
    "时间窗开始": "time_window_start",
    "time_window_end": "time_window_end",
    "window_end": "time_window_end",
    "latest": "time_window_end",
    "最晚离开": "time_window_end",
    "时间窗结束": "time_window_end",
    "contact_name": "contact_name",
    "联系人": "contact_name",
    "contact_email": "contact_email",
    "email": "contact_email",
    "邮箱": "contact_email",
    "contact_phone": "contact_phone",
    "phone": "contact_phone",
    "电话": "contact_phone",
    "industry": "industry",
    "行业": "industry",
}

REQUIRED_FIELDS = ("customer_name",)

#: 问题码 → (等级, 面向用户的说明, 建议动作)。
ISSUE_CATALOG: dict[str, tuple[str, str, str]] = {
    "MISSING_COLUMN": ("ERROR", "缺少必需列", "在表头补上该列后重新上传"),
    "NO_ROWS": ("ERROR", "表格没有数据行", "确认工作表是否为空"),
    "ROW_LIMIT_EXCEEDED": ("ERROR", f"数据行超过 {MAX_ROWS} 行上限", "拆分成多个文件后分批导入"),
    "MISSING_CUSTOMER_NAME": ("ERROR", "客户名称为空", "补齐客户名称，或删除该行"),
    "INVALID_LATITUDE": ("ERROR", "纬度不是有效数值或超出 ±90", "修正纬度或留空让系统按城市解析"),
    "INVALID_LONGITUDE": ("ERROR", "经度不是有效数值或超出 ±180", "修正经度或留空让系统按城市解析"),
    "COORDINATES_PARTIAL": ("WARNING", "只填了纬度或经度其中一项", "两项都填或都不填"),
    "COORDINATES_MISSING": (
        "WARNING",
        "无法确定坐标（既没有经纬度，城市也不在离线索引中）",
        "补经纬度，或改用索引中已知的城市名",
    ),
    "GEOCODE_LOW_CONFIDENCE": ("WARNING", "坐标来自城市级近似值", "如有精确地址请补经纬度"),
    "INVALID_COUNTRY_CODE": ("WARNING", "国家代码不是两位字母", "使用 ISO 3166-1 alpha-2，如 CN/DE"),
    "INVALID_SERVICE_MINUTES": ("WARNING", "停留时长不是正整数分钟", "填 1–1440 之间的整数"),
    "INVALID_PRIORITY": ("WARNING", "优先级不是 1–3", "填 1（高）到 3（低）"),
    "INVALID_BUSINESS_VALUE": ("WARNING", "商业价值不是有效金额", "填非负数值，不带货币符号"),
    "INVALID_TIME_WINDOW": ("WARNING", "时间窗起止顺序错误", "让结束晚于开始"),
    "INVALID_TIME_WINDOW_FORMAT": ("WARNING", "时间窗无法解析为时间", "用 YYYY-MM-DD HH:MM 格式"),
    "INVALID_EMAIL": ("WARNING", "邮箱格式可疑", "修正或留空"),
    "INVALID_MOVEMENT_MODE": ("WARNING", "交通方式不在允许集合内", "用 GROUND/RAIL/AIR/SEA"),
    "UNKNOWN_COLUMNS": ("WARNING", "存在无法识别的列", "这些列会被忽略，确认是否拼写有误"),
    "DUPLICATE_ROW": ("WARNING", "与前面某行指向同一客户与时间", "只保留一行，其余不要勾选"),
    "ALREADY_IN_TRIP": ("WARNING", "该出行中已存在同客户同地点的拜访", "确认是否重复安排"),
}

_SEVERITY_ORDER = {"ERROR": 0, "WARNING": 1}
_TRUTHY = {"1", "true", "yes", "y", "是", "固定", "immovable"}


@dataclass
class ParsedRow:
    row_number: int
    raw: dict[str, Any]
    normalized: dict[str, Any] | None
    issues: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_error(self) -> bool:
        return any(i["severity"] == "ERROR" for i in self.issues)

    @property
    def status(self) -> str:
        if self.has_error:
            return "INVALID"
        if any(i["code"] == "DUPLICATE_ROW" for i in self.issues):
            return "DUPLICATE"
        return "VALID"


# --------------------------------------------------------------------- 解析
def _issue(code: str, *, field_name: str | None = None, severity: str | None = None, message: str | None = None) -> dict[str, Any]:
    catalog_severity, catalog_message, _ = ISSUE_CATALOG.get(code, ("WARNING", code, ""))
    return {
        "code": code,
        "field": field_name,
        "severity": severity or catalog_severity,
        "message": message or catalog_message,
    }


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _decimal(value: Any, *, code: str, issues: list[dict[str, Any]], field_name: str) -> Decimal | None:
    text = _text(value)
    if text is None:
        return None
    cleaned = text.replace(",", "").replace("￥", "").replace("$", "").replace("¥", "").strip()
    try:
        amount = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        issues.append(_issue(code, field_name=field_name))
        return None
    if amount < 0:
        issues.append(_issue(code, field_name=field_name))
        return None
    return amount


def _int_in_range(
    value: Any,
    *,
    code: str,
    field_name: str,
    low: int,
    high: int,
    issues: list[dict[str, Any]],
) -> int | None:
    text = _text(value)
    if text is None:
        return None
    try:
        number = int(float(text))
    except (TypeError, ValueError):
        issues.append(_issue(code, field_name=field_name))
        return None
    if not low <= number <= high:
        issues.append(_issue(code, field_name=field_name))
        return None
    return number


def _float_coord(
    value: Any, *, code: str, field_name: str, low: float, high: float, issues: list[dict[str, Any]]
) -> float | None:
    text = _text(value)
    if text is None:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        issues.append(_issue(code, field_name=field_name))
        return None
    if not low <= number <= high:
        issues.append(_issue(code, field_name=field_name))
        return None
    return number


def _coerce_datetime(
    value: Any, *, tz_name: str, field_name: str, issues: list[dict[str, Any]]
) -> datetime | None:
    """把单元格值解析成带时区的 datetime。

    无时区的值（Excel 日期单元格、``2026-11-03 09:00``）按**该出行的主时区**解释——
    这是唯一不会让"上午 9 点"漂到别的国家去的选择，而且出行主页面上到处都写着这个时区。
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo("UTC")

    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip().replace("/", "-")
        parsed = None
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%d",
            "%Y年%m月%d日 %H:%M",
            "%Y年%m月%d日",
        ):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        if parsed is None:
            issues.append(_issue("INVALID_TIME_WINDOW_FORMAT", field_name=field_name))
            return None
    assert parsed is not None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=tz)
    return parsed


def _is_truthy(value: Any) -> bool:
    text = _text(value)
    return text is not None and text.casefold() in _TRUTHY


def _normalize_header(value: Any) -> str | None:
    text = _text(value)
    if text is None:
        return None
    return text.casefold().replace(" ", "").replace("_", "").replace("-", "")


def read_sheet(data: bytes, filename: str) -> tuple[list[str], list[tuple[int, dict[str, Any]]]]:
    """读取首个工作表，返回 ``(规范列名列表, [(excel 行号, {规范字段: 原始值})])``。"""
    suffix = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    if suffix not in ALLOWED_SUFFIXES:
        raise UnsupportedMedia(
            "只支持 .xlsx / .xlsm 文件；旧版 .xls 请另存为 .xlsx 后重试",
            details={"filename": filename, "allowed": list(ALLOWED_SUFFIXES)},
        )
    try:
        workbook = load_workbook(BytesIO(data), data_only=True, read_only=True)
    except (InvalidFileException, KeyError, OSError, ValueError) as exc:
        raise UnsupportedMedia("无法解析该文件，可能不是有效的 Excel 工作簿") from exc

    try:
        worksheet = workbook.worksheets[0] if workbook.worksheets else None
        if worksheet is None:
            raise Unprocessable("工作簿中没有工作表", code="EMPTY_WORKBOOK")

        raw_headers: list[Any] = []
        rows: list[tuple[int, dict[str, Any]]] = []
        mapping: dict[int, str] = {}
        seen_header = False

        for index, values in enumerate(worksheet.iter_rows(values_only=True), start=1):
            if all(v is None or (isinstance(v, str) and not v.strip()) for v in values):
                continue
            if not seen_header:
                raw_headers = list(values)
                alias_use: dict[str, str] = {}
                for position, header in enumerate(raw_headers):
                    canonical = COLUMN_ALIASES.get(_normalize_header(header) or "")
                    if canonical is None:
                        continue
                    # 同名列出现两次时以首次为准，第二次视为未知列（用户需要知道）。
                    if canonical not in alias_use.values():
                        mapping[position] = canonical
                        alias_use[_text(header) or str(position)] = canonical
                seen_header = True
                continue

            raw = {
                canonical: values[position] if position < len(values) else None
                for position, canonical in mapping.items()
            }
            rows.append((index, raw))
            if len(rows) > MAX_ROWS:
                raise Unprocessable(
                    f"数据行超过 {MAX_ROWS} 行上限，请拆分文件",
                    code="ROW_LIMIT_EXCEEDED",
                    details={"max_rows": MAX_ROWS},
                )
    finally:
        workbook.close()

    if not seen_header:
        raise Unprocessable("工作表为空，未找到表头", code="NO_ROWS")
    if not rows:
        raise Unprocessable("表格没有数据行", code="NO_ROWS")
    return [_text(h) or "" for h in raw_headers], rows


def _validate_row(
    row_number: int,
    raw: dict[str, Any],
    *,
    trip: Trip,
    geocoder: Geocoder,
) -> ParsedRow:
    issues: list[dict[str, Any]] = []
    customer_name = _text(raw.get("customer_name"))
    if customer_name is None:
        issues.append(_issue("MISSING_CUSTOMER_NAME", field_name="customer_name"))

    country_code = _text(raw.get("country_code"))
    if country_code is not None:
        country_code = country_code.upper()
        if len(country_code) != 2 or not country_code.isalpha():
            issues.append(_issue("INVALID_COUNTRY_CODE", field_name="country_code"))
            country_code = None

    city = _text(raw.get("city"))
    lat = _float_coord(raw.get("latitude"), code="INVALID_LATITUDE", field_name="latitude", low=-90, high=90, issues=issues)
    lon = _float_coord(raw.get("longitude"), code="INVALID_LONGITUDE", field_name="longitude", low=-180, high=180, issues=issues)
    if (lat is None) != (lon is None):
        issues.append(_issue("COORDINATES_PARTIAL"))
        lat = lon = None

    timezone = _text(raw.get("timezone"))
    if lat is None and lon is None:
        resolved = geocoder.resolve(city=city, country_code=country_code)
        if resolved is None:
            issues.append(_issue("COORDINATES_MISSING", field_name="city"))
        else:
            lat, lon = resolved.lat, resolved.lon
            timezone = timezone or resolved.timezone
            country_code = country_code or resolved.country_code
            if resolved.confidence < 0.7:
                issues.append(_issue("GEOCODE_LOW_CONFIDENCE", field_name="city"))

    service_minutes = _int_in_range(
        raw.get("service_minutes"),
        code="INVALID_SERVICE_MINUTES",
        field_name="service_minutes",
        low=1,
        high=24 * 60,
        issues=issues,
    )
    if raw.get("service_minutes") is not None and service_minutes is None:
        pass  # 问题已记录，下面用默认值

    priority = _int_in_range(
        raw.get("priority"), code="INVALID_PRIORITY", field_name="priority", low=1, high=3, issues=issues
    )
    business_value = _decimal(
        raw.get("business_value"), code="INVALID_BUSINESS_VALUE", issues=issues, field_name="business_value"
    )

    window_start = _coerce_datetime(
        raw.get("time_window_start"), tz_name=trip.home_timezone, field_name="time_window_start", issues=issues
    )
    window_end = _coerce_datetime(
        raw.get("time_window_end"), tz_name=trip.home_timezone, field_name="time_window_end", issues=issues
    )
    if window_start is not None and window_end is not None and window_end <= window_start:
        issues.append(_issue("INVALID_TIME_WINDOW"))
        window_end = None

    movement_mode = _text(raw.get("movement_mode"))
    if movement_mode is not None:
        movement_mode = movement_mode.upper()
        if movement_mode not in {"GROUND", "RAIL", "AIR", "SEA"}:
            issues.append(_issue("INVALID_MOVEMENT_MODE", field_name="movement_mode"))
            movement_mode = None

    email = _text(raw.get("contact_email"))
    if email is not None and ("@" not in email or "." not in email.split("@")[-1]):
        issues.append(_issue("INVALID_EMAIL", field_name="contact_email"))

    tier = (_text(raw.get("tier")) or "C").upper()
    if tier not in {"A", "B", "C"}:
        tier = "C"

    normalized: dict[str, Any] = {
        "customer_name": customer_name,
        "visit_title": _text(raw.get("visit_title")) or (customer_name and f"拜访 {customer_name}"),
        "external_ref": _text(raw.get("external_ref")),
        "tier": tier,
        "country_code": country_code,
        "city": city,
        "address_line": _text(raw.get("address_line")),
        "region": _text(raw.get("region")),
        "latitude": lat,
        "longitude": lon,
        "timezone": timezone,
        "service_minutes": service_minutes if service_minutes is not None else 60,
        "priority": priority if priority is not None else 2,
        "business_value": str(business_value) if business_value is not None else "0",
        "movement_mode": movement_mode or "GROUND",
        "is_immovable": _is_truthy(raw.get("is_immovable")),
        "time_window_start": window_start.isoformat() if window_start else None,
        "time_window_end": window_end.isoformat() if window_end else None,
        "contact_name": _text(raw.get("contact_name")),
        "contact_email": email,
        "contact_phone": _text(raw.get("contact_phone")),
        "industry": _text(raw.get("industry")),
        "geocode_status": "RESOLVED" if lat is not None else "MISSING",
    }
    return ParsedRow(row_number=row_number, raw=_jsonable(raw), normalized=normalized, issues=issues)


def _dedupe_key(normalized: dict[str, Any]) -> tuple[Any, ...]:
    return (
        (normalized.get("customer_name") or "").casefold(),
        (normalized.get("city") or "").casefold(),
        (normalized.get("country_code") or "").upper(),
        normalized.get("time_window_start") or "",
    )


def _existing_visit_keys(session: Session, organization_id: UUID | str, trip_id: UUID) -> set[tuple[str, str, str]]:
    """本出行已有拜访的 (客户名, 城市, 国家) 指纹，用于提示重复安排。"""
    rows = session.execute(
        select(Customer.name, Place.name, Place.city, Place.country_code)
        .join(Visit, Visit.customer_id == Customer.id)
        .join(Place, Place.id == Visit.place_id)
        .where(Visit.organization_id == organization_id, Visit.trip_id == trip_id)
    ).all()
    return {
        ((name or "").casefold(), (city or "").casefold(), (country or "").upper())
        for name, _place_name, city, country in rows
    }


def _build_report(
    *,
    parsed: list[ParsedRow],
    columns: list[str],
    recognized: list[str],
    unknown: list[str],
    missing: list[str],
    geocoder: Geocoder,
    already_in_trip: int,
) -> dict[str, Any]:
    issue_index: dict[str, dict[str, Any]] = {}
    for row in parsed:
        for issue in row.issues:
            entry = issue_index.setdefault(
                issue["code"],
                {
                    "code": issue["code"],
                    "severity": issue["severity"],
                    "message": issue["message"],
                    "suggestion": ISSUE_CATALOG.get(issue["code"], ("", "", ""))[2],
                    "count": 0,
                    "sample_rows": [],
                },
            )
            entry["count"] += 1
            if len(entry["sample_rows"]) < 10:
                entry["sample_rows"].append(row.row_number)

    unresolved = [r.row_number for r in parsed if r.normalized and r.normalized["geocode_status"] == "MISSING"]
    issues = sorted(issue_index.values(), key=lambda e: (_SEVERITY_ORDER.get(e["severity"], 2), -e["count"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "geocoder": {"source": getattr(geocoder, "SOURCE", "unknown"), "version": getattr(geocoder, "version", "unknown")},
        "columns": {
            "sheet": columns,
            "recognized": sorted(set(recognized)),
            "unknown": unknown,
            "missing_required": missing,
        },
        "counts": {
            "total": len(parsed),
            "valid": sum(1 for r in parsed if r.status == "VALID"),
            "invalid": sum(1 for r in parsed if r.status == "INVALID"),
            "duplicate": sum(1 for r in parsed if r.status == "DUPLICATE"),
            "unresolved_coordinates": len(unresolved),
            "already_in_trip": already_in_trip,
        },
        "issues": issues,
        "unresolved_coordinates": {"count": len(unresolved), "rows": unresolved[:50]},
    }


# ------------------------------------------------------------------- 阶段一
def parse_import(
    session: Session,
    organization_id: UUID | str,
    trip_id: UUID,
    *,
    filename: str,
    data: bytes,
    created_by_user_id: UUID | None = None,
    geocoder: Geocoder | None = None,
) -> tuple[ImportBatch, list[ImportRow]]:
    trip = trip_service.require_trip(session, organization_id, trip_id)
    geocoder = geocoder or get_geocoder()
    file_sha256 = hashlib.sha256(data).hexdigest()

    columns, raw_rows = read_sheet(data, filename)
    recognized: list[str] = []
    unknown: list[str] = []
    for header in columns:
        if not header.strip():
            continue  # 表头里的空列（尾部多选的空单元格）不算"未知列"
        canonical = COLUMN_ALIASES.get(_normalize_header(header) or "")
        if canonical is None:
            if header not in unknown:
                unknown.append(header)
        elif canonical not in recognized:
            recognized.append(canonical)
    missing = [f for f in REQUIRED_FIELDS if f not in recognized]
    if missing:
        raise Unprocessable(
            "表格缺少必需列：" + "、".join(missing),
            code="MISSING_COLUMN",
            details={"missing_columns": missing, "recognized": recognized, "unknown": unknown},
        )

    parsed = [_validate_row(number, raw, trip=trip, geocoder=geocoder) for number, raw in raw_rows]

    # 批内去重：从第二行起，命中相同 (客户, 城市, 国家, 时间窗) 的标为 DUPLICATE。
    seen: dict[tuple[Any, ...], int] = {}
    for row in parsed:
        if row.normalized is None:
            continue
        key = _dedupe_key(row.normalized)
        first = seen.get(key)
        if first is None:
            seen[key] = row.row_number
            continue
        row.issues.append(
            _issue("DUPLICATE_ROW", message=f"与第 {first} 行重复（同客户、同城市、同时间窗）")
        )

    existing = _existing_visit_keys(session, organization_id, trip_id)
    already = 0
    for row in parsed:
        if row.normalized is None:
            continue
        fingerprint = (
            (row.normalized.get("customer_name") or "").casefold(),
            (row.normalized.get("city") or "").casefold(),
            (row.normalized.get("country_code") or "").upper(),
        )
        if fingerprint in existing:
            already += 1
            row.issues.append(_issue("ALREADY_IN_TRIP"))
    if unknown:
        for row in parsed:
            row.issues.append(_issue("UNKNOWN_COLUMNS", message="以下列被忽略：" + "、".join(unknown)))

    report = _build_report(
        parsed=parsed,
        columns=columns,
        recognized=recognized,
        unknown=unknown,
        missing=missing,
        geocoder=geocoder,
        already_in_trip=already,
    )

    batch = ImportBatch(
        organization_id=organization_id,
        trip_id=trip_id,
        filename=filename,
        file_sha256=file_sha256,
        status="PARSED",
        total_rows=len(parsed),
        valid_rows=report["counts"]["valid"],
        invalid_rows=report["counts"]["invalid"],
        duplicate_rows=report["counts"]["duplicate"],
        report=report,
        created_by_user_id=created_by_user_id,
    )
    session.add(batch)
    session.flush()

    rows: list[ImportRow] = []
    for parsed_row in parsed:
        rows.append(
            ImportRow(
                organization_id=organization_id,
                batch_id=batch.id,
                row_number=parsed_row.row_number,
                raw=parsed_row.raw,
                normalized=parsed_row.normalized,
                status=parsed_row.status,
                issues=parsed_row.issues,
            )
        )
    session.add_all(rows)
    session.flush()
    return batch, rows


# ------------------------------------------------------------------- 阶段二
def confirm_import(
    session: Session,
    organization_id: UUID | str,
    trip_id: UUID,
    *,
    import_id: UUID,
    accepted_row_numbers: list[int] | None,
) -> dict[str, Any]:
    """按用户勾选落库。重复确认会被拒绝（409），因为落库不是幂等操作。"""
    trip = trip_service.require_trip(session, organization_id, trip_id)
    batch = session.scalars(
        select(ImportBatch)
        .where(
            ImportBatch.organization_id == organization_id,
            ImportBatch.id == import_id,
            ImportBatch.trip_id == trip_id,
        )
        .with_for_update()
    ).one_or_none()
    if batch is None:
        raise NotFound("导入批次不存在", details={"import_id": str(import_id)})
    if batch.status != "PARSED":
        raise Conflict(
            "该导入批次已处理，不能重复确认",
            code="IMPORT_ALREADY_CONFIRMED",
            details={"import_id": str(import_id), "status": batch.status},
        )

    rows = list(
        session.scalars(
            select(ImportRow).where(ImportRow.batch_id == batch.id).order_by(ImportRow.row_number)
        ).all()
    )
    requested = set(accepted_row_numbers or [])
    auto_accept = not requested

    stats = {
        "accepted_rows": 0,
        "rejected_rows": 0,
        "created_customers": 0,
        "created_places": 0,
        "created_visits": 0,
        "reused_customers": 0,
        "reused_places": 0,
    }
    warnings: list[str] = []

    for row in rows:
        normalized = row.normalized or {}
        selected = auto_accept and row.status == "VALID" or row.row_number in requested

        if row.status == "INVALID":
            if row.row_number in requested:
                warnings.append(f"第 {row.row_number} 行存在阻断性问题，已拒绝入库")
                row.status = "REJECTED"
                stats["rejected_rows"] += 1
            else:
                row.status = "SKIPPED"
            continue
        if not selected:
            row.status = "SKIPPED"
            continue

        customer, reused_customer = customer_service.resolve_or_create_customer(
            session,
            organization_id,
            name=normalized["customer_name"],
            country_code=normalized.get("country_code"),
            external_ref=normalized.get("external_ref"),
            tier=normalized.get("tier", "C"),
            industry=normalized.get("industry"),
            primary_contact_name=normalized.get("contact_name"),
            primary_contact_email=normalized.get("contact_email"),
            primary_contact_phone=normalized.get("contact_phone"),
        )
        stats["reused_customers" if reused_customer else "created_customers"] += 1

        place, reused_place = place_service.resolve_or_create_place(
            session,
            organization_id,
            name=normalized.get("address_line") or normalized["customer_name"],
            city=normalized.get("city"),
            country_code=normalized.get("country_code"),
            address_line=normalized.get("address_line"),
            region=normalized.get("region"),
            timezone=normalized.get("timezone"),
            lat=normalized.get("latitude"),
            lon=normalized.get("longitude"),
        )
        stats["reused_places" if reused_place else "created_places"] += 1
        place_service.link_customer_place(
            session, organization_id, customer_id=customer.id, place_id=place.id, is_primary=True
        )

        visit = visit_service.create_visit_raw(
            session,
            organization_id,
            trip_id=trip.id,
            place_id=place.id,
            customer_id=customer.id,
            title=normalized.get("visit_title"),
            service_minutes=normalized.get("service_minutes", 60),
            priority=normalized.get("priority", 2),
            business_value=Decimal(normalized.get("business_value") or "0"),
            is_immovable=bool(normalized.get("is_immovable")),
            movement_mode=normalized.get("movement_mode", "GROUND"),
            time_window_start=_parse_iso(normalized.get("time_window_start")),
            time_window_end=_parse_iso(normalized.get("time_window_end")),
        )
        stats["created_visits"] += 1
        stats["accepted_rows"] += 1
        row.status = "ACCEPTED"
        row.customer_id = customer.id
        row.place_id = place.id
        row.visit_id = visit.id

        if normalized.get("geocode_status") == "MISSING":
            warnings.append(f"第 {row.row_number} 行坐标缺失，该拜访不会进入路线优化")

    batch.status = "CONFIRMED"
    batch.confirmed_at = datetime.now(UTC)
    batch.report = {
        **(batch.report or {}),
        "confirmation": {
            "at": batch.confirmed_at.isoformat(),
            "accepted_row_numbers": sorted(requested) if requested else "ALL_VALID",
            "stats": dict(stats),
            "warnings": warnings[:50],
        },
    }
    session.flush()
    # 一次导入只让既有计划失效一次，而不是每行一次。
    trip_service.touch_trip(session, trip.id, organization_id)
    return {"import_id": batch.id, "status": batch.status, "warnings": warnings[:50], **stats}


# ------------------------------------------------------------------- 只读查询
def get_batch(
    session: Session, organization_id: UUID | str, import_id: UUID
) -> ImportBatch | None:
    """按组织取导入批次（只读）。路由侧用它组装 ``ImportParseRead``（如刷新预览页）。"""
    return session.scalars(
        select(ImportBatch).where(
            ImportBatch.organization_id == organization_id, ImportBatch.id == import_id
        )
    ).one_or_none()


def batch_rows(session: Session, batch_id: UUID) -> list[ImportRow]:
    """取批次下全部行，按 Excel 行号排序（与用户在表格里看到的顺序一致）。"""
    return list(
        session.scalars(
            select(ImportRow).where(ImportRow.batch_id == batch_id).order_by(ImportRow.row_number)
        ).all()
    )


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _jsonable(value: Any) -> dict[str, Any]:
    """单元格原始值可能含 datetime/Decimal，JSONB 需要可序列化结构。"""
    return json.loads(json.dumps(value, default=str))