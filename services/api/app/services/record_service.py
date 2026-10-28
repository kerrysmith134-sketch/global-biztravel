"""现场拜访记录服务：LWW 冲突判定、幂等重放、拜访状态推进。

设计文档 v2.0 第 10.5 章：

| 实体 | 冲突策略 |
|---|---|
| 拜访记录 | ``last-writer-wins``（按 ``client_updated_at``）+ 保留冲突副本 |
| 计划停靠 | ``reject-and-conflict-record``（计划不可变） |
| 客户资料 | 字段级 ``merge``（服务端非空优先） |

本模块只负责第一行；另外两行在 :mod:`app.services.sync_service` 里按同一份矩阵执行。

三条不变量：

1. **幂等优先于冲突**。同一 ``client_record_id`` 再次提交，直接返回首次结果——哪怕它的
   ``client_updated_at`` 已经落后于后来者。设备重试不是"又一次写字"，而是"同一件事的重复送达"。
2. **LWW 用客户端业务时间**，不用服务端 ``created_at``。离线补传的笔记可能比在线更新晚到，
   但业务上它发生在更早知道的时间点；用到达时间覆盖会把用户的记录顺序彻底打乱。
3. **落败的写入也入库**（``applied=false``）。覆盖是生效值的语义，不是删除证据。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.errors import Unprocessable
from app.api.schemas import VisitRecordCreate
from app.models.records import VisitRecord, record_snapshot
from app.models.trip import Visit
from app.services import visit_service

#: 拜访结果 → 拜访状态。改期意味着"还需要重新排"，因此回到 PLANNED 而不是保留 SCHEDULED。
_OUTCOME_TO_STATUS = {
    "MET": "DONE",
    "NOT_MET": "MISSED",
    "RESCHEDULED": "PLANNED",
    "CANCELLED": "CANCELLED",
}


@dataclass
class RecordOutcome:
    """一次提交的结果。``conflict`` 非空表示这次写入落败、**没有**改变拜访当前值。"""

    record: VisitRecord
    server_record: VisitRecord | None = None
    conflict: dict[str, Any] | None = None


def _aware(value: datetime) -> datetime:
    """把 naive 时间按 UTC 解释。

    移动端偶尔会发出没有时区的时间；直接和 ``timestamptz`` 比较会在驱动层报错。这里统一
    按 UTC 解释而不是按服务器本地时区——后者会让同一个请求在不同部署机器上得到不同判定。
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _latest_applied(session: Session, organization_id: UUID | str, visit_id: UUID) -> VisitRecord | None:
    return session.scalars(
        select(VisitRecord)
        .where(
            VisitRecord.organization_id == organization_id,
            VisitRecord.visit_id == visit_id,
            VisitRecord.applied.is_(True),
        )
        .order_by(VisitRecord.client_updated_at.desc(), VisitRecord.created_at.desc())
        .limit(1)
    ).one_or_none()


def _find_by_client_id(
    session: Session, organization_id: UUID | str, client_record_id: str
) -> VisitRecord | None:
    return session.scalars(
        select(VisitRecord).where(
            VisitRecord.organization_id == organization_id,
            VisitRecord.client_record_id == client_record_id,
        )
    ).one_or_none()


def _client_snapshot(payload: VisitRecordCreate) -> dict[str, Any]:
    return {
        "client_record_id": payload.client_record_id,
        "outcome": payload.outcome,
        "notes": payload.notes,
        "client_updated_at": _aware(payload.client_updated_at).isoformat(),
        "photos": list(payload.photos),
        "follow_up_tasks": [task.model_dump() for task in payload.follow_up_tasks],
    }


def _conflict_for(session: Session, organization_id: UUID | str, visit_id: UUID, client: dict) -> dict:
    latest = _latest_applied(session, organization_id, visit_id)
    return {
        "reason": "LAST_WRITER_WINS",
        "server": record_snapshot(latest) if latest is not None else None,
        "client": client,
    }


