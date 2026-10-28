"""现场拜访记录模型。对应迁移 ``0006_visit_records``。

设计文档 v2.0 第 10.5 章把拜访记录的离线冲突策略定为 **last-writer-wins + 保留冲突副本**。
这条策略在数据里落成两个刻意的设计：

1. **``client_record_id`` 是幂等键**：同一台设备重复提交同一条记录（弱网重试、离线队列重放）
   只会得到同一条记录，不会在库里堆出多份笔记。
2. **落败的写入也留档**（``applied=false`` + ``conflict_reason``）：LWW 的"覆盖"不是删除证据，
   被覆盖的版本必须能被人工看到——否则用户会经历"我明明写过，怎么没了"却无从查证。
   ``server_version`` 只在**生效**记录上递增；冲突副本记 0，避免污染版本序列。

``client_updated_at`` 由客户端提供，是冲突判定的依据；``created_at`` 是服务端收到的时间。
两者不能混用：设备时钟可能不准，但"谁后写"只能按客户端的业务时间判断，否则离线补传会被
当成新写入覆盖掉在线期间别人更新的内容。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, uuid_pk
from app.models.encrypted import EncryptedText

VISIT_OUTCOMES = ("MET", "NOT_MET", "RESCHEDULED", "CANCELLED")


class VisitRecord(Base):
    __tablename__ = "visit_records"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "client_record_id", name="uq_visit_records_org_client_record"
        ),
        CheckConstraint(
            "outcome IN ('MET','NOT_MET','RESCHEDULED','CANCELLED')", name="outcome_allowed"
        ),
        CheckConstraint("server_version >= 0", name="server_version_non_negative"),
        Index("ix_visit_records_visit_id_client_updated_at", "visit_id", "client_updated_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    visit_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("visits.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_record_id: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str] = mapped_column(EncryptedText(), nullable=False, server_default="")
    photos: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    follow_up_tasks: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    client_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    server_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    applied: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    conflict_reason: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at()


def record_snapshot(record: VisitRecord) -> dict[str, Any]:
    """给冲突响应用的服务端/客户端快照。时间统一 ISO 字符串，便于客户端原样展示。"""
    return {
        "client_record_id": record.client_record_id,
        "outcome": record.outcome,
        "notes": record.notes,
        "client_updated_at": record.client_updated_at.isoformat(),
        "photos": list(record.photos or []),
        "follow_up_tasks": list(record.follow_up_tasks or []),
    }


__all__ = ["VISIT_OUTCOMES", "VisitRecord", "record_snapshot"]
