"""离线批次同步：按设计文档第 10.5 章的冲突矩阵逐项裁决。

同步的语义是**逐项结果**，而不是"整批成功/失败"：一台设备可能同时带着拜访笔记、客户资料
修改和一条已经过期的计划停靠；其中一条冲突不应该让另外两条白白重传。因此：

- 每个 item 在自己的 ``SAVEPOINT`` 里处理，失败只回滚这一项。
- 返回值永远包含每个 item 的一条 verdict：``applied`` / ``conflict`` / ``rejected``。
- 没有返回 verdict 的项在客户端被视为"未送达"并保留在队列里（见 iOS ``SyncPushUseCase``）。

冲突矩阵（第 10.5 章）：

| 实体 | 策略 | 实现 |
|---|---|---|
| ``visit_record`` | last-writer-wins + 冲突副本 | 复用 :func:`app.services.record_service.submit_record` |
| ``plan_stop`` | reject-and-conflict-record | 计划不可变，直接拒绝并返回双方快照 |
| ``customer`` | 字段级 merge，服务端非空优先 | 只填空字段；服务端已有值不被覆盖 |

``delete`` 目前只对"原本就不支持删除"的实体显式拒绝：拜访记录是审计证据、计划停靠不可变、
客户没有离线删除入口。拒绝比静默忽略更诚实——客户端能看到哪一项需要人处理。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.api.schemas import SyncItemRequest, SyncPushRequest, VisitRecordCreate
from app.models.geo import Customer
from app.services import record_service

#: 客户资料允许通过离线同步写入的字段。白名单而不是黑名单：客户表里有 owner_user_id、
#: 时间戳等字段，客户端不该通过 payload 改它们。
_CUSTOMER_MERGE_FIELDS = (
    "name",
    "tier",
    "country_code",
    "industry",
    "primary_contact_name",
    "primary_contact_email",
    "primary_contact_phone",
    "notes",
)


@dataclass
class SyncItemOutcome:
    item_id: str
    entity: str
    status: str
    server_version: int | None = None
    conflict: dict[str, Any] | None = None


@dataclass
class SyncOutcome:
    results: list[SyncItemOutcome] = field(default_factory=list)

    @property
    def summary(self) -> dict[str, int]:
        return {
            "applied": sum(1 for item in self.results if item.status == "applied"),
            "conflict": sum(1 for item in self.results if item.status == "conflict"),
            "rejected": sum(1 for item in self.results if item.status == "rejected"),
        }


def push(
    session: Session,
    organization_id: UUID | str,
    request: SyncPushRequest,
    *,
    actor_user_id: UUID | str | None = None,
    now: datetime | None = None,
) -> SyncOutcome:
    """处理一批离线写入。调用方决定提交点，本函数只 ``flush``。"""
    current = now or datetime.now(UTC)
    outcome = SyncOutcome()
    for item in request.items:
        try:
            with session.begin_nested():
                result = _apply_item(
                    session,
                    organization_id,
                    item,
                    actor_user_id=actor_user_id,
                    now=current,
                )
        except ApiError as exc:
            result = SyncItemOutcome(
                item_id=item.item_id,
                entity=item.entity,
                status="rejected",
                conflict={
                    "reason": exc.code,
                    "server": None,
                    "client": {"message": exc.message, "payload": item.payload},
                },
            )
        except Exception as exc:  # noqa: BLE001 —— 单项异常必须变成逐项拒绝，不能炸整批
            result = SyncItemOutcome(
                item_id=item.item_id,
                entity=item.entity,
                status="rejected",
                conflict={
                    "reason": "SYNC_ITEM_FAILED",
                    "server": None,
                    "client": {"message": f"{type(exc).__name__}: {exc}", "payload": item.payload},
                },
            )
        outcome.results.append(result)
    return outcome


def _apply_item(
    session: Session,
    organization_id: UUID | str,
    item: SyncItemRequest,
    *,
    actor_user_id: UUID | str | None,
    now: datetime,
) -> SyncItemOutcome:
    if item.operation == "delete":
        return _rejected(item, "DELETE_NOT_SUPPORTED", "该实体不支持离线删除")

    if item.entity == "visit_record":
        data = dict(item.payload)
        data.setdefault("client_record_id", item.item_id)
        data.setdefault("client_updated_at", item.client_updated_at or now)
        visit_id = data.get("visit_id")
        if not visit_id:
            return _rejected(item, "MISSING_VISIT_ID", "拜访记录同步项缺少 visit_id")
        payload = VisitRecordCreate.model_validate(data)
        result = record_service.submit_record(
            session,
            organization_id,
            visit_id,
            payload,
            actor_user_id=actor_user_id,
            now=now,
        )
        if result.conflict is not None:
            return SyncItemOutcome(
                item_id=item.item_id,
                entity=item.entity,
                status="conflict",
                server_version=(
                    result.server_record.server_version if result.server_record is not None else None
                ),
                conflict=result.conflict,
            )
        return SyncItemOutcome(
            item_id=item.item_id,
            entity=item.entity,
            status="applied",
            server_version=result.record.server_version,
        )

    if item.entity == "customer":
        return _apply_customer(session, organization_id, item)

    if item.entity == "plan_stop":
        return _rejected(
            item,
            "PLAN_IMMUTABLE",
            "计划停靠不可通过离线同步改写；请在计划工作区生成新版本",
            conflict={
                "reason": "PLAN_IMMUTABLE",
                "server": None,
                "client": dict(item.payload),
            },
        )

    return _rejected(item, "UNKNOWN_ENTITY", f"未知实体 {item.entity!r}")


def _apply_customer(
    session: Session, organization_id: UUID | str, item: SyncItemRequest
) -> SyncItemOutcome:
    raw_id = item.payload.get("customer_id") or item.item_id
    try:
        customer_id = UUID(str(raw_id))
    except ValueError:
        return _rejected(item, "INVALID_CUSTOMER_ID", "customer_id 不是合法 UUID")

    customer = session.scalars(
        select(Customer).where(
            Customer.organization_id == organization_id, Customer.id == customer_id
        )
    ).one_or_none()
    if customer is None:
        return _rejected(item, "CUSTOMER_NOT_FOUND", "客户不存在或不属于当前组织")

    filled: list[str] = []
    kept_server: list[str] = []
    for key in _CUSTOMER_MERGE_FIELDS:
        if key not in item.payload:
            continue
        incoming = item.payload[key]
        if incoming is None or incoming == "":
            continue
        current = getattr(customer, key)
        if current is None or current == "":
            setattr(customer, key, incoming)
            filled.append(key)
        elif str(current) != str(incoming):
            # 字段级 merge 的"服务端非空优先"：双方都有值时保留服务端，客户端值不覆盖。
            kept_server.append(key)

    session.flush()
    conflict = None
    if kept_server:
        conflict = {
            "reason": "FIELD_MERGE_SERVER_PRIORITY",
            "server": {key: getattr(customer, key) for key in kept_server},
            "client": {key: item.payload[key] for key in kept_server},
        }
    return SyncItemOutcome(
        item_id=item.item_id,
        entity=item.entity,
        status="applied",
        server_version=None,
        conflict=conflict,
    )


def _rejected(
    item: SyncItemRequest, reason: str, message: str, *, conflict: dict[str, Any] | None = None
) -> SyncItemOutcome:
    return SyncItemOutcome(
        item_id=item.item_id,
        entity=item.entity,
        status="rejected",
        conflict=conflict
        or {
            "reason": reason,
            "server": None,
            "client": {"message": message, "payload": item.payload},
        },
    )


__all__ = ["SyncItemOutcome", "SyncOutcome", "push"]
