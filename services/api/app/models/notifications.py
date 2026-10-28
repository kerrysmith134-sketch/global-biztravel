"""通知模型。对应迁移 ``0012_notifications``。

设计文档 v2.0 第 8.6 章：主动动作必须通过 Outbox 投递、幂等、可恢复，失败可降级。通知是
"建议已推送"这一动作的落点：

- **一建议一通知**（``uq_notifications_org_suggestion_provider``），Outbox 重投不会产生第二条。
- ``status`` 区分 ``PENDING/SENT/FAILED``；``attempts`` 与 ``last_error`` 让失败可诊断。
- ``provider`` 记录实际通道（``inapp`` / ``log`` / ``apns``）。本地默认 ``inapp`` 表示
  "进入可见的通知列表"；真实 APNs 需要证书，未配置时显式失败而不是假装发送。
- ``read_at`` 支撑"未读数"，不改变送达语义。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, updated_at, uuid_pk

NOTIFICATION_STATUSES = ("PENDING", "SENT", "FAILED")


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "suggestion_id",
            "provider",
            name="uq_notifications_org_suggestion_provider",
        ),
        CheckConstraint(
            "status IN ('PENDING','SENT','FAILED')", name="status_allowed"
        ),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        Index("ix_notifications_org_user_created_at", "organization_id", "user_id", "created_at"),
        Index("ix_notifications_org_status", "organization_id", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    suggestion_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("proactive_suggestions.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False, server_default="PROACTIVE_SUGGESTION")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="PENDING")
    provider: Mapped[str] = mapped_column(Text, nullable=False, server_default="inapp")
    recipient: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


__all__ = ["NOTIFICATION_STATUSES", "Notification"]
