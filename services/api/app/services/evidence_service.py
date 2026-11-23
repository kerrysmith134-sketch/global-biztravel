"""证据服务：把建议/计划的可核对引用落成 `evidence` 行（设计 §9.1）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.workflow import Evidence


def record_many(
    session: Session,
    organization_id: UUID | str,
    *,
    subject_type: str,
    subject_id: UUID | str,
    items: list[dict[str, Any]],
    source: str | None = None,
) -> int:
    written = 0
    for item in items:
        kind = str(item.get("kind") or "UNKNOWN")
        ref = str(item.get("ref") or "")
        if not ref:
            continue
        retrieved_at = item.get("retrieved_at")
        parsed_at: datetime | None = None
        if isinstance(retrieved_at, datetime):
            parsed_at = retrieved_at
        elif isinstance(retrieved_at, str) and retrieved_at:
            try:
                parsed_at = datetime.fromisoformat(retrieved_at)
            except ValueError:
                parsed_at = None
        item_source = item.get("source") or source
        statement = (
            pg_insert(Evidence.__table__)
            .values(
                organization_id=str(organization_id),
                subject_type=subject_type,
                subject_id=str(subject_id),
                kind=kind,
                ref=ref,
                source=str(item_source) if item_source is not None else None,
                payload=(
                    item.get("payload")
                    if isinstance(item.get("payload"), dict)
                    else item
                ),
                retrieved_at=parsed_at,
            )
            .on_conflict_do_nothing(constraint="uq_evidence_org_subject_kind_ref")
            .returning(Evidence.__table__.c.id)
        )
        if session.execute(statement).scalar_one_or_none() is not None:
            written += 1
    session.flush()
    return written


def list_for_subject(
    session: Session,
    organization_id: UUID | str,
    *,
    subject_type: str,
    subject_id: UUID | str,
) -> list[Evidence]:
    return list(
        session.scalars(
            select(Evidence)
            .where(
                Evidence.organization_id == str(organization_id),
                Evidence.subject_type == subject_type,
                Evidence.subject_id == str(subject_id),
            )
            .order_by(Evidence.created_at)
        )
    )
