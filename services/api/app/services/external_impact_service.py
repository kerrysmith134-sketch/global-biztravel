"""Deterministic external-signal impact assessment against an active trip plan.

Weather is potentially relevant only when exact Place identity and a bounded signal
validity interval both overlap a persisted PlanStop. Connected travel legs are review
context, not proof of disruption. Flight telemetry is never inferred from callsign alone: only an explicitly identified booking segment
with a provider/reference match and a current PlanVersion binding can resolve to an AIR leg.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.flights import FlightSegmentPlanBinding, TripFlightSegment
from app.models.integrations import ExternalSignal
from app.models.planning import PlanStop, PlanTravelLeg, PlanVersion
from app.models.trip import Trip, Visit


def _active_plan(
    session: Session, organization_id: UUID, trip: Trip
) -> PlanVersion | None:
    if trip.active_plan_version_id is None:
        return None
    return session.scalars(
        select(PlanVersion).where(
            PlanVersion.organization_id == organization_id,
            PlanVersion.trip_id == trip.id,
            PlanVersion.id == trip.active_plan_version_id,
            PlanVersion.status == "ACTIVE",
        )
    ).one_or_none()


def assess_weather_signal_impact(
    session: Session,
    organization_id: UUID | str,
    trip: Trip,
    signal: ExternalSignal,
) -> dict:
    """Return a reviewable weather impact snapshot; never mutate Trip or plan inputs."""
    organization = UUID(str(organization_id))
    plan = _active_plan(session, organization, trip)

    base = {
        "signal_id": str(signal.id),
        "signal_type": signal.signal_type,
        "assessment_method": "EXACT_PLACE_ID_AND_TEMPORAL_OVERLAP",
        "plan_version_id": str(plan.id) if plan is not None else None,
        "plan_version_number": plan.version_number if plan is not None else None,
        "place_id": None,
        "matched_stop_count": 0,
        "affected_stop_count": 0,
        "matched_visit_ids": [],
        "affected_visit_ids": [],
        "matched_stops": [],
        "related_travel_leg_count": 0,
        "related_travel_legs": [],
    }
    if plan is None:
        return {
            **base,
            "status": "NO_ACTIVE_PLAN",
            "explanation": "当前出行没有可验证的 ACTIVE 计划，无法评估天气信号与拜访时刻表的重叠。",
        }

    raw_place_id = (signal.payload or {}).get("place_id")
    if not raw_place_id:
        return {
            **base,
            "status": "PLACE_REFERENCE_MISSING",
            "explanation": "天气信号未提供规范 place_id，无法精确映射到计划停靠点。",
        }
    try:
        place_id = UUID(str(raw_place_id))
    except (TypeError, ValueError):
        return {
            **base,
            "place_id": None,
            "status": "PLACE_REFERENCE_INVALID",
            "explanation": "天气信号的 place_id 不是合法 UUID，未尝试按地点名称或坐标猜测匹配。",
        }

    signal_start = max(signal.observed_at, signal.valid_from)
    signal_end = signal.valid_until
    if signal_end is None or signal_end <= signal_start:
        return {
            **base,
            "place_id": str(place_id),
            "status": "SIGNAL_WINDOW_UNBOUNDED",
            "explanation": "天气信号缺少有效结束时间或时间窗口无效；系统不会把当前天气外推到未来行程。",
        }

    rows = session.execute(
        select(PlanStop, Visit.place_id)
        .join(
            Visit,
            (Visit.id == PlanStop.visit_id)
            & (Visit.organization_id == PlanStop.organization_id),
        )
        .where(
            PlanStop.organization_id == organization,
            PlanStop.plan_version_id == plan.id,
            Visit.trip_id == trip.id,
            Visit.organization_id == organization,
        )
        .order_by(PlanStop.day_index, PlanStop.sequence, PlanStop.id)
    ).all()
    plan_stops_by_id = {stop.id: stop for stop, _ in rows}
    place_matches = [stop for stop, stop_place_id in rows if stop_place_id == place_id]
    if not place_matches:
        return {
            **base,
            "place_id": str(place_id),
            "status": "NO_PLANNED_STOP_AT_SIGNAL_PLACE",
            "explanation": (
                "当前生效计划没有停靠于该 place_id 的拜访；仅凭城市、名称或附近坐标相似"
                "不能推断该天气信号影响本次行程。"
            ),
        }

    matched_stops: list[dict] = []
    affected_visit_ids: list[str] = []
    affected_stop_ids: list[UUID] = []
    for stop in place_matches:
        overlaps = stop.arrive_at < signal_end and stop.depart_at > signal_start
        matched_stops.append(
            {
                "plan_stop_id": str(stop.id),
                "visit_id": str(stop.visit_id),
                "day_index": stop.day_index,
                "sequence": stop.sequence,
                "arrive_at": stop.arrive_at.isoformat(),
                "depart_at": stop.depart_at.isoformat(),
                "time_window_overlap": overlaps,
            }
        )
        if overlaps:
            affected_visit_ids.append(str(stop.visit_id))
            affected_stop_ids.append(stop.id)

    related_travel_legs: list[dict] = []
    if affected_stop_ids:
        legs = session.scalars(
            select(PlanTravelLeg)
            .where(
                PlanTravelLeg.organization_id == organization,
                PlanTravelLeg.plan_version_id == plan.id,
                or_(
                    PlanTravelLeg.from_stop_id.in_(affected_stop_ids),
                    PlanTravelLeg.to_stop_id.in_(affected_stop_ids),
                ),
            )
            .order_by(PlanTravelLeg.day_index, PlanTravelLeg.created_at, PlanTravelLeg.id)
        ).all()
        affected_stop_id_set = set(affected_stop_ids)
        for leg in legs:
            from_stop = (
                plan_stops_by_id.get(leg.from_stop_id)
                if leg.from_stop_id is not None
                else None
            )
            to_stop = plan_stops_by_id.get(leg.to_stop_id)
            # The database uses single-column FKs; reject a malformed cross-version endpoint
            # rather than using another plan's schedule as impact evidence.
            if to_stop is None or (leg.from_stop_id is not None and from_stop is None):
                continue

            from_affected = leg.from_stop_id in affected_stop_id_set
            to_affected = leg.to_stop_id in affected_stop_id_set
            if from_affected and to_affected:
                relation = "BETWEEN_AFFECTED_STOPS"
            elif to_affected:
                relation = "INBOUND_TO_AFFECTED_STOP"
            else:
                relation = "OUTBOUND_FROM_AFFECTED_STOP"

            gap_minutes: int | None = None
            slack_minutes: int | None = None
            if from_stop is None:
                schedule_assessment = "ORIGIN_TIMING_UNKNOWN"
            else:
                gap_minutes = round(
                    (to_stop.arrive_at - from_stop.depart_at).total_seconds() / 60
                )
                slack_minutes = gap_minutes - leg.travel_minutes
                schedule_assessment = (
                    "TRAVEL_EXCEEDS_AVAILABLE_GAP"
                    if slack_minutes < 0
                    else "ESTIMATED_BUFFER_AVAILABLE"
                )

            related_travel_legs.append(
                {
                    "plan_travel_leg_id": str(leg.id),
                    "from_stop_id": (
                        str(leg.from_stop_id) if leg.from_stop_id is not None else None
                    ),
                    "to_stop_id": str(leg.to_stop_id),
                    "day_index": leg.day_index,
                    "mode": leg.mode,
                    "relation": relation,
                    "travel_minutes": leg.travel_minutes,
                    "source": leg.source,
                    "stale": leg.stale,
                    "estimated_schedule_gap_minutes": gap_minutes,
                    "estimated_schedule_slack_minutes": slack_minutes,
                    "schedule_assessment": schedule_assessment,
                }
            )

    impact_status = "PLACE_AND_TIME_OVERLAP" if affected_visit_ids else "PLACE_MATCH_NO_TIME_OVERLAP"
    if affected_visit_ids:
        explanation = (
            f"发现 {len(affected_visit_ids)} 个已排拜访同时满足地点精确匹配与天气信号有效时间窗重叠。"
            "这表示潜在暴露，不代表拜访必然无法执行；需要人工结合现场情况判断。"
        )
    else:
        explanation = (
            f"发现 {len(place_matches)} 个地点精确匹配的已排拜访，但均不与天气信号有效时间窗重叠；"
            "不应据此自动调整计划。"
        )
    if related_travel_legs:
        explanation += (
            f"另有 {len(related_travel_legs)} 段与受影响停靠点直接相连的交通段需要复核；"
            "该关联不代表交通段已受影响，也不会据此自动传播延误或改写时刻表。"
        )

    return {
        **base,
        "place_id": str(place_id),
        "status": impact_status,
        "matched_stop_count": len(place_matches),
        "affected_stop_count": len(affected_visit_ids),
        "matched_visit_ids": [str(stop.visit_id) for stop in place_matches],
        "affected_visit_ids": affected_visit_ids,
        "matched_stops": matched_stops[:50],
        "related_travel_leg_count": len(related_travel_legs),
        "related_travel_legs": related_travel_legs[:100],
        "explanation": explanation,
        "signal_window": {
            "starts_at": signal_start.isoformat(),
            "ends_at": signal_end.isoformat(),
        },
    }


def _tracking_reference_matches(provider: str, expected: str, observed: str) -> bool:
    provider_key = provider.strip().casefold()
    if provider_key == "opensky":
        return expected.strip().upper() == observed.strip().upper()
    return expected.strip() == observed.strip()


def assess_flight_signal_impact(
    session: Session,
    organization_id: UUID | str,
    trip: Trip,
    signal: ExternalSignal,
) -> dict:
    """Link telemetry only through explicit segment id + provider reference + current plan binding."""
    organization = UUID(str(organization_id))
    plan = _active_plan(session, organization, trip)
    payload = signal.payload or {}
    callsign = str(payload.get("callsign") or "").strip() or None
    icao24 = str(payload.get("icao24") or "").strip().lower() or None
    raw_segment_id = payload.get("flight_segment_id")
    base = {
        "signal_id": str(signal.id),
        "signal_type": signal.signal_type,
        "assessment_method": (
            "EXPLICIT_SEGMENT_ID_PROVIDER_REFERENCE_AND_CURRENT_PLAN_BINDING"
            if raw_segment_id
            else "EXPLICIT_BOOKING_LINK_REQUIRED"
        ),
        "plan_version_id": str(plan.id) if plan is not None else None,
        "plan_version_number": plan.version_number if plan is not None else None,
        "callsign": callsign.upper() if callsign and signal.provider.casefold() == "opensky" else callsign,
        "icao24": icao24,
        "flight_segment_id": None,
        "flight_designator": None,
        "departure_airport_iata": None,
        "arrival_airport_iata": None,
        "scheduled_departure_at": None,
        "scheduled_arrival_at": None,
        "candidate_air_leg_count": 0,
        "linked_air_leg_count": 0,
        "linked_air_leg_ids": [],
        "unlinked_air_legs": [],
        "binding_id": None,
        "telemetry_snapshot": {
            "last_contact": payload.get("last_contact"),
            "on_ground": payload.get("on_ground"),
            "latitude": payload.get("latitude"),
            "longitude": payload.get("longitude"),
            "altitude_m": payload.get("baro_altitude_m"),
            "velocity_ms": payload.get("velocity_ms"),
        },
        "operational_delay_assessment": "NOT_DERIVABLE_FROM_AIRCRAFT_TELEMETRY",
    }

    segment: TripFlightSegment | None = None
    if raw_segment_id is not None:
        try:
            segment_id = UUID(str(raw_segment_id))
        except (TypeError, ValueError):
            return {
                **base,
                "status": "FLIGHT_SEGMENT_REFERENCE_INVALID",
                "explanation": "信号中的 flight_segment_id 不是有效 UUID；未尝试按航班号猜测机票。",
            }
        segment = session.scalars(
            select(TripFlightSegment).where(
                TripFlightSegment.organization_id == organization,
                TripFlightSegment.trip_id == trip.id,
                TripFlightSegment.id == segment_id,
            )
        ).one_or_none()
        if segment is None:
            return {
                **base,
                "flight_segment_id": str(segment_id),
                "status": "FLIGHT_SEGMENT_NOT_FOUND",
                "explanation": "信号引用的机票段不属于当前组织与出行，未建立任何计划关联。",
            }
        segment_fields = {
            "flight_segment_id": str(segment.id),
            "flight_designator": segment.flight_designator,
            "departure_airport_iata": segment.departure_airport_iata,
            "arrival_airport_iata": segment.arrival_airport_iata,
            "scheduled_departure_at": segment.scheduled_departure_at.isoformat(),
            "scheduled_arrival_at": segment.scheduled_arrival_at.isoformat(),
        }
        if segment.booking_status == "CANCELLED":
            return {
                **base,
                **segment_fields,
                "status": "CANCELLED_FLIGHT_SEGMENT",
                "explanation": "机票段已取消；不会把新的航迹快照关联到该航段。",
            }
        if not segment.tracking_provider or not segment.tracking_reference:
            return {
                **base,
                **segment_fields,
                "status": "TRACKING_REFERENCE_NOT_CONFIGURED",
                "explanation": "机票段缺少 tracking provider/reference；航迹信号不可验证。",
            }
        if signal.provider.strip().casefold() != segment.tracking_provider.strip().casefold():
            return {
                **base,
                **segment_fields,
                "status": "TRACKING_PROVIDER_MISMATCH",
                "explanation": "信号 Provider 与机票段声明的 tracking_provider 不一致，拒绝建立关联。",
            }
        if callsign is None or not _tracking_reference_matches(
            signal.provider, segment.tracking_reference, callsign
        ):
            return {
                **base,
                **segment_fields,
                "status": "TRACKING_REFERENCE_MISMATCH",
                "explanation": "信号 callsign 与机票段 tracking_reference 不匹配，拒绝建立关联。",
            }
    else:
        if plan is None:
            return {
                **base,
                "status": "NO_ACTIVE_PLAN",
                "explanation": "当前出行没有 ACTIVE 计划，无法列出需要人工核验的 AIR 交通段。",
            }
        if callsign is None and icao24 is None:
            return {
                **base,
                "status": "FLIGHT_REFERENCE_MISSING",
                "explanation": "航班状态信号缺少 callsign 和 icao24，无法提供可供人工核验的航空器标识。",
            }

    if plan is None:
        segment_fields = (
            {
                "flight_segment_id": str(segment.id),
                "flight_designator": segment.flight_designator,
                "departure_airport_iata": segment.departure_airport_iata,
                "arrival_airport_iata": segment.arrival_airport_iata,
                "scheduled_departure_at": segment.scheduled_departure_at.isoformat(),
                "scheduled_arrival_at": segment.scheduled_arrival_at.isoformat(),
            }
            if segment is not None
            else {}
        )
        return {
            **base,
            **segment_fields,
            "status": "NO_ACTIVE_PLAN",
            "explanation": "航迹已通过机票段 tracking reference 校验，但当前出行没有 ACTIVE 计划可供交通段绑定。",
        }

    if segment is not None:
        bindings = list(
            session.scalars(
                select(FlightSegmentPlanBinding)
                .where(
                    FlightSegmentPlanBinding.organization_id == organization,
                    FlightSegmentPlanBinding.trip_id == trip.id,
                    FlightSegmentPlanBinding.flight_segment_id == segment.id,
                    FlightSegmentPlanBinding.status == "ACTIVE",
                )
                .order_by(FlightSegmentPlanBinding.linked_at.desc(), FlightSegmentPlanBinding.id)
            ).all()
        )
        current_binding = next(
            (binding for binding in bindings if binding.plan_version_id == plan.id),
            None,
        )
        segment_fields = {
            "flight_segment_id": str(segment.id),
            "flight_designator": segment.flight_designator,
            "departure_airport_iata": segment.departure_airport_iata,
            "arrival_airport_iata": segment.arrival_airport_iata,
            "scheduled_departure_at": segment.scheduled_departure_at.isoformat(),
            "scheduled_arrival_at": segment.scheduled_arrival_at.isoformat(),
        }
        if current_binding is not None:
            leg = session.scalars(
                select(PlanTravelLeg).where(
                    PlanTravelLeg.organization_id == organization,
                    PlanTravelLeg.plan_version_id == plan.id,
                    PlanTravelLeg.id == current_binding.plan_travel_leg_id,
                    PlanTravelLeg.mode == "AIR",
                )
            ).one_or_none()
            if leg is None:
                return {
                    **base,
                    **segment_fields,
                    "binding_id": str(current_binding.id),
                    "status": "PLAN_BINDING_TARGET_INVALID",
                    "explanation": "绑定目标不再是当前计划中的 AIR 交通段；需要重新核验绑定。",
                }
            return {
                **base,
                **segment_fields,
                "status": "TRACKING_MATCHED_TO_CURRENT_AIR_LEG",
                "binding_id": str(current_binding.id),
                "linked_air_leg_count": 1,
                "linked_air_leg_ids": [str(leg.id)],
                "linked_air_leg": {
                    "plan_travel_leg_id": str(leg.id),
                    "plan_version_id": str(plan.id),
                    "mode": leg.mode,
                    "travel_minutes": leg.travel_minutes,
                    "source": leg.source,
                    "stale": leg.stale,
                },
                "explanation": (
                    f"航迹信号已通过显式机票段 {segment.flight_designator}、Provider 与 tracking reference 校验，"
                    "并匹配当前 ACTIVE 计划中的 AIR 交通段。该结果只确认航空器身份关联；"
                    "OpenSky 位置/速度快照不包含航班计划时刻或航司运行状态，不能据此判断延误、取消或 ETA。"
                ),
            }

        if bindings:
            latest = bindings[0]
            return {
                **base,
                **segment_fields,
                "status": "STALE_PLAN_BINDING",
                "binding_id": str(latest.id),
                "stale_binding": {
                    "plan_version_id": str(latest.plan_version_id),
                    "plan_travel_leg_id": str(latest.plan_travel_leg_id),
                },
                "explanation": (
                    "机票段与航迹引用已校验，但其计划绑定指向旧 PlanVersion。"
                    "计划换版后不会自动迁移关联；请在当前 ACTIVE 计划中重新确认 AIR 交通段绑定。"
                ),
            }
        return {
            **base,
            **segment_fields,
            "status": "FLIGHT_SEGMENT_NOT_LINKED_TO_PLAN",
            "explanation": (
                f"机票段 {segment.flight_designator} 与航迹引用已校验，但尚未人工绑定到当前 ACTIVE 计划的 AIR 交通段。"
                "请先在出行航班段工作区完成显式绑定。"
            ),
        }

    air_legs = session.scalars(
        select(PlanTravelLeg)
        .where(
            PlanTravelLeg.organization_id == organization,
            PlanTravelLeg.plan_version_id == plan.id,
            PlanTravelLeg.mode == "AIR",
        )
        .order_by(PlanTravelLeg.day_index, PlanTravelLeg.created_at, PlanTravelLeg.id)
    ).all()
    if not air_legs:
        return {
            **base,
            "status": "NO_AIR_TRAVEL_LEGS",
            "explanation": "当前 ACTIVE 计划没有 AIR 交通段；航迹快照不会被推断为本次行程的航班。",
        }

    unlinked_legs = [
        {
            "plan_travel_leg_id": str(leg.id),
            "from_stop_id": str(leg.from_stop_id) if leg.from_stop_id is not None else None,
            "to_stop_id": str(leg.to_stop_id),
            "day_index": leg.day_index,
            "travel_minutes": leg.travel_minutes,
            "source": leg.source,
            "stale": leg.stale,
        }
        for leg in air_legs
    ]
    return {
        **base,
        "status": "AIR_LEGS_REQUIRE_EXPLICIT_LINK",
        "candidate_air_leg_count": len(unlinked_legs),
        "unlinked_air_legs": unlinked_legs[:100],
        "explanation": (
            f"检测到 {len(unlinked_legs)} 段 AIR 交通，但当前信号没有显式 flight_segment_id。"
            "即使 callsign 与某个 booking tracking reference 相同，也不会仅凭这些标识猜测用户预订了这架飞机。"
            "请通过 flight-segments API 显式记录机票段并确认 PlanTravelLeg 绑定。"
        ),
    }

__all__ = ["assess_flight_signal_impact", "assess_weather_signal_impact"]
