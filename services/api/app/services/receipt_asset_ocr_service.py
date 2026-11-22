"""Persistent receipt OCR orchestration with AgentRun provenance and human review."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import Conflict, NotFound, Unprocessable, UnsupportedMedia
from app.models.receipts import ReceiptAsset, ReceiptOcrReview, ReceiptOcrRun
from app.models.workflow import AgentRun
from app.services import receipt_ocr_service
from app.services.receipt_asset_service import read_receipt_content

_IMAGE_SUFFIXES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


def _require_asset(
    session: Session,
    organization_id: UUID | str,
    asset_id: UUID | str,
) -> ReceiptAsset:
    asset = session.scalars(
        select(ReceiptAsset).where(
            ReceiptAsset.organization_id == str(organization_id),
            ReceiptAsset.id == str(asset_id),
            ReceiptAsset.storage_status == "AVAILABLE",
        )
    ).one_or_none()
    if asset is None:
        raise NotFound("票据资产不存在或不可用", details={"receipt_asset_id": str(asset_id)})
    return asset


def create_receipt_ocr_run(
    session: Session,
    organization_id: UUID | str,
    asset_id: UUID | str,
    *,
    actor_user_id: UUID | str,
) -> ReceiptOcrRun:
    """Run OCR against the persisted original; this operation never writes a TripExpense."""
    asset = _require_asset(session, organization_id, asset_id)
    suffix = _IMAGE_SUFFIXES.get(asset.mime_type)
    if suffix is None:
        raise UnsupportedMedia("OCR 仅支持 JPEG、PNG、GIF 或 WebP 图片票据")

    asset, image_bytes = read_receipt_content(session, organization_id, asset_id)
    now = datetime.now(UTC)
    engine_version = receipt_ocr_service.ocr_engine_version()
    agent_run = AgentRun(
        organization_id=UUID(str(organization_id)),
        agent_type="RECEIPT_OCR",
        status="RUNNING",
        input={
            "receipt_asset_id": str(asset.id),
            "content_sha256": asset.sha256,
            "mime_type": asset.mime_type,
            "byte_length": asset.byte_length,
            "engine": "tesseract",
            "engine_version": engine_version,
        },
        started_at=now,
    )
    session.add(agent_run)
    session.flush()

    ocr_run = ReceiptOcrRun(
        organization_id=UUID(str(organization_id)),
        receipt_asset_id=asset.id,
        agent_run_id=agent_run.id,
        content_sha256=asset.sha256,
        engine="tesseract",
        engine_version=engine_version,
        status="RUNNING",
        candidates={},
        started_at=now,
    )
    session.add(ocr_run)
    session.flush()

    try:
        candidates = receipt_ocr_service.extract_receipt_fields(
            image_bytes,
            file_extension=suffix,
            include_raw_text_preview=False,
        )
    except receipt_ocr_service.OcrUnavailable as exc:
        error = str(exc)[:2000]
        ocr_run.status = "FAILED"
        ocr_run.error = error
        agent_run.status = "FAILED"
        agent_run.error = error
    else:
        # Keep only structured candidates. Do not duplicate raw receipt text in AgentRun JSON.
        allowed_keys = (
            "merchant_hint",
            "amount_candidates",
            "date_candidates",
            "text_length",
        )
        safe_candidates = {key: candidates.get(key) for key in allowed_keys if key in candidates}
        ocr_run.status = "SUCCEEDED"
        ocr_run.candidates = safe_candidates
        agent_run.status = "SUCCEEDED"
        agent_run.output = safe_candidates
    finished = datetime.now(UTC)
    ocr_run.finished_at = finished
    agent_run.finished_at = finished
    session.flush()
    return ocr_run


def list_receipt_ocr_runs(
    session: Session,
    organization_id: UUID | str,
    asset_id: UUID | str,
) -> list[ReceiptOcrRun]:
    _require_asset(session, organization_id, asset_id)
    return list(
        session.scalars(
            select(ReceiptOcrRun)
            .where(
                ReceiptOcrRun.organization_id == str(organization_id),
                ReceiptOcrRun.receipt_asset_id == str(asset_id),
            )
            .order_by(ReceiptOcrRun.created_at.desc(), ReceiptOcrRun.id.desc())
        )
    )


def require_receipt_ocr_run(
    session: Session,
    organization_id: UUID | str,
    run_id: UUID | str,
    *,
    asset_id: UUID | str | None = None,
) -> ReceiptOcrRun:
    stmt = select(ReceiptOcrRun).where(
        ReceiptOcrRun.organization_id == str(organization_id),
        ReceiptOcrRun.id == str(run_id),
    )
    if asset_id is not None:
        stmt = stmt.where(ReceiptOcrRun.receipt_asset_id == str(asset_id))
    run = session.scalars(stmt).one_or_none()
    if run is None:
        raise NotFound("票据 OCR 记录不存在", details={"receipt_ocr_run_id": str(run_id)})
    return run


def get_receipt_ocr_review(
    session: Session,
    organization_id: UUID | str,
    run_id: UUID | str,
) -> ReceiptOcrReview | None:
    return session.scalars(
        select(ReceiptOcrReview).where(
            ReceiptOcrReview.organization_id == str(organization_id),
            ReceiptOcrReview.receipt_ocr_run_id == str(run_id),
        )
    ).one_or_none()


def review_receipt_ocr_run(
    session: Session,
    organization_id: UUID | str,
    run_id: UUID | str,
    *,
    decision: str,
    confirmed_fields: dict,
    note: str | None,
    actor_user_id: UUID | str,
) -> ReceiptOcrReview:
    run = require_receipt_ocr_run(session, organization_id, run_id)
    if run.status != "SUCCEEDED":
        raise Conflict(
            "只有成功完成的 OCR 结果可以人工审核",
            details={"receipt_ocr_run_id": str(run.id), "status": run.status},
        )
    existing = get_receipt_ocr_review(session, organization_id, run.id)
    if existing is not None:
        raise Conflict(
            "该 OCR 结果已经审核；如需修正，请重新执行 OCR 后创建新的审核记录",
            details={"receipt_ocr_run_id": str(run.id), "review_id": str(existing.id)},
        )
    if decision == "CONFIRMED" and not confirmed_fields:
        raise Unprocessable("确认 OCR 结果时至少需要确认一个字段")
    if decision == "REJECTED" and not (note and note.strip()):
        raise Unprocessable("驳回 OCR 结果必须填写原因")

    review = ReceiptOcrReview(
        organization_id=UUID(str(organization_id)),
        receipt_ocr_run_id=run.id,
        decision=decision,
        confirmed_fields=confirmed_fields,
        note=note.strip() if note and note.strip() else None,
        reviewed_by_user_id=UUID(str(actor_user_id)),
        reviewed_at=datetime.now(UTC),
    )
    session.add(review)
    session.flush()
    return review


__all__ = [
    "create_receipt_ocr_run",
    "get_receipt_ocr_review",
    "list_receipt_ocr_runs",
    "require_receipt_ocr_run",
    "review_receipt_ocr_run",
]
