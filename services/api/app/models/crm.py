"""CRM 同步任务模型。对应迁移 ``0023_crm_sync``。

PRD 把 CRM 同步定义为"访后跟进"能力。真实 CRM 供应商需要凭证与字段映射，因此本实现：

- 任务持久化（QUEUED → PREPARED/SUCCEEDED/FAILED），可轮询、可重试；
- 默认 ``file`` 适配器只把**待同步载荷**写到本地 ``var/crm``，状态标 ``PREPARED``，
  绝不宣称"已写入真实 CRM"；
- 配置真实 provider 后才可能进入 ``SUCCEEDED``。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, updated_at, uuid_pk

CRM_SYNC_STATUSES = ("QUEUED", "PREPARED", "SUCCEEDED", "FAILED")


class CrmSyncJob(Base):
    __tablename__ = "crm_sync_jobs"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "visit_record_id", name="uq_crm_sync_jobs_org_record"
        ),
        CheckConstraint(
            "status IN ('QUEUED','PREPARED','SUCCEEDED','FAILED')", name="status_allowed"
        ),
        Index("ix_crm_sync_jobs_org_status", "organization_id", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    trip_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), nullable=False, index=True
    )
    visit_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("visits.id", ondelete="CASCADE"), nullable=False, index=True
    )
    visit_record_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("visit_records.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="QUEUED")
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("background_jobs.id", ondelete="SET NULL")
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    result: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


__all__ = ["CRM_SYNC_STATUSES", "CrmSyncJob"]
