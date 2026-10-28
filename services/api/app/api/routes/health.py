"""健康检查与就绪探针。

契约（设计文档 v2.0 第 5 章）：``GET /v1/health`` **不要求身份头**——容器编排的健康探针
不该需要业务身份。它只回答两个问题：进程活着吗、数据库连得上吗。数据库不可用时返回
``200 + status="degraded"`` 而不是 5xx，因为"能响应但依赖降级"与"进程已死"是两种不同的
运维信号，混在一起会让探针无法区分。
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter
from sqlalchemy import text

from app import __version__
from app.api.schemas import HealthRead
from app.config import get_settings
from app.db import app_engine

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthRead, summary="健康检查")
def health() -> HealthRead:
    settings = get_settings()
    database = "up"
    status = "ok"
    try:
        with app_engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 —— 探针必须把任何连接失败都转成降级信号
        database = "down"
        status = "degraded"
    return HealthRead(
        status=status,
        env=settings.env,
        version=__version__,
        database=database,
        time=datetime.now(UTC),
    )