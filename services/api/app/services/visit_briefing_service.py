"""拜访前简报（PRD 页面 07「Visit Workspace」）。

只读聚合：客户档案、地点、本次拜访目标、同客户历史拜访、历史跟进项与复盘记忆。没有建模的
部分（CRM 实时商机、资料附件）通过 ``data_gaps`` 显式说明，不填猜测内容。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.geo import Customer, Place
from app.models.memory import TripMemorySummary
from app.models.records import VisitRecord
from app.models.trip import Trip
from app.models.trip import Visit as VisitModel
from app.services import trip_service, visit_service

GAP_CUSTOMER_NOT_LINKED = "CUSTOMER_NOT_LINKED"
GAP_CRM_OPPORTUNITY = "CRM_OPPORTUNITY_NOT_CONNECTED"
GAP_MATERIALS = "VISIT_MATERIALS_NOT_MODELED"

HISTORY_LIMIT = 10
FOLLOW_UP_LIMIT = 20
MEMORY_LIMIT = 5


def _customer_payload(customer: Customer | None) -> dict | None:
    if customer is None:
        return None
    return {
        "id": customer.id,
        "name": customer.name,
        "external_ref": customer.external_ref,
        "tier": customer.tier,
        "country_code": customer.country_code,
        "industry": customer.industry,
        "status": customer.status,
        "primary_contact_name": customer.primary_contact_name,
        "primary_contact_email": customer.primary_contact_email,
        "primary_contact_phone": customer.primary_contact_phone,
        "notes": customer.notes,
    }


def _place_payload(place: Place | None) -> dict | None:
    if place is None:
        return None
    return {
        "id": place.id,
        "name": place.name,
        "address_line": place.address_line,
        "city": place.city,
        "region": place.region,
        "country_code": place.country_code,
        "timezone": place.timezone,
        "lat": place.lat,
        "lon": place.lon,
        "geocode_status": place.geocode_status,
    }


def build_briefing(
    session: Session, organization_id: UUID | str, visit_id: UUID | str
) -> dict:
    visit: VisitModel = visit_service.require_visit(
        session, organization_id, UUID(str(visit_id))
    )
    trip = trip_service.require_trip(session, organization_id, visit.trip_id)
    customer = session.get(Customer, visit.customer_id) if visit.customer_id else None
    place = session.get(Place, visit.place_id)

    gaps: list[dict] = []
    if customer is None:
        gaps.append(
            {
                "code": GAP_CUSTOMER_NOT_LINKED,
                "message": "这条拜访没有关联客户，无法汇总客户档案与历史拜访。",
            }
        )
    gaps.extend(
        [
            {
                "code": GAP_CRM_OPPORTUNITY,
                "message": "尚未连接 CRM，实时商机阶段与金额不可用；请以 CRM 为准人工核对。",
            },
            {
                "code": GAP_MATERIALS,
                "message": "资料附件未建模，相关文档需从企业文档系统获取。",
            },
        ]
    )

    history: list[dict] = []
    follow_ups: list[dict] = []
    memories: list[dict] = []
    if customer is not None:
        previous = session.execute(
            select(VisitModel, Trip)
            .join(Trip, VisitModel.trip_id == Trip.id)
            .where(
                VisitModel.organization_id == str(organization_id),
                VisitModel.customer_id == customer.id,
                VisitModel.id != visit.id,
                Trip.window_end < trip.window_start,
            )
            .order_by(Trip.window_end.desc())
            .limit(HISTORY_LIMIT)
        ).all()

        visit_ids = [row[0].id for row in previous]
        records: dict[str, VisitRecord] = {}
        if visit_ids:
            for record in session.scalars(
                select(VisitRecord)
                .where(
                    VisitRecord.organization_id == str(organization_id),
                    VisitRecord.visit_id.in_(visit_ids),
                )
                .order_by(VisitRecord.created_at.desc())
            ):
                records.setdefault(str(record.visit_id), record)

        for past_visit, past_trip in previous:
            record = records.get(str(past_visit.id))
            history.append(
                {
                    "visit_id": past_visit.id,
                    "trip_id": past_trip.id,
                    "trip_name": past_trip.name,
                    "date": past_trip.window_start.date(),
                    "title": past_visit.title,
                    "status": past_visit.status,
                    "outcome": record.outcome if record is not None else None,
                    "notes": record.notes if record is not None else None,
                }
            )
            if record is None:
                continue
            for task in record.follow_up_tasks or []:
                title = task.get("title") if isinstance(task, dict) else None
                if not title:
                    continue
                follow_ups.append(
                    {
                        "title": str(title),
                        "due_date": task.get("due_date"),
                        "trip_id": past_trip.id,
                        "trip_name": past_trip.name,
                        "visit_id": past_visit.id,
                    }
                )

        for memory in session.scalars(
            select(TripMemorySummary)
            .where(
                TripMemorySummary.organization_id == str(organization_id),
                TripMemorySummary.customer_id == customer.id,
                TripMemorySummary.valid_until.is_(None),
            )
            .order_by(TripMemorySummary.valid_from.desc())
            .limit(MEMORY_LIMIT)
        ):
            memories.append(
                {
                    "id": memory.id,
                    "kind": memory.kind,
                    "summary": memory.summary,
                    "decisions": list(memory.decisions or []),
                    "follow_ups": list(memory.follow_ups or []),
                    "lessons": list(memory.lessons or []),
                    "confidence": memory.confidence,
                    "valid_from": memory.valid_from,
                }
            )

    return {
        "visit": visit,
        "customer": _customer_payload(customer),
        "place": _place_payload(place),
        "history": history,
        "follow_ups": follow_ups[:FOLLOW_UP_LIMIT],
        "memories": memories,
        "data_gaps": gaps,
    }


__all__ = [
    "GAP_CRM_OPPORTUNITY",
    "GAP_CUSTOMER_NOT_LINKED",
    "GAP_MATERIALS",
    "build_briefing",
]
