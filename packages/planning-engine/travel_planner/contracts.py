"""规划引擎输入/输出契约 v1.0（冻结）。

对应设计文档 `docs/design/global-business-travel-os-design-v2.0.md` 第 6.3 节。

不变量 I1：LLM 负责理解、研究、协调和解释；优化算法负责计算；独立验证器负责检查结果。
任何 LLM 都不能仅凭自己的判断宣布行程可行。

本模块是**纯数据契约**：没有 IO、没有数据库、没有网络、没有时钟依赖。
"""

from __future__ import annotations

import hashlib
import json
from datetime import date as date_type
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

STRICT = ConfigDict(frozen=True, extra="forbid")

HASH_PREFIX = "sha256:"

# ---------------------------------------------------------------------------
# 独立验证器规则名（冻结；validator.py 与 API 层共用）
# ---------------------------------------------------------------------------
RULE_STOP_COVERAGE = "STOP_COVERAGE"
RULE_NO_DUPLICATE_STOP = "NO_DUPLICATE_STOP"
RULE_DAY_WITHIN_WINDOW = "DAY_WITHIN_WINDOW"
RULE_TIME_WINDOW = "TIME_WINDOW"
RULE_SERVICE_DURATION = "SERVICE_DURATION"
RULE_TRAVEL_TIME_CONSISTENCY = "TRAVEL_TIME_CONSISTENCY"
RULE_MAX_STOPS_PER_DAY = "MAX_STOPS_PER_DAY"
RULE_LOCKED_ORDER = "LOCKED_ORDER"
RULE_OBJECTIVE_RECOMPUTED = "OBJECTIVE_RECOMPUTED"

#: 验证器必须逐条输出的规则，顺序冻结（便于 API 与前端稳定渲染）
VALIDATION_RULES: tuple[str, ...] = (
    RULE_STOP_COVERAGE,
    RULE_NO_DUPLICATE_STOP,
    RULE_DAY_WITHIN_WINDOW,
    RULE_TIME_WINDOW,
    RULE_SERVICE_DURATION,
    RULE_TRAVEL_TIME_CONSISTENCY,
    RULE_MAX_STOPS_PER_DAY,
    RULE_LOCKED_ORDER,
    RULE_OBJECTIVE_RECOMPUTED,
)


class MovementMode(StrEnum):
    """城际/市内移动方式。影响行程时间估算，不影响可行性判定。"""

    GROUND = "GROUND"
    RAIL = "RAIL"
    AIR = "AIR"
    SEA = "SEA"


class SolverKind(StrEnum):
    ORTOOLS = "ortools"
    HEURISTIC = "heuristic"


class CheckStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class UnplacedReason(StrEnum):
    """未排入行程的原因。契约冻结为三种。"""

    NO_FEASIBLE_SLOT = "NO_FEASIBLE_SLOT"
    TIME_WINDOW_INFEASIBLE = "TIME_WINDOW_INFEASIBLE"
    CAPACITY_EXCEEDED = "CAPACITY_EXCEEDED"


class PrimaryObjective(StrEnum):
    MINIMIZE_TRAVEL_AND_MAXIMIZE_VALUE = "MINIMIZE_TRAVEL_AND_MAXIMIZE_VALUE"


class TimeWindow(BaseModel):
    model_config = STRICT

    start: datetime
    end: datetime

    @model_validator(mode="after")
    def _ordered(self) -> TimeWindow:
        if self.end < self.start:
            raise ValueError("time window end must not precede start")
        return self


class Origin(BaseModel):
    model_config = STRICT

    place_id: UUID
    lat: float
    lon: float


class ProblemWindow(BaseModel):
    model_config = STRICT

    start: datetime
    end: datetime

    @model_validator(mode="after")
    def _ordered(self) -> ProblemWindow:
        if self.end <= self.start:
            raise ValueError("trip window end must be after start")
        return self


class StopInput(BaseModel):
    model_config = STRICT

    visit_id: UUID
    place_id: UUID
    lat: float
    lon: float
    service_minutes: int = Field(default=60, ge=0)
    time_windows: tuple[TimeWindow, ...] = ()
    priority: int = Field(default=2, ge=1)
    business_value: float = Field(default=0.0, ge=0.0)
    movement_mode: MovementMode = MovementMode.GROUND


class ObjectiveWeights(BaseModel):
    model_config = STRICT

    travel: float = 1.0
    value: float = 0.001


