"""外部信号服务：接入、幂等更新、按出行上下文检索。

设计文档 v2.0 第 14 章。这一层的职责边界很窄：

- **只存事实**。``provider`` 报告"德国商务签证受理周期从 10 个工作日变为 15 个"，
  服务只落这条事实；是否提醒、提醒谁、是否需要人审，全部由主动式运行时决定。
- **幂等接入**。同一 ``(provider, dedupe_key)`` 重复推送更新原行，不产生重复信号。
- **按上下文匹配**。信号可以挂国家或具体出行；扫描时把"这次出行涉及的国家 + 出行本身"
  作为匹配键。
- **不伪造数据**。没有配置外部源时信号表为空，主动式不会凭空产生 EXTERNAL 建议；
  本地验证通过 ``POST /v1/integrations/signals`` 注入真实结构的信号。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.api.schemas import ExternalSignalCreate, FlightOperationalUpdatePayload
from app.models.integrations import ExternalSignal
from app.models.trip import Trip
from app.services import flight_operations_service


def ingest(
    session: Session, organization_id: UUID | str, payload: ExternalSignalCreate
) -> ExternalSignal:
    """接入一条外部信号。同 provider + dedupe_key 重推时更新内容与有效期。"""
    signal_payload = dict(payload.payload)
    raw_operational_update = signal_payload.get("flight_operational_update")
    if raw_operational_update is not None:
        operational_payload = FlightOperationalUpdatePayload.model_validate(raw_operational_update)
        signal_payload["flight_operational_update"] = operational_payload.model_dump(mode="json")
        operational_update = flight_operations_service.ingest_operational_update(
            session,
            organization_id,
            UUID(payload.subject_ref),
            payload.provider,
            payload.observed_at,
            payload.valid_until,
            payload.confidence,
            operational_payload,
            summary=signal_payload.get("summary"),
        )
        signal_payload["operational_update_id"] = str(operational_update.id)
        signal_payload["operational_update_content_sha256"] = operational_update.content_sha256
        signal_payload["operational_update_type"] = operational_update.update_type

    statement = (
        pg_insert(ExternalSignal.__table__)
        .values(
            organization_id=str(organization_id),
            provider=payload.provider,
            signal_type=payload.signal_type,
            subject_type=payload.subject_type,
            subject_ref=payload.subject_ref,
            payload=signal_payload,
            confidence=payload.confidence,
            observed_at=payload.observed_at,
            valid_from=payload.observed_at,
            valid_until=payload.valid_until,
            dedupe_key=payload.dedupe_key,
        )
        .on_conflict_do_update(
            constraint="uq_external_signals_org_provider_dedupe",
            set_={
                "signal_type": payload.signal_type,
                "subject_type": payload.subject_type,
                "subject_ref": payload.subject_ref,
                "payload": signal_payload,
                "confidence": payload.confidence,
                "observed_at": payload.observed_at,
                "valid_until": payload.valid_until,
                "updated_at": func.now(),
            },
        )
        .returning(ExternalSignal.__table__.c.id)
    )
    signal_id = session.execute(statement).scalar_one()
    signal = session.get(ExternalSignal, signal_id)
    session.flush()
    return signal


def active_for_trip(
    session: Session,
    organization_id: UUID | str,
    trip: Trip,
    *,
    now: datetime,
    limit: int = 5,
) -> list[ExternalSignal]:
    """返回与本次出行相关且仍在有效期内的信号，最新的在前。"""
    refs: list[tuple[str, str]] = [("TRIP", str(trip.id))]
    for country in trip.countries or []:
        refs.append(("COUNTRY", str(country).upper()))

    subject_match = or_(
        *[
            (ExternalSignal.subject_type == subject_type)
            & (ExternalSignal.subject_ref == subject_ref)
            for subject_type, subject_ref in refs
        ]
    )
    return list(
        session.scalars(
            select(ExternalSignal)
            .where(
                ExternalSignal.organization_id == str(organization_id),
                subject_match,
                ExternalSignal.observed_at <= now,
                or_(
                    ExternalSignal.valid_until.is_(None),
                    ExternalSignal.valid_until > now,
                ),
            )
            .order_by(ExternalSignal.observed_at.desc(), ExternalSignal.id)
            .limit(limit)
        )
    )


def list_signals(
    session: Session,
    organization_id: UUID | str,
    *,
    subject_ref: str | None = None,
    signal_type: str | None = None,
    limit: int = 50,
) -> list[ExternalSignal]:
    stmt = select(ExternalSignal).where(ExternalSignal.organization_id == str(organization_id))
    if subject_ref:
        stmt = stmt.where(ExternalSignal.subject_ref == subject_ref)
    if signal_type:
        stmt = stmt.where(ExternalSignal.signal_type == signal_type)
    return list(
        session.scalars(
            stmt.order_by(ExternalSignal.observed_at.desc()).limit(limit)
        )
    )


__all__ = ["active_for_trip", "ingest", "list_signals"]
