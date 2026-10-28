"""身份域模型：组织 / 用户 / 成员。对应迁移 0001_core_identity。

列类型与迁移里的显式 DDL **逐字对齐**（``text``/``char(n)``/``smallint``/``timestamptz``）：
本项目开启 ``compare_type=True``，模型与库的类型必须一致，否则 ``alembic check`` 会持续报漂移，
把真正需要告警的结构变更淹没在噪音里。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CHAR, CheckConstraint, ForeignKey, SmallInteger, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, updated_at, uuid_pk

ROLES = ("OWNER", "ADMIN", "TRAVELER", "VIEWER")


class Organization(Base):
    __tablename__ = "organizations"
    __table_args__ = (UniqueConstraint("slug"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    default_timezone: Mapped[str] = mapped_column(Text, nullable=False, server_default="Asia/Shanghai")
    default_currency: Mapped[str] = mapped_column(CHAR(3), nullable=False, server_default="CNY")
    daily_suggestion_cap: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="3")
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    home_timezone: Mapped[str] = mapped_column(Text, nullable=False, server_default="Asia/Shanghai")
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id"),
        CheckConstraint("role IN ('OWNER', 'ADMIN', 'TRAVELER', 'VIEWER')", name="role_allowed"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default="TRAVELER")
    created_at: Mapped[datetime] = created_at()