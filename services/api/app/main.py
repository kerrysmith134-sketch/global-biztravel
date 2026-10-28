"""FastAPI 应用装配（设计文档 v2.0 第 5.1 章）。

这个文件刻意保持很薄：只负责**把已经实现好的东西接起来**——trace 中间件、错误处理器、
路由聚合。业务规则在 services 层，HTTP 语义在 api 层，这里不写任何 if/else 业务分支。

关于 trace 中间件，有一处容易踩的坑值得写在代码里：必须用**纯 ASGI 中间件**，不能用
``@app.middleware("http")``。``BaseHTTPMiddleware`` 会在独立 task 里调用下游应用，
而 ``contextvars`` 不会跨 task 传播——结果是错误响应体里写的 trace_id 与响应头里的
trace_id 是两个不同的值。客户端拿到 500 去查日志、却对不上号，这是最气人的一类 bug，
所以这里直接绕开。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from fastapi import FastAPI
from starlette.datastructures import MutableHeaders

from app import __version__
from app.api.errors import register_error_handlers
from app.api.routes import api_router
from app.api.trace import TRACE_HEADER, set_trace_id
from app.api.transaction import CommitBeforeResponseMiddleware
from app.observability.logging import RequestLogMiddleware, configure_http_logging

API_PREFIX = "/v1"

Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]


class TraceMiddleware:
    """为每个 HTTP 请求分配/透传 trace id，并把它写回响应头。

    客户端可以自带 ``X-Trace-Id``（便于把移动端日志和服务端日志串起来）；没带就新建。
    同一个值会写进 ``request.state``、contextvar（供错误处理器读）和响应头。
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Message, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = MutableHeaders(scope=scope).get(TRACE_HEADER)
        trace_id = set_trace_id(incoming)
        scope.setdefault("state", {})
        scope["state"]["trace_id"] = trace_id

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[TRACE_HEADER] = trace_id
            await send(message)

        await self.app(scope, receive, send_wrapper)


def create_app() -> FastAPI:
    application = FastAPI(
        title="全球商务出行智能体 API",
        version=__version__,
        summary="Global Business Travel OS —— 主动式商务出行规划与执行平台",
        description=(
            "服务端契约。核心不变式：计划不可变、操作可审计、后台任务可恢复、结果可验证；"
            "LLM 负责理解与解释，优化算法负责计算，独立验证器负责检查——任何单一模型都不宣布行程可行。"
        ),
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    # 先加请求日志、再加 trace：Starlette 后加的中间件在外层，于是 trace 先写入 scope.state，
    # 日志中间件在响应开始时就能带上同一个 trace_id（顺序写反会得到空 trace）。
    configure_http_logging()
    application.add_middleware(RequestLogMiddleware)
    application.add_middleware(TraceMiddleware)
    # 最后加 → 最外层：先由 trace 写入 scope.state，再由本中间件在响应开始前提交事务。
    application.add_middleware(CommitBeforeResponseMiddleware)
    register_error_handlers(application)
    application.include_router(api_router, prefix=API_PREFIX)
    # PRD MVP 基线使用 /api/v1，设计 v2.0 统一为 /v1。为兼容既有客户端与文档，额外挂一个
    # 不出现在 OpenAPI 里的别名；契约真相源仍是 /v1。
    application.include_router(api_router, prefix="/api/v1", include_in_schema=False)
    return application


app = create_app()