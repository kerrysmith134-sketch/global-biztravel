"""推送通道与通知服务。

设计文档 v2.0 第 8.6 章。建议"已推送"是一个需要可靠投递的动作，因此拆成两步：

1. **入队（与建议同事务）**：Outbox 消费者按建议创建一条 ``notifications`` 行。唯一约束
   保证重投不会产生第二条。
2. **投递（Worker）**：按配置的 provider 发送；失败累计次数，超限转 ``FAILED`` 并保留
   ``last_error``。

provider 的诚实边界：

- ``inapp``：通知列表本身就是通道，落库即送达（本地默认）。
- ``log``：只写结构化日志，用于排查。
- ``apns``：需要真实证书与设备 token；本仓库未配置，显式抛 ``PushUnavailable``，
  让通知进入失败/重试，而不是假装发送成功。
"""

from __future__ import annotations

import base64
import json
import logging
import smtplib
import time
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol
from uuid import UUID

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.devices import DeviceToken
from app.models.identity import User
from app.models.notifications import Notification
from app.models.proactive import ProactiveSuggestion
from app.services import notification_policy_service

logger = logging.getLogger("app.notifications")


class PushUnavailable(RuntimeError):
    """provider 未配置或发送失败。"""


class PushProvider(Protocol):
    name: str

    def send(self, notification: Notification) -> None: ...


class InAppPushProvider:
    """落库即送达：通知列表就是用户看到的通道。"""

    name = "inapp"

    def send(self, notification: Notification) -> None:  # noqa: ARG002
        return None


class LogPushProvider:
    name = "log"

    def send(self, notification: Notification) -> None:
        logger.info(
            "推送通知 user=%s title=%s suggestion=%s",
            notification.user_id,
            notification.title,
            notification.suggestion_id,
        )


class SMTPPushProvider:
    """SMTP 邮件通道。recipient 在入队时快照为用户邮箱。"""

    name = "smtp"

    def __init__(self) -> None:
        settings = get_settings()
        self.host = settings.smtp_host or ""
        self.port = settings.smtp_port
        self.username = settings.smtp_username
        self.password = settings.smtp_password
        self.sender = settings.smtp_from
        self.use_tls = settings.smtp_use_tls
        if not self.host or not self.sender:
            raise PushUnavailable("SMTP 未配置 host/from")

    def send(self, notification: Notification) -> None:
        if not notification.recipient:
            raise PushUnavailable("通知缺少收件人邮箱")
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = notification.recipient
        message["Subject"] = notification.title
        message.set_content(notification.body or notification.title)
        try:
            with smtplib.SMTP(self.host, int(self.port), timeout=15) as smtp:
                smtp.ehlo()
                if self.use_tls:
                    smtp.starttls()
                    smtp.ehlo()
                if self.username and self.password:
                    smtp.login(self.username, self.password)
                smtp.send_message(message)
        except Exception as exc:  # noqa: BLE001 —— 统一转成可重试失败
            raise PushUnavailable(f"SMTP 发送失败：{type(exc).__name__}: {exc}") from exc


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class APNsPushProvider:
    """APNs token-based（.p8）HTTP/2 推送。

    未配置 key/team/topic，或通知没有设备 token 时显式抛 ``PushUnavailable``；绝不伪造"已发送"。
    JWT 缓存 50 分钟（Apple 要求不超过 1 小时，且不要频繁刷新）。
    """

    name = "apns"

    def __init__(self) -> None:
        settings = get_settings()
        self.key_id = settings.apns_key_id
        self.team_id = settings.apns_team_id
        self.topic = settings.apns_topic
        self.sandbox = settings.apns_use_sandbox
        raw = settings.apns_private_key
        if not (self.key_id and self.team_id and self.topic and raw):
            raise PushUnavailable("APNs 未配置 key_id/team_id/topic/private_key")
        path = Path(raw)
        pem = path.read_text(encoding="utf-8") if path.exists() else raw.replace("\\n", "\n")
        try:
            self._key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
        except Exception as exc:  # noqa: BLE001
            raise PushUnavailable(f"APNs 私钥无法解析：{type(exc).__name__}") from exc
        self._jwt: str | None = None
        self._jwt_expires_at = 0.0

    def _token(self) -> str:
        now = time.time()
        if self._jwt and now < self._jwt_expires_at:
            return self._jwt
        header = _b64url(json.dumps({"alg": "ES256", "kid": self.key_id}).encode("utf-8"))
        claims = _b64url(json.dumps({"iss": self.team_id, "iat": int(now)}).encode("utf-8"))
        signing_input = f"{header}.{claims}".encode("ascii")
        der = self._key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        self._jwt = f"{header}.{claims}.{_b64url(raw)}"
        self._jwt_expires_at = now + 3000
        return self._jwt

    def send(self, notification: Notification) -> None:
        if not notification.recipient:
            raise PushUnavailable("通知缺少 APNs 设备 token")
        host = "api.sandbox.push.apple.com" if self.sandbox else "api.push.apple.com"
        try:
            import httpx

            response = httpx.post(
                f"https://{host}/3/device/{notification.recipient}",
                headers={
                    "authorization": f"bearer {self._token()}",
                    "apns-topic": self.topic,
                    "apns-push-type": "alert",
                    "apns-priority": "10",
                },
                json={
                    "aps": {
                        "alert": {"title": notification.title, "body": notification.body or ""},
                        "sound": "default",
                    }
                },
                http2=True,
                timeout=15,
            )
        except Exception as exc:  # noqa: BLE001
            raise PushUnavailable(f"APNs 请求失败：{type(exc).__name__}: {exc}") from exc
        if response.status_code >= 400:
            raise PushUnavailable(f"APNs 拒绝：{response.status_code} {response.text[:200]}")


