"""可观测性：结构化日志与请求级延迟。

设计文档 v2.0 第 15 章要求日志必含 ``trace_id / org_id / trip_id / usecase / latency_ms``，
且一次用户动作贯穿 API → 用例 → 领域 → 规划 → LLM → DB。本模块只解决"出口"：用一个纯 ASGI
中间件在响应开始时写一条 JSON 日志；业务代码继续用普通 logger，不需要到处手工拼字段。
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import MutableMapping
from typing import Any

HTTP_LOGGER = logging.getLogger("app.http")

#: 路径里的 UUID。审计/日志要的是"哪个端点"，不是"哪一条记录"——后者在字段里。
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_TRIP_PATH_RE = re.compile(r"^/v1/trips/([0-9a-fA-F-]{36})(?:/|$)")


def route_template(path: str) -> str:
    return _UUID_RE.sub("{id}", path)


def trip_id_from_path(path: str) -> str | None:
    match = _TRIP_PATH_RE.match(path)
    return match.group(1) if match else None


class JsonFormatter(logging.Formatter):
    """把一条日志渲染成单行 JSON。

    ``extras`` 里的字段用后缀约定：调用方通过 ``extra={...}`` 传入；formatter 只挑
    allow-list 字段输出，避免把异常对象或密钥顺手序列化出去（红线 RL-05）。
    """

    FIELDS = (
        "trace_id",
        "org_id",
        "trip_id",
        "usecase",
        "latency_ms",
        "method",
        "path",
        "status_code",
        "error",
    )

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in self.FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = str(value) if field in {"org_id", "trip_id", "trace_id"} else value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_http_logging(level: int = logging.INFO) -> None:
    """给 ``app.http`` 挂一个 JSON handler；重复调用不会叠加 handler。"""
    logger = HTTP_LOGGER
    logger.setLevel(level)
    if any(isinstance(handler.formatter, JsonFormatter) for handler in logger.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)


class RequestLogMiddleware:
    """纯 ASGI 中间件：测一次请求的真实耗时并在响应开始时写日志。

    为什么不用 ``@app.middleware("http")``：与 trace 中间件相同的坑——``BaseHTTPMiddleware``
    在独立 task 里调用下游，contextvars 不跨 task 传播，日志里的 trace_id 会和响应头对不上。
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: MutableMapping[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status_code = 500
        error: str | None = None

        async def send_wrapper(message: MutableMapping[str, Any]) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                latency_ms = round((time.perf_counter() - started) * 1000, 2)
                state = scope.get("state") or {}
                path = scope.get("path", "")
                HTTP_LOGGER.info(
                    "http_request",
                    extra={
                        "trace_id": state.get("trace_id"),
                        "org_id": state.get("org_id"),
                        "trip_id": state.get("trip_id") or trip_id_from_path(path),
                        "usecase": f"{scope.get('method', 'GET')} {route_template(path)}",
                        "latency_ms": latency_ms,
                        "method": scope.get("method"),
                        "path": path,
                        "status_code": status_code,
                        "error": error,
                    },
                )
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:  # noqa: BLE001 —— 先记日志再抛，错误处理器仍会响应
            error = f"{type(exc).__name__}"
            raise


__all__ = [
    "HTTP_LOGGER",
    "JsonFormatter",
    "RequestLogMiddleware",
    "configure_http_logging",
    "route_template",
    "trip_id_from_path",
]
