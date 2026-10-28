"""事务必须在响应开始前提交（回归测试）。

FastAPI 的 yield 依赖 teardown 发生在响应发送之后；本测试直接驱动中间件，验证：

- 2xx ⇒ 提交；
- 4xx ⇒ 回滚；
- 提交失败 ⇒ 原响应被丢弃、返回 500、会话标记跳过 teardown 提交。
"""

from __future__ import annotations

import pytest
from app.api.transaction import CommitBeforeResponseMiddleware


class FakeSession:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.committed = False
        self.rolledback = False
        self.info: dict = {}

    def commit(self) -> None:
        if self.fail:
            raise RuntimeError("commit failed")
        self.committed = True

    def rollback(self) -> None:
        self.rolledback = True


async def drive(session: FakeSession, status: int):
    sent: list[dict] = []

    async def app(scope, receive, send):  # noqa: ANN001
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": b'{"ok":true}'})

    async def receive():  # noqa: ANN202
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/things",
        "state": {"db_session": session, "trace_id": "trace-1"},
    }
    async def send(message):  # noqa: ANN001
        sent.append(message)

    middleware = CommitBeforeResponseMiddleware(app)
    await middleware(scope, receive, send)
    return sent


@pytest.mark.asyncio
async def test_success_commits_before_response() -> None:
    session = FakeSession()
    sent = await drive(session, 201)
    assert session.committed is True
    assert session.rolledback is False
    assert sent[0]["status"] == 201
    assert session.info.get("skip_commit") is True


@pytest.mark.asyncio
async def test_error_rolls_back_instead_of_committing() -> None:
    session = FakeSession()
    sent = await drive(session, 422)
    assert session.committed is False
    assert session.rolledback is True
    assert sent[0]["status"] == 422


@pytest.mark.asyncio
async def test_commit_failure_replaces_success_with_500() -> None:
    session = FakeSession(fail=True)
    sent = await drive(session, 201)
    assert session.rolledback is True
    assert session.info.get("skip_commit") is True
    assert sent[0]["status"] == 500
    assert sent[1]["body"]