def build_push_provider(name: str | None = None) -> PushProvider:
    selected = name or get_settings().push_provider
    if selected == "log":
        return LogPushProvider()
    if selected == "smtp":
        return SMTPPushProvider()
    if selected == "apns":
        return APNsPushProvider()
    return InAppPushProvider()


def resolve_recipient(
    session: Session,
    organization_id: UUID | str,
    user_id: UUID | str | None,
    provider: str,
) -> str | None:
    """通知入队时解析收件人：SMTP→邮箱，APNs→最近活跃设备 token。"""
    if user_id is None:
        return None
    if provider == "smtp":
        user = session.get(User, str(user_id))
        return user.email if user is not None else None
    if provider == "apns":
        token = session.scalars(
            select(DeviceToken)
            .where(
                DeviceToken.organization_id == str(organization_id),
                DeviceToken.user_id == str(user_id),
                DeviceToken.enabled.is_(True),
            )
            .order_by(DeviceToken.last_seen_at.desc().nullslast(), DeviceToken.created_at.desc())
            .limit(1)
        ).first()
        return token.token if token is not None else None
    return None


@dataclass
class DispatchReport:
    claimed: int = 0
    sent: int = 0
    failed: int = 0
    retried: int = 0


def enqueue_for_suggestion(
    session: Session,
    suggestion: ProactiveSuggestion,
    *,
    user_id: UUID | None,
) -> Notification | None:
    """为一条**已推送**建议创建通知。抑制/保留的建议不通知（它们本就不该打扰用户）。"""
    if suggestion.push_status != "PUSHED":
        return None
    provider = get_settings().push_provider
    recipient = resolve_recipient(session, suggestion.organization_id, user_id, provider)
    statement = (
        pg_insert(Notification.__table__)
        .values(
            organization_id=suggestion.organization_id,
            user_id=user_id,
            suggestion_id=suggestion.id,
            kind="PROACTIVE_SUGGESTION",
            priority=notification_policy_service.classify(
                suggestion_type=suggestion.suggestion_type,
                severity=suggestion.severity,
            ),
            issue_key=suggestion.dedupe_key,
            title=suggestion.title,
            body=suggestion.rationale,
            status="PENDING",
            provider=provider,
            recipient=recipient,
            attempts=0,
        )
        .on_conflict_do_nothing(constraint="uq_notifications_org_suggestion_provider")
        .returning(Notification.__table__.c.id)
    )
    inserted = session.execute(statement).scalar_one_or_none()
    if inserted is None:
        return session.scalars(
            select(Notification).where(
                Notification.organization_id == suggestion.organization_id,
                Notification.suggestion_id == suggestion.id,
                Notification.provider == provider,
            )
        ).one_or_none()
    session.flush()
    return session.get(Notification, inserted)