def submit_record(
    session: Session,
    organization_id: UUID | str,
    visit_id: UUID | str,
    payload: VisitRecordCreate,
    *,
    actor_user_id: UUID | str | None = None,
    now: datetime | None = None,
) -> RecordOutcome:
    """提交一条现场记录。调用方决定事务提交点，本函数只 ``flush``。"""
    visit = visit_service.require_visit(session, organization_id, UUID(str(visit_id)))

    existing = _find_by_client_id(session, organization_id, payload.client_record_id)
    if existing is not None:
        # 幂等重放：同一条记录原样返回。若它当初就是冲突副本，把冲突一并带回去，
        # 客户端才能继续把该项留在"需人工处理"里。
        conflict = None
        if not existing.applied:
            conflict = _conflict_for(
                session, organization_id, visit.id, _client_snapshot(payload)
            )
        return RecordOutcome(record=existing, conflict=conflict)

    incoming_at = _aware(payload.client_updated_at)
    latest = _latest_applied(session, organization_id, visit.id)

    if latest is not None and latest.client_updated_at >= incoming_at:
        # 服务端已有同等或更新的一条：本次落败，留冲突副本。
        loser = VisitRecord(
            organization_id=str(organization_id),
            visit_id=visit.id,
            client_record_id=payload.client_record_id,
            outcome=payload.outcome,
            notes=payload.notes,
            photos=list(payload.photos),
            follow_up_tasks=[task.model_dump() for task in payload.follow_up_tasks],
            client_updated_at=incoming_at,
            server_version=0,
            applied=False,
            conflict_reason="LAST_WRITER_WINS",
            created_by_user_id=None if actor_user_id is None else str(actor_user_id),
        )
        session.add(loser)
        session.flush()
        return RecordOutcome(
            record=loser,
            server_record=latest,
            conflict={
                "reason": "LAST_WRITER_WINS",
                "server": record_snapshot(latest),
                "client": _client_snapshot(payload),
            },
        )

    next_version = int(
        session.scalar(
            select(func.coalesce(func.max(VisitRecord.server_version), 0)).where(
                VisitRecord.organization_id == organization_id,
                VisitRecord.visit_id == visit.id,
                VisitRecord.applied.is_(True),
            )
        )
        or 0
    ) + 1

    record = VisitRecord(
        organization_id=str(organization_id),
        visit_id=visit.id,
        client_record_id=payload.client_record_id,
        outcome=payload.outcome,
        notes=payload.notes,
        photos=list(payload.photos),
        follow_up_tasks=[task.model_dump() for task in payload.follow_up_tasks],
        client_updated_at=incoming_at,
        server_version=next_version,
        applied=True,
        created_by_user_id=None if actor_user_id is None else str(actor_user_id),
    )
    session.add(record)

    # 拜访当前值随生效记录推进；不递增 Trip 版本——现场结果不是"规划输入变化"，
    # 不让它把已激活的计划判成基于旧输入。
    visit.status = _OUTCOME_TO_STATUS[payload.outcome]
    visit.client_updated_at = incoming_at
    session.flush()
    return RecordOutcome(record=record)


def list_records(
    session: Session, organization_id: UUID | str, visit_id: UUID | str
) -> list[VisitRecord]:
    return list(
        session.scalars(
            select(VisitRecord)
            .where(
                VisitRecord.organization_id == organization_id,
                VisitRecord.visit_id == str(visit_id),
            )
            .order_by(VisitRecord.created_at, VisitRecord.id)
        )
    )


def ensure_visit_exists(session: Session, organization_id: UUID | str, visit_id: UUID | str) -> Visit:
    return visit_service.require_visit(session, organization_id, UUID(str(visit_id)))


def parse_payload(organization_id: UUID | str, item_id: str, payload: dict[str, Any]) -> VisitRecordCreate:
    """把同步队列里的 ``payload`` 转成记录请求。

    离线队列的 item id 与 ``client_record_id`` 同源；payload 里没有显式 id 时用 item id 兜底。
    ``visit_id`` 必须由客户端放进 payload——没有它就无法判断这条笔记属于哪次拜访，
    宁可明确拒绝，也不要猜。
    """
    data = dict(payload)
    data.setdefault("client_record_id", item_id)
    missing = [key for key in ("visit_id", "outcome", "client_updated_at") if not data.get(key)]
    if missing:
        raise Unprocessable(
            "同步项缺少必要字段", details={"missing": missing, "item_id": item_id}
        )
    return VisitRecordCreate.model_validate(data)


__all__ = [
    "RecordOutcome",
    "ensure_visit_exists",
    "list_records",
    "parse_payload",
    "submit_record",
]
