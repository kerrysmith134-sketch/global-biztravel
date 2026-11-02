"""签证案件与入境评估服务（PRD 方向二）。

边界：

- **评估只读目录**：``assess_entry`` 生成"需核验"的事实快照，来源/版本/置信度/人工复核标记
  必须随结果返回；不宣布"需要/不需要签证"。
- **案件是用户自己的办理进度**：NEW → PREPARING → SUBMITTED → APPROVED/REJECTED，或任意
  非终态 CANCELLED。每次状态推进是幂等赋值，不重复触发外部动作。
- **一出行一国家一案件**：唯一约束防止重复提醒产生多条互相矛盾的进度。
- 不存护照号；``passport_expires_at`` 仅用于出发前时效检查。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.api.errors import Conflict, NotFound, Unprocessable
from app.models.memory import TravelerProfile
from app.models.trip import Trip
from app.models.visa import EntryAssessment, VisaCase
from app.services import visa_catalog

CASE_NEW = "NEW"
CASE_PREPARING = "PREPARING"
CASE_SUBMITTED = "SUBMITTED"
CASE_APPROVED = "APPROVED"
CASE_REJECTED = "REJECTED"
CASE_CANCELLED = "CANCELLED"

_TRANSITIONS: dict[str, tuple[str, ...]] = {
    CASE_NEW: (CASE_PREPARING, CASE_CANCELLED),
    CASE_PREPARING: (CASE_SUBMITTED, CASE_CANCELLED),
    CASE_SUBMITTED: (CASE_APPROVED, CASE_REJECTED, CASE_CANCELLED),
    CASE_APPROVED: (),
    CASE_REJECTED: (),
    CASE_CANCELLED: (),
}

CHECKLIST_STATUSES = ("MISSING", "READY", "NOT_APPLICABLE")


def _country(code: str) -> str:
    normalized = str(code).strip().upper()
    if len(normalized) != 2 or not normalized.isalpha():
        raise Unprocessable("国家代码必须是两位 ISO 3166-1 alpha-2", details={"code": code})
    return normalized


def ensure_cases(
    session: Session,
    organization_id: UUID | str,
    trip: Trip,
    *,
    countries: list[str] | None = None,
    actor_user_id: UUID | str | None = None,
) -> list[VisaCase]:
    codes = [_country(code) for code in (countries or list(trip.countries or []))]
    if not codes:
        return []
    catalog = visa_catalog.load_catalog()
    for code in sorted(set(codes)):
        knowledge = catalog["countries"].get(code)
        statement = (
            pg_insert(VisaCase.__table__)
            .values(
                organization_id=str(organization_id),
                trip_id=trip.id,
                country_code=code,
                status=CASE_NEW,
                checklist=visa_catalog.default_checklist(),
                confidence=0.6 if knowledge is not None else 0.4,
                created_by_user_id=None if actor_user_id is None else str(actor_user_id),
            )
            .on_conflict_do_nothing(constraint="uq_visa_cases_org_trip_country")
        )
        session.execute(statement)
    session.flush()
    return list_cases(session, organization_id, trip.id)


def list_cases(
    session: Session, organization_id: UUID | str, trip_id: UUID | str
) -> list[VisaCase]:
    return list(
        session.scalars(
            select(VisaCase)
            .where(
                VisaCase.organization_id == str(organization_id),
                VisaCase.trip_id == str(trip_id),
            )
            .order_by(VisaCase.country_code)
        )
    )


def require_case(
    session: Session, organization_id: UUID | str, case_id: UUID | str
) -> VisaCase:
    case = session.scalars(
        select(VisaCase).where(
            VisaCase.organization_id == str(organization_id),
            VisaCase.id == str(case_id),
        )
    ).one_or_none()
    if case is None:
        raise NotFound("签证案件不存在", details={"visa_case_id": str(case_id)})
    return case


def _validate_checklist(checklist: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for item in checklist:
        key = str(item.get("key") or "").strip()
        label = str(item.get("label") or "").strip()
        status = str(item.get("status") or "MISSING").upper()
        if not key or not label:
            raise Unprocessable("清单项缺少 key/label", details={"item": item})
        if status not in CHECKLIST_STATUSES:
            raise Unprocessable(
                "清单状态非法",
                details={"status": status, "allowed": list(CHECKLIST_STATUSES)},
            )
        cleaned.append({"key": key, "label": label, "status": status, "note": item.get("note")})
    return cleaned


def update_case(
    session: Session,
    organization_id: UUID | str,
    case_id: UUID | str,
    *,
    status: str | None = None,
    notes: str | None = None,
    checklist: list[dict[str, Any]] | None = None,
    passport_expires_at: date | None = None,
    now: datetime | None = None,
) -> VisaCase:
    case = require_case(session, organization_id, case_id)
    current = now or session.scalar(select(func.clock_timestamp()))
    if status is not None:
        target = status.upper()
        if target != case.status:
            allowed = _TRANSITIONS.get(case.status, ())
            if target not in allowed:
                raise Conflict(
                    "签证案件状态不能这样推进",
                    details={"current": case.status, "target": target, "allowed": list(allowed)},
                )
            case.status = target
            if target == CASE_SUBMITTED:
                case.submitted_at = current
            if target in (CASE_APPROVED, CASE_REJECTED):
                case.decision_at = current
    if notes is not None:
        case.notes = notes
    if checklist is not None:
        case.checklist = _validate_checklist(checklist)
    if passport_expires_at is not None:
        case.passport_expires_at = passport_expires_at
    session.flush()
    return case


def traveler_nationality(
    session: Session, organization_id: UUID | str, trip: Trip
) -> str | None:
    """取出差人的护照国籍（画像事实记忆层）；没有就返回 None，**不猜**。"""
    if trip.owner_user_id is None:
        return None
    profile = session.scalars(
        select(TravelerProfile)
        .where(
            TravelerProfile.organization_id == organization_id,
            TravelerProfile.user_id == trip.owner_user_id,
            TravelerProfile.valid_until.is_(None),
        )
        .order_by(TravelerProfile.version.desc())
        .limit(1)
    ).one_or_none()
    if profile is None or not profile.nationality:
        return None
    return str(profile.nationality).upper()


def assess_entry(
    session: Session, organization_id: UUID | str, trip: Trip, *, now: datetime | None = None
) -> list[EntryAssessment]:
    catalog = visa_catalog.load_catalog()
    current = now or session.scalar(select(func.clock_timestamp()))
    nationality = traveler_nationality(session, organization_id, trip)
    results: list[EntryAssessment] = []
    for raw_code in sorted({str(code).upper() for code in (trip.countries or [])}):
        code = _country(raw_code)
        knowledge = catalog["countries"].get(code)
        # 证件前提：PRD P0-7 要求结合证件/国籍核验。目录只按国别组织、不含按国籍的
        # 差异结论，因此这里只陈述"以哪本护照为前提"与缺口，**不宣布需要/免签**。
        if nationality:
            premise = f"证件前提：{nationality} 护照。"
        else:
            premise = "证件前提：未登记护照国籍，无法结合证件核验。"
        if knowledge is None:
            summary = (
                f"{premise}{code}：本地目录未收录，必须直接查询官方入境要求。"
            )
            confidence = 0.4
        else:
            summary = (
                f"{premise}{knowledge['label']}：{knowledge['business_entry']}。"
                f"{knowledge['review_hint']}（来源 {catalog['version']}，需人工复核）"
            )
            confidence = 0.6
        # 缺证件信息时置信度更低：评估对象不明的结论更不可靠。
        if nationality is None:
            confidence = min(confidence, 0.35)
        statement = (
            pg_insert(EntryAssessment.__table__)
            .values(
                organization_id=str(organization_id),
                trip_id=trip.id,
                country_code=code,
                summary=summary,
                source="offline-visa-catalog",
                source_version=catalog["version"],
                confidence=confidence,
                manual_review_required=True,
                assessed_at=current,
            )
            .on_conflict_do_update(
                constraint="uq_entry_assessments_org_trip_country_version",
                set_={
                    "summary": summary,
                    "confidence": confidence,
                    "manual_review_required": True,
                    "assessed_at": current,
                },
            )
            .returning(EntryAssessment.__table__.c.id)
        )
        assessment_id = session.execute(statement).scalar_one()
        row = session.get(EntryAssessment, assessment_id)
        if row is not None:
            results.append(row)
    session.flush()
    return results


def list_assessments(
    session: Session, organization_id: UUID | str, trip_id: UUID | str
) -> list[EntryAssessment]:
    return list(
        session.scalars(
            select(EntryAssessment)
            .where(
                EntryAssessment.organization_id == str(organization_id),
                EntryAssessment.trip_id == str(trip_id),
            )
            .order_by(EntryAssessment.country_code)
        )
    )


__all__ = [
    "CASE_APPROVED",
    "traveler_nationality",
    "CASE_CANCELLED",
    "CASE_NEW",
    "CASE_PREPARING",
    "CASE_REJECTED",
    "CASE_SUBMITTED",
    "CHECKLIST_STATUSES",
    "assess_entry",
    "ensure_cases",
    "list_assessments",
    "list_cases",
    "require_case",
    "update_case",
]
