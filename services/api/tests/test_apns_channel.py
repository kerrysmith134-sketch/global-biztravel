"""APNs 收口（§7-2 最后一项，ADR-0034 补篇）：本地可联调 + 失效 token 自动清理。

- JWT：ES256、kid/iss/iat 结构正确、50 分钟缓存复用；
- ``GBT_APNS_API_BASE``：可指向本地 mock（无需 Apple 凭证即可端到端联调）；
- 410 Unregistered：dispatch 自动禁用失效设备 token（Apple 语义：别再发）；
  其他 4xx/5xx 只记录失败，不动设备；
- payload 携带 suggestion_id（deep-link 预留）。

真实 .p8 用 cryptography 现场生成（P-256）——provider 只要求合法 PEM，不关心签发方。
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from app.config import get_settings
from app.db import org_session
from app.main import app
from app.services.notification_service import (
    APNsPushProvider,
    ApnsUnregistered,
    Notification,
    PushUnavailable,
    dispatch_pending,
)
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from sqlalchemy import text

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
USER_A = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers(org: str = ORG_A, user: str = USER_A) -> dict[str, str]:
    return {"X-Org-Id": org, "X-User-Id": user}


def _p8(tmp_path: Path) -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    path = tmp_path / "AuthKey_TEST.p8"
    path.write_bytes(pem)
    return str(path)


def _configure(tmp_path: Path, monkeypatch, *, api_base: str | None = None) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "apns_key_id", "TESTKEYID", raising=False)
    monkeypatch.setattr(settings, "apns_team_id", "TEAMTEAM", raising=False)
    monkeypatch.setattr(settings, "apns_topic", "com.anari.globaltravelos", raising=False)
    monkeypatch.setattr(settings, "apns_private_key", _p8(tmp_path), raising=False)
    monkeypatch.setattr(settings, "apns_use_sandbox", True, raising=False)
    monkeypatch.setattr(settings, "apns_api_base", api_base, raising=False)


def _notification(token: str, suggestion_id=None) -> Notification:
    return Notification(
        organization_id=ORG_A,
        user_id=USER_A,
        suggestion_id=suggestion_id,
        kind="PROACTIVE_SUGGESTION",
        priority="P1",
        issue_key="test",
        title="签证材料可能不足",
        body="距离出发不足 10 个工作日",
        status="PENDING",
        provider="apns",
        recipient=token,
        attempts=0,
    )


def _register_device(client: TestClient, token: str) -> None:
    registered = client.post(
        "/v1/devices",
        headers=headers(),
        json={"token": token, "platform": "IOS"},
    )
    assert registered.status_code in (200, 201), registered.text


def _device_enabled(token: str) -> bool:
    with org_session(ORG_A) as session:
        row = session.execute(
            text("SELECT enabled FROM device_tokens WHERE token = :t"), {"t": token}
        ).scalar()
        return bool(row)


def _cleanup_device(token: str) -> None:
    with org_session(ORG_A) as session:
        session.execute(text("DELETE FROM device_tokens WHERE token = :t"), {"t": token})


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


def _capture_post(monkeypatch, *, status_code: int = 200, text: str = ""):
    """拦截 httpx.post，返回 (captured requests, responder)。"""
    captured: list[dict] = []

    def fake_post(url, **kwargs):
        captured.append({"url": url, "headers": kwargs.get("headers", {}), "json": kwargs.get("json")})
        return _FakeResponse(status_code, text)

    monkeypatch.setattr("httpx.post", fake_post)
    return captured


# ---------------------------------------------------------------------------
# 轮 1–2：JWT 结构与缓存
# ---------------------------------------------------------------------------


def test_apns_jwt_structure(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    captured = _capture_post(monkeypatch)
    provider = APNsPushProvider()
    provider.send(_notification("device-token-1"))

    auth = captured[0]["headers"]["authorization"]
    assert auth.startswith("bearer ")
    header, claims, signature = auth[len("bearer "):].split(".")
    header_decoded = json.loads(base64.urlsafe_b64decode(header + "=="))
    claims_decoded = json.loads(base64.urlsafe_b64decode(claims + "=="))
    assert header_decoded == {"alg": "ES256", "kid": "TESTKEYID"}
    assert claims_decoded["iss"] == "TEAMTEAM"
    now = datetime.now(UTC).timestamp()
    assert abs(claims_decoded["iat"] - now) < 120
    assert len(signature) > 40  # 64 字节 r+s 的 base64url（去掉 padding）


def test_apns_jwt_is_cached_across_sends(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    captured = _capture_post(monkeypatch)
    provider = APNsPushProvider()
    provider.send(_notification("device-token-1"))
    provider.send(_notification("device-token-2"))

    first = captured[0]["headers"]["authorization"]
    second = captured[1]["headers"]["authorization"]
    assert first == second  # 缓存内复用同一 JWT，不重复签名


# ---------------------------------------------------------------------------
# 轮 3–4：API base 覆盖（本地 mock 联调路径）与成功路径
# ---------------------------------------------------------------------------


def test_apns_api_base_override_points_at_mock(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch, api_base="http://127.0.0.1:9123")
    captured = _capture_post(monkeypatch)
    provider = APNsPushProvider()
    provider.send(_notification("device-token-3"))

    assert captured[0]["url"].startswith("http://127.0.0.1:9123/3/device/")
    assert "api.push.apple.com" not in captured[0]["url"]


def test_apns_success_and_default_sandbox_host(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    captured = _capture_post(monkeypatch)
    provider = APNsPushProvider()
    provider.send(_notification("device-token-4"))

    assert captured[0]["url"] == "https://api.sandbox.push.apple.com/3/device/device-token-4"
    assert captured[0]["headers"]["apns-topic"] == "com.anari.globaltravelos"
    assert captured[0]["headers"]["apns-push-type"] == "alert"


# ---------------------------------------------------------------------------
# 轮 5–7：410 清理 token、其他 4xx 不动设备、payload 业务键
# ---------------------------------------------------------------------------


def test_apns_410_raises_unregistered_type(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    _capture_post(monkeypatch, status_code=410, text='{"reason": "Unregistered"}')
    provider = APNsPushProvider()
    with pytest.raises(ApnsUnregistered):
        provider.send(_notification("device-token-5"))


def _insert_pending_notification(token: str, *, attempts: int = 0) -> str:
    from app.models.notifications import Notification as NotificationRow

    with org_session(ORG_A) as session:
        row = NotificationRow(
            organization_id=ORG_A,
            user_id=USER_A,
            kind="PROACTIVE_SUGGESTION",
            priority="P1",
            issue_key=f"apns-test-{uuid4().hex[:8]}",
            title="测试通知",
            body="410 清理路径",
            status="PENDING",
            provider="apns",
            recipient=token,
            attempts=attempts,
        )
        session.add(row)
        session.flush()
        notification_id = str(row.id)
    return notification_id


def test_dispatch_disables_device_on_410(client: TestClient, tmp_path, monkeypatch):
    from app.models.notifications import Notification as NotificationRow

    _configure(tmp_path, monkeypatch)
    token = f"dev-{uuid4().hex[:12]}"
    _register_device(client, token)
    notification_id = _insert_pending_notification(token)
    try:
        assert _device_enabled(token) is True
        _capture_post(monkeypatch, status_code=410, text='{"reason": "Unregistered"}')

        with org_session(ORG_A) as session:
            report = dispatch_pending(session, ORG_A)

        assert report.claimed == 1
        assert _device_enabled(token) is False  # Apple 说 token 失效 ⇒ 不再向它发送
        with org_session(ORG_A) as session:
            row = session.get(NotificationRow, notification_id)
            assert row.status == "FAILED"
            assert "Unregistered" in (row.last_error or "")
    finally:
        _cleanup_device(token)
        with org_session(ORG_A) as session:
            session.execute(
                text("DELETE FROM notifications WHERE id = :n"), {"n": notification_id}
            )


def test_apns_other_4xx_keeps_device_enabled(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    _capture_post(monkeypatch, status_code=400, text='{"reason": "BadDeviceToken"}')
    provider = APNsPushProvider()
    with pytest.raises(PushUnavailable) as excinfo:
        provider.send(_notification("device-token-6"))
    assert not isinstance(excinfo.value, ApnsUnregistered)


def test_apns_payload_carries_suggestion_id(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    captured = _capture_post(monkeypatch)
    provider = APNsPushProvider()
    suggestion_id = uuid4()
    provider.send(_notification("device-token-7", suggestion_id=suggestion_id))

    payload = captured[0]["json"]
    assert payload["aps"]["alert"]["title"] == "签证材料可能不足"
    assert payload["aps"]["sound"] == "default"
    assert payload["suggestion_id"] == str(suggestion_id)


def test_apns_omits_suggestion_id_when_absent(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    captured = _capture_post(monkeypatch)
    provider = APNsPushProvider()
    provider.send(_notification("device-token-8"))
    assert "suggestion_id" not in captured[0]["json"]


# ---------------------------------------------------------------------------
# 轮 8：缺 recipient 不发请求
# ---------------------------------------------------------------------------


def test_apns_missing_recipient_fails_without_request(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    captured = _capture_post(monkeypatch)
    provider = APNsPushProvider()
    with pytest.raises(PushUnavailable):
        provider.send(_notification(""))
    assert captured == []
