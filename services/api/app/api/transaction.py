"""在响应开始前提交事务的 ASGI 中间件。

**为什么需要它**：FastAPI 的 ``yield`` 依赖在 0.106+ 会在响应发送**之后**才执行退出代码。
也就是说，``get_db`` 里的 ``session.commit()`` 可能晚于 201 响应到达客户端。客户端立刻用新建
资源发下一个请求时，就会读到"还不存在"的行——这不是测试才有的问题，弱网/高并发下同样会出现。

本中间件在 ``http.response.start`` 转发给客户端之前提交事务：

- 2xx：提交；失败则丢弃原响应，返回 500（客户端不会看到"成功但没落库"）。
- 4xx/5xx：回滚（错误响应不能顺手提交半截写入）。
- 依赖层的 teardown 提交会被标记跳过，避免二次提交/二次异常。
"""

from __future__ import annotations

import json
import logging
from collections.abc import MutableMapping
from typing import Any

from app.api.errors import ApiError, error_body

logger = logging.getLogger("app.http.transaction")


class CommitBeforeResponseMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: MutableMapping[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        state = scope.setdefault("state", {})
        replaced = False

        async def send_wrapper(message: MutableMapping[str, Any]) -> None:
            nonlocal replaced
            if replaced:
                return
            if message["type"] == "http.response.start":
                session = state.get("db_session")
                status = int(message["status"])
                if session is not None:
                    try:
                        if status < 400:
                            session.commit()
                        else:
                            session.rollback()
                    except Exception as exc:  # noqa: BLE001 —— 提交失败必须显式告诉客户端
                        logger.exception("事务提交失败，响应替换为 500")
                        session.rollback()
                        session.info["skip_commit"] = True
                        replaced = True
                        trace_id = state.get("trace_id")
                        payload = error_body(
                            ApiError(
                                "服务端提交失败",
                                code="COMMIT_FAILED",
                                status_code=500,
                                details={"error": f"{type(exc).__name__}"},
                            ),
                            trace_id or "",
                        )
                        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                        headers = [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode("ascii")),
                        ]
                        if trace_id:
                            headers.append((b"x-trace-id", str(trace_id).encode("ascii")))
                        await send(
                            {
                                "type": "http.response.start",
                                "status": 500,
                                "headers": headers,
                            }
                        )
                        await send({"type": "http.response.body", "body": body})
                        return
                    session.info["skip_commit"] = True
            await send(message)

        await self.app(scope, receive, send_wrapper)


__all__ = ["CommitBeforeResponseMiddleware"]
