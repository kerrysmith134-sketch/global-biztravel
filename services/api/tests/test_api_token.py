"""部署 token 鉴权（云上部署，ADR-0035）：

``GBT_API_TOKEN`` 未设置（本地开发）⇒ 全放行；
设置后 ⇒ 除健康检查外，所有请求必须带 ``X-API-Key: <token>``，否则 401。

纯 ASGI 调用测试（不起服务器）。
"""

from __future__ import annotations

import json
from typing import Any

from app.main import create_app


def _call(app: Any, path: str, *, headers: dict[str, str] | None = None) -> tuple[int, dict]:
    status_code: dict[str, int] = {}
    body_chunks: list[bytes] = []

    async def receive() -> dict:  # noqa: ARG001
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        if message["type"] == "http.response.start":
            status_code["value"] = message["status"]
        elif message["type"] == "http.response.body":
            body_chunks.append(message.get("body", b""))

    import anyio

    async def drive() -> None:
        await app(
            {
                "type": "http",
                "method": "GET",
                "path": path,
                "headers": [
                    (key.lower().encode(), value.encode()) for key, value in (headers or {}).items()
                ],
                "query_string": b"",
            },
            receive,
            send,
        )

    anyio.run(drive)
    body: dict = {}
    if body_chunks:
        try:
            body = json.loads(b"".join(body_chunks))
        except json.JSONDecodeError:
            body = {}
    return status_code.get("value", 0), body


def test_token_unset_allows_everything(monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "api_token", None, raising=False)
    app = create_app()
    status, _ = _call(app, "/v1/trips", headers={"X-Org-Id": "11111111-1111-1111-1111-111111111111"})
    assert status == 200  # 本地开发行为完全不变


def test_token_set_rejects_missing_key(monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "api_token", "secret-token", raising=False)
    app = create_app()
    status, body = _call(app, "/v1/trips", headers={"X-Org-Id": "11111111-1111-1111-1111-111111111111"})
    assert status == 401
    assert body["error"]["code"] == "UNAUTHORIZED"


def test_token_set_rejects_wrong_key(monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "api_token", "secret-token", raising=False)
    app = create_app()
    status, _ = _call(app, "/v1/trips", headers={"X-API-Key": "wrong", "X-Org-Id": "11111111-1111-1111-1111-111111111111"})
    assert status == 401


def test_token_set_accepts_correct_key(monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "api_token", "secret-token", raising=False)
    app = create_app()
    status, _ = _call(
        app,
        "/v1/trips",
        headers={"X-API-Key": "secret-token", "X-Org-Id": "11111111-1111-1111-1111-111111111111"},
    )
    assert status == 200


def test_health_is_exempt_from_token(monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "api_token", "secret-token", raising=False)
    app = create_app()
    status, _ = _call(app, "/v1/health")
    assert status == 200
