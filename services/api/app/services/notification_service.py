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

import logging
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.notifications import Notification
from app.models.proactive import ProactiveSuggestion

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


class APNsPushProvider:
    name = "apns"

    def send(self, notification: Notification) -> None:  # noqa: ARG002
        raise PushUnavailable("APNs 未配置证书与设备 token，拒绝伪造发送成功")


def build_push_provider() -> PushProvider:
    settings = get_settings()
    if settings.push_provider == "log":
        return LogPushProvider()
    if settings.push_provider == "apns":
        return APNsPushProvider()
    return InAppPushProvider()


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
    statement = (
        pg_insert(Notification.__table__)
        .values(
            organization_id=suggestion.organization_id,
            user_id=user_id,
            suggestion_id=suggestion.id,
            kind="PROACTIVE_SUGGESTION",
            title=suggestion.title,
            body=suggestion.rationale,
            status="PENDING",
            provider=provider,
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
    provider = build_push_provider()
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
        row.provider = provider.name
        try:
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
    limit: int = 50,
) -> list[Notification]:
    stmt = select(Notification).where(
        Notification.organization_id == str(organization_id),
        Notification.user_id == str(user_id),
    )
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
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


__all__ = [
    "APNsPushProvider",
    "DispatchReport",
    "InAppPushProvider",
    "LogPushProvider",
    "PushProvider",
    "PushUnavailable",
    "build_push_provider",
    "dispatch_pending",
    "enqueue_for_suggestion",
    "list_for_user",
    "mark_read",
    "unread_count",
]
