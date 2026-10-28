"""指标服务：从真实业务表计算指标，并按窗口留存快照。

设计文档 v2.0 第 16 章。指标全部**从库里算**，不接受外部上报——上报的数字无法审计，
也无法回答"这个比率的分母是什么"。每个指标都带 ``sample_size``，让"0% 降级率"和
"没有任何调用"能被区分开。

指标树（与设计文档一致）：

| 维度 | 指标键 | 目标 |
|---|---|---|
| 规划质量 | ``planning.validator_pass_rate`` | ≥ 0.90 |
| 规划质量 | ``planning.activation_rate`` | 记录基线 |
| 主动式价值 | ``proactive.adoption_rate`` | ≥ 0.30 |
| 主动式价值 | ``proactive.false_positive_rate`` | ≤ 0.20 |
| 可靠性 | ``reliability.job_success_rate`` | ≥ 0.99 |
| 可靠性 | ``reliability.dlq_rate`` | ≤ 0.01 |
| AI 质量 | ``ai.degraded_rate`` | ≤ 0.05 |
| AI 质量 | ``ai.cache_hit_rate`` | 记录基线 |
| 记忆 | ``memory.summaries_total`` | 记录基线 |
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import distinct, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.llm import LLMInvocation
from app.models.memory import TripMemorySummary
from app.models.metrics import MetricSnapshot
from app.models.planning import PlanVersion
from app.models.proactive import ProactiveSuggestion
from app.models.reliability import BackgroundJob, OutboxEvent


@dataclass(frozen=True)
class Metric:
    key: str
    value: float
    sample_size: int
    target: str | None = None
    unit: str = "ratio"


def _ratio(numerator: int, denominator: int) -> tuple[float, int]:
    if denominator <= 0:
        return 0.0, 0
    return round(numerator / denominator, 4), denominator


def compute_metrics(session: Session, organization_id: UUID | str) -> list[Metric]:
    org = str(organization_id)
    metrics: list[Metric] = []

    # ---------------------------------------------------------- 规划质量
    plan_total = int(
        session.scalar(
            select(func.count()).select_from(PlanVersion).where(PlanVersion.organization_id == org)
        )
        or 0
    )
    feasible_total = int(
        session.scalar(
            select(func.count())
            .select_from(PlanVersion)
            .where(
                PlanVersion.organization_id == org,
                PlanVersion.validation["feasible"].astext == "true",
            )
        )
        or 0
    )
    value, sample = _ratio(feasible_total, plan_total)
    metrics.append(Metric("planning.validator_pass_rate", value, sample, ">= 0.90"))

    trips_with_plan = int(
        session.scalar(
            select(func.count(distinct(PlanVersion.trip_id))).where(
                PlanVersion.organization_id == org
            )
        )
        or 0
    )
    active_trips = int(
        session.scalar(
            select(func.count(distinct(PlanVersion.trip_id))).where(
                PlanVersion.organization_id == org,
                PlanVersion.status == "ACTIVE",
            )
        )
        or 0
    )
    value, sample = _ratio(active_trips, trips_with_plan)
    metrics.append(Metric("planning.activation_rate", value, sample, None))

    # -------------------------------------------------------- 主动式价值
    rows = session.execute(
        select(ProactiveSuggestion.status, func.count())
        .where(
            ProactiveSuggestion.organization_id == org,
            ProactiveSuggestion.status != "SUPPRESSED",
            ProactiveSuggestion.push_status != "WITHHELD",
        )
        .group_by(ProactiveSuggestion.status)
    ).all()
    counts = {status: int(count) for status, count in rows}
    proposed = sum(counts.values())
    adopted = counts.get("APPROVED", 0) + counts.get("EXECUTED", 0)
    value, sample = _ratio(adopted, proposed)
    metrics.append(Metric("proactive.adoption_rate", value, sample, ">= 0.30"))

    executed_or_dismissed = counts.get("EXECUTED", 0) + counts.get("DISMISSED", 0)
    value, sample = _ratio(counts.get("DISMISSED", 0), executed_or_dismissed)
    metrics.append(Metric("proactive.false_positive_rate", value, sample, "<= 0.20"))

    # ---------------------------------------------------------- 可靠性
    job_rows = session.execute(
        select(BackgroundJob.status, func.count())
        .where(BackgroundJob.organization_id == org)
        .group_by(BackgroundJob.status)
    ).all()
    jobs = {status: int(count) for status, count in job_rows}
    finished = jobs.get("SUCCEEDED", 0) + jobs.get("FAILED", 0) + jobs.get("DEAD", 0)
    value, sample = _ratio(jobs.get("SUCCEEDED", 0), finished)
    metrics.append(Metric("reliability.job_success_rate", value, sample, ">= 0.99"))

    outbox_total = int(
        session.scalar(
            select(func.count()).select_from(OutboxEvent).where(OutboxEvent.organization_id == org)
        )
        or 0
    )
    dead = int(
        session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(OutboxEvent.organization_id == org, OutboxEvent.status == "DEAD")
        )
        or 0
    )
    value, sample = _ratio(dead, outbox_total)
    metrics.append(Metric("reliability.dlq_rate", value, sample, "<= 0.01"))

    # ------------------------------------------------------------ AI 质量
    llm_total = int(
        session.scalar(
            select(func.count()).select_from(LLMInvocation).where(LLMInvocation.organization_id == org)
        )
        or 0
    )
    degraded = int(
        session.scalar(
            select(func.count())
            .select_from(LLMInvocation)
            .where(LLMInvocation.organization_id == org, LLMInvocation.degraded.is_(True))
        )
        or 0
    )
    value, sample = _ratio(degraded, llm_total)
    metrics.append(Metric("ai.degraded_rate", value, sample, "<= 0.05"))

    cached = int(
        session.scalar(
            select(func.count())
            .select_from(LLMInvocation)
            .where(LLMInvocation.organization_id == org, LLMInvocation.cached.is_(True))
        )
        or 0
    )
    value, sample = _ratio(cached, llm_total)
    metrics.append(Metric("ai.cache_hit_rate", value, sample, None))

    # -------------------------------------------------------------- 记忆
    summaries = int(
        session.scalar(
            select(func.count())
            .select_from(TripMemorySummary)
            .where(
                TripMemorySummary.organization_id == org,
                TripMemorySummary.valid_until.is_(None),
            )
        )
        or 0
    )
    metrics.append(Metric("memory.summaries_total", float(summaries), summaries, None, unit="count"))

    return metrics


def snapshot(
    session: Session, organization_id: UUID | str, *, now: datetime | None = None
) -> list[MetricSnapshot]:
    """把当前指标写成"今日到此刻"的快照；同窗口重跑覆盖而不是堆重复行。"""
    current = now or session.scalar(select(func.clock_timestamp()))
    window_start = session.scalar(select(func.date_trunc("day", current)))
    # 窗口结束固定为次日零点，而不是"此刻"：否则每次调用都会生成不同窗口，唯一键失效、
    # 快照变成一条条重复行。指标是"这一天截至当前"的累计，窗口边界必须稳定。
    window_end = window_start + timedelta(days=1)
    written: list[MetricSnapshot] = []
    for metric in compute_metrics(session, organization_id):
        statement = (
            pg_insert(MetricSnapshot.__table__)
            .values(
                organization_id=str(organization_id),
                metric_key=metric.key,
                value=metric.value,
                sample_size=metric.sample_size,
                window_start=window_start,
                window_end=window_end,
                dimensions={},
            )
            .on_conflict_do_update(
                constraint="uq_metric_snapshots_org_key_window",
                set_={
                    "value": metric.value,
                    "sample_size": metric.sample_size,
                    "dimensions": {},
                    "created_at": func.now(),
                },
            )
            .returning(MetricSnapshot.__table__.c.id)
        )
        snapshot_id = session.execute(statement).scalar_one()
        row = session.get(MetricSnapshot, snapshot_id)
        if row is not None:
            written.append(row)
    session.flush()
    return written


def list_snapshots(
    session: Session,
    organization_id: UUID | str,
    *,
    metric_key: str | None = None,
    limit: int = 100,
) -> list[MetricSnapshot]:
    stmt = select(MetricSnapshot).where(MetricSnapshot.organization_id == str(organization_id))
    if metric_key:
        stmt = stmt.where(MetricSnapshot.metric_key == metric_key)
    stmt = stmt.order_by(MetricSnapshot.created_at.desc()).limit(limit)
    return list(session.scalars(stmt))


__all__ = ["Metric", "compute_metrics", "list_snapshots", "snapshot"]
