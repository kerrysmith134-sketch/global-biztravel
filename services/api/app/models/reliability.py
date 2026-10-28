"""可靠性基础设施模型。

对应迁移：

- ``0003_reliability_core``：幂等记录 ``IdempotencyRecord``。
- ``0004_planning_reliability``：后台任务 ``BackgroundJob`` 与事务性发件箱
  ``OutboxEvent`` / ``OutboxDelivery``。

幂等的语义（设计文档 v2.0 第 8.4 章）：

- 同一 ``(organization_id, endpoint, key)`` 上重复提交**同一请求体** ⇒ 返回首次的响应与状态码，
  不产生第二次副作用（``replayed = true``）。
- 同一 key 但**请求体不同** ⇒ 409 ``IDEMPOTENCY_KEY_REUSED``，因为那说明客户端复用了 key，
  继续执行会掩盖真实的一笔新请求。

异步任务的语义（第 10.3 章）：

- 任务是**可恢复**的：抢占时置 ``RUNNING`` 并写租约 ``lease_expires_at``，执行中用心跳续租；
  进程崩溃后租约到期，``recover_expired_leases`` 把它放回 ``PENDING``（或到次数上限判 ``DEAD``）。
- 抢占用 ``FOR UPDATE SKIP LOCKED``，因此多个 Worker 并发不会拿到同一条任务。
- 租约与心跳的时间戳一律走数据库 ``clock_timestamp()``：PostgreSQL 的 ``now()`` 在一个事务内
  恒定不变，用它算租约会让"长事务里刚拿到的租约"看起来已经过期。
- 入队幂等由部分唯一索引 ``uq_background_jobs_organization_id_dedupe_key`` 兜底：
  同一租户同一 ``dedupe_key`` 至多一条**在途**（``PENDING``/``RUNNING``）任务；历史终态任务不参与。

事务性发件箱的语义（第 10.4 章）：

- ``emit`` 在业务写入的**同一事务**里插一条 ``OutboxEvent`` + 每个消费者一条 ``OutboxDelivery``，
  于是"业务已提交"与"事件已登记"不会分叉。
- 消费者按 ``(event_id, consumer)`` 去重（唯一约束），重复投递不会产生第二次副作用。
- 单条 event 的状态由它的 deliveries 汇总：全部 ``DONE`` ⇒ ``DONE``；任一 ``FAILED`` ⇒ ``DEAD``。
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
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, updated_at, uuid_pk

JOB_STATUSES = ("PENDING", "RUNNING", "SUCCEEDED", "FAILED", "DEAD")
OUTBOX_EVENT_STATUSES = ("PENDING", "PROCESSING", "DONE", "DEAD")
OUTBOX_DELIVERY_STATUSES = ("PENDING", "DONE", "FAILED")


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("organization_id", "endpoint", "key"),
        CheckConstraint("response_status BETWEEN 100 AND 599", name="status_range"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = created_at()
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class BackgroundJob(Base):
    __tablename__ = "background_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','RUNNING','SUCCEEDED','FAILED','DEAD')", name="status_allowed"
        ),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
        # 队列主索引：Worker 的 claim 只扫 (status, available_at) 前缀。
        Index("ix_background_jobs_status_available_at", "status", "available_at"),
        # 入队幂等：同一租户同一 dedupe_key 只能有一条在途任务；终态任务不参与唯一性，
        # 否则同一个 key 一生只能成功一次。
        Index(
            "uq_background_jobs_organization_id_dedupe_key",
            "organization_id",
            "dedupe_key",
            unique=True,
            postgresql_where=text("dedupe_key IS NOT NULL AND status IN ('PENDING','RUNNING')"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="PENDING")
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    result: Mapped[dict | None] = mapped_column(JSONB)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="5")
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(Text)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    dedupe_key: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','PROCESSING','DONE','DEAD')", name="status_allowed"
        ),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        Index("ix_outbox_events_aggregate_id", "aggregate_type", "aggregate_id"),
        Index("ix_outbox_events_status_next_attempt_at", "status", "next_attempt_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    aggregate_type: Mapped[str] = mapped_column(Text, nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="5")
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class OutboxDelivery(Base):
    __tablename__ = "outbox_deliveries"
    __table_args__ = (
        UniqueConstraint("event_id", "consumer"),
        CheckConstraint("status IN ('PENDING','DONE','FAILED')", name="status_allowed"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        Index("ix_outbox_deliveries_status", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("outbox_events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    consumer: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()