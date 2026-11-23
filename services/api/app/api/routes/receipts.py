"""私有原始票据上传与读取接口。"""

from __future__ import annotations

import hashlib
import re
from pathlib import PurePath
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, File, UploadFile, status
from fastapi.responses import Response

from app.api.deps import (
    ROLE_TRAVELER,
    ROLE_VIEWER,
    ContextDep,
    IdempotencyKeyDep,
    SessionDep,
    require_role,
)
from app.api.errors import BadRequest, UnsupportedMedia
from app.api.idempotent import IdempotentWrite
from app.api.schemas import (
    ReceiptAssetRead,
    ReceiptOcrReviewCreate,
    ReceiptOcrReviewRead,
    ReceiptOcrRunRead,
)
from app.config import get_settings
from app.services import receipt_asset_ocr_service, receipt_asset_service, trip_service

router = APIRouter(tags=["receipt-assets"])

_SUPPORTED_TYPES = {
    "application/pdf",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
}


def _detect_mime_type(content: bytes) -> str:
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    raise UnsupportedMedia("仅支持真实 PDF、JPEG、PNG、GIF 或 WebP 票据文件")


def _sanitize_filename(value: str | None) -> str:
    raw = (value or "receipt").replace("\\", "/")
    name = PurePath(raw).name
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    name = name[:240]
    return name or "receipt"


def _content_disposition(filename: str) -> str:
    fallback = filename.encode("ascii", "ignore").decode("ascii")
    fallback = re.sub(r"[^A-Za-z0-9._ -]", "_", fallback).strip(" .") or "receipt"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


@router.post(
    "/trips/{trip_id}/receipt-assets",
    response_model=ReceiptAssetRead,
    status_code=status.HTTP_201_CREATED,
    summary="保存一份原始票据到私有资产库",
)
async def upload_receipt_asset(
    trip_id: UUID,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
    file: Annotated[UploadFile, File(...)],
):
    require_role(context, ROLE_TRAVELER)
    trip_service.require_trip(session, context.organization_id, trip_id)

    settings = get_settings()
    content = await file.read(settings.receipt_max_upload_bytes + 1)
    if not content:
        raise BadRequest("票据文件不能为空")
    if len(content) > settings.receipt_max_upload_bytes:
        raise BadRequest(
            "票据文件超过大小限制",
            details={"max_bytes": settings.receipt_max_upload_bytes},
        )

    mime_type = _detect_mime_type(content)
    if mime_type not in _SUPPORTED_TYPES:
        raise UnsupportedMedia("不支持该票据文件类型")
    filename = _sanitize_filename(file.filename)
    digest = hashlib.sha256(content).hexdigest()

    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint="POST /v1/trips/{trip_id}/receipt-assets",
        key=idempotency_key,
        payload={
            "trip_id": str(trip_id),
            "filename": filename,
            "mime_type": mime_type,
            "byte_length": len(content),
            "sha256": digest,
        },
    )
    if (replay := guard.begin()) is not None:
        return replay

    asset = receipt_asset_service.create_receipt_asset(
        session,
        context.organization_id,
        trip_id,
        original_filename=filename,
        mime_type=mime_type,
        content=content,
        sha256=digest,
        actor_user_id=context.user_id,
    )
    body = ReceiptAssetRead.model_validate(asset)
    return guard.finish(status_code=status.HTTP_201_CREATED, body=body)


