"""CRM 同步服务：把现场记录整理成待同步载荷并交给适配器（PRD 访后跟进）。"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import NotFound, Unprocessable
from app.integrations.crm import CrmUnavailable, get_crm_provider
from app.models.crm import CrmSyncJob
from app.models.geo import Customer, Place
from app.models.records import VisitRecord
from app.models.trip import Trip, Visit
from app.services import job_service


def _record(session: Session, organization_id: UUID | str, record_id: UUID | str) -> VisitRecord:
    row = session.scalars(
        select(VisitRecord).where(
            VisitRecord.organization_id == str(organization_id),
            VisitRecord.id == str(record_id),
        )
    ).one_or_none()
    if row is None:
        raise NotFound("拜访记录不存在", details={"visit_record_id": str(record_id)})
    return row


def enqueue_sync(
    session: Session,
    organization_id: UUID | str,
    record_id: UUID | str,
    *,
    actor_user_id: UUID | str | None = None,
) -> CrmSyncJob:
    record = _record(session, organization_id, record_id)
    visit = session.get(Visit, record.visit_id)
    if visit is None:
        raise Unprocessable("拜访记录缺少所属拜访", details={"visit_record_id": str(record.id)})

    existing = session.scalars(
        select(CrmSyncJob).where(
            CrmSyncJob.organization_id == str(organization_id),
            CrmSyncJob.visit_record_id == record.id,
        )
    ).one_or_none()
    if existing is not None and existing.status != "FAILED":
        return existing

    if existing is None:
        crm_job = CrmSyncJob(
            organization_id=str(organization_id),
            trip_id=visit.trip_id,
            visit_id=visit.id,
            visit_record_id=record.id,
            status="QUEUED",
        )
        session.add(crm_job)
        session.flush()
    else:
        crm_job = existing
        crm_job.status = "QUEUED"
        crm_job.error = None
        crm_job.result = None

    job = job_service.enqueue(
        session,
        organization_id,
        job_type=job_service.JOB_TYPE_CRM_SYNC,
        payload={"crm_sync_id": str(crm_job.id)},
        dedupe_key=f"crm_sync:{record.id}",
        created_by_user_id=actor_user_id,
    )
    crm_job.job_id = job.id
    session.flush()
    return crm_job


def get_job(
    session: Session, organization_id: UUID | str, crm_job_id: UUID | str
) -> CrmSyncJob:
    row = session.scalars(
        select(CrmSyncJob).where(
            CrmSyncJob.organization_id == str(organization_id),
            CrmSyncJob.id == str(crm_job_id),
        )
    ).one_or_none()
    if row is None:
        raise NotFound("CRM 同步任务不存在", details={"crm_sync_id": str(crm_job_id)})
    return row


def list_jobs(
    session: Session, organization_id: UUID | str, trip_id: UUID | str
) -> list[CrmSyncJob]:
    return list(
        session.scalars(
            select(CrmSyncJob)
            .where(
                CrmSyncJob.organization_id == str(organization_id),
                CrmSyncJob.trip_id == str(trip_id),
            )
            .order_by(CrmSyncJob.created_at.desc())
        )
    )


def build_payload(session: Session, crm_job: CrmSyncJob) -> dict:
    record = session.get(VisitRecord, crm_job.visit_record_id)
    visit = session.get(Visit, crm_job.visit_id)
    trip = session.get(Trip, crm_job.trip_id)
    if record is None or visit is None:
        raise Unprocessable("CRM 同步对象已不存在", details={"crm_sync_id": str(crm_job.id)})
    customer = session.get(Customer, visit.customer_id) if visit.customer_id else None
    place = session.get(Place, visit.place_id)
    return {
        "organization_id": str(crm_job.organization_id),
        "trip": {"id": str(trip.id), "name": trip.name} if trip is not None else None,
        "visit": {
            "id": str(visit.id),
            "title": visit.title,
            "customer_id": str(visit.customer_id) if visit.customer_id else None,
            "place_id": str(visit.place_id),
        },
        "customer": (
            {
                "id": str(customer.id),
                "name": customer.name,
                "external_ref": customer.external_ref,
                "tier": customer.tier,
            }
            if customer is not None
            else None
        ),
        "place": (
            {"id": str(place.id), "name": place.name, "city": place.city, "country": place.country_code}
            if place is not None
            else None
        ),
        "record": {
            "id": str(record.id),
            "outcome": record.outcome,
            "notes": record.notes,
            "follow_up_tasks": list(record.follow_up_tasks or []),
            "client_updated_at": record.client_updated_at.isoformat(),
        },
    }


def execute_sync(session: Session, crm_job: CrmSyncJob) -> dict:
    payload = build_payload(session, crm_job)
    crm_job.payload = payload
    try:
        outcome = get_crm_provider().sync(
            str(crm_job.organization_id), str(crm_job.visit_record_id), payload
        )
    except CrmUnavailable as exc:
        crm_job.status = "FAILED"
        crm_job.error = str(exc)
        session.flush()
        raise
    crm_job.status = outcome.status
    crm_job.result = {
        "external_id": outcome.external_id,
        "detail": outcome.detail,
    }
    crm_job.error = None
    session.flush()
    return {"status": crm_job.status, **crm_job.result}


__all__ = [
    "build_payload",
    "enqueue_sync",
    "execute_sync",
    "get_job",
    "list_jobs",
]
