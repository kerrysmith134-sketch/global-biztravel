"""Agent 记忆服务：事实 / 过程 / 情景三层 + 混合检索 + 复盘。

设计文档 v2.0 第 9 章。要点与取舍：

**向量由本地确定性哈希生成，不依赖外部 embedding 服务。**
``GBT_LLM_PROVIDER=stub`` 时没有真实模型可用，但记忆层不能因此不存在。这里用
"分词 + 哈希 + 符号投影 + L2 归一化"生成 256 维向量：它不具备语义泛化能力，但同词/同字
必然落在同一维度上，足以支撑"关键词 + 向量"的混合检索与 P95 延迟验证。接入真实 embedding
时只替换 :func:`embed_text`，表结构与检索逻辑不变；``provider`` 记在检索结果里，避免
把哈希向量伪装成语义向量。

**检索只读当前有效条目。**
``valid_until IS NULL`` 是"这一条还算数"的唯一判据。历史版本保留在表里用于复盘与审计，
不参与召回。RRF 融合后叠加时间衰减：越近的行程权重越高，但不会把旧教训直接归零
（半个衰期 90 天，旧条目的排序仍由相关性主导）。

**复盘只写摘要，不写结论。**
复盘产物是"这次发生了什么、留下什么待办与教训"，供下一次规划读作上下文；它**不**参与
可行性判定（第 9.5 章）。可行性仍然只由求解器 + 独立验证器决定。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Text, func, select
from sqlalchemy.orm import Session

from app.api.errors import NotFound
from app.config import get_settings
from app.models.memory import (
    PlanAttemptLog,
    TravelerProfile,
    TripMemorySummary,
)
from app.models.planning import PlanVersion
from app.models.records import VisitRecord
from app.models.trip import Trip, Visit
from app.services.embedding_service import embed_text

#: 复盘的 source_ref 前缀。唯一索引靠它保证"同一次行程只复盘一次"。
REVIEW_SOURCE_PREFIX = "trip"

ATTEMPT_GENERATED = "PLAN_GENERATED"
ATTEMPT_REJECTED = "PLAN_REJECTED"
ATTEMPT_ACTIVATED = "PLAN_ACTIVATED"
ATTEMPT_SUPERSEDED = "PLAN_SUPERSEDED"



@dataclass
class MemoryHit:
    summary: TripMemorySummary
    score: float
    lexical_rank: int | None = None
    semantic_rank: int | None = None


def _now(session: Session, now: datetime | None) -> datetime:
    return now or session.scalar(select(func.clock_timestamp()))


# ---------------------------------------------------------------------------
# 事实记忆：出差人画像（版本化）
# ---------------------------------------------------------------------------
def get_profile(
    session: Session, organization_id: UUID | str, user_id: UUID | str
) -> TravelerProfile | None:
    return session.scalars(
        select(TravelerProfile)
        .where(
            TravelerProfile.organization_id == organization_id,
            TravelerProfile.user_id == user_id,
            TravelerProfile.valid_until.is_(None),
        )
        .order_by(TravelerProfile.version.desc())
        .limit(1)
    ).one_or_none()


def update_profile(
    session: Session,
    organization_id: UUID | str,
    user_id: UUID | str,
    *,
    preferences: dict[str, Any],
    notes: str | None = None,
    confidence: float = 1.0,
    nationality: str | None = None,
    clear_nationality: bool = False,
    now: datetime | None = None,
) -> TravelerProfile:
    """写入新一版画像。内容没变时返回当前版本，不制造无意义的版本号。

    ``nationality`` 传两位大写字母即设置；``clear_nationality=True`` 显式清除
    （请求里 nationality 缺省为 null 时区分"没提"与"清空"：路由用
    ``"nationality" in payload.model_fields_set`` 判定）。
    """
    current = get_profile(session, organization_id, user_id)
    merged = dict(current.preferences or {}) if current is not None else {}
    merged.update(preferences or {})
    next_nationality = (
        None
        if clear_nationality
        else (nationality.upper() if nationality else (current.nationality if current else None))
    )
    if (
        current is not None
        and merged == (current.preferences or {})
        and notes == current.notes
        and next_nationality == current.nationality
    ):
        return current

    current_time = _now(session, now)
    if current is not None:
        current.valid_until = current_time
    profile = TravelerProfile(
        organization_id=str(organization_id),
        user_id=str(user_id),
        version=(current.version + 1) if current is not None else 1,
        preferences=merged,
        notes=notes,
        nationality=next_nationality,
        confidence=max(0.0, min(1.0, confidence)),
        valid_from=current_time,
    )
    session.add(profile)
    session.flush()
    if current is not None:
        current.superseded_by = profile.id
    return profile


# ---------------------------------------------------------------------------
# 过程记忆：尝试与否决
# ---------------------------------------------------------------------------
def log_attempt(
    session: Session,
    organization_id: UUID | str,
    trip_id: UUID | str,
    *,
    attempt_type: str,
    outcome: str,
    reason: str | None = None,
    details: dict[str, Any] | None = None,
    plan_version_id: UUID | str | None = None,
) -> PlanAttemptLog:
    row = PlanAttemptLog(
        organization_id=str(organization_id),
        trip_id=str(trip_id),
        plan_version_id=None if plan_version_id is None else str(plan_version_id),
        attempt_type=attempt_type,
        outcome=outcome,
        reason=reason,
        details=details or {},
    )
    session.add(row)
    session.flush()
    return row


def list_attempts(
    session: Session, organization_id: UUID | str, trip_id: UUID | str
) -> list[PlanAttemptLog]:
    return list(
        session.scalars(
            select(PlanAttemptLog)
            .where(
                PlanAttemptLog.organization_id == organization_id,
                PlanAttemptLog.trip_id == trip_id,
            )
            .order_by(PlanAttemptLog.created_at.desc(), PlanAttemptLog.id)
        )
    )


# ---------------------------------------------------------------------------
# 情景记忆：复盘
# ---------------------------------------------------------------------------
def review_trip(
    session: Session,
    organization_id: UUID | str,
    trip_id: UUID | str,
    *,
    now: datetime | None = None,
) -> TripMemorySummary | None:
    """为一次已完成的出行生成摘要。已复盘过则直接返回现有条目（幂等）。

    先锁 Trip 行再查摘要：两个 Worker 同时复盘同一行程时，后到者会在锁上等待，随后看到
    已存在的摘要直接返回，而不是撞上 ``source_ref`` 唯一索引把事务打挂。
    """
    org = UUID(str(organization_id))
    trip_uuid = UUID(str(trip_id))
    trip = session.scalars(
        select(Trip)
        .where(Trip.organization_id == org, Trip.id == trip_uuid)
        .with_for_update()
    ).one_or_none()
    if trip is None:
        return None

    source_ref = f"{REVIEW_SOURCE_PREFIX}:{trip_uuid}:review"
    existing = session.scalars(
        select(TripMemorySummary).where(
            TripMemorySummary.organization_id == org,
            TripMemorySummary.source_ref == source_ref,
        )
    ).one_or_none()
    if existing is not None:
        return existing

    visits = list(
        session.scalars(
            select(Visit).where(Visit.organization_id == org, Visit.trip_id == trip_uuid)
        )
    )
    visit_ids = [visit.id for visit in visits]
    records = (
        list(
            session.scalars(
                select(VisitRecord)
                .where(
                    VisitRecord.organization_id == org,
                    VisitRecord.visit_id.in_(visit_ids),
                    VisitRecord.applied.is_(True),
                )
                .order_by(VisitRecord.client_updated_at)
            )
        )
        if visit_ids
        else []
    )
    attempts = list_attempts(session, org, trip_uuid)
    active_plan = session.scalars(
        select(PlanVersion).where(
            PlanVersion.organization_id == org,
            PlanVersion.trip_id == trip_uuid,
            PlanVersion.status == "ACTIVE",
        )
    ).one_or_none()

    decisions: list[str] = []
    follow_ups: list[str] = []
    lessons: list[str] = []

    if active_plan is not None:
        decisions.append(
            f"激活计划 v{active_plan.version_number}（求解器 {active_plan.solver}，"
            f"目标值 {active_plan.objective_value:.2f}）"
        )
        for item in active_plan.unplaced or []:
            lessons.append(
                f"拜访 {item.get('visit_id')} 未能排入：{item.get('reason') or 'UNKNOWN'}"
            )
    for attempt in attempts:
        if attempt.attempt_type == ATTEMPT_REJECTED:
            lessons.append(f"规划被否决（{attempt.outcome}）：{attempt.reason or '未记录原因'}")
        elif attempt.attempt_type == ATTEMPT_SUPERSEDED:
            decisions.append("旧计划版本被新版本替代，历史版本仍可回滚")

    outcome_counts: dict[str, int] = {}
    for record in records:
        outcome_counts[record.outcome] = outcome_counts.get(record.outcome, 0) + 1
        for task in record.follow_up_tasks or []:
            title = str(task.get("title") or "").strip()
            if title:
                follow_ups.append(title)
        if record.outcome in ("NOT_MET", "CANCELLED"):
            lessons.append(f"拜访结果 {record.outcome}：{record.notes or '未填写原因'}")

    done = sum(1 for visit in visits if visit.status == "DONE")
    missed = sum(1 for visit in visits if visit.status == "MISSED")
    cancelled = sum(1 for visit in visits if visit.status == "CANCELLED")
    summary = (
        f"本次出行共 {len(visits)} 个拜访：完成 {done}、未会面 {missed}、取消 {cancelled}；"
        f"现场记录 {len(records)} 条，规划尝试 {len(attempts)} 次。"
    )
    if outcome_counts:
        summary += " 结果分布：" + "、".join(
            f"{key}×{value}" for key, value in sorted(outcome_counts.items())
        ) + "。"

    payload = " ".join([summary, *decisions, *follow_ups, *lessons])
    current_time = _now(session, now)
    item = TripMemorySummary(
        organization_id=org,
        trip_id=trip_uuid,
        kind="TRIP_REVIEW",
        summary=summary,
        decisions=decisions,
        follow_ups=follow_ups,
        lessons=lessons,
        embedding=embed_text(payload),
        confidence=0.85 if records else 0.7,
        source_ref=source_ref,
        valid_from=current_time,
    )
    session.add(item)
    session.flush()

    # 事实记忆的轻量学习：最常用的交通方式与平均停留时长。只写"可观察的统计"，
    # 不从几次记录里推断性格/偏好，避免把噪声写成画像。
    if trip.owner_user_id is not None and visits:
        modes = [visit.movement_mode for visit in visits]
        preferred_mode = max(set(modes), key=modes.count)
        average_service = round(sum(visit.service_minutes for visit in visits) / len(visits), 1)
        update_profile(
            session,
            org,
            trip.owner_user_id,
            preferences={
                "preferred_movement_mode": preferred_mode,
                "average_service_minutes": average_service,
            },
            confidence=0.6,
            now=current_time,
        )

    return item


def review_completed_trips(
    session: Session, organization_id: UUID | str, *, limit: int = 20, now: datetime | None = None
) -> int:
    """复盘所有已完成但尚无摘要的出行，返回本轮生成条数。"""
    org = UUID(str(organization_id))
    reviewed = 0
    reviewed_exists = (
        select(TripMemorySummary.id)
        .where(
            TripMemorySummary.organization_id == org,
            TripMemorySummary.source_ref == func.concat("trip:", Trip.id, ":review"),
        )
        .exists()
    )
    trips = list(
        session.scalars(
            select(Trip)
            .where(
                Trip.organization_id == org,
                Trip.status == "COMPLETED",
                ~reviewed_exists,
            )
            .order_by(Trip.window_end)
            .limit(limit)
        )
    )
    for trip in trips:
        if review_trip(session, org, trip.id, now=now) is not None:
            reviewed += 1
    return reviewed


# ---------------------------------------------------------------------------
# 读取与混合检索
# ---------------------------------------------------------------------------
def get_trip_memory(
    session: Session, organization_id: UUID | str, trip_id: UUID | str
) -> tuple[list[TripMemorySummary], list[PlanAttemptLog]]:
    summaries = list(
        session.scalars(
            select(TripMemorySummary)
            .where(
                TripMemorySummary.organization_id == organization_id,
                TripMemorySummary.trip_id == trip_id,
                TripMemorySummary.valid_until.is_(None),
            )
            .order_by(TripMemorySummary.created_at.desc())
        )
    )
    return summaries, list_attempts(session, organization_id, trip_id)


def forget_summary(
    session: Session,
    organization_id: UUID | str,
    summary_id: UUID | str,
    *,
    now: datetime | None = None,
) -> TripMemorySummary:
    """合规删除一条记忆（设计文档 §9.3 / §11.4）。

    不是"软删除"：摘要正文、决策、待办、教训与向量全部清空，只保留一条不可再召回的空壳
    用于审计"何时删除过"。只改 ``valid_until`` 而保留正文不满足"被遗忘权"。
    """
    item = session.scalars(
        select(TripMemorySummary)
        .where(
            TripMemorySummary.organization_id == str(organization_id),
            TripMemorySummary.id == str(summary_id),
        )
        .with_for_update()
    ).one_or_none()
    if item is None:
        raise NotFound("记忆条目不存在", details={"summary_id": str(summary_id)})
    item.valid_until = _now(session, now)
    item.summary = "[已按合规要求删除]"
    item.decisions = []
    item.follow_ups = []
    item.lessons = []
    item.embedding = None
    item.superseded_by = None
    session.flush()
    return item


def search_memory(
    session: Session,
    organization_id: UUID | str,
    query: str,
    *,
    trip_id: UUID | str | None = None,
    limit: int = 5,
    now: datetime | None = None,
) -> list[MemoryHit]:
    """关键词 + 向量 + RRF + 时间衰减。

    ``limit`` 是最终返回条数；两路召回各取 ``limit * 4`` 条，保证融合池足够大。
    """
    text = (query or "").strip()
    if not text:
        return []
    settings = get_settings()
    org = UUID(str(organization_id))
    pool = max(limit * 4, 10)
    # 用户输入里的 % / _ 是 LIKE 通配符，必须转义，否则一次检索会变成全表扫描。
    needle = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{needle}%"

    base_filters = [
        TripMemorySummary.organization_id == org,
        TripMemorySummary.valid_until.is_(None),
    ]
    if trip_id is not None:
        base_filters.append(TripMemorySummary.trip_id == str(trip_id))

    lexical_stmt = (
        select(TripMemorySummary)
        .where(
            *base_filters,
            TripMemorySummary.summary.ilike(pattern, escape="\\")
            | TripMemorySummary.decisions.cast(Text).ilike(pattern, escape="\\")
            | TripMemorySummary.follow_ups.cast(Text).ilike(pattern, escape="\\")
            | TripMemorySummary.lessons.cast(Text).ilike(pattern, escape="\\"),
        )
        .order_by(TripMemorySummary.created_at.desc())
        .limit(pool)
    )
    lexical = list(session.scalars(lexical_stmt))

    vector = embed_text(text)
    semantic_stmt = (
        select(TripMemorySummary)
        .where(*base_filters, TripMemorySummary.embedding.is_not(None))
        .order_by(TripMemorySummary.embedding.cosine_distance(vector))
        .limit(pool)
    )
    semantic = list(session.scalars(semantic_stmt))

    scores: dict[UUID, MemoryHit] = {}
    for rank, item in enumerate(lexical):
        hit = scores.setdefault(item.id, MemoryHit(summary=item, score=0.0))
        hit.lexical_rank = rank
        hit.score += 1.0 / (60 + rank)
    for rank, item in enumerate(semantic):
        hit = scores.setdefault(item.id, MemoryHit(summary=item, score=0.0))
        hit.semantic_rank = rank
        hit.score += 1.0 / (60 + rank)

    current = _now(session, now)
    half_life = max(settings.memory_time_decay_half_life_days, 1)
    for hit in scores.values():
        age_days = max((current - hit.summary.created_at).total_seconds() / 86400, 0)
        decay = 0.5 ** (age_days / half_life)
        hit.score = hit.score * decay
    return sorted(scores.values(), key=lambda hit: hit.score, reverse=True)[:limit]


def build_weekly_briefing(
    session: Session, organization_id: UUID | str, *, now: datetime | None = None
) -> str:
    """给指标/晨报用的轻量摘要：最近 7 天生成的复盘条数与待办数。

    不是验收硬指标，只是把记忆层暴露给运维/产品看的一个窗口；放在服务层便于测试。
    """
    current = _now(session, now)
    since = current - timedelta(days=7)
    rows = list(
        session.scalars(
            select(TripMemorySummary).where(
                TripMemorySummary.organization_id == organization_id,
                TripMemorySummary.valid_until.is_(None),
                TripMemorySummary.created_at >= since,
            )
        )
    )
    follow_ups = sum(len(item.follow_ups or []) for item in rows)
    return f"最近 7 天生成记忆 {len(rows)} 条，其中待办 {follow_ups} 项。"


__all__ = [
    "ATTEMPT_ACTIVATED",
    "ATTEMPT_GENERATED",
    "ATTEMPT_REJECTED",
    "ATTEMPT_SUPERSEDED",
    "MemoryHit",
    "REVIEW_SOURCE_PREFIX",
    "build_weekly_briefing",
    "embed_text",
    "forget_summary",
    "get_profile",
    "get_trip_memory",
    "list_attempts",
    "log_attempt",
    "review_completed_trips",
    "review_trip",
    "search_memory",
    "update_profile",
]
