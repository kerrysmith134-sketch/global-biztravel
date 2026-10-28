"""Excel 两阶段导入的 HTTP 接口（设计文档 v2.0 第 4.4、5.4 章）。

接口分两跳，对应"先看清再落库"：

1. ``POST /trips/{trip_id}/imports:parse``：上传文件，**只读**解析。返回逐行预览与
   按问题码聚合的校验报告；业务数据一个字节都不写库。客户端可以反复刷新预览
   （``GET /imports/{import_id}``），不会产生副作用。
2. ``POST /trips/{trip_id}/imports:confirm``：携带 ``import_id`` 与用户勾选的行号，
   服务端才真正创建客户/地点/拜访。**同一批次只能确认一次**（第二次 409）。

两个接口都接受可选的 ``Idempotency-Key``：网络重试不会产生第二批导入，也不会重复建拜访。
指纹刻意用"内容指纹"而不是原始请求体——``parse`` 用 ``(filename, sha256)``，
``confirm`` 用 ``(import_id, 勾选行号)``；这样同一个文件重传、同一个确认重发都能被识别为重放。
"""

from __future__ import annotations

import hashlib
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, UploadFile, status

from app.api.deps import (
    ROLE_TRAVELER,
    ROLE_VIEWER,
    ContextDep,
    IdempotencyKeyDep,
    SessionDep,
    require_role,
)
from app.api.errors import ApiError, NotFound
from app.api.idempotent import IdempotentWrite
from app.api.schemas import (
    ImportConfirmRead,
    ImportConfirmRequest,
    ImportParseRead,
    ImportRowRead,
)
from app.models.trip import ImportBatch, ImportRow
from app.services import import_service

router = APIRouter(tags=["imports"])

#: 上传体积上限。客户名单是"几十行到几千行"的小表，10 MiB 足够；超限直接 413，
#: 避免把一个集装箱大小的文件读进内存。
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

PARSE_ENDPOINT = "POST /v1/trips/{trip_id}/imports:parse"
CONFIRM_ENDPOINT = "POST /v1/trips/{trip_id}/imports:confirm"


def import_read(batch: ImportBatch, rows: list[ImportRow]) -> ImportParseRead:
    """把批次 + 行记录组装成预览读模型（parse 首答与 GET 预览共用同一函数）。"""
    report = batch.report or {}
    columns = report.get("columns") or {}
    return ImportParseRead(
        import_id=batch.id,
        trip_id=batch.trip_id,
        filename=batch.filename,
        file_sha256=batch.file_sha256,
        status=batch.status,
        total_rows=batch.total_rows,
        valid_rows=batch.valid_rows,
        invalid_rows=batch.invalid_rows,
        duplicate_rows=batch.duplicate_rows,
        columns=list(columns.get("sheet") or []),
        missing_columns=list(columns.get("missing_required") or []),
        unknown_columns=list(columns.get("unknown") or []),
        rows=[
            ImportRowRead(
                row_number=row.row_number,
                status=row.status,
                issues=list(row.issues or []),
                raw=dict(row.raw or {}),
                normalized=row.normalized,
            )
            for row in rows
        ],
        report=report,
    )


@router.post(
    "/trips/{trip_id}/imports:parse",
    response_model=ImportParseRead,
    status_code=status.HTTP_201_CREATED,
    summary="上传并解析客户名单（只读预览）",
)
def parse_import(
    trip_id: UUID,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
    file: Annotated[UploadFile, File(description=".xlsx / .xlsm 客户名单")],
):
    """解析上传的 Excel 并返回逐行预览与校验报告。**此调用不创建任何客户或拜访。**"""
    require_role(context, ROLE_TRAVELER)
    filename = file.filename or "upload.xlsx"
    data = file.file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise ApiError(
            "文件超过上传上限",
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            code="PAYLOAD_TOO_LARGE",
            details={"max_bytes": MAX_UPLOAD_BYTES, "bytes": len(data), "filename": filename},
        )

    # 指纹刻意用"内容指纹"而不是原始请求体：``(filename, sha256)`` 让同一个文件重传被识别为重放。
    # sha256 必须在解析之前算出来（解析本身要建批次行），所以这里对上传字节先算一次——
    # 与 :func:`app.services.import_service.parse_import` 内部同一算法，指纹因此一致。
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=PARSE_ENDPOINT,
        key=idempotency_key,
        payload={
            "trip_id": str(trip_id),
            "filename": filename,
            "file_sha256": hashlib.sha256(data).hexdigest(),
        },
    )
    # 先检查、后解析：否则重传同一文件会在库里再落一批 ImportBatch + ImportRow。
    if (replay := guard.begin()) is not None:
        return replay

    batch, rows = import_service.parse_import(
        session,
        context.organization_id,
        trip_id,
        filename=filename,
        data=data,
        created_by_user_id=context.user_id,
    )
    body = import_read(batch, rows)
    return guard.finish(status_code=status.HTTP_201_CREATED, body=body)


@router.post(
    "/trips/{trip_id}/imports:confirm",
    response_model=ImportConfirmRead,
    status_code=status.HTTP_200_OK,
    summary="确认导入（按勾选行号落库）",
)
def confirm_import(
    trip_id: UUID,
    payload: ImportConfirmRequest,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    """把用户勾选的行真正落库。``accepted_row_numbers`` 为空表示"接受全部有效行"。

    重复确认同一批次返回 409 ``IMPORT_ALREADY_CONFIRMED``——落库不是幂等操作，
    必须让客户端知道"这次没有产生新数据，是第二次提交"。
    """
    require_role(context, ROLE_TRAVELER)
    # 指纹是"勾选内容"而不是原始请求体：``(import_id, 勾选行号)``——同一确认重发才是重放，
    # 改了勾选就是另一次真实的确认。
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=CONFIRM_ENDPOINT,
        key=idempotency_key,
        payload={
            "trip_id": str(trip_id),
            "import_id": str(payload.import_id),
            "accepted_row_numbers": sorted(payload.accepted_row_numbers),
        },
    )
    # 先检查、后落库：否则重发确认会再建一批客户/地点/拜访（第二次确认必然 409，但重放不该走到那里）。
    if (replay := guard.begin()) is not None:
        return replay
    result = import_service.confirm_import(
        session,
        context.organization_id,
        trip_id,
        import_id=payload.import_id,
        accepted_row_numbers=payload.accepted_row_numbers,
    )
    body = ImportConfirmRead.model_validate(result)
    return guard.finish(status_code=status.HTTP_200_OK, body=body)


@router.get(
    "/imports/{import_id}",
    response_model=ImportParseRead,
    summary="导入批次详情（刷新预览页）",
)
def get_import(import_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_VIEWER)
    batch = import_service.get_batch(session, context.organization_id, import_id)
    if batch is None:
        raise NotFound("导入批次不存在", details={"import_id": str(import_id)})
    return import_read(batch, import_service.batch_rows(session, batch.id))