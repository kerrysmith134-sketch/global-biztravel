"""Flight itinerary segment lifecycle and explicit plan-leg binding use cases."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import Conflict, NotFound, Unprocessable
from app.api.schemas import FlightSegmentCreate
from app.models.flights import FlightSegmentPlanBinding, TripFlightSegment
from app.models.planning import PlanTravelLeg, PlanVersion
from app.models.trip import Trip


def create_flight_segment(
    session: Session,
    organization_id: UUID | str,
    trip_id: UUID,
    payload: FlightSegmentCreate,
    *,
    actor_user_id: UUID | str | None,
) -> TripFlightSegment:
    trip = session.scalars(
        select(Trip)
        .where(Trip.organization_id == organization_id, Trip.id == trip_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if trip is None:
        raise NotFound("出行不存在", details={"trip_id": str(trip_id)})
    if trip.status in ("COMPLETED", "CANCELLED"):
        raise Conflict("已结束或取消的出行不能新增航班段", details={"trip_status": trip.status})

    duplicate = session.scalars(
        select(TripFlightSegment).where(
            TripFlightSegment.organization_id == organization_id,
            TripFlightSegment.trip_id == trip.id,
            TripFlightSegment.flight_designator == payload.flight_designator,
            TripFlightSegment.scheduled_departure_at == payload.scheduled_departure_at,
        )
    ).one_or_none()
    if duplicate is not None:
        raise Conflict(
            "该航班段已存在",
            details={"flight_segment_id": str(duplicate.id)},
        )

    segment = TripFlightSegment(
        organization_id=organization_id,
        trip_id=trip.id,
        flight_designator=payload.flight_designator,
        departure_airport_iata=payload.departure_airport_iata,
        arrival_airport_iata=payload.arrival_airport_iata,
        scheduled_departure_at=payload.scheduled_departure_at,
        scheduled_arrival_at=payload.scheduled_arrival_at,
        departure_timezone=payload.departure_timezone,
        arrival_timezone=payload.arrival_timezone,
        booking_status=payload.booking_status,
        source=payload.source,
        tracking_provider=payload.tracking_provider,
        tracking_reference=payload.tracking_reference,
        created_by_user_id=actor_user_id,
    )
    session.add(segment)
    session.flush()
    return segment


def require_flight_segment(
    session: Session,
    organization_id: UUID | str,
    trip_id: UUID,
    flight_segment_id: UUID,
    *,
    for_update: bool = False,
) -> TripFlightSegment:
    statement = select(TripFlightSegment).where(
        TripFlightSegment.organization_id == organization_id,
        TripFlightSegment.trip_id == trip_id,
        TripFlightSegment.id == flight_segment_id,
    )
    if for_update:
        statement = statement.with_for_update()
    segment = session.scalars(statement).one_or_none()
    if segment is None:
        raise NotFound("航班段不存在", details={"flight_segment_id": str(flight_segment_id)})
    return segment


def list_flight_segments(
    session: Session, organization_id: UUID | str, trip_id: UUID
) -> list[TripFlightSegment]:
    return list(
        session.scalars(
            select(TripFlightSegment)
            .where(
                TripFlightSegment.organization_id == organization_id,
                TripFlightSegment.trip_id == trip_id,
            )
            .order_by(TripFlightSegment.scheduled_departure_at, TripFlightSegment.id)
        ).all()
    )


def list_segment_bindings(
    session: Session,
    organization_id: UUID | str,
    trip_id: UUID,
    flight_segment_id: UUID,
) -> list[FlightSegmentPlanBinding]:
    return list(
        session.scalars(
            select(FlightSegmentPlanBinding)
            .where(
                FlightSegmentPlanBinding.organization_id == organization_id,
                FlightSegmentPlanBinding.trip_id == trip_id,
                FlightSegmentPlanBinding.flight_segment_id == flight_segment_id,
            )
            .order_by(FlightSegmentPlanBinding.linked_at, FlightSegmentPlanBinding.id)
        ).all()
    )


def link_flight_segment_to_active_plan_leg(
    session: Session,
    organization_id: UUID | str,
    trip_id: UUID,
    flight_segment_id: UUID,
    plan_travel_leg_id: UUID,
    *,
    actor_user_id: UUID,
    now: datetime | None = None,
) -> TripFlightSegment:
    current = now or datetime.now(UTC)
    trip = session.scalars(
        select(Trip)
        .where(Trip.organization_id == organization_id, Trip.id == trip_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if trip is None:
        raise NotFound("出行不存在", details={"trip_id": str(trip_id)})
    if trip.status in ("COMPLETED", "CANCELLED"):
        raise Conflict("已结束或取消的出行不能关联航班段", details={"trip_status": trip.status})

    segment = require_flight_segment(
        session, organization_id, trip_id, flight_segment_id, for_update=True
    )
    if segment.booking_status == "CANCELLED":
        raise Conflict("已取消的航班段不能关联计划")

    if trip.active_plan_version_id is None:
        raise Conflict("该出行没有 ACTIVE 计划，无法关联交通段")
    plan = session.scalars(
        select(PlanVersion).where(
            PlanVersion.organization_id == organization_id,
            PlanVersion.trip_id == trip.id,
            PlanVersion.id == trip.active_plan_version_id,
            PlanVersion.status == "ACTIVE",
        )
    ).one_or_none()
    if plan is None:
        raise Conflict("出行的 ACTIVE 计划指针无效，请重新生成或激活计划")

    leg = session.scalars(
        select(PlanTravelLeg).where(
            PlanTravelLeg.organization_id == organization_id,
            PlanTravelLeg.plan_version_id == plan.id,
            PlanTravelLeg.id == plan_travel_leg_id,
        )
    ).one_or_none()
    if leg is None:
        raise NotFound(
            "交通段不属于当前 ACTIVE 计划",
            details={"plan_travel_leg_id": str(plan_travel_leg_id), "plan_version_id": str(plan.id)},
        )
    if leg.mode != "AIR":
        raise Unprocessable(
            "航班段只能关联 mode=AIR 的计划交通段",
            details={"plan_travel_leg_id": str(leg.id), "mode": leg.mode},
        )

    existing = session.scalars(
        select(FlightSegmentPlanBinding)
        .where(
            FlightSegmentPlanBinding.organization_id == organization_id,
            FlightSegmentPlanBinding.trip_id == trip.id,
            FlightSegmentPlanBinding.flight_segment_id == segment.id,
            FlightSegmentPlanBinding.status == "ACTIVE",
        )
        .with_for_update()
    ).one_or_none()
    if (
        existing is not None
        and existing.plan_version_id == plan.id
        and existing.plan_travel_leg_id == leg.id
    ):
        return segment

    if existing is not None:
        existing.status = "RELEASED"
        existing.released_at = current
        existing.released_by_user_id = actor_user_id
        existing.release_reason = "RELINKED"

    binding = FlightSegmentPlanBinding(
        organization_id=organization_id,
        trip_id=trip.id,
        flight_segment_id=segment.id,
        plan_version_id=plan.id,
        plan_travel_leg_id=leg.id,
        status="ACTIVE",
        linked_by_user_id=actor_user_id,
        linked_at=current,
    )
    session.add(binding)
    session.flush()
    return segment


def unlink_flight_segment(
    session: Session,
    organization_id: UUID | str,
    trip_id: UUID,
    flight_segment_id: UUID,
    *,
    actor_user_id: UUID,
    reason: str = "USER_UNLINKED",
    now: datetime | None = None,
) -> TripFlightSegment:
    current = now or datetime.now(UTC)
    segment = require_flight_segment(
        session, organization_id, trip_id, flight_segment_id, for_update=True
    )
    active = session.scalars(
        select(FlightSegmentPlanBinding)
        .where(
            FlightSegmentPlanBinding.organization_id == organization_id,
            FlightSegmentPlanBinding.trip_id == trip_id,
            FlightSegmentPlanBinding.flight_segment_id == segment.id,
            FlightSegmentPlanBinding.status == "ACTIVE",
        )
        .with_for_update()
    ).one_or_none()
    if active is None:
        return segment

    active.status = "RELEASED"
    active.released_at = current
    active.released_by_user_id = actor_user_id
    active.release_reason = reason
    session.flush()
    return segment


__all__ = [
    "create_flight_segment",
    "link_flight_segment_to_active_plan_leg",
    "list_flight_segments",
    "list_segment_bindings",
    "require_flight_segment",
    "unlink_flight_segment",
]
