"""Read-only, evidence-backed Trip closeout readiness assessment."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.expense_reports import ExpenseReport, ExpenseReportItem
from app.models.expenses import TripExpense
from app.models.receipts import ReceiptAsset, ReceiptOcrReview, ReceiptOcrRun
from app.models.records import VisitFollowUp, VisitRecord
from app.models.trip import Trip, Visit
from app.services import trip_service

IMAGE_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})


@dataclass(frozen=True)
class CloseoutIssue:
    code: str
    severity: str
    title: str
    detail: str
    source_type: str
    source_id: UUID
    action_hint: str

    @property
    def stable_id(self) -> str:
        return f"{self.code}:{self.source_type}:{self.source_id}"


def _issue(
    code: str,
    severity: str,
    title: str,
    detail: str,
    source_type: str,
    source_id: UUID,
    action_hint: str,
) -> CloseoutIssue:
    return CloseoutIssue(
        code=code,
        severity=severity,
        title=title,
        detail=detail,
        source_type=source_type,
        source_id=source_id,
        action_hint=action_hint,
    )


def build_trip_closeout_readiness(
    session: Session,
    organization_id: UUID | str,
    trip_id: UUID | str,
) -> dict:
    """Assess persisted Trip facts without mutating Trip, Visit, Expense or Report state."""
    org = UUID(str(organization_id))
    trip = trip_service.require_trip(session, org, trip_id)

    visits = list(
        session.scalars(
            select(Visit)
            .where(Visit.organization_id == org, Visit.trip_id == trip.id)
            .order_by(Visit.created_at, Visit.id)
        )
    )
    visit_ids = [row.id for row in visits]
    applied_records: list[VisitRecord] = []
    if visit_ids:
        applied_records = list(
            session.scalars(
                select(VisitRecord).where(
                    VisitRecord.organization_id == org,
                    VisitRecord.visit_id.in_(visit_ids),
                    VisitRecord.applied.is_(True),
                )
            )
        )
    recorded_visit_ids = {row.visit_id for row in applied_records}

    blockers: list[CloseoutIssue] = []
    warnings: list[CloseoutIssue] = []
    for visit in visits:
        if visit.status in ("PLANNED", "SCHEDULED"):
            blockers.append(
                _issue(
                    "VISIT_NOT_CLOSED",
                    "BLOCKER",
                    "拜访尚未完成",
                    f"拜访状态为 {visit.status}，请完成、取消或更新真实拜访结果",
                    "VISIT",
                    visit.id,
                    "打开该拜访并记录真实结果；不要为结账虚构完成状态",
                )
            )
        elif visit.status in ("DONE", "MISSED") and visit.id not in recorded_visit_ids:
            blockers.append(
                _issue(
                    "VISIT_OUTCOME_MISSING",
                    "BLOCKER",
                    "缺少拜访结果记录",
                    "该拜访已标记完成或未能拜访，但没有已生效的 VisitRecord",
                    "VISIT",
                    visit.id,
                    "补录并同步现场拜访结果",
                )
            )

    expenses = list(
        session.scalars(
            select(TripExpense)
            .where(TripExpense.organization_id == org, TripExpense.trip_id == trip.id)
            .order_by(TripExpense.occurred_on, TripExpense.created_at, TripExpense.id)
        )
    )
    reports = list(
        session.scalars(
            select(ExpenseReport)
            .where(ExpenseReport.organization_id == org, ExpenseReport.trip_id == trip.id)
            .order_by(ExpenseReport.created_at.desc(), ExpenseReport.id.desc())
        )
    )
    report_ids = [row.id for row in reports]
    report_items: list[ExpenseReportItem] = []
    if report_ids:
        report_items = list(
            session.scalars(
                select(ExpenseReportItem).where(
                    ExpenseReportItem.organization_id == org,
                    ExpenseReportItem.report_id.in_(report_ids),
                )
            )
        )

    snapshotted_expense_ids = {
        row.expense_id for row in report_items if row.expense_id is not None
    }
    unreported_expenses = [
        row for row in expenses if row.id not in snapshotted_expense_ids
    ]
    for expense in unreported_expenses:
        blockers.append(
            _issue(
                "EXPENSE_NOT_IN_REPORT",
                "BLOCKER",
                "费用尚未纳入报销快照",
                f"{expense.category} · {expense.amount} {expense.currency} · {expense.occurred_on.isoformat()}",
                "EXPENSE",
                expense.id,
                "将该费用加入报销单并重新检查报销快照",
            )
        )

    latest_report = reports[0] if reports else None
    if latest_report is not None and latest_report.status == "REJECTED":
        blockers.append(
            _issue(
                "LATEST_REPORT_REJECTED",
                "BLOCKER",
                "最新报销单已被驳回",
                "需要修正费用或凭证并创建新的报销快照；历史驳回记录仍保留",
                "EXPENSE_REPORT",
                latest_report.id,
                "查看驳回原因，修正费用/票据后创建新的报销单",
            )
        )

    expense_totals: dict[str, dict] = defaultdict(lambda: {"amount": 0.0, "count": 0})
    for expense in expenses:
        total = expense_totals[expense.currency]
        total["amount"] += float(expense.amount)
        total["count"] += 1
    totals_by_currency = [
        {"currency": currency, "amount": round(values["amount"], 2), "count": values["count"]}
        for currency, values in sorted(expense_totals.items())
    ]
    if len(totals_by_currency) > 1:
        warnings.append(
            _issue(
                "MULTI_CURRENCY_REVIEW",
                "WARNING",
                "费用包含多种币种",
                "各币种金额分别核对；本评估不会使用估算或缺失汇率合并总额",
                "TRIP",
                trip.id,
                "按原始币种分别核对金额与报销政策",
            )
        )

    expense_ids_without_receipt = [row for row in expenses if row.receipt_asset_id is None]
    for expense in expense_ids_without_receipt:
        warnings.append(
            _issue(
                "RECEIPT_MISSING",
                "WARNING",
                "费用缺少原始票据",
                f"{expense.category} · {expense.amount} {expense.currency}；当前没有绑定可追溯的 Receipt Asset",
                "EXPENSE",
                expense.id,
                "补充原始票据，或按组织适用政策记录例外",
            )
        )

    receipt_asset_ids = {
        row.receipt_asset_id for row in expenses if row.receipt_asset_id is not None
    }
    receipt_asset_ids.update(
        row.receipt_asset_id for row in report_items if row.receipt_asset_id is not None
    )
    assets: list[ReceiptAsset] = []
    if receipt_asset_ids:
        assets = list(
            session.scalars(
                select(ReceiptAsset).where(
                    ReceiptAsset.organization_id == org,
                    ReceiptAsset.trip_id == trip.id,
                    ReceiptAsset.id.in_(receipt_asset_ids),
                )
            )
        )
    assets_by_id = {row.id: row for row in assets}
    for asset_id in sorted(receipt_asset_ids - set(assets_by_id), key=str):
        warnings.append(
            _issue(
                "RECEIPT_ASSET_MISSING",
                "WARNING",
                "票据资产不可用",
                "费用或报销快照引用了不存在的票据资产记录",
                "TRIP",
                trip.id,
                "核对票据存储与报销快照；不要用新文件静默替换原始证据",
            )
        )
    for asset in assets:
        if asset.storage_status != "AVAILABLE":
            warnings.append(
                _issue(
                    "RECEIPT_ASSET_UNAVAILABLE",
                    "WARNING",
                    "原始票据当前不可读取",
                    f"票据资产状态为 {asset.storage_status}",
                    "RECEIPT_ASSET",
                    asset.id,
                    "检查私有存储对象与完整性状态",
                )
            )

    ocr_runs: list[ReceiptOcrRun] = []
    if assets:
        ocr_runs = list(
            session.scalars(
                select(ReceiptOcrRun)
                .where(
                    ReceiptOcrRun.organization_id == org,
                    ReceiptOcrRun.receipt_asset_id.in_([row.id for row in assets]),
                )
                .order_by(ReceiptOcrRun.created_at.desc(), ReceiptOcrRun.id.desc())
            )
        )
    latest_ocr_by_asset: dict[UUID, ReceiptOcrRun] = {}
    for run in ocr_runs:
        latest_ocr_by_asset.setdefault(run.receipt_asset_id, run)
    latest_run_ids = [row.id for row in latest_ocr_by_asset.values()]
    reviews_by_run: dict[UUID, ReceiptOcrReview] = {}
    if latest_run_ids:
        reviews = list(
            session.scalars(
                select(ReceiptOcrReview).where(
                    ReceiptOcrReview.organization_id == org,
                    ReceiptOcrReview.receipt_ocr_run_id.in_(latest_run_ids),
                )
            )
        )
        reviews_by_run = {row.receipt_ocr_run_id: row for row in reviews}
    for asset in assets:
        if asset.mime_type not in IMAGE_MIME_TYPES:
            continue
        run = latest_ocr_by_asset.get(asset.id)
        review = reviews_by_run.get(run.id) if run is not None else None
        if run is None:
            warnings.append(
                _issue(
                    "RECEIPT_OCR_NOT_RUN",
                    "WARNING",
                    "票据 OCR 尚未执行",
                    "当前图片票据尚无结构化识别结果；人工核对仍可直接完成",
                    "RECEIPT_ASSET",
                    asset.id,
                    "对已保存原始票据运行 OCR，并人工核对候选字段",
                )
            )
        elif run.status != "SUCCEEDED":
            warnings.append(
                _issue(
                    "RECEIPT_OCR_FAILED",
                    "WARNING",
                    "票据 OCR 未成功",
                    run.error or f"OCR 状态为 {run.status}",
                    "RECEIPT_ASSET",
                    asset.id,
                    "检查原始图片质量或 OCR 环境；也可直接人工核对费用",
                )
            )
        elif review is None or review.decision != "CONFIRMED":
            warnings.append(
                _issue(
                    "RECEIPT_OCR_NOT_CONFIRMED",
                    "WARNING",
                    "OCR 字段尚未人工确认",
                    "识别结果仍是候选值，不会自动写入费用金额、日期或商户",
                    "RECEIPT_ASSET",
                    asset.id,
                    "逐字段核对并确认或驳回 OCR 候选值",
                )
            )

    follow_ups = list(
        session.scalars(
            select(VisitFollowUp).where(
                VisitFollowUp.organization_id == org,
                VisitFollowUp.trip_id == trip.id,
                VisitFollowUp.status.in_(("OPEN", "IN_PROGRESS")),
            )
        )
    )
    for follow_up in follow_ups:
        warnings.append(
            _issue(
                "BUSINESS_FOLLOW_UP_OPEN",
                "WARNING",
                "仍有商务跟进事项未完成",
                follow_up.title,
                "VISIT_FOLLOW_UP",
                follow_up.id,
                "打开拜访记录更新商务跟进进度；该事项不会阻断财务结账",
            )
        )

    blockers.sort(key=lambda item: (item.code, str(item.source_id)))
    warnings.sort(key=lambda item: (item.code, str(item.source_id)))

    if blockers:
        status = "BLOCKED"
    elif latest_report is not None and latest_report.status == "APPROVED":
        status = "REPORT_APPROVED"
    elif latest_report is not None and latest_report.status == "SUBMITTED":
        status = "REPORT_SUBMITTED"
    elif warnings:
        status = "READY_WITH_EXCEPTIONS"
    else:
        status = "READY_FOR_REVIEW"

    return {
        "trip_id": trip.id,
        "status": status,
        "generated_at": datetime.now(UTC),
        "summary": {
            "visit_count": len(visits),
            "visit_outcome_count": len(recorded_visit_ids),
            "visits_open_count": sum(row.status in ("PLANNED", "SCHEDULED") for row in visits),
            "visits_missing_outcome_count": sum(
                row.status in ("DONE", "MISSED") and row.id not in recorded_visit_ids
                for row in visits
            ),
            "expense_count": len(expenses),
            "expenses_in_report_count": len(expenses) - len(unreported_expenses),
            "expenses_not_in_report_count": len(unreported_expenses),
            "expenses_without_receipt_count": len(expense_ids_without_receipt),
            "receipt_asset_count": len(assets),
            "receipt_asset_unavailable_count": sum(
                row.storage_status != "AVAILABLE" for row in assets
            ),
            "ocr_needs_review_count": sum(
                run.status == "SUCCEEDED" and (
                    run.id not in reviews_by_run or reviews_by_run[run.id].decision != "CONFIRMED"
                )
                for run in latest_ocr_by_asset.values()
            ),
            "open_business_follow_up_count": len(follow_ups),
            "latest_report_id": latest_report.id if latest_report else None,
            "latest_report_status": latest_report.status if latest_report else None,
            "blocker_count": len(blockers),
            "warning_count": len(warnings),
        },
        "totals_by_currency": totals_by_currency,
        "blockers": [item.__dict__ | {"id": item.stable_id} for item in blockers],
        "warnings": [item.__dict__ | {"id": item.stable_id} for item in warnings],
    }


__all__ = ["build_trip_closeout_readiness"]
