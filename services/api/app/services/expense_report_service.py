"""报销单服务：建单快照 → 提交 → 审批/驳回。

多币种不换算：报销单按币种分组返回总额。审批写 `approvals`（subject_type=expense_report），
流程写 `workflow_instances`，与建议审批共用同一套审计语言。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.errors import Conflict, NotFound, Unprocessable
from app.api.schemas import ExpenseReportCreate
from app.models.expense_reports import ExpenseReport, ExpenseReportItem
from app.models.expenses import TripExpense
from app.models.proactive import Approval
from app.models.workflow import WorkflowInstance
from app.services import trip_service

REPORT_SUBJECT = "expense_report"
STATUS_DRAFT = "DRAFT"
STATUS_SUBMITTED = "SUBMITTED"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"


@dataclass
class ReportDetail:
    report: ExpenseReport
    items: list[ExpenseReportItem]
    totals: list[dict]


def _detail(session: Session, report: ExpenseReport) -> ReportDetail:
    items = list(
        session.scalars(
            select(ExpenseReportItem)
            .where(
                ExpenseReportItem.organization_id == report.organization_id,
                ExpenseReportItem.report_id == report.id,
            )
            .order_by(ExpenseReportItem.occurred_on, ExpenseReportItem.created_at)
        )
    )
    rows = session.execute(
        select(
            ExpenseReportItem.currency,
            func.coalesce(func.sum(ExpenseReportItem.amount), 0),
            func.count(),
        )
        .where(
            ExpenseReportItem.organization_id == report.organization_id,
            ExpenseReportItem.report_id == report.id,
        )
        .group_by(ExpenseReportItem.currency)
        .order_by(ExpenseReportItem.currency)
    ).all()
    totals = [
        {"currency": row[0], "amount": float(row[1]), "count": int(row[2])}
        for row in rows
    ]
    return ReportDetail(report=report, items=items, totals=totals)


def create_report(
    session: Session,
    organization_id: UUID | str,
    trip_id: UUID | str,
    payload: ExpenseReportCreate,
    *,
    actor_user_id: UUID | str | None = None,
) -> ReportDetail:
    trip = trip_service.require_trip(session, organization_id, trip_id)
    stmt = select(TripExpense).where(
        TripExpense.organization_id == str(organization_id),
        TripExpense.trip_id == trip.id,
    )
    if payload.expense_ids:
        wanted = {str(item) for item in payload.expense_ids}
        stmt = stmt.where(TripExpense.id.in_(wanted))
    expenses = list(session.scalars(stmt))
    if not expenses:
        raise Unprocessable("没有可报销的费用记录", details={"trip_id": str(trip.id)})
    if payload.expense_ids:
        found = {str(item.id) for item in expenses}
        missing = sorted({str(item) for item in payload.expense_ids} - found)
        if missing:
            raise Unprocessable("部分费用不属于该出行或不存在", details={"missing": missing})

    report = ExpenseReport(
        organization_id=str(organization_id),
        trip_id=trip.id,
        status=STATUS_DRAFT,
        notes=payload.notes,
        created_by_user_id=None if actor_user_id is None else str(actor_user_id),
    )
    session.add(report)
    session.flush()
    for expense in expenses:
        session.add(
            ExpenseReportItem(
                organization_id=str(organization_id),
                report_id=report.id,
                expense_id=expense.id,
                category=expense.category,
                amount=expense.amount,
                currency=expense.currency,
                occurred_on=expense.occurred_on,
                note=expense.note,
                receipt_ref=expense.receipt_ref,
            )
        )
    session.flush()
    return _detail(session, report)


def list_reports(
    session: Session, organization_id: UUID | str, trip_id: UUID | str
) -> list[ExpenseReport]:
    return list(
        session.scalars(
            select(ExpenseReport)
            .where(
                ExpenseReport.organization_id == str(organization_id),
                ExpenseReport.trip_id == str(trip_id),
            )
            .order_by(ExpenseReport.created_at.desc())
        )
    )


def require_report(
    session: Session, organization_id: UUID | str, report_id: UUID | str
) -> ExpenseReport:
    row = session.scalars(
        select(ExpenseReport).where(
            ExpenseReport.organization_id == str(organization_id),
            ExpenseReport.id == str(report_id),
        )
    ).one_or_none()
    if row is None:
        raise NotFound("报销单不存在", details={"report_id": str(report_id)})
    return row


def get_detail(
    session: Session, organization_id: UUID | str, report_id: UUID | str
) -> ReportDetail:
    return _detail(session, require_report(session, organization_id, report_id))


def submit_report(
    session: Session,
    organization_id: UUID | str,
    report_id: UUID | str,
    *,
    actor_user_id: UUID | str | None = None,
    now: datetime | None = None,
) -> ReportDetail:
    report = require_report(session, organization_id, report_id)
    if report.status == STATUS_SUBMITTED:
        return _detail(session, report)
    if report.status != STATUS_DRAFT:
        raise Conflict(
            "只有草稿报销单可以提交",
            details={"report_id": str(report.id), "status": report.status},
        )
    current = now or session.scalar(select(func.clock_timestamp()))
    report.status = STATUS_SUBMITTED
    report.submitted_at = current
    session.add(
        WorkflowInstance(
            organization_id=str(organization_id),
            trip_id=report.trip_id,
            workflow_type="EXPENSE_REPORT",
            status="ACCEPTED",
            payload={"report_id": str(report.id)},
            created_by_user_id=None if actor_user_id is None else str(actor_user_id),
        )
    )
    session.flush()
    return _detail(session, report)


def decide_report(
    session: Session,
    organization_id: UUID | str,
    report_id: UUID | str,
    *,
    approve: bool,
    actor_user_id: UUID | str,
    note: str | None = None,
    now: datetime | None = None,
) -> ReportDetail:
    report = require_report(session, organization_id, report_id)
    target = STATUS_APPROVED if approve else STATUS_REJECTED
    if report.status == target:
        return _detail(session, report)
    if report.status != STATUS_SUBMITTED:
        raise Conflict(
            "只有已提交的报销单可以审批",
            details={"report_id": str(report.id), "status": report.status},
        )
    current = now or session.scalar(select(func.clock_timestamp()))
    report.status = target
    report.decided_at = current
    report.decided_by_user_id = str(actor_user_id)
    session.add(
        Approval(
            organization_id=str(organization_id),
            subject_type=REPORT_SUBJECT,
            subject_id=report.id,
            decision=target,
            decided_by_user_id=str(actor_user_id),
            decided_at=current,
            note=note,
        )
    )
    workflow = session.scalars(
        select(WorkflowInstance)
        .where(
            WorkflowInstance.organization_id == str(organization_id),
            WorkflowInstance.workflow_type == "EXPENSE_REPORT",
            WorkflowInstance.payload["report_id"].astext == str(report.id),
        )
        .order_by(WorkflowInstance.created_at.desc())
        .limit(1)
    ).one_or_none()
    if workflow is not None:
        workflow.status = "COMPLETED" if approve else "REJECTED"
    session.flush()
    return _detail(session, report)


__all__ = [
    "REPORT_SUBJECT",
    "ReportDetail",
    "create_report",
    "decide_report",
    "get_detail",
    "list_reports",
    "require_report",
    "submit_report",
]
