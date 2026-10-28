"""数据留存与脱敏（设计文档 §11.4 PII）。

超过留存期限的现场笔记与记忆正文会被**就地清空**，而不是删除整行：保留壳与时间戳，
审计仍能回答"这条数据曾经存在、何时被脱敏"，但内容不再可读。客户联系人的字段级加密由
:class:`app.models.encrypted.EncryptedText` 在写入时完成；本服务负责"时间到了就抹掉"。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.memory import TripMemorySummary
from app.models.records import VisitRecord

REDACTED = "[已按留存策略删除]"


@dataclass
class RetentionReport:
    records_redacted: int = 0
    summaries_redacted: int = 0
    cutoff: datetime | None = None


def apply_retention(
    session: Session,
    organization_id: UUID | str,
    *,
    days: int | None = None,
    now: datetime | None = None,
) -> RetentionReport:
    settings = get_settings()
    current = now or session.scalar(select(func.clock_timestamp()))
    cutoff = current - timedelta(days=settings.retention_days if days is None else days)

    records = session.execute(
        update(VisitRecord)
        .where(
            VisitRecord.organization_id == str(organization_id),
            VisitRecord.created_at < cutoff,
        )
        .values(notes=REDACTED, photos=[])
    ).rowcount

    summaries = session.execute(
        update(TripMemorySummary)
        .where(
            TripMemorySummary.organization_id == str(organization_id),
            TripMemorySummary.created_at < cutoff,
            TripMemorySummary.valid_until.is_(None),
        )
        .values(
            summary=REDACTED,
            decisions=[],
            follow_ups=[],
            lessons=[],
            embedding=None,
            valid_until=current,
        )
    ).rowcount

    session.flush()
    return RetentionReport(
        records_redacted=int(records or 0),
        summaries_redacted=int(summaries or 0),
        cutoff=cutoff,
    )


__all__ = ["REDACTED", "RetentionReport", "apply_retention"]
