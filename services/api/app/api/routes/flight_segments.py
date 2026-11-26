"""Flight itinerary capture and user-confirmed PlanTravelLeg bindings."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status
from sqlalchemy.orm import Session

from app.api.deps import (
    ROLE_TRAVELER,
    ROLE_VIEWER,
    ContextDep,
    IdempotencyKeyDep,
    SessionDep,
    require_role,
)
from app.api.idempotent import IdempotentWrite
from app.api.schemas import (
    FlightSegmentCreate,
    FlightSegmentPlanBindingCreate,
    FlightSegmentPlanBindingRead,
    FlightSegmentRead,
)
from app.models.trip import Trip
from app.services import flight_segment_service, trip_service

router = APIRouter(tags=["flight segments"])

FLIGHT_SEGMENT_CREATE_ENDPOINT = "POST /v1/trips/{trip_id}/flight-segments"
FLIGHT_SEGMENT_LINK_ENDPOINT = "POST /v1/trips/{trip_id}/flight-segments/{flight_segment_id}:link"
FLIGHT_SEGMENT_UNLINK_ENDPOINT = "POST /v1/trips/{trip_id}/flight-segments/{flight_segment_id}:unlink"


def _segment_read(
    session: Session,
    organization_id,
    trip: Trip,
    segment,
) -> FlightSegmentRead:
    bindings = flight_segment_service.list_segment_bindings(
        session, organization_id, trip.id, segment.id
    )
    result = FlightSegmentRead.model_validate(segment)
    result.bindings = [
        FlightSegmentPlanBindingRead.model_validate(binding) for binding in bindings
    ]
    active = next((item for item in bindings if item.status == "ACTIVE"), None)
    if active is None:
        result.binding_state = "UNLINKED"
    elif trip.active_plan_version_id == active.plan_version_id:
        result.binding_state = "CURRENT_PLAN"
    else:
        result.binding_state = "STALE_PLAN_BINDING"
    return result


@router.post(
    "/trips/{trip_id}/flight-segments",
    response_model=FlightSegmentRead,
    status_code=status.HTTP_201_CREATED,
    summary="录入一段机票行程（不保存 PNR/票号）",
)
def create_flight_segment(
    trip_id: UUID,
    payload: FlightSegmentCreate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    trip = trip_service.require_trip(session, context.organization_id, trip_id)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=FLIGHT_SEGMENT_CREATE_ENDPOINT,
        key=idempotency_key,
        payload={"trip_id": str(trip_id), **payload.model_dump(mode="json")},
    )
    if (replay := guard.begin()) is not None:
        return replay
    segment = flight_segment_service.create_flight_segment(
        session,
        context.organization_id,
        trip_id,
        payload,
        actor_user_id=context.user_id,
    )
    body = _segment_read(session, context.organization_id, trip, segment)
    return guard.finish(status_code=status.HTTP_201_CREATED, body=body)


@router.get(
    "/trips/{trip_id}/flight-segments",
    response_model=list[FlightSegmentRead],
    summary="读取出行机票段与计划绑定历史",
)
def list_flight_segments(trip_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_VIEWER)
    trip = trip_service.require_trip(session, context.organization_id, trip_id)
    segments = flight_segment_service.list_flight_segments(
        session, context.organization_id, trip_id
    )
    return [
        _segment_read(session, context.organization_id, trip, segment)
        for segment in segments
    ]


@router.post(
    "/trips/{trip_id}/flight-segments/{flight_segment_id}:link",
    response_model=FlightSegmentRead,
    summary="人工确认航班段与当前 ACTIVE 计划 AIR 交通段的关联",
)
def link_flight_segment(
    trip_id: UUID,
    flight_segment_id: UUID,
    payload: FlightSegmentPlanBindingCreate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    trip = trip_service.require_trip(session, context.organization_id, trip_id)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=FLIGHT_SEGMENT_LINK_ENDPOINT,
        key=idempotency_key,
        payload={
            "trip_id": str(trip_id),
            "flight_segment_id": str(flight_segment_id),
            **payload.model_dump(mode="json"),
        },
    )
    if (replay := guard.begin()) is not None:
        return replay
    segment = flight_segment_service.link_flight_segment_to_active_plan_leg(
        session,
        context.organization_id,
        trip_id,
        flight_segment_id,
        payload.plan_travel_leg_id,
        actor_user_id=context.user_id,
    )
    body = _segment_read(session, context.organization_id, trip, segment)
    return guard.finish(status_code=status.HTTP_200_OK, body=body)


@router.post(
    "/trips/{trip_id}/flight-segments/{flight_segment_id}:unlink",
    response_model=FlightSegmentRead,
    summary="解除航班段与计划交通段的关联并保留审计历史",
)
def unlink_flight_segment(
    trip_id: UUID,
    flight_segment_id: UUID,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    trip = trip_service.require_trip(session, context.organization_id, trip_id)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=FLIGHT_SEGMENT_UNLINK_ENDPOINT,
        key=idempotency_key,
        payload={"trip_id": str(trip_id), "flight_segment_id": str(flight_segment_id)},
    )
    if (replay := guard.begin()) is not None:
        return replay
    segment = flight_segment_service.unlink_flight_segment(
        session,
        context.organization_id,
        trip_id,
        flight_segment_id,
        actor_user_id=context.user_id,
    )
    body = _segment_read(session, context.organization_id, trip, segment)
    return guard.finish(status_code=status.HTTP_200_OK, body=body)