@router.get(
    "/receipt-assets/{asset_id}/content",
    response_class=Response,
    summary="鉴权读取原始票据（二进制不进入公开 URL）",
)
def download_receipt_asset(
    asset_id: UUID,
    context: ContextDep,
    session: SessionDep,
):
    require_role(context, ROLE_VIEWER)
    asset, content = receipt_asset_service.read_receipt_content(
        session, context.organization_id, asset_id
    )
    return Response(
        content=content,
        media_type=asset.mime_type,
        headers={
            "Content-Disposition": _content_disposition(asset.original_filename),
            "Cache-Control": "private, no-store",
            "ETag": f'"{asset.sha256}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


def _ocr_run_read(session, organization_id: UUID, run) -> ReceiptOcrRunRead:  # noqa: ANN001
    review = receipt_asset_ocr_service.get_receipt_ocr_review(
        session, organization_id, run.id
    )
    return ReceiptOcrRunRead(
        id=run.id,
        receipt_asset_id=run.receipt_asset_id,
        agent_run_id=run.agent_run_id,
        content_sha256=run.content_sha256,
        engine=run.engine,
        engine_version=run.engine_version,
        status=run.status,
        candidates=run.candidates,
        error=run.error,
        started_at=run.started_at,
        finished_at=run.finished_at,
        created_at=run.created_at,
        review=ReceiptOcrReviewRead.model_validate(review) if review is not None else None,
    )


@router.post(
    "/receipt-assets/{asset_id}:ocr",
    response_model=ReceiptOcrRunRead,
    status_code=status.HTTP_201_CREATED,
    summary="对已保存的原始票据执行 OCR（仅返回候选字段）",
)
def extract_saved_receipt(
    asset_id: UUID,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint="POST /v1/receipt-assets/{asset_id}:ocr",
        key=idempotency_key,
        payload={"receipt_asset_id": str(asset_id)},
    )
    if (replay := guard.begin()) is not None:
        return replay
    run = receipt_asset_ocr_service.create_receipt_ocr_run(
        session,
        context.organization_id,
        asset_id,
        actor_user_id=context.user_id,
    )
    body = _ocr_run_read(session, context.organization_id, run)
    return guard.finish(status_code=status.HTTP_201_CREATED, body=body)


@router.get(
    "/receipt-assets/{asset_id}/ocr-runs",
    response_model=list[ReceiptOcrRunRead],
    summary="读取原始票据的 OCR 运行历史",
)
def list_saved_receipt_ocr_runs(
    asset_id: UUID,
    context: ContextDep,
    session: SessionDep,
):
    require_role(context, ROLE_VIEWER)
    runs = receipt_asset_ocr_service.list_receipt_ocr_runs(
        session, context.organization_id, asset_id
    )
    return [_ocr_run_read(session, context.organization_id, run) for run in runs]


@router.get(
    "/receipt-ocr-runs/{run_id}",
    response_model=ReceiptOcrRunRead,
    summary="读取 OCR 候选字段、AgentRun 追溯与人工审核结果",
)
def get_saved_receipt_ocr_run(
    run_id: UUID,
    context: ContextDep,
    session: SessionDep,
):
    require_role(context, ROLE_VIEWER)
    run = receipt_asset_ocr_service.require_receipt_ocr_run(
        session, context.organization_id, run_id
    )
    return _ocr_run_read(session, context.organization_id, run)


@router.post(
    "/receipt-ocr-runs/{run_id}:review",
    response_model=ReceiptOcrReviewRead,
    status_code=status.HTTP_201_CREATED,
    summary="人工确认或驳回 OCR 候选字段",
)
def review_saved_receipt_ocr_run(
    run_id: UUID,
    payload: ReceiptOcrReviewCreate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint="POST /v1/receipt-ocr-runs/{run_id}:review",
        key=idempotency_key,
        payload={"receipt_ocr_run_id": str(run_id), **payload.model_dump(mode="json")},
    )
    if (replay := guard.begin()) is not None:
        return replay
    fields = payload.model_dump(
        exclude={"decision", "note"},
        exclude_none=True,
        mode="json",
    )
    review = receipt_asset_ocr_service.review_receipt_ocr_run(
        session,
        context.organization_id,
        run_id,
        decision=payload.decision,
        confirmed_fields=fields,
        note=payload.note,
        actor_user_id=context.user_id,
    )
    body = ReceiptOcrReviewRead.model_validate(review)
    return guard.finish(status_code=status.HTTP_201_CREATED, body=body)



__all__ = ["router"]
