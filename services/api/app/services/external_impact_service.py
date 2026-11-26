"""Deterministic external-signal impact assessment against an active trip plan.

Weather is potentially relevant only when exact Place identity and a bounded signal
validity interval both overlap a persisted PlanStop. No city-name, proximity, or
model-generated inference is used.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.integrations import ExternalSignal
from app.models.planning import PlanStop, PlanVersion
from app.models.trip import Trip, Visit


def assess_weather_signal_impact(
    session: Session,
    organization_id: UUID | str,
    trip: Trip,
    signal: ExternalSignal,
) -> dict:
    """Return a reviewable impact snapshot; never mutate Trip or plan inputs."""
    organization = UUID(str(organization_id))
    plan: PlanVersion | None = None
    if trip.active_plan_version_id is not None:
        plan = session.scalars(
            select(PlanVersion).where(
                PlanVersion.organization_id == organization,
                PlanVersion.trip_id == trip.id,
                PlanVersion.id == trip.active_plan_version_id,
                PlanVersion.status == "ACTIVE",
            )
        ).one_or_none()

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
    return {
        **base,
        "place_id": str(place_id),
        "status": impact_status,
        "matched_stop_count": len(place_matches),
        "affected_stop_count": len(affected_visit_ids),
        "matched_visit_ids": [str(stop.visit_id) for stop in place_matches],
        "affected_visit_ids": affected_visit_ids,
        "matched_stops": matched_stops[:50],
        "explanation": explanation,
        "signal_window": {
            "starts_at": signal_start.isoformat(),
            "ends_at": signal_end.isoformat(),
        },
    }


__all__ = ["assess_weather_signal_impact"]
