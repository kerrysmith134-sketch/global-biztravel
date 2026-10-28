"""设备 token 模型。对应迁移 ``0014_push_channels``。

APNs 需要设备 token、SMTP 需要邮箱地址。通知在入队时解析收件人并快照到
``notifications.recipient``：投递时不再回查用户/设备，避免"投递那一刻用户改了邮箱/换了设备"
导致通知发错对象，也让失败重试可解释。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, updated_at, uuid_pk

PLATFORMS = ("IOS", "ANDROID")


class DeviceToken(Base):
    __tablename__ = "device_tokens"
    __table_args__ = (
        UniqueConstraint("organization_id", "token", name="uq_device_tokens_org_token"),
        CheckConstraint("platform IN ('IOS','ANDROID')", name="platform_allowed"),
        Index("ix_device_tokens_org_user", "organization_id", "user_id", "enabled"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    platform: Mapped[str] = mapped_column(Text, nullable=False, server_default="IOS")
    token: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


__all__ = ["DeviceToken", "PLATFORMS"]
