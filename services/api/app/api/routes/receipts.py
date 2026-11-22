"""私有原始票据上传与读取接口。"""

from __future__ import annotations

import hashlib
import re
from pathlib import PurePath
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
from app.api.schemas import ReceiptAssetRead
from app.config import get_settings
from app.services import receipt_asset_service, trip_service

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
    file: UploadFile = File(...),
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


__all__ = ["router"]
