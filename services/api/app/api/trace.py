"""请求追踪：为每个请求分配 ``trace_id``，贯穿日志与错误响应。

用 ``ContextVar`` 而不是塞进 ``request.state``：服务层与后台任务（Worker）也需要打同一个
trace_id，而它们拿不到 Request 对象。这样"一条日志 → 一个 ID → 一个客户端截图"可以对齐。
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar

TRACE_HEADER = "X-Trace-Id"

_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)


def new_trace_id() -> str:
    return uuid.uuid4().hex


def set_trace_id(value: str | None) -> str:
    trace_id = value or new_trace_id()
    _trace_id.set(trace_id)
    return trace_id


def get_trace_id() -> str:
    trace_id = _trace_id.get()
    if trace_id is None:
        trace_id = set_trace_id(None)
    return trace_id