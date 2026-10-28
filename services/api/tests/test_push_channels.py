"""推送通道验收：设备 token、收件人快照、APNs JWT 与失败语义。"""

from __future__ import annotations

import base64
import json
import uuid
from uuid import UUID

import pytest
from app.config import get_settings
from app.db import org_session
from app.main import app
from app.models.notifications import Notification
from app.services import notification_service
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from sqlalchemy import delete

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
USER_A = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers() -> dict[str, str]:
    return {"X-Org-Id": ORG_A, "X-User-Id": USER_A}


def test_device_token_registration_is_idempotent(client: TestClient) -> None:
    token = f"apns-{uuid.uuid4().hex}"
    try:
        first = client.post(
            "/v1/devices", headers=headers(), json={"platform": "IOS", "token": token}
        )
        assert first.status_code == 201, first.text
        second = client.post(
            "/v1/devices", headers=headers(), json={"platform": "IOS", "token": token}
        )
        assert second.status_code == 201
        assert second.json()["id"] == first.json()["id"]

        listed = client.get("/v1/devices", headers=headers())
        assert listed.status_code == 200
        assert any(item["token"] == token for item in listed.json())

        with org_session(UUID(ORG_A)) as session:
            resolved = notification_service.resolve_recipient(
                session, UUID(ORG_A), UUID(USER_A), "apns"
            )
        assert resolved == token

        removed = client.delete(f"/v1/devices/{first.json()['id']}", headers=headers())
        assert removed.status_code == 204
        after = client.get("/v1/devices", headers=headers())
        assert all(item["id"] != first.json()["id"] for item in after.json())
    finally:
        with org_session(UUID(ORG_A)) as session:
            from app.models.devices import DeviceToken

            session.execute(delete(DeviceToken).where(DeviceToken.token == token))


def test_smtp_recipient_is_user_email() -> None:
    with org_session(UUID(ORG_A)) as session:
        recipient = notification_service.resolve_recipient(
            session, UUID(ORG_A), UUID(USER_A), "smtp"
        )
    assert recipient == "kerry@anari.example"


def test_apns_token_has_valid_es256_jwt(monkeypatch) -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    settings = get_settings()
    monkeypatch.setattr(settings, "apns_key_id", "KEY123", raising=False)
    monkeypatch.setattr(settings, "apns_team_id", "TEAM123", raising=False)
    monkeypatch.setattr(settings, "apns_topic", "com.anari.globaltravelos", raising=False)
    monkeypatch.setattr(settings, "apns_private_key", pem, raising=False)
    monkeypatch.setattr(settings, "apns_use_sandbox", True, raising=False)

    provider = notification_service.APNsPushProvider()
    token = provider._token()
    header_b64, claims_b64, signature_b64 = token.split(".")

    def decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    header = json.loads(decode(header_b64))
    claims = json.loads(decode(claims_b64))
    assert header == {"alg": "ES256", "kid": "KEY123"}
    assert claims["iss"] == "TEAM123"
    assert len(decode(signature_b64)) == 64  # JWT ES256 uses raw r||s, not DER
    assert provider._token() == token  # 50 分钟内复用


def test_unconfigured_provider_fails_explicitly(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "smtp_host", None, raising=False)
    monkeypatch.setattr(settings, "smtp_from", None, raising=False)
    with pytest.raises(notification_service.PushUnavailable):
        notification_service.SMTPPushProvider()

    monkeypatch.setattr(settings, "apns_key_id", None, raising=False)
    with pytest.raises(notification_service.PushUnavailable):
        notification_service.APNsPushProvider()


def test_dispatch_records_failure_instead_of_faking_success() -> None:
    notification_id = uuid.uuid4()
    with org_session(UUID(ORG_A)) as session:
        session.add(
            Notification(
                id=notification_id,
                organization_id=UUID(ORG_A),
                user_id=UUID(USER_A),
                kind="PROACTIVE_SUGGESTION",
                title="无 token 的 APNs 通知",
                body="应当失败",
                status="PENDING",
                provider="apns",
                recipient=None,
                attempts=0,
            )
        )
    try:
        with org_session(UUID(ORG_A)) as session:
            report = notification_service.dispatch_pending(session, UUID(ORG_A), limit=100)
        assert report.claimed >= 1
        with org_session(UUID(ORG_A)) as session:
            row = session.get(Notification, notification_id)
        assert row is not None
        assert row.status in ("PENDING", "FAILED")
        assert row.attempts == 1
        assert row.last_error and "APNs" in row.last_error
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(delete(Notification).where(Notification.id == notification_id))
