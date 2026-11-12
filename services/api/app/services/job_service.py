"""后台任务服务：入队、抢占、心跳、完成、失败重试与租约恢复（设计文档 v2.0 第 10.3 章）。

任务队列解决的是"生成计划要几秒到几十秒，不能把 HTTP 请求挂在那里等"的问题。它与
"内存队列"的区别在于**崩溃后仍可恢复**：

- 抢占即写租约（``lease_expires_at``）。进程被杀、网络分区、部署重启都不会让任务消失——
  租约到期后 ``recover_expired_leases`` 把它放回 ``PENDING``，由任意一个 Worker 重做。
- 失败按指数退避重试，次数用尽转 ``DEAD``（终态，需要人工介入），而不是无限重试。
- 抢占与执行**必须分两个事务**：抢占事务若长时间持有行锁，就会把同一条任务锁死，
  且崩溃后连"租约已过期"这个判据都写不进去。

时间戳一律取数据库 ``clock_timestamp()``：PostgreSQL 的 ``now()`` 在一个事务内恒定不变，
用它算租约会让"长事务里刚拿到的租约"看起来已经过期（或反过来，永不过期）。
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.errors import Conflict, NotFound
from app.config import get_settings
from app.models.reliability import BackgroundJob

#: 队列里的任务类型。集中登记，避免各处写字符串导致 typo 静默变成"没有 Worker 会处理"。
JOB_TYPE_PLAN_TRIP = "plan_trip"
JOB_TYPE_DRAFT_MINUTES = "draft_minutes"
JOB_TYPE_CRM_SYNC = "crm_sync"
JOB_TYPE_FX_SYNC = "fx_rate_sync"
KNOWN_JOB_TYPES = (JOB_TYPE_PLAN_TRIP, JOB_TYPE_DRAFT_MINUTES, JOB_TYPE_CRM_SYNC, JOB_TYPE_FX_SYNC)


def enqueue(
    session: Session,
    organization_id: UUID | str,
    *,
    job_type: str,
    payload: dict[str, Any] | None = None,
    dedupe_key: str | None = None,
    created_by_user_id: UUID | str | None = None,
    max_attempts: int | None = None,
) -> BackgroundJob:
    """入队一条任务。

    带 ``dedupe_key`` 时是**幂等入队**：同一租户同一 key 若已有在途任务（``PENDING``/
    ``RUNNING``），直接返回那一条，不新建。竞态由部分唯一索引
    ``uq_background_jobs_organization_id_dedupe_key`` 兜底（``ON CONFLICT DO NOTHING``），
    而不是"先查后写"——后者在并发下两个请求会双双查不到、双双插入。
    """
    settings = get_settings()
    body = json.dumps(payload or {}, default=str, ensure_ascii=False)
    attempts_cap = max_attempts if max_attempts is not None else settings.max_attempts

    if dedupe_key is None:
        job = BackgroundJob(
            organization_id=str(organization_id),
            job_type=job_type,
            payload=payload or {},
            dedupe_key=None,
            created_by_user_id=None if created_by_user_id is None else str(created_by_user_id),
            max_attempts=attempts_cap,
        )
        session.add(job)
        session.flush()
        return job

    inserted_id = session.execute(
        text(
            """
            INSERT INTO background_jobs
                (organization_id, job_type, payload, dedupe_key, created_by_user_id, max_attempts)
            VALUES
                (CAST(:organization_id AS uuid), :job_type, CAST(:payload AS jsonb), :dedupe_key,
                 CAST(:created_by_user_id AS uuid), :max_attempts)
            ON CONFLICT (organization_id, dedupe_key)
                WHERE dedupe_key IS NOT NULL AND status IN ('PENDING','RUNNING')
                DO NOTHING
            RETURNING id
            """
        ),
        {
            "organization_id": str(organization_id),
            "job_type": job_type,
            "payload": body,
            "dedupe_key": dedupe_key,
            "created_by_user_id": None if created_by_user_id is None else str(created_by_user_id),
            "max_attempts": attempts_cap,
        },
    ).scalar_one_or_none()

    return _one(
        session,
        select(BackgroundJob).where(
            BackgroundJob.organization_id == str(organization_id),
            BackgroundJob.id == inserted_id,
        )
        if inserted_id is not None
        else select(BackgroundJob)
        .where(
            BackgroundJob.organization_id == str(organization_id),
            BackgroundJob.dedupe_key == dedupe_key,
            BackgroundJob.status.in_(("PENDING", "RUNNING")),
        )
        .order_by(BackgroundJob.created_at.desc())
        .limit(1),
        not_found="幂等入队未找到任务（dedupe 记录刚被并发消费）",
    )


def get_job(session: Session, organization_id: UUID | str, job_id: UUID | str) -> BackgroundJob:
    return _one(
        session,
        select(BackgroundJob).where(
            BackgroundJob.organization_id == str(organization_id),
            BackgroundJob.id == str(job_id),
        ),
        not_found="任务不存在",
    )


def find_inflight(
    session: Session, organization_id: UUID | str, dedupe_key: str
) -> BackgroundJob | None:
    """查一条在途任务（``PENDING``/``RUNNING``）；没有则返回 ``None``。

    只用于**回答"这次请求是不是复用了已有任务"**，不参与幂等判定本身——幂等由
    ``IdempotentWrite`` 负责，入队去重由 ``enqueue`` 的 ``ON CONFLICT`` 负责。
    读取不加锁：这里允许"读到一条刚好被消费掉的在途任务"，那只会让响应里的
    ``reused`` 多一次 ``true``，不会造成重复入队。
    """
    return session.scalars(
        select(BackgroundJob)
        .where(
            BackgroundJob.organization_id == str(organization_id),
            BackgroundJob.dedupe_key == dedupe_key,
            BackgroundJob.status.in_(("PENDING", "RUNNING")),
        )
        .order_by(BackgroundJob.created_at.desc())
        .limit(1)
    ).first()


def claim_next(
    session: Session,
    *,
    worker_id: str,
    lease_seconds: int | None = None,
    job_type: str | None = None,
) -> BackgroundJob | None:
    """抢占一条待执行任务；无任务时返回 ``None``。

    ``FOR UPDATE SKIP LOCKED`` 让多个 Worker 并发抢占时各自拿到不同行，而不是互相等待。
    RLS 仍然生效，因此本函数只能看到**当前会话组织**的任务——跨租户由 Worker 逐组织调用。
    """
    settings = get_settings()
    lease = lease_seconds if lease_seconds is not None else settings.job_lease_seconds

    stmt = (
        select(BackgroundJob)
        .where(
            BackgroundJob.status == "PENDING",
            BackgroundJob.available_at <= func.clock_timestamp(),
        )
        .order_by(BackgroundJob.available_at, BackgroundJob.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job_type is not None:
        stmt = stmt.where(BackgroundJob.job_type == job_type)

    job = session.scalars(stmt).first()
    if job is None:
        return None

    now = session.scalar(select(func.clock_timestamp()))
    job.status = "RUNNING"
    job.attempts += 1
    job.locked_at = now
    job.locked_by = worker_id
    job.heartbeat_at = now
    job.lease_expires_at = now + timedelta(seconds=lease)
    if job.started_at is None:
        job.started_at = now
    job.last_error = None
    session.flush()
    return job


def heartbeat(session: Session, job_id: UUID | str, *, worker_id: str, lease_seconds: int | None = None) -> bool:
    """续租。返回 ``False`` 表示任务已不在本 Worker 名下（被恢复并重分配）。"""
    settings = get_settings()
    lease = lease_seconds if lease_seconds is not None else settings.job_lease_seconds
    now = session.scalar(select(func.clock_timestamp()))
    updated = session.execute(
        text(
            """
            UPDATE background_jobs
               SET heartbeat_at = :now,
                   lease_expires_at = :now + make_interval(secs => :lease),
                   locked_at = :now
             WHERE id = CAST(:job_id AS uuid)
               AND status = 'RUNNING'
               AND locked_by = :worker_id
            """
        ),
        {
            "now": now,
            "lease": lease,
            "job_id": str(job_id),
            "worker_id": worker_id,
        },
    ).rowcount
    return bool(updated)


def complete(session: Session, job_id: UUID | str, *, result: dict[str, Any] | None = None) -> BackgroundJob:
    """标记成功（终态）。"""
    job = _running_job(session, job_id)
    now = session.scalar(select(func.clock_timestamp()))
    job.status = "SUCCEEDED"
    job.result = result or {}
    job.finished_at = now
    job.locked_at = None
    job.locked_by = None
    job.lease_expires_at = None
    job.last_error = None
    session.flush()
    return job


def fail(
    session: Session,
    job_id: UUID | str,
    *,
    error: str,
    retryable: bool = True,
) -> BackgroundJob:
    """标记失败。

    - ``retryable=False``（例如输入非法）：直接 ``FAILED`` 终态，重试只会重复同一个错误。
    - 次数用尽：``DEAD`` 终态，等待人工处理。
    - 否则回到 ``PENDING``，``available_at`` 按指数退避推后（上限 ``retry_max_seconds``）。
    """
    settings = get_settings()
    job = _running_job(session, job_id)
    now = session.scalar(select(func.clock_timestamp()))
    job.last_error = error[:4000]

    if not retryable or job.attempts >= job.max_attempts:
        job.status = "DEAD" if retryable else "FAILED"
        job.finished_at = now
        job.locked_at = None
        job.locked_by = None
        job.lease_expires_at = None
        session.flush()
        return job

    backoff = min(settings.retry_max_seconds, settings.retry_base_seconds * 2 ** (job.attempts - 1))
    job.status = "PENDING"
    job.available_at = now + timedelta(seconds=backoff)
    job.locked_at = None
    job.locked_by = None
    job.lease_expires_at = None
    job.heartbeat_at = None
    session.flush()
    return job


def recover_expired_leases(session: Session, *, limit: int = 50) -> list[UUID]:
    """恢复租约已过期的 ``RUNNING`` 任务：次数未用尽 ⇒ 回 ``PENDING``；用尽 ⇒ ``DEAD``。

    返回被处理的 task id 列表。这是"进程崩溃不影响任务最终执行"的兑现点。
    """
    now = session.scalar(select(func.clock_timestamp()))
    expired = (
        session.scalars(
            select(BackgroundJob)
            .where(
                BackgroundJob.status == "RUNNING",
                BackgroundJob.lease_expires_at.is_not(None),
                BackgroundJob.lease_expires_at < now,
            )
            .order_by(BackgroundJob.lease_expires_at)
            .with_for_update(skip_locked=True)
            .limit(limit)
        ).all()
    )
    touched: list[UUID] = []
    for job in expired:
        job.last_error = "租约过期：执行进程未续租，任务已回收重排"
        job.locked_at = None
        job.locked_by = None
        job.lease_expires_at = None
        job.heartbeat_at = None
        if job.attempts >= job.max_attempts:
            job.status = "DEAD"
            job.finished_at = now
        else:
            job.status = "PENDING"
            job.available_at = now
        touched.append(job.id)
    session.flush()
    return touched


# ---------------------------------------------------------------------------
# 内部助手
# ---------------------------------------------------------------------------
def _one(session: Session, stmt, *, not_found: str) -> Any:
    obj = session.scalars(stmt).one_or_none()
    if obj is None:
        raise NotFound(not_found)
    return obj


def _running_job(session: Session, job_id: UUID | str) -> BackgroundJob:
    job = session.scalars(
        select(BackgroundJob).where(BackgroundJob.id == str(job_id)).with_for_update()
    ).one_or_none()
    if job is None:
        raise NotFound("任务不存在")
    return job


__all__ = [
    "JOB_TYPE_FX_SYNC",
    "JOB_TYPE_PLAN_TRIP",
    "KNOWN_JOB_TYPES",
    "claim_next",
    "complete",
    "enqueue",
    "fail",
    "find_inflight",
    "get_job",
    "heartbeat",
    "recover_expired_leases",
]

def cancel(session: Session, organization_id: UUID | str, job_id: UUID | str) -> BackgroundJob:
    """取消尚未开始的任务。RUNNING 已交给求解器，无法安全中断，返回 409。"""
    job = session.scalars(
        select(BackgroundJob)
        .where(
            BackgroundJob.organization_id == str(organization_id),
            BackgroundJob.id == str(job_id),
        )
        .with_for_update()
    ).one_or_none()
    if job is None:
        raise NotFound("任务不存在", details={"job_id": str(job_id)})
    if job.status == "CANCELLED":
        return job
    if job.status != "PENDING":
        raise Conflict(
            "任务已开始执行，无法取消",
            details={"job_id": str(job.id), "status": job.status},
        )
    job.status = "CANCELLED"
    job.finished_at = session.scalar(select(func.clock_timestamp()))
    session.flush()
    return job
