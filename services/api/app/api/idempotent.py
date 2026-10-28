"""写接口的幂等守卫（设计文档 v2.0 第 8.4 章）。

**必须是两阶段**：先检查、再执行、后落库。把顺序写反（先执行业务、最后由封装写记录）
是本模块最危险的退化方式——重放请求同样会先把副作用做一遍（再建一条 Trip、再落一批导入），
只是把首次响应回放给客户端。客户端看到的响应没错，库里的数据却多了一份，
而这恰好是幂等要防的事，且只有"数一次副作用"的测试才能发现它。
因此这里不再提供"一步到位"的封装，只提供 :class:`IdempotentWrite`：

.. code-block:: python

    guard = IdempotentWrite(session, org_id, endpoint=..., key=..., payload=...)
    if (replay := guard.begin()) is not None:
        return replay                      # 重放：一个字节的业务都不执行
    ...执行业务...
    return guard.finish(status_code=201, body=body)

为什么响应要手工构造，而不是让 FastAPI 的 ``response_model`` 序列化：**首答与重放必须字节一致**。
若首答走自动序列化、重放走 ``model_dump(mode="json")``，两者对 ``Decimal``（金额）、``datetime``
（时区表示）的处理并不同，客户端会看到"同一次请求两次不同的响应"——那比没有幂等更糟。

第二个更隐蔽的坑只有在真实数据库上才暴露：幂等记录体存在 **JSONB** 列里，
而 PostgreSQL 的 JSONB **会规范化键序**（按长度与字节序重排）并丢弃重复键。于是即便
值完全相同，"首答"（按 Pydantic 字段序）与"重放"（按 JSONB 序）的字节也会不同。
解决办法不是放弃 JSONB，而是让**两条路径都经过同一套规范序列化**：
``sort_keys=True`` + 紧凑分隔符 + ``ensure_ascii=False``。这样键序由序列化器决定，
而不是由存储引擎决定，字节一致才真正成立。

幂等键是可选头：不带就正常执行；带了就一定保证重试不产生第二次副作用。
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session
from starlette.responses import Response

from app.services.idempotency import IdempotencyStore, request_fingerprint

#: 命中幂等记录时回放的响应会带上这个头，客户端据此区分"本次真的执行了"与"回放了首次结果"。
REPLAY_HEADER = "Idempotent-Replay"


def canonical_json(payload: Any) -> str:
    """把已编码的响应体序列化成**确定性字节**。

    键排序 + 紧凑分隔符 + 不转义非 ASCII。两侧（首答与重放）都走这里，键序便与
    PostgreSQL JSONB 的内部排列无关，从而让"重放 == 首答"在字节层面成立。
    """
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )


def _response(status_code: int, payload: Any, *, headers: dict[str, str] | None = None) -> Response:
    return Response(
        content=canonical_json(payload),
        status_code=status_code,
        media_type="application/json",
        headers=headers,
    )


def _as_mapping(encoded: Any) -> dict[str, Any]:
    """幂等记录体固定为 JSONB 对象；非对象响应包一层，保证可回放且结构稳定。"""
    if isinstance(encoded, dict):
        return encoded
    return {"data": encoded}


class IdempotentWrite:
    """一次写请求的幂等守卫。**必须在业务副作用之前 ``begin``**。

    语义（对应 ``0003_reliability_core``）：

    - ``key`` 为空 ⇒ 不做幂等，``begin`` 返回 ``None``，``finish`` 不落记录。
    - ``key`` 已存在且请求体一致 ⇒ ``begin`` 回放首次的状态码与响应体（带 ``Idempotent-Replay`` 头）。
    - ``key`` 已存在但请求体不同 ⇒ ``begin`` 抛 409 ``IDEMPOTENCY_KEY_REUSED``（那是误用 key，不是重试）。

    并发重试由 :class:`IdempotencyStore` 的事务级咨询锁串行化：锁在 ``begin`` 取、随事务释放，
    因此"同一 key 的两个并发请求"会一先一后地看到彼此的结果，不存在"双双落库"。
    """

    def __init__(
        self,
        session: Session,
        organization_id: UUID | str,
        *,
        endpoint: str,
        key: str | None,
        payload: Any,
    ) -> None:
        self.session = session
        self.organization_id = organization_id
        self.endpoint = endpoint
        self.key = key
        self._store = IdempotencyStore(session, organization_id)
        self._fingerprint = request_fingerprint(payload)

    @property
    def enabled(self) -> bool:
        """客户端是否带了幂等键。"""
        return bool(self.key)

    def begin(self) -> Response | None:
        """取锁并检查是否已有记录。返回 ``Response`` 表示应直接回放、**不得**再执行业务。"""
        key = self.key
        if not key:
            return None
        replay = self._store.begin(self.endpoint, key, self._fingerprint)
        if replay is None:
            return None
        return _response(replay.status, replay.body, headers={REPLAY_HEADER: "true"})

    def finish(self, *, status_code: int, body: Any) -> Response:
        """业务已执行完毕：落幂等记录并返回响应（与业务写入同事务、同提交）。"""
        encoded = jsonable_encoder(body)
        key = self.key
        if key:
            self._store.complete(
                self.endpoint,
                key,
                self._fingerprint,
                status_code,
                _as_mapping(encoded),
            )
        return _response(status_code, encoded)