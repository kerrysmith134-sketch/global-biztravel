"""Ingest normalized airline / airport operational updates as immutable evidence."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.api.errors import Conflict, NotFound, Unprocessable
from app.api.schemas import FlightOperationalUpdatePayload
from app.models.flight_operations import FlightOperationalUpdate
from app.models.flights import TripFlightSegment
from app.models.trip import Trip


def ingest_operational_update(
    session: Session,
    organization_id: UUID | str,
    trip_id: UUID,
    provider: str,
    observed_at: datetime,
    valid_until: datetime | None,
    confidence: float,
    payload: FlightOperationalUpdatePayload,
    *,
    summary: str | None,
) -> FlightOperationalUpdate:
    """Persist one provider event; identical replays are safe, event-id collisions are rejected."""
    organization = UUID(str(organization_id))
    trip = session.scalars(
        select(Trip).where(
            Trip.organization_id == organization,
            Trip.id == trip_id,
        )
    ).one_or_none()
    if trip is None:
        raise NotFound("外部航班运营事件引用的出行不存在", details={"trip_id": str(trip_id)})

    segment = session.scalars(
        select(TripFlightSegment).where(
            TripFlightSegment.organization_id == organization,
            TripFlightSegment.trip_id == trip_id,
            TripFlightSegment.id == payload.flight_segment_id,
        )
    ).one_or_none()
    if segment is None:
        raise NotFound(
            "外部航班运营事件引用的机票段不属于当前组织与出行",
            details={"flight_segment_id": str(payload.flight_segment_id)},
        )

    identity = (
        payload.flight_designator == segment.flight_designator
        and payload.departure_airport_iata == segment.departure_airport_iata
        and payload.arrival_airport_iata == segment.arrival_airport_iata
    )
    if not identity:
        raise Unprocessable(
            "Provider 航班号或计划起讫机场与显式机票段不匹配",
            details={
                "flight_segment_id": str(segment.id),
                "expected": {
                    "flight_designator": segment.flight_designator,
                    "departure_airport_iata": segment.departure_airport_iata,
                    "arrival_airport_iata": segment.arrival_airport_iata,
                },
            },
        )

    normalized = {
        "organization_id": str(organization),
        "trip_id": str(trip_id),
        "flight_segment_id": str(segment.id),
        "provider": provider.strip().lower(),
        "provider_event_id": payload.provider_event_id,
        "update_type": payload.update_type,
        "flight_designator": payload.flight_designator,
        "departure_airport_iata": payload.departure_airport_iata,
        "arrival_airport_iata": payload.arrival_airport_iata,
        "estimated_departure_at": (
            payload.estimated_departure_at.isoformat()
            if payload.estimated_departure_at is not None
            else None
        ),
        "estimated_arrival_at": (
            payload.estimated_arrival_at.isoformat()
            if payload.estimated_arrival_at is not None
            else None
        ),
        "reported_arrival_airport_iata": payload.reported_arrival_airport_iata,
        "observed_at": observed_at.isoformat(),
        "valid_until": valid_until.isoformat() if valid_until is not None else None,
        "confidence": confidence,
        "summary": summary,
        "source_payload_sha256": payload.source_payload_sha256,
    }
    digest = hashlib.sha256(
        json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()

    statement = (
        pg_insert(FlightOperationalUpdate.__table__)
        .values(
            **normalized,
            content_sha256=digest,
        )
        .on_conflict_do_nothing(constraint="uq_flight_operational_updates_org_provider_event")
        .returning(FlightOperationalUpdate.__table__.c.id)
    )
    inserted_id = session.execute(statement).scalar_one_or_none()
    if inserted_id is not None:
        session.flush()
        return session.scalars(
            select(FlightOperationalUpdate).where(FlightOperationalUpdate.id == inserted_id)
        ).one()

    existing = session.scalars(
        select(FlightOperationalUpdate).where(
            FlightOperationalUpdate.organization_id == organization,
            FlightOperationalUpdate.provider == provider.strip().lower(),
            FlightOperationalUpdate.provider_event_id == payload.provider_event_id,
        )
    ).one_or_none()
    if existing is None:
        raise Conflict("Provider event 并发写入冲突，请使用同一事件 ID 重试")
    if existing.content_sha256 != digest:
        raise Conflict(
            "同一 Provider event ID 收到不同内容；已拒绝覆盖历史运营事实",
            details={
                "provider": provider,
                "provider_event_id": payload.provider_event_id,
                "existing_update_id": str(existing.id),
            },
        )
    return existing


__all__ = ["ingest_operational_update"]
