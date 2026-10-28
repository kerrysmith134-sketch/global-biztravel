"""出行（Trip）服务：创建、更新、检索，以及"内容被改就换版本号"的并发令牌维护。

**为什么每次改动都递增 ``trips.version``**：AI 生成计划要花几秒到几十秒。期间用户可能
增删了一个拜访，或者改了时间窗。如果激活计划时不检测这一点，就会把一份基于旧输入算出的
行程标成"当前生效"，而它已经与输入不符——这正是设计文档 I1（计划必须可追溯到输入）要防的。

因此 ``version`` 是**乐观并发令牌**：任何影响规划输入的写操作都会让它 +1，激活计划时用
``If-Match`` 比对；不符即 409 ``PLAN_VERSION_CONFLICT``，让客户端拿到最新计划后重试。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.errors import NotFound, PreconditionFailed, Unprocessable
from app.api.schemas import TripCreate, TripUpdate
from app.models.geo import Place
from app.models.planning import PlanVersion
from app.models.trip import Trip, Visit

#: 允许客户端在更新时触发的状态迁移。刻意排除 ACTIVE：生效状态只能由"激活计划"进入，
#: 否则会出现"标记为进行中但没有任何生效计划"的空洞状态。
_UPDATABLE_STATUSES = ("DRAFT", "PLANNING", "READY", "COMPLETED", "CANCELLED")


def create_trip(
    session: Session,
    organization_id: UUID | str,
    payload: TripCreate,
    *,
    owner_user_id: UUID | None = None,
) -> Trip:
    if payload.origin_place_id is not None:
        origin = session.scalars(
            select(Place).where(
                Place.organization_id == organization_id, Place.id == payload.origin_place_id
            )
        ).one_or_none()
        if origin is None:
            raise NotFound(
                "出发地不存在", details={"origin_place_id": str(payload.origin_place_id)}
            )
    trip = Trip(
        organization_id=organization_id,
        name=payload.name.strip(),
        purpose=payload.purpose,
        owner_user_id=owner_user_id,
        origin_place_id=payload.origin_place_id,
        home_timezone=payload.home_timezone or "Asia/Shanghai",
        currency=(payload.currency or "CNY").upper(),
        window_start=payload.window_start,
        window_end=payload.window_end,
        countries=[c.upper() for c in payload.countries],
        budget_amount=payload.budget_amount,
        status="DRAFT",
        version=1,
    )
    session.add(trip)
    session.flush()
    return trip


def get_trip(session: Session, organization_id: UUID | str, trip_id: UUID) -> Trip | None:
    return session.scalars(
        select(Trip).where(Trip.organization_id == organization_id, Trip.id == trip_id)
    ).one_or_none()


def require_trip(session: Session, organization_id: UUID | str, trip_id: UUID) -> Trip:
    trip = get_trip(session, organization_id, trip_id)
    if trip is None:
        raise NotFound("出行不存在", details={"trip_id": str(trip_id)})
    return trip


def visit_count(session: Session, trip_id: UUID) -> int:
    return int(
        session.scalar(select(func.count()).select_from(Visit).where(Visit.trip_id == trip_id)) or 0
    )


def visit_counts(session: Session, trip_ids: list[UUID]) -> dict[UUID, int]:
    """批量取计数，避免列表接口出现 N+1 查询。"""
    if not trip_ids:
        return {}
    rows = session.execute(
        select(Visit.trip_id, func.count()).where(Visit.trip_id.in_(trip_ids)).group_by(Visit.trip_id)
    ).all()
    return {row[0]: int(row[1]) for row in rows}


def customer_counts(session: Session, trip_ids: list[UUID]) -> dict[UUID, int]:
    """每个出行的去重客户数（客户是弱引用，空值不计）。"""
    if not trip_ids:
        return {}
    rows = session.execute(
        select(Visit.trip_id, func.count(func.distinct(Visit.customer_id)))
        .where(Visit.trip_id.in_(trip_ids), Visit.customer_id.is_not(None))
        .group_by(Visit.trip_id)
    ).all()
    return {row[0]: int(row[1]) for row in rows}


def plan_version_counts(session: Session, trip_ids: list[UUID]) -> dict[UUID, int]:
    if not trip_ids:
        return {}
    rows = session.execute(
        select(PlanVersion.trip_id, func.count())
        .where(PlanVersion.trip_id.in_(trip_ids))
        .group_by(PlanVersion.trip_id)
    ).all()
    return {row[0]: int(row[1]) for row in rows}


def list_trips(
    session: Session,
    organization_id: UUID | str,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Trip]:
    stmt = select(Trip).where(Trip.organization_id == organization_id)
    if status:
        stmt = stmt.where(Trip.status == status.upper())
    stmt = stmt.order_by(Trip.window_start.desc()).limit(limit).offset(offset)
    return list(session.scalars(stmt).all())


def update_trip(
    session: Session,
    organization_id: UUID | str,
    trip_id: UUID,
    payload: TripUpdate,
    *,
    expected_version: int | None = None,
) -> Trip:
    trip = require_trip(session, organization_id, trip_id)
    if expected_version is not None and trip.version != expected_version:
        raise PreconditionFailed(
            "出行已被修改，请重新获取",
            details={"expected_version": expected_version, "current_version": trip.version},
        )

    fields = payload.model_dump(exclude_unset=True)
    window_start = fields.get("window_start", trip.window_start)
    window_end = fields.get("window_end", trip.window_end)
    if window_end <= window_start:
        # 数据库也有 ck_trips_window_order，这里提前给出可读错误而不是 IntegrityError。
        raise Unprocessable("window_end 必须晚于 window_start")

    if "status" in fields and fields["status"] not in _UPDATABLE_STATUSES:
        raise Unprocessable(
            "该状态不能由客户端直接设置",
            details={"status": fields["status"], "allowed": list(_UPDATABLE_STATUSES)},
        )

    for key, value in fields.items():
        if key == "countries" and value is not None:
            value = [c.upper() for c in value]
        if key == "currency" and value is not None:
            value = value.upper()
        setattr(trip, key, value)

    _touch(session, trip.id, organization_id)
    session.refresh(trip)
    return trip


def _touch(session: Session, trip_id: UUID, organization_id: UUID | str | None = None) -> None:
    """原子递增并发令牌。用 SQL 表达式而非读-改-写，避免并发下丢更新。

    ``organization_id`` 是可选的纵深防御条件——RLS 已经保证跨租户写不进去，但把它写进
    WHERE 能让审计日志里的语句自解释，也避免将来有人误用 owner 连接时越界。
    """
    stmt = update(Trip).where(Trip.id == trip_id)
    if organization_id is not None:
        stmt = stmt.where(Trip.organization_id == organization_id)
    session.execute(stmt.values(version=Trip.version + 1, updated_at=datetime.now(UTC)))
    session.flush()


def touch_trip(session: Session, trip_id: UUID, organization_id: UUID | str | None = None) -> None:
    """供拜访变更调用：让已生成的计划立刻失效（版本令牌变化）。"""
    _touch(session, trip_id, organization_id)