class Objective(BaseModel):
    model_config = STRICT

    primary: PrimaryObjective = PrimaryObjective.MINIMIZE_TRAVEL_AND_MAXIMIZE_VALUE
    weights: ObjectiveWeights = ObjectiveWeights()


class Constraints(BaseModel):
    model_config = STRICT

    max_stops_per_day: int = Field(default=4, ge=1)
    immovable_stop_ids: tuple[UUID, ...] = ()
    locked_order: tuple[UUID, ...] = ()


class PlanningProblem(BaseModel):
    """规范化后的规划输入。所有字段都是显式的、可哈希的、可重放的。"""

    model_config = STRICT

    trip_id: UUID
    organization_id: UUID
    timezone_home: str
    origin: Origin
    window: ProblemWindow
    stops: tuple[StopInput, ...]
    objective: Objective = Objective()
    constraints: Constraints = Constraints()
    solver_budget_seconds: int = Field(default=10, ge=1)

    @model_validator(mode="after")
    def _consistent(self) -> PlanningProblem:
        ids = [stop.visit_id for stop in self.stops]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate visit_id in stops")
        known = set(ids)

        unknown_immovable = set(self.constraints.immovable_stop_ids) - known
        if unknown_immovable:
            raise ValueError(
                "immovable_stop_ids not present in stops: "
                f"{sorted(str(v) for v in unknown_immovable)}"
            )
        locked = self.constraints.locked_order
        if len(set(locked)) != len(locked):
            raise ValueError("locked_order contains duplicate visit_id")
        unknown_locked = set(locked) - known
        if unknown_locked:
            raise ValueError(
                f"locked_order not present in stops: {sorted(str(v) for v in unknown_locked)}"
            )
        return self

    def problem_hash(self) -> str:
        """内容寻址哈希：相同问题必得相同哈希，用于缓存与幂等。

        `solver_budget_seconds` 计入哈希，因为它会改变求解搜索预算、进而可能改变输出。
        """
        return compute_problem_hash(self)

    def stop_by_id(self, visit_id: UUID) -> StopInput:
        for stop in self.stops:
            if stop.visit_id == visit_id:
                return stop
        raise KeyError(str(visit_id))


def canonical_problem_payload(problem: PlanningProblem) -> str:
    """规范化 JSON：键排序 + 紧凑分隔符 + 不做 ASCII 转义。

    必须与 API 层 `app.api.idempotent.canonical_json` 使用同一套序列化约定，
    否则同一问题在两个层会算出不同哈希。
    """
    payload = problem.model_dump(mode="json")
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_problem_hash(problem: PlanningProblem) -> str:
    canonical = canonical_problem_payload(problem)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{HASH_PREFIX}{digest}"


class StopPlan(BaseModel):
    model_config = STRICT

    visit_id: UUID
    sequence: int = Field(ge=1)
    arrive: datetime
    depart: datetime
    travel_minutes: int = Field(ge=0)


class DayPlan(BaseModel):
    model_config = STRICT

    date: date_type
    stops: tuple[StopPlan, ...] = ()


class UnplacedStop(BaseModel):
    model_config = STRICT

    visit_id: UUID
    reason: UnplacedReason


class ValidationCheck(BaseModel):
    model_config = STRICT

    rule: str
    status: CheckStatus
    detail: str | None = None


class ValidationReport(BaseModel):
    model_config = STRICT

    feasible: bool
    checks: tuple[ValidationCheck, ...] = ()

    @property
    def failures(self) -> tuple[ValidationCheck, ...]:
        return tuple(c for c in self.checks if c.status is CheckStatus.FAIL)


class PlanningResult(BaseModel):
    """规划输出。`validation.feasible` 只能由独立验证器写入。"""

    model_config = STRICT

    trip_id: UUID
    problem_hash: str
    solver: SolverKind
    days: tuple[DayPlan, ...] = ()
    objective_value: float
    validation: ValidationReport
    unplaced: tuple[UnplacedStop, ...] = ()
    degraded: bool = False
    explanation_ref: str | None = None

    @property
    def placed_visit_ids(self) -> tuple[UUID, ...]:
        return tuple(stop.visit_id for day in self.days for stop in day.stops)

    @property
    def unplaced_visit_ids(self) -> tuple[UUID, ...]:
        return tuple(item.visit_id for item in self.unplaced)