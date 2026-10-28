"""指标接口：实时汇总、快照与历史。

设计文档 v2.0 第 16 章。实时汇总是"现在算"，快照是"存一份证据"；两者用同一个
:func:`app.services.metrics_service.compute_metrics`，避免看板与留存数据出现两套口径。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import ROLE_ADMIN, ROLE_VIEWER, ContextDep, SessionDep, require_role
from app.api.schemas import MetricRead, MetricSnapshotRead, MetricsSummaryRead
from app.services import metrics_service

router = APIRouter(tags=["metrics"])


@router.get("/metrics/summary", response_model=MetricsSummaryRead, summary="实时指标汇总")
def metrics_summary(context: ContextDep, session: SessionDep):
    require_role(context, ROLE_VIEWER)
    metrics = metrics_service.compute_metrics(session, context.organization_id)
    return MetricsSummaryRead(
        generated_at=datetime.now(UTC),
        metrics=[
            MetricRead(
                key=metric.key,
                value=metric.value,
                sample_size=metric.sample_size,
                target=metric.target,
                unit=metric.unit,
            )
            for metric in metrics
        ],
    )


@router.post(
    "/metrics:snapshot",
    response_model=list[MetricSnapshotRead],
    summary="写入当前窗口的指标快照（同窗口可重跑覆盖）",
)
def create_snapshot(context: ContextDep, session: SessionDep):
    require_role(context, ROLE_ADMIN)
    rows = metrics_service.snapshot(session, context.organization_id)
    return [MetricSnapshotRead.model_validate(row) for row in rows]


@router.get(
    "/metrics/snapshots",
    response_model=list[MetricSnapshotRead],
    summary="指标快照历史（最新在前）",
)
def list_snapshots(
    context: ContextDep,
    session: SessionDep,
    key: Annotated[str | None, Query(description="按指标键过滤")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
):
    require_role(context, ROLE_VIEWER)
    rows = metrics_service.list_snapshots(
        session, context.organization_id, metric_key=key, limit=limit
    )
    return [MetricSnapshotRead.model_validate(row) for row in rows]
