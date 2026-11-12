"""差旅费用与预算接口（PRD 方向六）。"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Query, UploadFile, status
from fastapi.responses import PlainTextResponse

from app.api.deps import (
    ROLE_ADMIN,
    ROLE_TRAVELER,
    ROLE_VIEWER,
    ContextDep,
    IdempotencyKeyDep,
    SessionDep,
    require_role,
)
from app.api.errors import ServiceUnavailable
from app.api.idempotent import IdempotentWrite
from app.api.schemas import (
    BudgetSummaryRead,
    CurrencyTotalRead,
    ExpenseCreate,
    ExpenseRead,
)
from app.services import expense_service, receipt_ocr_service, trip_service

CSV_HEADERS = ("occurred_on", "category", "amount", "currency", "note", "receipt_ref")


def _csv_cell(value) -> str:
    """CSV 单元格转义：包裹引号并翻倍内部引号（防注入，含公式前缀）。"""
    text = "" if value is None else str(value)
    if text.startswith(("=", "+", "-", "@")):
        text = "'" + text  # Excel 公式注入防护（OWASP CSV 导出规范）
    return '"' + text.replace('"', '""') + '"'

router = APIRouter(tags=["expenses"])


@router.get(
    "/trips/{trip_id}/expenses",
    response_model=list[ExpenseRead],
    summary="出行的费用记录",
)
def list_expenses(trip_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_VIEWER)
    trip_service.require_trip(session, context.organization_id, trip_id)
    return [
        ExpenseRead.model_validate(row)
        for row in expense_service.list_expenses(session, context.organization_id, trip_id)
    ]


@router.post(
    "/expenses:ocr",
    summary="票据 OCR 提取候选字段（开源 Tesseract；未安装显式 503，ADR-0034）",
)
async def ocr_receipt(
    context: ContextDep,
    file: Annotated[UploadFile, File(...)],
):
    """只产候选字段（金额/日期/商户提示），不自动创建费用——人确认后经常规录入入库。"""
    require_role(context, ROLE_TRAVELER)
    image = await file.read()
    try:
        return receipt_ocr_service.extract_receipt_fields(image)
    except receipt_ocr_service.OcrUnavailable as exc:
        raise ServiceUnavailable(str(exc)) from exc



@router.get(
    "/trips/{trip_id}/expenses:export",
    response_class=PlainTextResponse,
    summary="费用 CSV 导出（财务系统对接；网页形式兜底，ADR-0034）",
)
def export_expenses_csv(trip_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_ADMIN)
    trip_service.require_trip(session, context.organization_id, trip_id)
    lines = [",".join(_csv_cell(header) for header in CSV_HEADERS)]
    for row in expense_service.list_expenses(session, context.organization_id, trip_id):
        fields = (
            row.occurred_on.isoformat(),
            row.category,
            str(row.amount),
            row.currency,
            row.note,
            row.receipt_ref,
        )
        lines.append(",".join(_csv_cell(field) for field in fields))
    return PlainTextResponse(
        "\n".join(lines) + "\n",
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="expenses-{trip_id}.csv"'},
    )



@router.post(
    "/trips/{trip_id}/expenses",
    response_model=ExpenseRead,
    status_code=status.HTTP_201_CREATED,
    summary="录入一笔费用",
)
def create_expense(
    trip_id: UUID,
    payload: ExpenseCreate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint="POST /v1/trips/{trip_id}/expenses",
        key=idempotency_key,
        payload={"trip_id": str(trip_id), **payload.model_dump(mode="json")},
    )
    if (replay := guard.begin()) is not None:
        return replay
    row = expense_service.create_expense(
        session,
        context.organization_id,
        trip_id,
        payload,
        actor_user_id=context.user_id,
    )
    return guard.finish(status_code=status.HTTP_201_CREATED, body=ExpenseRead.model_validate(row))


@router.delete(
    "/expenses/{expense_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除费用记录",
)
def delete_expense(expense_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_TRAVELER)
    expense_service.delete_expense(session, context.organization_id, expense_id)


@router.get(
    "/trips/{trip_id}/budget",
    response_model=BudgetSummaryRead,
    summary="预算、计划估算与实际费用（多币种分列，不伪造汇率）",
)
def get_budget(
    trip_id: UUID,
    context: ContextDep,
    session: SessionDep,
    base_currency: str | None = Query(
        default=None,
        min_length=3,
        max_length=3,
        description="可选：按组织已录入汇率折算到该币种；缺汇率的币种保持未折算",
    ),
):
    require_role(context, ROLE_VIEWER)
    summary = expense_service.budget_summary(
        session, context.organization_id, trip_id, base_currency=base_currency
    )
    return BudgetSummaryRead(
        budget_amount=summary.budget_amount,
        budget_currency=summary.budget_currency,
        planned_cost_total=summary.planned_cost_total,
        planned_cost_currency=summary.planned_cost_currency,
        planned_cost_unknown_count=summary.planned_cost_unknown_count,
        actual_by_currency=[CurrencyTotalRead(**item) for item in summary.actual_by_currency],
        actual_total_in_budget_currency=summary.actual_total_in_budget_currency,
        variance_vs_budget=summary.variance_vs_budget,
        variance_vs_plan=summary.variance_vs_plan,
        conversion_base_currency=summary.conversion_base_currency,
        converted_actual_total=summary.converted_actual_total,
        converted_planned_cost_total=summary.converted_planned_cost_total,
        converted_variance_vs_budget=summary.converted_variance_vs_budget,
        conversion_complete=summary.conversion_complete,
        unconverted_currencies=summary.unconverted_currencies,
        fx_conversions=summary.fx_conversions,
    )
