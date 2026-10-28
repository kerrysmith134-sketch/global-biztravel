"""计划版本域模型：``PlanVersion`` / ``PlanStop``。对应迁移 ``0004_planning_reliability``。

不可变快照的语义（设计文档 v2.0 第 9 章）：

- 每次"生成计划"落一份**新的、此后不再修改**的 ``PlanVersion``；旧版本绝不原地改写，只被
  标记 ``SUPERSEDED``。于是历史计划可回看、可逐版本对比、可作为审计证据。
- ``version_number`` 在 ``trip_id`` 内递增，由 ``persist_plan`` 在锁住 Trip 行之后计算，
  与 ``uq_plan_versions_trip_id_version_number`` 一起保证并发下不重号。
- 同一出行至多一份 ``ACTIVE``：由部分唯一索引 ``uq_plan_versions_trip_id_active`` 强制，
  并发激活时由数据库裁决，而不是应用层"先查后写"。
- ``problem_hash`` 是**内容寻址**：同一问题输入必然得到同一 hash，用于结果去重、
  以及回答"这份计划是依据什么问题算出来的"。
- ``trip_version`` 记录生成时的 Trip 乐观并发令牌；激活时比对，用于检测"生成期间 Trip 已被改"，
  从而拒绝把一份基于旧输入的计划改成生效计划。
- ``plan_stops`` 是计划里**落到分钟的时刻表**：``arrive_at``/``depart_at`` 由
  ``packages/planning-engine`` 的 ``build_schedule`` 物化，规划引擎本身不碰数据库。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    event,
    inspect,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.models.base import Base, created_at, updated_at, uuid_pk

PLAN_VERSION_STATUSES = ("DRAFT", "ACTIVE", "SUPERSEDED")
PLAN_SOLVERS = ("ortools", "heuristic")


class PlanVersion(Base):
    __tablename__ = "plan_versions"
    __table_args__ = (
        UniqueConstraint("trip_id", "version_number"),
        CheckConstraint("status IN ('DRAFT','ACTIVE','SUPERSEDED')", name="status_allowed"),
        CheckConstraint("solver IN ('ortools','heuristic')", name="solver_allowed"),
        CheckConstraint("version_number >= 1", name="version_number_positive"),
        CheckConstraint("trip_version >= 1", name="trip_version_positive"),
        CheckConstraint(
            "solution_status IN ('OPTIMAL','FEASIBLE','PARTIAL','INFEASIBLE','UNKNOWN','ERROR')",
            name="solution_status_allowed",
        ),
        CheckConstraint("cost_unknown_count >= 0", name="cost_unknown_count_non_negative"),
        # 每个出行至多一份生效计划：并发激活由数据库说了算。
        Index(
            "uq_plan_versions_trip_id_active",
            "trip_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    trip_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="DRAFT")
    problem_hash: Mapped[str] = mapped_column(Text, nullable=False)
    solver: Mapped[str] = mapped_column(Text, nullable=False)
    objective_value: Mapped[float] = mapped_column(nullable=False, server_default="0")
    solution_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="FEASIBLE")
    cost_total: Mapped[float | None] = mapped_column(Float)
    cost_currency: Mapped[str | None] = mapped_column(CHAR(3))
    cost_unknown_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    degraded: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    trip_version: Mapped[int] = mapped_column(Integer, nullable=False)
    validation: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    unplaced: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    explanation_ref: Mapped[str | None] = mapped_column(Text)
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("background_jobs.id", ondelete="SET NULL"), index=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class PlanStop(Base):
    __tablename__ = "plan_stops"
    __table_args__ = (
        UniqueConstraint("plan_version_id", "day_index", "sequence"),
        UniqueConstraint("plan_version_id", "visit_id"),
        CheckConstraint("day_index >= 0", name="day_index_non_negative"),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        CheckConstraint("travel_minutes >= 0", name="travel_minutes_non_negative"),
        CheckConstraint("service_minutes >= 0", name="service_minutes_non_negative"),
        CheckConstraint("depart_at >= arrive_at", name="depart_after_arrive"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    plan_version_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("plan_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    visit_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("visits.id", ondelete="CASCADE"), nullable=False, index=True
    )
    day_index: Mapped[int] = mapped_column(Integer, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    day_date: Mapped[date] = mapped_column(Date, nullable=False)
    arrive_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    depart_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    travel_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    service_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    distance_km: Mapped[float | None] = mapped_column(Float)
    cost_amount: Mapped[float | None] = mapped_column(Float)
    cost_currency: Mapped[str | None] = mapped_column(CHAR(3))
    travel_source: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="haversine-estimate"
    )
    travel_stale: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = created_at()


class PlanTravelLeg(Base):
    """一段交通；起点/终点是 `plan_stops` 的真实外键（PRD DDL §4.3）。"""

    __tablename__ = "plan_travel_legs"
    __table_args__ = (
        CheckConstraint("day_index >= 0", name="day_index_non_negative"),
        CheckConstraint("travel_minutes >= 0", name="travel_minutes_non_negative"),
        CheckConstraint("distance_km >= 0", name="distance_non_negative"),
        CheckConstraint("mode IN ('GROUND','RAIL','AIR','SEA')", name="mode_allowed"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    plan_version_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("plan_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    from_stop_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("plan_stops.id", ondelete="CASCADE")
    )
    to_stop_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("plan_stops.id", ondelete="CASCADE"), index=True
    )
    day_index: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    travel_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    distance_km: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    cost_amount: Mapped[float | None] = mapped_column(Float)
    cost_currency: Mapped[str | None] = mapped_column(CHAR(3))
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default="haversine-estimate")
    stale: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = created_at()


# ------------------------------------------------------------------ 不可变守卫
# 设计文档 I2「计划不可变、操作可审计」在这里落成一条**真能拦下来**的规则：Session 的
# ``before_flush`` 钩子。为什么不把不可变交给"服务层自觉"：不可变是数据的属性，不是调用
# 约定——只要有一条新写的路径绕过它，历史计划就被原地改写了，而这件事恰恰要在事后逐版本
# 对比和审计时才被发现。挂在 Session 上是让**所有** ORM 写路径共用同一道闸门。
#
# 刻意留白两处，都在下面写明理由：
#   1. 允许生命周期字段变更。激活/作废的本质是"把同一份快照的状态往前推"，它不改内容；
#      若连 status 也锁死，激活就无法在同一事务里完成，只能退化成"先删后插"，反而破坏快照。
#   2. 只拦 ORM，不拦原生 SQL。数据库触发器能连原生 SQL 一起拦，但代价是
#      ``DELETE FROM trips`` 的级联删除也会被拦（``plan_stops`` 上任何 BEFORE DELETE 触发器
#      都会在级联时触发）。原生 SQL 在本仓库只出现在一次性运维脚本与集成测试清理里，
#      由代码评审兜底；把只读的审计证据保护到"应用永远改不动"，收益远大于把运维一起封死。
LIFECYCLE_FIELDS: frozenset[str] = frozenset(
    {"status", "activated_at", "superseded_at", "updated_at"}
)

#: ``PlanVersion`` 的**内容**字段：一经落库即不可改写。既是守卫的白名单补集，也是
#: 回归测试的断言对象（新加列若忘了归类，测试会失败，而不是静默变成可改字段）。
PLAN_VERSION_CONTENT_FIELDS: tuple[str, ...] = (
    "organization_id",
    "trip_id",
    "version_number",
    "problem_hash",
    "solver",
    "objective_value",
    "solution_status",
    "cost_total",
    "cost_currency",
    "cost_unknown_count",
    "degraded",
    "trip_version",
    "validation",
    "unplaced",
    "explanation_ref",
    "job_id",
    "created_by_user_id",
    "created_at",
)


class PlanVersionImmutable(RuntimeError):
    """试图原地改写已落库的计划版本或它的停靠。

    这是**程序性错误**（写路径违反了不变量），不是客户端能触发的业务错误，因此不走
    :class:`app.api.errors.ApiError`：模型层不依赖 API 层，且真要发生时应以 500 暴露出来，
    而不是伪装成一个"客户端可以重试"的 4xx。
    """


def _changed_fields(obj: object) -> list[str]:
    return sorted(attr.key for attr in inspect(obj).attrs if attr.history.has_changes())


@event.listens_for(Session, "before_flush")
def _guard_plan_immutability(session: Session, flush_context: object, instances: object) -> None:
    """在 flush 前拦下对计划快照的 UPDATE / DELETE。INSERT 不受影响。"""
    for obj in session.dirty:
        if isinstance(obj, PlanVersion):
            illegal = sorted(set(_changed_fields(obj)) - LIFECYCLE_FIELDS)
            if illegal:
                raise PlanVersionImmutable(
                    f"计划版本内容不可改写（plan_version_id={obj.id}）：{', '.join(illegal)}"
                )
        elif isinstance(obj, PlanStop):
            changed = _changed_fields(obj)
            if changed:
                # PlanStop 没有生命周期字段：整行都是内容，任何改动都是违反。
                raise PlanVersionImmutable(
                    f"计划停靠不可改写（plan_stop_id={obj.id}）：{', '.join(changed)}"
                )
        elif isinstance(obj, PlanTravelLeg):
            changed = _changed_fields(obj)
            if changed:
                raise PlanVersionImmutable(
                    f"计划交通段不可改写（plan_travel_leg_id={obj.id}）：{', '.join(changed)}"
                )

    for obj in session.deleted:
        if isinstance(obj, PlanStop):
            raise PlanVersionImmutable(f"计划停靠不可删除（plan_stop_id={obj.id}）")
        if isinstance(obj, PlanTravelLeg):
            raise PlanVersionImmutable(f"计划交通段不可删除（plan_travel_leg_id={obj.id}）")
        if isinstance(obj, PlanVersion):
            raise PlanVersionImmutable(f"计划版本不可删除（plan_version_id={obj.id}）")