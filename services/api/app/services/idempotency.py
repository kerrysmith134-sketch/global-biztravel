"""幂等原语（设计文档 v2.0 第 8.4 章）。

移动端在弱网下会重试写请求，服务端可能已经成功但响应丢在路上。如果没有幂等，
"重试"就等于"再建一条"——这正是设计文档把幂等列为**首个写接口之前**就必须具备的原语
的原因（见 ``0003_reliability_core`` 的 docstring 与 ADR-0002）。

三层保护：

1. **请求指纹**：``request_fingerprint`` 对规范化 JSON 求 sha256。同一 key 携带不同请求体
   ⇒ 409 ``IDEMPOTENCY_KEY_REUSED``，因为那是客户端复用 key，不是重试。
2. **事务级咨询锁**：``pg_advisory_xact_lock`` 按 ``(org, endpoint, key)`` 串行化并发重试，
   避免两个线程同时看不到记录、双双落库（这是纯 SELECT-then-INSERT 的经典竞态）。
3. **已存响应回放**：命中记录直接返回首次的状态码与响应体，``replayed`` 标记出来供客户端区分。

因为 ``response_status`` 在 DDL 里是 NOT NULL，这里不引入"进行中"占位行：进行中状态由
咨询锁表达，落库只在响应确定之后发生。锁随事务释放，回滚即"这笔没发生过"，语义干净。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.errors import BadRequest, IdempotencyKeyReused
from app.models.reliability import IdempotencyRecord

#: 幂等记录保留期。过期后可被清理任务回收；期间内的重试一律回放首次响应。
DEFAULT_TTL = timedelta(hours=24)


def request_fingerprint(payload: Any) -> str:
    """对请求体求稳定指纹：键排序、紧凑分隔符、``default=str`` 兜住 datetime/UUID。"""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ReplayedResponse:
    """命中幂等记录时回放的内容。"""

    status: int
    body: dict[str, Any]


class IdempotencyStore:
    """以 ``(organization_id, endpoint, key)`` 为键的幂等存储。

    用法（在写用例内部、事务中）：:

        store = IdempotencyStore(session, org_id)
        if (replay := store.begin(endpoint, key, fingerprint)) is not None:
            return replay          # 直接回放，不再执行业务副作用
        ...执行业务...
        store.complete(endpoint, key, fingerprint, 201, body)
    """

    def __init__(self, session: Session, organization_id: UUID | str) -> None:
        self.session = session
        self.organization_id = str(organization_id)

    # ---------------------------------------------------------------- helpers
    def _lock(self, endpoint: str, key: str) -> None:
        token = f"{self.organization_id}|{endpoint}|{key}"
        # hashtextextended 返回 bigint，正好是 pg_advisory_xact_lock 的入参类型。
        self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:token, 0))"),
            {"token": token},
        )

    def _find(self, endpoint: str, key: str) -> IdempotencyRecord | None:
        return self.session.scalars(
            select(IdempotencyRecord).where(
                IdempotencyRecord.organization_id == self.organization_id,
                IdempotencyRecord.endpoint == endpoint,
                IdempotencyRecord.key == key,
            )
        ).one_or_none()

    # -------------------------------------------------------------- public API
    def begin(self, endpoint: str, key: str, fingerprint: str) -> ReplayedResponse | None:
        """取锁并检查是否已有记录。

        - 返回 ``None`` ⇒ 首次请求，调用方继续执行业务并调用 :meth:`complete`。
        - 返回 :class:`ReplayedResponse` ⇒ 重试，调用方直接回放，**不得**再执行业务。
        - 抛出 :class:`IdempotencyKeyReused` ⇒ key 被复用但请求体不同。
        """
        if not key:
            raise BadRequest("缺少 Idempotency-Key 请求头", code="IDEMPOTENCY_KEY_REQUIRED")
        self._lock(endpoint, key)
        record = self._find(endpoint, key)
        if record is None:
            return None
        if record.request_hash != fingerprint:
            raise IdempotencyKeyReused(
                "该 Idempotency-Key 已用于不同的请求体",
                details={
                    "endpoint": endpoint,
                    "key": key,
                    "first_recorded_at": record.created_at.isoformat(),
                },
            )
        return ReplayedResponse(status=record.response_status, body=dict(record.response_body or {}))

    def complete(
        self,
        endpoint: str,
        key: str,
        fingerprint: str,
        status: int,
        body: dict[str, Any],
        *,
        ttl: timedelta = DEFAULT_TTL,
    ) -> IdempotencyRecord:
        """落库已完成的响应。同一事务内提交，与业务写入原子。"""
        if not 100 <= status <= 599:
            raise BadRequest(f"不可记录的响应状态码：{status}", code="IDEMPOTENCY_STATUS_INVALID")
        record = IdempotencyRecord(
            organization_id=self.organization_id,
            endpoint=endpoint,
            key=key,
            request_hash=fingerprint,
            response_status=status,
            response_body=_jsonable(body),
            expires_at=datetime.now(UTC) + ttl,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def purge_expired(self, *, now: datetime | None = None) -> int:
        """删除过期记录（运维/测试用）。返回删除条数。"""
        cutoff = now or datetime.now(UTC)
        rows = self.session.scalars(
            select(IdempotencyRecord).where(
                IdempotencyRecord.expires_at.is_not(None),
                IdempotencyRecord.expires_at < cutoff,
            )
        ).all()
        for row in rows:
            self.session.delete(row)
        self.session.flush()
        return len(rows)


def _jsonable(value: Any) -> Any:
    """把 Pydantic 模型/UUID/datetime 等转成可直接写 JSONB 的结构。"""
    return json.loads(json.dumps(value, default=str))