def dispatch_pending(
    session: Session, organization_id: UUID | str, *, limit: int = 50
) -> DispatchReport:
    """投递一批待发通知。失败按次数上限转 FAILED，未超限保留 PENDING 等下一轮。"""
    settings = get_settings()
    now = session.scalar(select(func.clock_timestamp()))
    rows = list(
        session.scalars(
            select(Notification)
            .where(
                Notification.organization_id == str(organization_id),
                Notification.status == "PENDING",
                Notification.attempts < settings.push_max_attempts,
            )
            .order_by(Notification.created_at)
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
    )
    report = DispatchReport(claimed=len(rows))
    for row in rows:
        row.attempts += 1
        try:
            provider = build_push_provider(row.provider)
            provider.send(row)
        except PushUnavailable as exc:
            row.last_error = str(exc)
            if row.attempts >= settings.push_max_attempts:
                row.status = "FAILED"
                report.failed += 1
            else:
                row.status = "PENDING"
                report.retried += 1
        except Exception as exc:  # noqa: BLE001 —— provider 异常不能炸掉整批
            row.last_error = f"{type(exc).__name__}: {exc}"
            if row.attempts >= settings.push_max_attempts:
                row.status = "FAILED"
                report.failed += 1
            else:
                report.retried += 1
        else:
            row.status = "SENT"
            row.sent_at = now
            row.last_error = None
            report.sent += 1
    session.flush()
    return report


def list_for_user(
    session: Session,
    organization_id: UUID | str,
    user_id: UUID | str,
    *,
    unread_only: bool = False,
    priority: str | None = None,
    limit: int = 50,
) -> list[Notification]:
    stmt = select(Notification).where(
        Notification.organization_id == str(organization_id),
        Notification.user_id == str(user_id),
    )
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    if priority:
        stmt = stmt.where(Notification.priority == priority.upper())
    return list(
        session.scalars(stmt.order_by(Notification.created_at.desc()).limit(limit))
    )


def mark_read(
    session: Session,
    organization_id: UUID | str,
    user_id: UUID | str,
    notification_id: UUID | str,
) -> Notification | None:
    row = session.scalars(
        select(Notification).where(
            Notification.organization_id == str(organization_id),
            Notification.user_id == str(user_id),
            Notification.id == str(notification_id),
        )
    ).one_or_none()
    if row is None:
        return None
    if row.read_at is None:
        row.read_at = session.scalar(select(func.clock_timestamp()))
        session.flush()
    return row


def unread_count(
    session: Session, organization_id: UUID | str, user_id: UUID | str
) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.organization_id == str(organization_id),
                Notification.user_id == str(user_id),
                Notification.read_at.is_(None),
                Notification.status == "SENT",
            )
        )
        or 0
    )


def unread_by_priority(
    session: Session, organization_id: UUID | str, user_id: UUID | str
) -> dict[str, int]:
    """按等级统计未读（口径与 ``unread_count`` 一致：已送达 + 未读）。"""
    counts = dict.fromkeys(notification_policy_service.PRIORITY_LABELS, 0)
    rows = session.execute(
        select(Notification.priority, func.count())
        .where(
            Notification.organization_id == str(organization_id),
            Notification.user_id == str(user_id),
            Notification.read_at.is_(None),
            Notification.status == "SENT",
        )
        .group_by(Notification.priority)
    ).all()
    for priority, count in rows:
        counts[str(priority)] = int(count)
    return counts


__all__ = [
    "APNsPushProvider",
    "DispatchReport",
    "InAppPushProvider",
    "LogPushProvider",
    "PushProvider",
    "PushUnavailable",
    "SMTPPushProvider",
    "build_push_provider",
    "dispatch_pending",
    "enqueue_for_suggestion",
    "list_for_user",
    "mark_read",
    "resolve_recipient",
    "unread_by_priority",
    "unread_count",
]
