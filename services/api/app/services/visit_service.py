"""拜访（Visit）服务：行程内的一个待拜访点。

拜访是"生成计划"的**输入**：改一个拜访的时间窗或停留时长，最优顺序就可能变。因此本模块
所有写操作都调用 :func:`trip_service.touch_trip` 递增 Trip 版本令牌，让既有计划立刻失效。

两个刻意的校验：

- ``place_id`` 必须属于同一组织的已有地点——不允许"顺手建个地点"混进拜访创建，
  否则坐标来源会变得不可追溯（地理编码是独立决定，应该留下独立记录）。
- ``time_window`` 允许单边缺失：只有最早可到或只有最晚必须离开都是常见约束，
  求解器按单边窗口处理。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import NotFound, Unprocessable
from app.api.schemas import VisitCreate, VisitUpdate
from app.models.geo import Customer, Place
from app.models.trip import Trip, Visit
from app.services import trip_service


def get_visit(session: Session, organization_id: UUID | str, visit_id: UUID) -> Visit | None:
    return session.scalars(
        select(Visit).where(Visit.organization_id == organization_id, Visit.id == visit_id)
    ).one_or_none()


def require_visit(session: Session, organization_id: UUID | str, visit_id: UUID) -> Visit:
    visit = get_visit(session, organization_id, visit_id)
    if visit is None:
        raise NotFound("拜访不存在", details={"visit_id": str(visit_id)})
    return visit


def list_visits(
    session: Session,
    organization_id: UUID | str,
    trip_id: UUID,
    *,
    status: str | None = None,
) -> list[Visit]:
    stmt = select(Visit).where(Visit.organization_id == organization_id, Visit.trip_id == trip_id)
    if status:
        stmt = stmt.where(Visit.status == status.upper())
    return list(session.scalars(stmt.order_by(Visit.created_at)).all())


def _lock_trip_for_visit_write(
    session: Session, organization_id: UUID | str, trip_id: UUID
) -> Trip:
    """Acquire the Trip row before any Visit write to keep a consistent lock order."""
    trip = session.scalars(
        select(Trip)
        .where(Trip.organization_id == organization_id, Trip.id == trip_id)
        .with_for_update()
    ).one_or_none()
    if trip is None:
        raise NotFound("出行不存在", details={"trip_id": str(trip_id)})
    return trip


def _lock_visit_for_write(
    session: Session, organization_id: UUID | str, trip_id: UUID, visit_id: UUID
) -> Visit:
    visit = session.scalars(
        select(Visit)
        .where(
            Visit.organization_id == organization_id,
            Visit.trip_id == trip_id,
            Visit.id == visit_id,
        )
        .with_for_update()
    ).one_or_none()
    if visit is None:
        raise NotFound("拜访不存在", details={"visit_id": str(visit_id)})
    return visit


def create_visit(
    session: Session,
    organization_id: UUID | str,
    trip_id: UUID,
    payload: VisitCreate,
) -> Visit:
    trip = _lock_trip_for_visit_write(session, organization_id, trip_id)
    _assert_place(session, organization_id, payload.place_id)
    if payload.customer_id is not None:
        _assert_customer(session, organization_id, payload.customer_id)

    visit = Visit(
        organization_id=organization_id,
        trip_id=trip.id,
        customer_id=payload.customer_id,
        place_id=payload.place_id,
        title=payload.title,
        objective=payload.objective,
        service_minutes=payload.service_minutes,
        time_window_start=payload.time_window_start,
        time_window_end=payload.time_window_end,
        priority=payload.priority,
        business_value=payload.business_value,
        movement_mode=payload.movement_mode,
        is_immovable=payload.is_immovable,
        status="PLANNED",
    )
    session.add(visit)
    session.flush()
    trip_service.touch_trip(session, trip.id, organization_id)
    return visit


def update_visit(
    session: Session,
    organization_id: UUID | str,
    visit_id: UUID,
    payload: VisitUpdate,
) -> Visit:
    # Lock order is Trip → Visit for every writer, matching plan-change and activation flows.
    visit_ref = require_visit(session, organization_id, visit_id)
    _lock_trip_for_visit_write(session, organization_id, visit_ref.trip_id)
    visit = _lock_visit_for_write(session, organization_id, visit_ref.trip_id, visit_id)
    fields = payload.model_dump(exclude_unset=True)
    if "customer_id" in fields and fields["customer_id"] is not None:
        _assert_customer(session, organization_id, fields["customer_id"])

    window_start = fields.get("time_window_start", visit.time_window_start)
    window_end = fields.get("time_window_end", visit.time_window_end)
    if window_start is not None and window_end is not None and window_end <= window_start:
        raise Unprocessable("time_window_end 必须晚于 time_window_start")

    for key, value in fields.items():
        setattr(visit, key, value)
    session.flush()
    trip_service.touch_trip(session, visit.trip_id, organization_id)
    return visit


def delete_visit(session: Session, organization_id: UUID | str, visit_id: UUID) -> Trip:
    visit_ref = require_visit(session, organization_id, visit_id)
    trip_id = visit_ref.trip_id
    trip = _lock_trip_for_visit_write(session, organization_id, trip_id)
    visit = _lock_visit_for_write(session, organization_id, trip_id, visit_id)
    session.delete(visit)
    session.flush()
    trip_service.touch_trip(session, trip_id, organization_id)
    return trip


def create_visit_raw(
    session: Session,
    organization_id: UUID | str,
    *,
    trip_id: UUID,
    place_id: UUID,
    customer_id: UUID | None = None,
    title: str | None = None,
    service_minutes: int = 60,
    priority: int = 2,
    business_value: Decimal | None = None,
    is_immovable: bool = False,
    movement_mode: str = "GROUND",
    time_window_start: datetime | None = None,
    time_window_end: datetime | None = None,
) -> Visit:
    """导入确认阶段使用：已在上游完成校验，只负责落库。**不**单独递增版本，
    由导入事务在末尾统一 ``touch_trip`` 一次（一次导入只让计划失效一次，而不是每行一次）。
    """
    visit = Visit(
        organization_id=organization_id,
        trip_id=trip_id,
        customer_id=customer_id,
        place_id=place_id,
        title=title,
        service_minutes=service_minutes,
        time_window_start=time_window_start,
        time_window_end=time_window_end,
        priority=priority,
        business_value=business_value if business_value is not None else Decimal("0"),
        movement_mode=movement_mode,
        is_immovable=is_immovable,
        status="PLANNED",
    )
    session.add(visit)
    session.flush()
    return visit


def _assert_place(session: Session, organization_id: UUID | str, place_id: UUID) -> Place:
    place = session.scalars(
        select(Place).where(Place.organization_id == organization_id, Place.id == place_id)
    ).one_or_none()
    if place is None:
        raise Unprocessable("地点不存在或不属于当前组织", details={"place_id": str(place_id)})
    return place


def _assert_customer(session: Session, organization_id: UUID | str, customer_id: UUID) -> Customer:
    customer = session.scalars(
        select(Customer).where(
            Customer.organization_id == organization_id, Customer.id == customer_id
        )
    ).one_or_none()
    if customer is None:
        # 客户是弱引用（SET NULL），给错 id 说明客户端状态过期，422 比 404 更准确。
        raise Unprocessable("客户不存在或不属于当前组织", details={"customer_id": str(customer_id)})
    return customer