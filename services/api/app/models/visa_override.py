"""签证目录组织级覆盖（ADR-0034 网页形式兜底）。

签证要求没有免费官方 API（Timatic 收费）。离线目录 ``visa_catalog.json`` 是只读基线；
本表存 ADMIN 经维护页修改的条目，读取时按国别整体覆盖基线，且
``manual_confirmation_required`` 永远强制为真（不给确定性签证结论，ADR-0027）。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CHAR, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, updated_at, uuid_pk


class VisaCatalogOverride(Base):
    __tablename__ = "visa_catalog_overrides"
    __table_args__ = (
        UniqueConstraint("organization_id", "country_code", name="uq_visa_overrides_org_country"),
        Index("ix_visa_overrides_organization_id", "organization_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    country_code: Mapped[str] = mapped_column(CHAR(2), nullable=False)
    entry: Mapped[dict] = mapped_column(JSONB, nullable=False)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


__all__ = ["VisaCatalogOverride"]
