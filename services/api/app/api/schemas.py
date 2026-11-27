"""API 契约的 Pydantic 模型（设计文档 v2.0 第 5 章）。

约定：
- 所有枚举以**字符串字面量**暴露，与数据库 CHECK 约束一致；客户端做分支判断时依赖它。
- ``from_attributes=True`` 让模型可直接由 ORM 对象构造，避免在路由里手写字段搬运。
- 分页统一 ``{"items": [...], "next_cursor": null}``。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from re import fullmatch
from typing import Any, Generic, Literal, TypeVar
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

T = TypeVar("T")

TripStatus = Literal["DRAFT", "PLANNING", "READY", "ACTIVE", "HOLD", "COMPLETED", "CANCELLED"]
VisitStatus = Literal["PLANNED", "SCHEDULED", "DONE", "MISSED", "CANCELLED"]
MovementMode = Literal["GROUND", "RAIL", "AIR", "SEA"]
GeocodeStatus = Literal["MISSING", "RESOLVED", "FAILED"]
Tier = Literal["A", "B", "C"]
CustomerStatus = Literal["ACTIVE", "INACTIVE", "PROSPECT"]
Severity = Literal["ERROR", "WARNING"]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None = None


class HealthRead(BaseModel):
    status: str
    env: str
    version: str
    database: str
    time: datetime


# ------------------------------------------------------------------ places
class PlaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    address_line: str | None = None
    city: str | None = None
    region: str | None = None
    country_code: str | None = Field(default=None, max_length=2)
    timezone: str | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)

    @model_validator(mode="after")
    def _coords_come_in_pairs(self) -> PlaceCreate:
        if (self.lat is None) != (self.lon is None):
            raise ValueError("lat 与 lon 必须同时提供或同时省略")
        if self.country_code is not None:
            object.__setattr__(self, "country_code", self.country_code.upper())
        return self


class PlaceRead(ORMModel):
    id: UUID
    name: str
    address_line: str | None
    city: str | None
    region: str | None
    country_code: str | None
    timezone: str | None
    lat: float | None
    lon: float | None
    geocode_status: GeocodeStatus
    geocode_source: str | None
    geocode_confidence: Decimal | None
    created_at: datetime
    updated_at: datetime


class CustomerPlaceRead(BaseModel):
    """客户的一个地址（PRD P0-3：一家企业可有多个地址）。

    ``geocode_source``/``geocode_confidence`` 即"信息来源及可信度"——地址坐标从哪来、有多可靠。
    """

    place_id: UUID
    label: str | None = None
    is_primary: bool = False
    name: str
    address_line: str | None = None
    city: str | None = None
    region: str | None = None
    country_code: str | None = None
    lat: float | None = None
    lon: float | None = None
    geocode_status: GeocodeStatus
    geocode_source: str | None = None
    geocode_confidence: Decimal | None = None


class CustomerPlaceCreate(BaseModel):
    place_id: UUID
    label: str | None = Field(default=None, max_length=120)
    is_primary: bool = False


# --------------------------------------------------------------- customers
class CustomerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    external_ref: str | None = Field(default=None, max_length=64)
    tier: Tier = "C"
    country_code: str | None = Field(default=None, max_length=2)
    industry: str | None = None
    primary_contact_name: str | None = None
    primary_contact_email: str | None = Field(default=None, max_length=254)
    primary_contact_phone: str | None = None
    status: CustomerStatus = "ACTIVE"
    notes: str | None = None


class CustomerRead(ORMModel):
    id: UUID
    name: str
    external_ref: str | None
    tier: Tier
    country_code: str | None
    industry: str | None
    primary_contact_name: str | None
    primary_contact_email: str | None
    primary_contact_phone: str | None
    status: CustomerStatus
    owner_user_id: UUID | None
    created_at: datetime
    updated_at: datetime


class CustomerMapCustomerRead(BaseModel):
    """地图最小客户投影：不返回联系人等与地图无关的个人信息。"""

    id: UUID
    name: str
    tier: Tier
    country_code: str | None = None
    industry: str | None = None
    status: CustomerStatus


class CustomerMapPlaceRead(BaseModel):
    id: UUID
    label: str | None = None
    is_primary: bool = False
    name: str
    address_line: str | None = None
    city: str | None = None
    region: str | None = None
    country_code: str | None = None
    timezone: str | None = None
    lat: float | None = None
    lon: float | None = None
    geocode_status: GeocodeStatus
    geocode_source: str | None = None
    geocode_confidence: Decimal | None = None


class CustomerMapLocationRead(BaseModel):
    customer: CustomerMapCustomerRead
    place: CustomerMapPlaceRead


class CustomerMapCountryCoverageRead(BaseModel):
    """按实际关联地点归集的国家客户覆盖；客户按国家去重，不表示外部市场规模。"""

    country_code: str
    customers_total: int = 0
    tier_a_customers: int = 0
    tier_b_customers: int = 0
    tier_c_customers: int = 0
    active_customers: int = 0
    prospect_customers: int = 0
    inactive_customers: int = 0
    locations_total: int = 0
    locations_mapped: int = 0


class CustomerMapSummaryRead(BaseModel):
    customers_with_locations: int = 0
    customers_without_locations: int = 0
    locations_total: int = 0
    locations_mapped: int = 0
    locations_missing_coordinates: int = 0
    countries: dict[str, int] = Field(default_factory=dict)
    country_stats: list[CustomerMapCountryCoverageRead] = Field(default_factory=list)


class CustomerMapLocationsRead(BaseModel):
    items: list[CustomerMapLocationRead]
    summary: CustomerMapSummaryRead
    next_cursor: str | None = None


# ------------------------------------------------------------------- trips
class TripCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    purpose: str | None = None
    origin_place_id: UUID | None = None
    home_timezone: str | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    window_start: datetime
    window_end: datetime
    countries: list[str] = Field(default_factory=list)
    budget_amount: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _window_ordered(self) -> TripCreate:
        if self.window_end <= self.window_start:
            raise ValueError("window_end 必须晚于 window_start")
        return self


class TripUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    purpose: str | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    countries: list[str] | None = None
    budget_amount: Decimal | None = Field(default=None, ge=0)
    status: TripStatus | None = None


class TripRead(ORMModel):
    id: UUID
    name: str
    purpose: str | None
    owner_user_id: UUID | None
    origin_place_id: UUID | None
    home_timezone: str
    currency: str
    window_start: datetime
    window_end: datetime
    countries: list[str]
    status: TripStatus
    budget_amount: Decimal | None
    version: int
    active_plan_version_id: UUID | None
    created_at: datetime
    updated_at: datetime
    visit_count: int = 0
    customer_count: int = 0
    plan_version_count: int = 0


# ------------------------------------------------------------------ visits
class VisitCreate(BaseModel):
    place_id: UUID
    customer_id: UUID | None = None
    title: str | None = None
    objective: str | None = Field(default=None, max_length=2000)
    service_minutes: int = Field(default=60, gt=0, le=24 * 60)
    time_window_start: datetime | None = None
    time_window_end: datetime | None = None
    priority: int = Field(default=2, ge=1, le=3)
    business_value: Decimal = Field(default=Decimal("0"), ge=0)
    movement_mode: MovementMode = "GROUND"
    is_immovable: bool = False

    @model_validator(mode="after")
    def _window_ordered(self) -> VisitCreate:
        if (
            self.time_window_start is not None
            and self.time_window_end is not None
            and self.time_window_end <= self.time_window_start
        ):
            raise ValueError("time_window_end 必须晚于 time_window_start")
        return self


class VisitUpdate(BaseModel):
    customer_id: UUID | None = None
    title: str | None = None
    objective: str | None = Field(default=None, max_length=2000)
    service_minutes: int | None = Field(default=None, gt=0, le=24 * 60)
    time_window_start: datetime | None = None
    time_window_end: datetime | None = None
    priority: int | None = Field(default=None, ge=1, le=3)
    business_value: Decimal | None = Field(default=None, ge=0)
    movement_mode: MovementMode | None = None
    is_immovable: bool | None = None
    status: VisitStatus | None = None


class VisitRead(ORMModel):
    id: UUID
    trip_id: UUID
    customer_id: UUID | None
    place_id: UUID
    title: str | None
    objective: str | None = None
    service_minutes: int
    time_window_start: datetime | None
    time_window_end: datetime | None
    priority: int
    business_value: Decimal
    movement_mode: MovementMode
    is_immovable: bool
    status: VisitStatus
    client_updated_at: datetime | None
    created_at: datetime
    updated_at: datetime


class BriefingCustomerRead(BaseModel):
    id: UUID
    name: str
    external_ref: str | None = None
    tier: Tier
    country_code: str | None = None
    industry: str | None = None
    status: CustomerStatus
    primary_contact_name: str | None = None
    primary_contact_email: str | None = None
    primary_contact_phone: str | None = None
    notes: str | None = None


class BriefingPlaceRead(BaseModel):
    id: UUID
    name: str
    address_line: str | None = None
    city: str | None = None
    region: str | None = None
    country_code: str | None = None
    timezone: str | None = None
    lat: float | None = None
    lon: float | None = None
    geocode_status: str


class BriefingHistoryRead(BaseModel):
    visit_id: UUID
    trip_id: UUID
    trip_name: str
    date: date
    title: str | None = None
    status: VisitStatus
    outcome: str | None = None
    notes: str | None = None


class BriefingFollowUpRead(BaseModel):
    title: str
    due_date: str | None = None
    trip_id: UUID
    trip_name: str
    visit_id: UUID


class BriefingMemoryRead(BaseModel):
    id: UUID
    kind: str
    summary: str
    decisions: list[str] = Field(default_factory=list)
    follow_ups: list[str] = Field(default_factory=list)
    lessons: list[str] = Field(default_factory=list)
    confidence: float
    valid_from: datetime


class BriefingGapRead(BaseModel):
    code: str
    message: str


class VisitBriefingRead(BaseModel):
    visit: VisitRead
    customer: BriefingCustomerRead | None = None
    place: BriefingPlaceRead | None = None
    history: list[BriefingHistoryRead] = Field(default_factory=list)
    follow_ups: list[BriefingFollowUpRead] = Field(default_factory=list)
    memories: list[BriefingMemoryRead] = Field(default_factory=list)
    data_gaps: list[BriefingGapRead] = Field(default_factory=list)


# ------------------------------------------------------------ excel imports
class ImportIssue(BaseModel):
    code: str
    field: str | None = None
    message: str
    severity: Severity


class ImportRowRead(BaseModel):
    row_number: int
    status: str
    issues: list[ImportIssue] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)
    normalized: dict[str, Any] | None = None


class ImportParseRead(BaseModel):
    import_id: UUID
    trip_id: UUID
    filename: str
    file_sha256: str
    status: str
    total_rows: int
    valid_rows: int
    invalid_rows: int
    duplicate_rows: int
    columns: list[str] = Field(default_factory=list)
    missing_columns: list[str] = Field(default_factory=list)
    unknown_columns: list[str] = Field(default_factory=list)
    rows: list[ImportRowRead] = Field(default_factory=list)
    #: 结构化校验报告：按问题码聚合计数，并给出每类问题的说明与建议动作。
    report: dict[str, Any] = Field(default_factory=dict)


class ImportConfirmRequest(BaseModel):
    import_id: UUID
    accepted_row_numbers: list[int] = Field(default_factory=list)


class ImportConfirmRead(BaseModel):
    import_id: UUID
    status: str
    accepted_rows: int
    rejected_rows: int
    created_customers: int
    created_places: int
    created_visits: int
    reused_customers: int
    reused_places: int
    warnings: list[str] = Field(default_factory=list)


# ------------------------------------------------------------------- plans
PlanVersionStatus = Literal["DRAFT", "ACTIVE", "SUPERSEDED"]
PlanSolutionStatus = Literal["OPTIMAL", "FEASIBLE", "PARTIAL", "INFEASIBLE", "UNKNOWN", "ERROR"]
PlanSolver = Literal["ortools", "heuristic"]
JobType = Literal["plan_trip", "draft_minutes", "crm_sync", "fx_rate_sync", "research_execute"]
JobStatus = Literal["PENDING", "RUNNING", "SUCCEEDED", "FAILED", "DEAD", "CANCELLED"]


class FxSyncRequest(BaseModel):
    """行情同步请求：把 `symbols` 的最新行情写进组织 `fx_rates`（ADR-0033）。

    ``symbols`` 是显式的管理员意图（同步哪些币种），行情没覆盖的币种进任务结果
    `missing`，绝不伪造。无行情覆盖保证的币种（ECB 目录外）会一直留在缺口里。
    """

    base_currency: str = Field(min_length=3, max_length=3, pattern="^[A-Za-z]{3}$")
    symbols: list[str] = Field(min_length=1, max_length=32)


class FxSyncRead(BaseModel):
    """``fx-rates:sync`` 的受理响应；结果经 ``GET /v1/jobs/{job_id}`` 轮询。"""

    job_id: UUID
    status: JobStatus
    base_currency: str
    symbols: list[str]
    reused: bool = False


class PlanGenerateRead(BaseModel):
    """``plans:generate`` 的受理响应。

    返回 **202 + job_id** 而不是计划本身：求解要走 OR-Tools，耗时不可预期，不能把 HTTP 请求
    挂在那里等。客户端拿 ``job_id`` 去轮询 ``GET /v1/jobs/{job_id}``，或等出行状态变化。
    ``reused=true`` 表示这次请求复用了已经在途的同一任务（同一出行同一版本重复点了按钮）。
    """

    job_id: UUID
    trip_id: UUID
    trip_version: int
    status: JobStatus
    reused: bool = False


class JobRead(ORMModel):
    id: UUID
    job_type: JobType
    status: JobStatus
    attempts: int
    max_attempts: int
    payload: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    last_error: str | None
    available_at: datetime
    lease_expires_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PlanStopRead(ORMModel):
    id: UUID
    visit_id: UUID
    day_index: int
    sequence: int
    day_date: date
    arrive_at: datetime
    depart_at: datetime
    travel_minutes: int
    service_minutes: int
    distance_km: float | None = None
    cost_amount: float | None = None
    cost_currency: str | None = None
    travel_source: str = "haversine-estimate"
    travel_stale: bool = False
    place_id: UUID | None = None
    place_name: str | None = None
    city: str | None = None
    country_code: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class PlanTravelLegRead(ORMModel):
    id: UUID
    from_stop_id: UUID | None = None
    to_stop_id: UUID
    day_index: int
    mode: MovementMode
    travel_minutes: int
    distance_km: float
    cost_amount: float | None = None
    cost_currency: str | None = None
    source: str
    stale: bool


class PlanUnplacedRead(BaseModel):
    """未排进计划的拜访（求解器没放下 + 输入阶段就被排除，用 ``source`` 区分）。

    契约此前写成 ``list[dict]``，实际结构固定（见 ``planning_service._unplaced_payload``）：
    客户端要按 ``visit_id`` 回到具体拜访，靠 ``reason`` 解释为什么没排上。
    """

    visit_id: str
    reason: str
    source: str
    detail: str | None = None


class PlanVersionRead(ORMModel):
    id: UUID
    trip_id: UUID
    version_number: int
    status: PlanVersionStatus
    problem_hash: str
    solver: PlanSolver
    objective_value: float
    solution_status: PlanSolutionStatus = "FEASIBLE"
    cost_total: float | None = None
    cost_currency: str | None = None
    cost_unknown_count: int = 0
    degraded: bool
    trip_version: int
    validation: dict[str, Any] = Field(default_factory=dict)
    unplaced: list[PlanUnplacedRead] = Field(default_factory=list)
    policy_violations: list[PolicyViolationRead] = Field(default_factory=list)
    explanation_ref: str | None
    job_id: UUID | None
    stop_count: int = 0
    day_count: int = 0
    activated_at: datetime | None
    superseded_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PlanVersionDetailRead(PlanVersionRead):
    stops: list[PlanStopRead] = Field(default_factory=list)
    legs: list[PlanTravelLegRead] = Field(default_factory=list)


# --------------------------------------------- plan versions: activate & diff
#: 单个停靠在两版计划之间的变更类型。语义见 ``plan_change_service._stop_delta``：
#: 位置变了算 ``MOVED``（时刻随之变是结果，不再单独报 ``RETIMED``）。
PlanStopChange = Literal["ADDED", "REMOVED", "MOVED", "RETIMED", "UNCHANGED"]
#: 未排项只有进出两个方向。
PlanUnplacedAction = Literal["ADDED", "REMOVED"]


class PlanActivationRead(BaseModel):
    """``plans/{plan_version_id}:activate`` 的响应。

    返回激活**之后**的事实，而不是"请求已接受"：激活是同步的、事务性的（只有求解是异步的），
    响应里给出目标版本、被顶替的上一版、出行状态与两侧的版本号，客户端无需再回查就能更新本地状态。
    ``trip_version`` 与 ``plan_trip_version`` 都回传，是因为二者在激活前后**应当相等**——
    客户端可以据此断言"生效的计划确实基于当前的出行输入"。
    """

    trip_id: UUID
    plan_version_id: UUID
    version_number: int
    status: PlanVersionStatus
    superseded_plan_version_id: UUID | None = None
    trip_status: TripStatus
    trip_version: int
    plan_trip_version: int
    activated_at: datetime


class PlanStopDeltaRead(BaseModel):
    """一个拜访在两版计划里的对照。缺失的一侧留 ``None``（不塞哨兵值）。"""

    visit_id: UUID
    change: PlanStopChange
    day_index_from: int | None = None
    day_index_to: int | None = None
    sequence_from: int | None = None
    sequence_to: int | None = None
    arrive_at_from: datetime | None = None
    arrive_at_to: datetime | None = None
    depart_at_from: datetime | None = None
    depart_at_to: datetime | None = None
    travel_minutes_from: int | None = None
    travel_minutes_to: int | None = None
    service_minutes_from: int | None = None
    service_minutes_to: int | None = None


class PlanUnplacedDeltaRead(BaseModel):
    """一个未排拜访在基准版与目标版之间的进出。"""

    visit_id: UUID
    action: PlanUnplacedAction
    reason: str | None = None
    source: str | None = None


class PlanVersionDiffRead(BaseModel):
    """``plans/{plan_version_id}:diff`` 的响应。

    以路径里那份计划为主语（target），``against`` 为基准（base）。``summary`` 是各变更类型的
    计数（键为小写变更名 + ``total``），供客户端一句话概括"改了多少"。
    """

    trip_id: UUID
    base_plan_version_id: UUID
    target_plan_version_id: UUID
    base_version_number: int
    target_version_number: int
    base_status: PlanVersionStatus
    target_status: PlanVersionStatus
    base_solver: PlanSolver
    target_solver: PlanSolver
    base_degraded: bool
    target_degraded: bool
    objective_from: float
    objective_to: float
    objective_delta: float
    travel_minutes_from: int
    travel_minutes_to: int
    travel_minutes_delta: int
    stops: list[PlanStopDeltaRead] = Field(default_factory=list)
    unplaced: list[PlanUnplacedDeltaRead] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)

# ------------------------------------------------------ proactive suggestions
SuggestionStatus = Literal[
    "PENDING", "SUPPRESSED", "APPROVED", "EXECUTED", "DISMISSED", "EXPIRED"
]
SuggestionPushStatus = Literal["PENDING", "PUSHED", "WITHHELD"]
SuggestionSeverity = Literal["LOW", "MEDIUM", "HIGH"]
SuggestionSeverityWire = SuggestionSeverity


class SuggestionEvidenceRead(BaseModel):
    """一条证据。``retrieved_at`` 只在真的检索过外部/缓存数据时才有值。"""

    kind: str | None = None
    ref: str | None = None
    retrieved_at: datetime | None = None


class ProposedActionRead(BaseModel):
    """建议被批准后系统打算做的动作。``requires_approval=true`` 时只是草稿。"""

    kind: str | None = None
    tool: str | None = None
    params: dict[str, Any] | None = None


class ProactiveSuggestionRead(ORMModel):
    id: UUID
    trip_id: UUID
    suggestion_type: str
    status: SuggestionStatus
    title: str
    rationale: str
    evidence: list[SuggestionEvidenceRead] = Field(default_factory=list)
    proposed_action: ProposedActionRead = Field(default_factory=ProposedActionRead)
    confidence: float
    requires_approval: bool
    severity: SuggestionSeverityWire
    dedupe_key: str
    created_at: datetime


class DailyCapRead(BaseModel):
    used: int
    cap: int


class SuggestionFeedRead(BaseModel):
    """``GET /v1/proactive/suggestions`` 的分页壳。

    与通用 ``Page`` 不同，这个列表额外携带"今日打扰额度"——客户端 Today 页要同时展示
    "还剩几次提醒"，把它放进同一响应可以少一次往返。
    """

    items: list[ProactiveSuggestionRead] = Field(default_factory=list)
    daily_cap: DailyCapRead
    next_cursor: str | None = None


class SuggestionApprovalRead(BaseModel):
    approved_by: UUID | None = None
    approved_at: datetime | None = None


class SuggestionExecutionRead(BaseModel):
    status: str | None = None
    detail: str | None = None


class SuggestionApproveRead(BaseModel):
    id: UUID
    status: SuggestionStatus
    approval: SuggestionApprovalRead | None = None
    execution: SuggestionExecutionRead | None = None
    executed: bool = True
    approval_count: int = 1
    required_approvals: int = 1


class SuggestionDismissRequest(BaseModel):
    """驳回原因可选。契约没有强制请求体；空 body 与 ``{}`` 都应被接受。"""

    reason: str | None = Field(default=None, max_length=500)


class MemoryFeedbackRead(BaseModel):
    type: str | None = None
    weight_delta: float | None = None


class SuggestionDismissRead(BaseModel):
    id: UUID
    status: SuggestionStatus
    dismissed_reason: str | None = None
    memory_feedback: MemoryFeedbackRead | None = None


# ------------------------------------------------------- visit records
VisitOutcome = Literal["MET", "NOT_MET", "RESCHEDULED", "CANCELLED"]


class FollowUpTaskRead(BaseModel):
    title: str
    due_date: str | None = None


class VisitRecordCreate(BaseModel):
    """``POST /v1/visits/{visit_id}/records`` 请求体。

    ``client_record_id`` 是客户端生成并持久化的幂等键（离线队列的 item id 与它相同）。
    ``client_updated_at`` 是 LWW 冲突判定的依据，必须由客户端给出；服务端收到的时间
    只用于审计，不参与"谁后写"的判断。
    """

    client_record_id: str = Field(min_length=1, max_length=100)
    outcome: VisitOutcome
    notes: str = ""
    client_updated_at: datetime
    photos: list[str] = Field(default_factory=list)
    follow_up_tasks: list[FollowUpTaskRead] = Field(default_factory=list)


class ConflictRecordRead(BaseModel):
    """冲突双方的快照。客户端把 ``server`` 与 ``client`` 并排展示，由人决定怎么处理。"""

    reason: str | None = None
    server: dict[str, Any] | None = None
    client: dict[str, Any] | None = None


class VisitRecordRead(BaseModel):
    id: UUID
    visit_id: UUID
    client_record_id: str
    outcome: VisitOutcome
    server_version: int
    applied: bool
    conflict: ConflictRecordRead | None = None
    created_at: datetime



# --------------------------------------------------------- visit follow-ups
VisitFollowUpSource = Literal["RECORD", "MINUTES_DRAFT", "APPOINTMENT_RECOVERY", "MANUAL"]
VisitFollowUpSuggestionSource = Literal["RECORD", "MINUTES_DRAFT", "APPOINTMENT_RECOVERY"]
VisitFollowUpDecision = Literal["ACCEPT", "DISMISS", "CREATE"]
VisitFollowUpStatus = Literal["OPEN", "IN_PROGRESS", "COMPLETED", "DISMISSED"]


class VisitFollowUpEventRead(BaseModel):
    id: UUID
    event_type: str
    from_status: str | None = None
    to_status: str
    actor_user_id: UUID | None = None
    note: str | None = None
    created_at: datetime


class VisitFollowUpActionContextRead(BaseModel):
    integrity_status: str | None = None
    action_code: str | None = None
    priority: str | None = None
    decision_status: str | None = None
    appointment_status: str | None = None
    rationale: str | None = None
    required_inputs: list[str] = Field(default_factory=list)
    recommended_steps: list[str] = Field(default_factory=list)


class CustomerAppointmentWindowAttestationCreate(BaseModel):
    start_at: datetime
    end_at: datetime
    timezone: str = Field(min_length=1, max_length=80)
    confirmation_method: Literal["EMAIL", "PHONE", "WHATSAPP", "CRM", "IN_PERSON", "OTHER"]
    confirmation_reference: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def _validate_customer_window(self) -> CustomerAppointmentWindowAttestationCreate:
        if self.start_at.tzinfo is None or self.start_at.utcoffset() is None:
            raise ValueError("start_at 必须包含明确时区偏移")
        if self.end_at.tzinfo is None or self.end_at.utcoffset() is None:
            raise ValueError("end_at 必须包含明确时区偏移")
        if self.end_at <= self.start_at:
            raise ValueError("预约窗口结束时间必须晚于开始时间")
        try:
            customer_zone = ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("timezone 必须是有效的 IANA 时区标识") from None
        for field_name, value in (("start_at", self.start_at), ("end_at", self.end_at)):
            if value.astimezone(customer_zone).utcoffset() != value.utcoffset():
                raise ValueError(f"{field_name} 的 UTC offset 与 timezone 不一致")
        if self.start_at <= datetime.now(ZoneInfo("UTC")):
            raise ValueError("客户预约窗口开始时间必须在未来")
        return self


class AppointmentRecoveryReassessmentCreate(BaseModel):
    customer_window: CustomerAppointmentWindowAttestationCreate | None = None
    replacement_flight_segment_id: UUID | None = None


class AppointmentRecoveryReassessmentPointRead(BaseModel):
    appointment_status: str | None = None
    projected_arrival_at: datetime | None = None
    delay_minutes: int | None = None
    time_window_start: datetime | None = None
    time_window_end: datetime | None = None
    arrival_before_window_start: bool | None = None


class PlanChangeRecommendationRead(BaseModel):
    operation_type: Literal["CHANGE_VISIT_TIME"]
    visit_id: UUID
    base_plan_version_id: UUID
    base_trip_version: int
    new_window_start: datetime
    new_window_end: datetime
    timezone: str | None = None
    reason: str
    requires_human_submission: bool = True


class AppointmentRecoveryReassessmentSummaryRead(BaseModel):
    workflow_id: UUID
    agent_run_id: UUID
    outcome: str
    decision: str
    created_at: datetime
    input_sha256: str
    current_plan_version_id: UUID | None = None
    appointment_status: str | None = None
    projected_arrival_at: datetime | None = None
    delay_minutes: int | None = None
    customer_window_verification_status: Literal["PARTIALLY_VERIFIED", "NOT_PROVIDED"]
    evidence_ids: list[UUID] = Field(default_factory=list)
    plan_change_recommendation: PlanChangeRecommendationRead | None = None


class AppointmentRecoveryReassessmentRead(BaseModel):
    follow_up_id: UUID
    workflow_id: UUID
    agent_run_id: UUID
    outcome: str
    decision: str
    source_signal_id: UUID
    flight_operational_update_id: UUID
    flight_segment_id: UUID
    current_plan_version_id: UUID | None = None
    input_sha256: str
    before: AppointmentRecoveryReassessmentPointRead
    after: AppointmentRecoveryReassessmentPointRead
    changed_fields: list[str] = Field(default_factory=list)
    data_gaps: list[str] = Field(default_factory=list)
    customer_window_verification_status: Literal["PARTIALLY_VERIFIED", "NOT_PROVIDED"]
    evidence_ids: list[UUID] = Field(default_factory=list)
    plan_change_recommendation: PlanChangeRecommendationRead | None = None
    created_at: datetime
    guardrails: dict[str, bool]


class VisitFollowUpRead(ORMModel):
    id: UUID
    visit_id: UUID
    trip_id: UUID
    source_type: VisitFollowUpSource
    source_record_id: UUID | None = None
    source_agent_run_id: UUID | None = None
    source_index: int | None = None
    source_title: str | None = None
    source_due_date: str | None = None
    source_action_context: VisitFollowUpActionContextRead | None = None
    latest_reassessment: AppointmentRecoveryReassessmentSummaryRead | None = None
    title: str
    due_date: str | None = None
    status: VisitFollowUpStatus
    created_by_user_id: UUID | None = None
    confirmed_by_user_id: UUID | None = None
    confirmed_at: datetime | None = None
    completed_by_user_id: UUID | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    events: list[VisitFollowUpEventRead] = Field(default_factory=list)


class VisitFollowUpSuggestionRead(BaseModel):
    source_type: VisitFollowUpSuggestionSource
    source_record_id: UUID | None = None
    source_agent_run_id: UUID | None = None
    source_index: int
    title: str
    due_date: str | None = None
    source_created_at: datetime
    action_code: str | None = None
    priority: str | None = None
    decision_status: str | None = None
    appointment_status: str | None = None
    rationale: str | None = None
    required_inputs: list[str] = Field(default_factory=list)
    recommended_steps: list[str] = Field(default_factory=list)


class VisitFollowUpFeedRead(BaseModel):
    visit_id: UUID
    trip_id: UUID
    items: list[VisitFollowUpRead] = Field(default_factory=list)
    suggestions: list[VisitFollowUpSuggestionRead] = Field(default_factory=list)


class VisitFollowUpDecisionCreate(BaseModel):
    source_type: VisitFollowUpSource
    decision: VisitFollowUpDecision
    source_record_id: UUID | None = None
    source_agent_run_id: UUID | None = None
    source_index: int | None = Field(default=None, ge=0)
    title: str | None = Field(default=None, min_length=1, max_length=500)
    due_date: str | None = Field(default=None, max_length=40)
    note: str | None = Field(default=None, max_length=2000)


class VisitFollowUpUpdate(BaseModel):
    status: VisitFollowUpStatus | None = None
    title: str | None = Field(default=None, min_length=1, max_length=500)
    due_date: str | None = Field(default=None, max_length=40)
    note: str | None = Field(default=None, max_length=2000)


# ------------------------------------------------------------ offline sync
SyncEntity = Literal["visit_record", "customer", "plan_stop"]
SyncOperation = Literal["upsert", "delete"]
SyncItemStatus = Literal["applied", "conflict", "rejected"]


class SyncItemRequest(BaseModel):
    item_id: str = Field(min_length=1, max_length=100)
    entity: SyncEntity
    operation: SyncOperation = "upsert"
    payload: dict[str, Any] = Field(default_factory=dict)
    client_updated_at: datetime | None = None
    base_version: int | None = None


class SyncPushRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=200)
    items: list[SyncItemRequest] = Field(min_length=1, max_length=200)


class SyncItemResultRead(BaseModel):
    item_id: str
    entity: SyncEntity
    status: SyncItemStatus
    server_version: int | None = None
    conflict: ConflictRecordRead | None = None


class SyncSummaryRead(BaseModel):
    applied: int
    conflict: int
    rejected: int


class SyncPushRead(BaseModel):
    """逐项结果。**部分成功是可接受的**——离线队列按每一行的 verdict 决定去留。"""

    results: list[SyncItemResultRead]
    summary: SyncSummaryRead


# --------------------------------------------------------------- memory
class MemorySummaryRead(BaseModel):
    id: UUID
    kind: str
    summary: str
    decisions: list[str] = Field(default_factory=list)
    follow_ups: list[str] = Field(default_factory=list)
    lessons: list[str] = Field(default_factory=list)
    valid_from: datetime | None = None
    confidence: float


class MemoryProcedureRead(BaseModel):
    attempt_id: UUID
    outcome: str
    reason: str | None = None
    created_at: datetime


class TripMemoryRead(BaseModel):
    trip_id: UUID
    summaries: list[MemorySummaryRead] = Field(default_factory=list)
    procedure: list[MemoryProcedureRead] = Field(default_factory=list)


class MemorySearchItemRead(MemorySummaryRead):
    score: float


class MemorySearchRead(BaseModel):
    query: str
    items: list[MemorySearchItemRead] = Field(default_factory=list)


class TravelerProfileRead(BaseModel):
    version: int
    preferences: dict[str, Any] = Field(default_factory=dict)
    nationality: str | None = None
    notes: str | None = None
    confidence: float


class TravelerProfileUpdate(BaseModel):
    preferences: dict[str, Any] = Field(default_factory=dict)
    nationality: str | None = Field(
        default=None,
        min_length=2,
        max_length=2,
        pattern="^[A-Za-z]{2}$",
        description="护照国籍 ISO 3166-1 alpha-2；用于入境可行性核验（PRD P0-7）",
    )
    notes: str | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)


# ------------------------------------------------------- meeting minutes
MinutesTone = Literal["PROFESSIONAL", "CONCISE"]


class MinutesDraftRequest(BaseModel):
    tone: MinutesTone = "PROFESSIONAL"


class MinutesFollowUpRead(BaseModel):
    title: str
    due_date: str | None = None


class MinutesInsightRead(BaseModel):
    """一条被识别的信息（PRD 页面 07 §8.2）。

    ``requires_confirmation`` 是服务端强制的业务规则：除 SUMMARY 外一律 True，
    客户需求/商机信号/交易条件/商业承诺/阶段变化都必须人工核对后才能采信。
    """

    kind: Literal[
        "SUMMARY",
        "CUSTOMER_NEED",
        "OPPORTUNITY_SIGNAL",
        "DEAL_TERM",
        "COMMITMENT",
        "STAGE_CHANGE",
    ]
    text: str
    requires_confirmation: bool


class MinutesDraftRead(BaseModel):
    """会议纪要草稿。``degraded=true`` 表示 LLM 不可用、内容来自模板兜底。"""

    draft_id: UUID
    llm_invocation_id: UUID
    title: str
    summary: str
    decisions: list[str] = Field(default_factory=list)
    follow_up_tasks: list[MinutesFollowUpRead] = Field(default_factory=list)
    insights: list[MinutesInsightRead] = Field(default_factory=list)
    degraded: bool


# --------------------------------------------------------- LLM tooling
class ToolRead(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    scope: str
    requires_approval: bool
    idempotent: bool
    handler: str


class LLMInvocationRead(BaseModel):
    id: UUID
    task: str
    model: str
    prompt_template_id: str
    prompt_version: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    cost_estimate: float
    degraded: bool
    cached: bool
    error: str | None = None
    trace_id: str | None = None
    created_at: datetime


# -------------------------------------------------------------- metrics
class MetricRead(BaseModel):
    key: str
    value: float
    sample_size: int
    target: str | None = None
    unit: str = "ratio"


class MetricsSummaryRead(BaseModel):
    generated_at: datetime
    metrics: list[MetricRead] = Field(default_factory=list)


class MetricSnapshotRead(ORMModel):
    id: UUID
    metric_key: str
    value: float
    sample_size: int
    window_start: datetime
    window_end: datetime
    created_at: datetime


# ------------------------------------------------------- external signals
ExternalSignalType = Literal[
    "VISA_POLICY_CHANGE", "WEATHER_DISRUPTION", "FX_VOLATILITY", "FLIGHT_STATUS"
]
ExternalSubjectType = Literal["COUNTRY", "TRIP", "CUSTOMER"]


class WeatherPullRequest(BaseModel):
    """按出行拉取目的地当前天气（Open-Meteo，免钥公开源，ADR-0034）。"""

    trip_id: UUID


class WeatherPullItemRead(BaseModel):
    place_id: str
    place_name: str
    temperature_c: float | None = None
    wind_kmh: float | None = None
    precipitation_mm: float | None = None
    weather_code: int | None = None
    severe: bool = False
    signal: bool = False
    error: str | None = None


class WeatherPullRead(BaseModel):
    trip_id: str
    checked: int
    signals_ingested: int
    items: list[WeatherPullItemRead] = Field(default_factory=list)


class FlightPullRequest(BaseModel):
    """按 callsign 拉取航迹；提供 flight_segment_id 时必须匹配该机票段的 tracking reference。"""

    trip_id: UUID
    callsign: str | None = Field(default=None, min_length=1, max_length=20)
    flight_segment_id: UUID | None = None

    @model_validator(mode="after")
    def _require_callsign_or_flight_segment(self) -> FlightPullRequest:
        if self.callsign is None and self.flight_segment_id is None:
            raise ValueError("callsign 或 flight_segment_id 至少提供一个")
        return self


class FlightPullItemRead(BaseModel):
    icao24: str
    callsign: str
    origin_country: str
    latitude: float | None = None
    longitude: float | None = None
    altitude_m: float | None = None
    velocity_ms: float | None = None
    on_ground: bool = False
    last_contact: str | None = None


class FlightPullRead(BaseModel):
    trip_id: str
    callsign: str
    flight_segment_id: UUID | None = None
    aircraft: int
    signals_ingested: int
    items: list[FlightPullItemRead] = Field(default_factory=list)


class FlightOperationalUpdatePayload(BaseModel):
    """Normalized airline/airport event embedded in an FLIGHT_STATUS signal."""

    flight_segment_id: UUID
    provider_event_id: str = Field(min_length=1, max_length=160)
    update_type: Literal[
        "SCHEDULE_CHANGE",
        "DEPARTURE_DELAY",
        "ARRIVAL_DELAY",
        "CANCELLED",
        "DIVERTED",
        "GATE_CHANGE",
    ]
    flight_designator: str = Field(min_length=2, max_length=12)
    departure_airport_iata: str = Field(min_length=3, max_length=3)
    arrival_airport_iata: str = Field(min_length=3, max_length=3)
    estimated_departure_at: datetime | None = None
    estimated_arrival_at: datetime | None = None
    reported_arrival_airport_iata: str | None = Field(default=None, min_length=3, max_length=3)
    source_payload_sha256: str | None = Field(default=None, min_length=64, max_length=64)

    @field_validator("provider_event_id")
    @classmethod
    def _trim_provider_event_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("provider_event_id 不能为空")
        return normalized

    @field_validator("flight_designator")
    @classmethod
    def _normalize_operational_flight_designator(cls, value: str) -> str:
        normalized = value.strip().upper().replace(" ", "")
        if not fullmatch(r"[A-Z0-9]{2,12}", normalized):
            raise ValueError("flight_designator 格式无效")
        return normalized

    @field_validator("departure_airport_iata", "arrival_airport_iata", "reported_arrival_airport_iata")
    @classmethod
    def _normalize_operational_airport(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if not fullmatch(r"[A-Z]{3}", normalized):
            raise ValueError("机场代码必须是 3 位 IATA 字母代码")
        return normalized

    @field_validator("source_payload_sha256")
    @classmethod
    def _validate_source_payload_hash(cls, value: str | None) -> str | None:
        if value is not None and not fullmatch(r"[a-fA-F0-9]{64}", value):
            raise ValueError("source_payload_sha256 必须为 64 位十六进制 SHA-256")
        return value.lower() if value is not None else None

    @model_validator(mode="after")
    def _validate_operational_times(self) -> FlightOperationalUpdatePayload:
        for field_name, value in (
            ("estimated_departure_at", self.estimated_departure_at),
            ("estimated_arrival_at", self.estimated_arrival_at),
        ):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{field_name} 必须包含时区偏移")
        if (
            self.estimated_departure_at is not None
            and self.estimated_arrival_at is not None
            and self.estimated_arrival_at <= self.estimated_departure_at
        ):
            raise ValueError("预计到达时间必须晚于预计出发时间")
        if self.update_type == "DEPARTURE_DELAY" and self.estimated_departure_at is None:
            raise ValueError("DEPARTURE_DELAY 必须提供 estimated_departure_at")
        if self.update_type == "ARRIVAL_DELAY" and self.estimated_arrival_at is None:
            raise ValueError("ARRIVAL_DELAY 必须提供 estimated_arrival_at")
        if self.update_type == "SCHEDULE_CHANGE" and (
            self.estimated_departure_at is None and self.estimated_arrival_at is None
        ):
            raise ValueError("SCHEDULE_CHANGE 至少提供一个更新后的时刻")
        if self.update_type == "DIVERTED":
            if self.reported_arrival_airport_iata is None:
                raise ValueError("DIVERTED 必须提供 reported_arrival_airport_iata")
            if self.reported_arrival_airport_iata == self.arrival_airport_iata:
                raise ValueError("备降机场不能与原计划到达机场相同")
        return self


class ExternalSignalCreate(BaseModel):
    """外部源上报的一条事实。

    ``dedupe_key`` 由上报方保证稳定（例如 ``visa_policy:DE:2026-11``）；同一键重推是更新，
    不是新信号。``payload`` 的具体字段由信号类型约定，至少应包含可展示的 ``summary``。
    """

    provider: str = Field(min_length=1, max_length=100)
    signal_type: ExternalSignalType
    subject_type: ExternalSubjectType
    subject_ref: str = Field(min_length=1, max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=0.7, ge=0, le=1)
    observed_at: datetime
    valid_until: datetime | None = None
    dedupe_key: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def _validate_flight_operational_envelope(self) -> ExternalSignalCreate:
        # These fields are materialized only after the immutable event has been
        # persisted. Accepting them from a caller would let a stale event ID be
        # re-wrapped with a fresh observation time / validity window.
        server_owned_fields = {
            "operational_update_id",
            "operational_update_content_sha256",
            "operational_update_type",
        }
        supplied_server_fields = server_owned_fields.intersection(self.payload)
        if supplied_server_fields:
            raise ValueError(
                "operational update reference and digest fields are server-managed"
            )

        raw_update = self.payload.get("flight_operational_update")
        if raw_update is None:
            return self
        if self.signal_type != "FLIGHT_STATUS" or self.subject_type != "TRIP":
            raise ValueError("flight_operational_update 仅允许出现在 TRIP 的 FLIGHT_STATUS 信号中")
        if self.provider.strip().casefold() == "opensky":
            raise ValueError("OpenSky 航迹遥测不能作为航司/机场运营事件来源")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("运营事件 observed_at 必须包含时区偏移")
        if self.valid_until is not None:
            if self.valid_until.tzinfo is None or self.valid_until.utcoffset() is None:
                raise ValueError("运营事件 valid_until 必须包含时区偏移")
            if self.valid_until <= self.observed_at:
                raise ValueError("运营事件 valid_until 必须晚于 observed_at")
        try:
            UUID(self.subject_ref)
        except ValueError as exc:
            raise ValueError("运营事件 subject_ref 必须是 Trip UUID") from exc
        operational = FlightOperationalUpdatePayload.model_validate(raw_update)
        expected_dedupe = f"flight_ops:{operational.provider_event_id}"
        if self.dedupe_key != expected_dedupe:
            raise ValueError(f"运营事件 dedupe_key 必须为 {expected_dedupe}")
        return self


class ExternalSignalRead(ORMModel):
    id: UUID
    provider: str
    signal_type: ExternalSignalType
    subject_type: ExternalSubjectType
    subject_ref: str
    payload: dict[str, Any] = Field(default_factory=dict)
    confidence: float
    observed_at: datetime
    valid_until: datetime | None = None
    dedupe_key: str
    created_at: datetime


# --------------------------------------------------------------- notifications
NotificationStatus = Literal["PENDING", "SENT", "FAILED"]


NotificationPriority = Literal["P0", "P1", "P2", "P3"]


class NotificationRead(ORMModel):
    id: UUID
    suggestion_id: UUID | None = None
    kind: str
    title: str
    body: str
    status: NotificationStatus
    provider: str
    priority: NotificationPriority = "P2"
    issue_key: str | None = None
    attempts: int
    sent_at: datetime | None = None
    read_at: datetime | None = None
    created_at: datetime


class NotificationFeedRead(BaseModel):
    items: list[NotificationRead] = Field(default_factory=list)
    unread: int = 0
    unread_by_priority: dict[str, int] = Field(default_factory=dict)


class NotificationPreferenceRead(BaseModel):
    muted_priorities: list[NotificationPriority] = Field(default_factory=list)


class NotificationPreferenceUpdate(BaseModel):
    muted_priorities: list[NotificationPriority] = Field(default_factory=list)


# ------------------------------------------------------------------ audit
class AuditEventRead(BaseModel):
    at: datetime
    kind: str
    action: str
    actor: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class MemoryForgetRead(BaseModel):
    id: UUID
    forgotten: bool
    valid_until: datetime | None = None


class RetentionReportRead(BaseModel):
    records_redacted: int
    summaries_redacted: int
    cutoff: datetime | None = None


# -------------------------------------------------------- approval policies
class ApprovalPolicyCreate(BaseModel):
    subject_type: str = "proactive_suggestion"
    min_severity: Literal["LOW", "MEDIUM", "HIGH"] = "LOW"
    required_role: Literal["VIEWER", "TRAVELER", "ADMIN", "OWNER"] = "TRAVELER"
    required_approvals: int = Field(default=1, ge=1, le=10)
    enabled: bool = True


class ApprovalPolicyRead(ORMModel):
    id: UUID
    subject_type: str
    min_severity: Literal["LOW", "MEDIUM", "HIGH"]
    required_role: Literal["VIEWER", "TRAVELER", "ADMIN", "OWNER"]
    required_approvals: int = 1
    enabled: bool
    created_at: datetime


# --------------------------------------------------------------- devices
DevicePlatform = Literal["IOS", "ANDROID"]


class DeviceTokenRegister(BaseModel):
    platform: DevicePlatform = "IOS"
    token: str = Field(min_length=8, max_length=512)


class DeviceTokenRead(ORMModel):
    id: UUID
    platform: DevicePlatform
    token: str
    enabled: bool
    last_seen_at: datetime | None = None
    created_at: datetime


# ------------------------------------------------------------ plan changes
PlanChangeOperation = Literal[
    "CHANGE_VISIT_TIME",
    "CHANGE_VISIT_DURATION",
    "CANCEL_VISIT",
    "ADD_VISIT",
    "REMOVE_VISIT",
]


class PlanChangeCreate(BaseModel):
    base_plan_version_id: UUID
    base_trip_version: int = Field(ge=1)
    source: str = "MAP"
    operation_type: PlanChangeOperation
    payload: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None


class PlanChangeRead(BaseModel):
    change_request_id: UUID
    status: str
    optimization_job_id: UUID
    trip_version: int


class EvidenceRead(ORMModel):
    id: UUID
    subject_type: str
    subject_id: UUID
    kind: str
    ref: str
    source: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    retrieved_at: datetime | None = None
    created_at: datetime


IntegrationStatus = Literal["ACTIVE", "DISABLED", "DEGRADED"]


class IntegrationConnectionCreate(BaseModel):
    provider: str = Field(min_length=1, max_length=100)
    capability: str = Field(min_length=1, max_length=100)
    status: IntegrationStatus = "DISABLED"
    config: dict[str, Any] = Field(default_factory=dict)


class IntegrationConnectionRead(ORMModel):
    id: UUID
    provider: str
    capability: str
    status: IntegrationStatus
    config: dict[str, Any] = Field(default_factory=dict)
    last_synced_at: datetime | None = None
    created_at: datetime


class MinutesJobAcceptedRead(BaseModel):
    job_id: UUID
    visit_id: UUID
    status: JobStatus


# --------------------------------------------------------------- visa cases
VisaCaseStatus = Literal["NEW", "PREPARING", "SUBMITTED", "APPROVED", "REJECTED", "CANCELLED"]
ChecklistStatus = Literal["MISSING", "READY", "NOT_APPLICABLE"]


class VisaChecklistItem(BaseModel):
    key: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=300)
    status: ChecklistStatus = "MISSING"
    note: str | None = None


class VisaCaseCreate(BaseModel):
    countries: list[str] | None = None


class VisaCaseUpdate(BaseModel):
    status: VisaCaseStatus | None = None
    notes: str | None = None
    checklist: list[VisaChecklistItem] | None = None
    passport_expires_at: date | None = None


class VisaCaseRead(ORMModel):
    id: UUID
    trip_id: UUID
    country_code: str
    status: VisaCaseStatus
    checklist: list[VisaChecklistItem] = Field(default_factory=list)
    notes: str | None = None
    passport_expires_at: date | None = None
    confidence: float
    submitted_at: datetime | None = None
    decision_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class EntryAssessmentRead(ORMModel):
    id: UUID
    trip_id: UUID
    country_code: str
    summary: str
    source: str
    source_version: str
    confidence: float
    manual_review_required: bool
    assessed_at: datetime
    created_at: datetime


# -------------------------------------------------------------- expenses
ExpenseCategory = Literal["TRANSPORT", "LODGING", "MEALS", "OTHER"]


class ExpenseCreate(BaseModel):
    category: ExpenseCategory
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    currency: str = Field(min_length=3, max_length=3)
    occurred_on: date
    note: str | None = None
    receipt_ref: str | None = None
    receipt_asset_id: UUID | None = None


class ExpenseRead(ORMModel):
    id: UUID
    trip_id: UUID
    category: ExpenseCategory
    amount: Decimal
    currency: str
    occurred_on: date
    note: str | None = None
    receipt_ref: str | None = None
    receipt_asset_id: UUID | None = None
    created_at: datetime


class ReceiptAssetRead(ORMModel):
    id: UUID
    trip_id: UUID
    original_filename: str
    mime_type: str
    byte_length: int
    sha256: str
    storage_status: Literal["AVAILABLE", "QUARANTINED", "DELETE_PENDING"]
    uploaded_at: datetime


class ReceiptOcrReviewCreate(BaseModel):
    decision: Literal["CONFIRMED", "REJECTED"]
    merchant: str | None = Field(default=None, min_length=1, max_length=200)
    amount: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3, pattern="^[A-Za-z]{3}$")
    occurred_on: date | None = None
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _validate_review(self) -> ReceiptOcrReviewCreate:
        if self.merchant is not None:
            merchant = self.merchant.strip()
            if not merchant:
                raise ValueError("商户名称不能为空白")
            object.__setattr__(self, "merchant", merchant)
        if (self.amount is None) != (self.currency is None):
            raise ValueError("确认金额时必须同时确认币种")
        has_fields = any(
            value is not None
            for value in (self.merchant, self.amount, self.occurred_on)
        )
        if self.decision == "CONFIRMED" and not has_fields:
            raise ValueError("确认 OCR 结果时至少需要确认一个字段")
        if self.decision == "REJECTED" and not (self.note and self.note.strip()):
            raise ValueError("驳回 OCR 结果必须填写原因")
        if self.decision == "REJECTED" and has_fields:
            raise ValueError("驳回 OCR 结果不能同时提交确认字段")
        if self.note is not None:
            note = self.note.strip()
            object.__setattr__(self, "note", note or None)
        if self.currency is not None:
            object.__setattr__(self, "currency", self.currency.upper())
        return self


class ReceiptOcrReviewRead(ORMModel):
    id: UUID
    receipt_ocr_run_id: UUID
    decision: Literal["CONFIRMED", "REJECTED"]
    confirmed_fields: dict[str, Any]
    note: str | None = None
    reviewed_by_user_id: UUID | None
    reviewed_at: datetime


class ReceiptOcrRunRead(ORMModel):
    id: UUID
    receipt_asset_id: UUID
    agent_run_id: UUID
    content_sha256: str
    engine: str
    engine_version: str
    status: Literal["RUNNING", "SUCCEEDED", "FAILED"]
    candidates: dict[str, Any]
    error: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    created_at: datetime
    review: ReceiptOcrReviewRead | None = None


class CurrencyTotalRead(BaseModel):
    currency: str
    amount: float
    count: int


class FxRateCreate(BaseModel):
    """``rate`` 语义：1 单位 base = rate 单位 quote。"""

    base_currency: str = Field(min_length=3, max_length=3, pattern="^[A-Za-z]{3}$")
    quote_currency: str = Field(min_length=3, max_length=3, pattern="^[A-Za-z]{3}$")
    rate: Decimal = Field(gt=0)
    as_of: datetime | None = None
    source: str | None = Field(default=None, max_length=200)


class FxRateRead(ORMModel):
    id: UUID
    base_currency: str
    quote_currency: str
    rate: Decimal
    as_of: datetime
    source: str | None = None
    created_at: datetime


class FxConversionRead(BaseModel):
    currency: str
    rate: float
    as_of: datetime | None = None
    source: str | None = None
    via_inverse: bool = False


class BudgetSummaryRead(BaseModel):
    budget_amount: Decimal | None = None
    budget_currency: str
    planned_cost_total: float | None = None
    planned_cost_currency: str | None = None
    planned_cost_unknown_count: int = 0
    actual_by_currency: list[CurrencyTotalRead] = Field(default_factory=list)
    actual_total_in_budget_currency: Decimal | None = None
    variance_vs_budget: Decimal | None = None
    variance_vs_plan: float | None = None
    conversion_base_currency: str | None = None
    converted_actual_total: Decimal | None = None
    converted_planned_cost_total: Decimal | None = None
    converted_variance_vs_budget: Decimal | None = None
    conversion_complete: bool | None = None
    unconverted_currencies: list[str] = Field(default_factory=list)
    fx_conversions: list[FxConversionRead] = Field(default_factory=list)


# ------------------------------------------------------------ assistant
class AssistantConversationCreate(BaseModel):
    trip_id: UUID | None = None
    title: str = Field(default="", max_length=200)


class AssistantConversationRead(ORMModel):
    id: UUID
    trip_id: UUID | None = None
    title: str
    created_at: datetime
    updated_at: datetime


class AssistantCardRead(BaseModel):
    """助手消息里的卡片：服务端算好的事实快照，客户端直接展示。

    契约此前写成 ``list[dict]``，但六种卡片（TRIP_STATUS / ACTION_CENTER /
    PENDING_SUGGESTION / PLAN_JOB / ACTION_RESULT / EXPENSE_REPORT）结构一致：
    ``type``/``title``/``detail`` 必有，``entity_id``/``link`` 用于跳转。
    ``ACTION_RESULT`` 额外带 ``proposal_id``——显式保留，避免契约收紧时丢字段。
    """

    type: str
    title: str
    detail: str
    entity_id: str | None = None
    link: str | None = None
    #: 仅 ``ACTION_RESULT`` 卡片携带：触发本次动作的提案消息 id（回跳用）。
    proposal_id: str | None = None
    #: 仅 ``TRIP_PROPOSAL`` 卡片携带：提案载荷（国家/日期/名称/用途），确认创建时使用。
    payload: dict[str, Any] | None = None

    #: 卡片是多态展示面：未来新增卡片字段不能被响应模型静默丢弃（否则客户端拿不到）。
    model_config = ConfigDict(extra="allow")


class AssistantMessageRead(ORMModel):
    id: UUID
    role: str
    content: str
    cards: list[AssistantCardRead] = Field(default_factory=list)
    llm_invocation_id: UUID | None = None
    created_at: datetime


class AssistantConversationDetailRead(BaseModel):
    conversation: AssistantConversationRead
    messages: list[AssistantMessageRead] = Field(default_factory=list)


class AssistantMessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class AssistantActionCreate(BaseModel):
    action: Literal[
        "approve_suggestion",
        "dismiss_suggestion",
        "generate_plan",
        "create_expense_report",
        "create_trip_draft",
    ]
    subject_id: UUID


# ------------------------------------------------------------ user-intent research
ResearchSourceScope = Literal[
    "AUTO",
    "INTERNAL_ONLY",
    "EXTERNAL_ONLY",
    "INTERNAL_AND_EXTERNAL",
]


class ResearchPlanCreate(BaseModel):
    """显式研究请求。上下文文本由用户主动提供或从用户材料中提取。"""

    request: str = Field(min_length=3, max_length=6000)
    source_scope: ResearchSourceScope = "AUTO"
    trip_id: UUID | None = None
    customer_id: UUID | None = None
    workspace_id: UUID | None = None
    target_question_id: UUID | None = None
    context_text: str | None = Field(default=None, max_length=16000)
    locale: str | None = Field(default=None, max_length=35)

    @model_validator(mode="after")
    def _normalise_request(self) -> ResearchPlanCreate:
        self.request = self.request.strip()
        if len(self.request) < 3:
            raise ValueError("request 至少需要 3 个非空字符")
        if self.target_question_id is not None and self.workspace_id is None:
            raise ValueError("target_question_id 必须同时指定 workspace_id")
        if self.context_text is not None:
            self.context_text = self.context_text.strip() or None
        if self.locale is not None:
            self.locale = self.locale.strip() or None
        return self


class ResearchPlanGraphNode(BaseModel):
    key: str
    question: str
    rationale: str | None = None
    priority: int = Field(default=3, ge=1, le=5)
    acceptance_criteria: list[str] = Field(default_factory=list)
    expected_evidence: list[str] = Field(default_factory=list)
    depends_on_keys: list[str] = Field(default_factory=list)


class ResearchPlanIntentRead(BaseModel):
    """模型从本次用户输入中提取的任务范围，不内置任何行业。"""

    intent_summary: str
    objective: str
    subject: str
    domain: str | None = None
    geographies: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    time_scope: str | None = None
    research_questions: list[str] = Field(default_factory=list)
    research_graph: list[ResearchPlanGraphNode] = Field(default_factory=list)
    planning_warnings: list[str] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)
    source_types: list[str] = Field(default_factory=list)
    deliverables: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    external_research_needed: bool = False


class ResearchPlannerAuditRead(BaseModel):
    agent_run_id: UUID
    llm_invocation_id: UUID | None = None
    model: str
    degraded: bool = False
    error: str | None = None


class ResearchEvidenceRead(ORMModel):
    id: UUID
    kind: str
    ref: str
    source: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    retrieved_at: datetime | None = None
    created_at: datetime


class ResearchExecuteCreate(BaseModel):
    """用户审阅后确认的查询。执行器只允许使用此列表。"""

    search_queries: list[str] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def _normalise_queries(self) -> ResearchExecuteCreate:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in self.search_queries:
            query = raw.strip()
            if not query:
                continue
            if len(query) > 500:
                raise ValueError("每条搜索查询不能超过 500 个字符")
            key = query.casefold()
            if key not in seen:
                seen.add(key)
                normalized.append(query)
        if not normalized:
            raise ValueError("至少需要一条非空搜索查询")
        if len(normalized) > 12:
            raise ValueError("搜索查询最多 12 条")
        self.search_queries = normalized
        return self


class ResearchExecuteAccepted(BaseModel):
    plan_id: UUID
    job_id: UUID
    status: JobStatus
    reused: bool = False


class ResearchPlanRead(BaseModel):
    id: UUID
    trip_id: UUID | None = None
    customer_id: UUID | None = None
    workspace_id: UUID | None = None
    target_question_id: UUID | None = None
    generated_question_ids: list[UUID] = Field(default_factory=list)
    job_id: UUID | None = None
    retrieval_error: str | None = None
    results_count: int = 0
    reviewed_search_queries: list[str] = Field(default_factory=list)
    request: str
    requested_scope: ResearchSourceScope
    recommended_scope: Literal[
        "INTERNAL_ONLY", "EXTERNAL_ONLY", "INTERNAL_AND_EXTERNAL"
    ]
    status: Literal["PLANNED"] = "PLANNED"
    retrieval_status: Literal[
        "NOT_STARTED", "QUEUED", "RUNNING", "COMPLETED", "FAILED"
    ] = "NOT_STARTED"
    plan: ResearchPlanIntentRead
    planner: ResearchPlannerAuditRead
    evidence: list[ResearchEvidenceRead] = Field(default_factory=list)
    created_at: datetime


# ------------------------------------------------------------ local guide
class LocalGuideSectionRead(BaseModel):
    key: str
    title: str
    items: list[str] = Field(default_factory=list)


class LocalGuideCityRead(BaseModel):
    city: str
    tips: list[str] = Field(default_factory=list)


class LocalGuideCountryRead(BaseModel):
    country_code: str
    name: str
    sections: list[LocalGuideSectionRead] = Field(default_factory=list)
    cities: list[LocalGuideCityRead] = Field(default_factory=list)


class DataGapRead(BaseModel):
    """本地指南的数据缺口：目录未覆盖的国家，或未接入的实时源（天气/POI/餐厅）。"""

    code: str
    message: str
    country_code: str | None = None


class GuideWeatherRead(BaseModel):
    """指南附带的实时天气观测（Open-Meteo，ADR-0034；源未接入时列表为空）。"""

    place_id: str
    place_name: str
    temperature_c: float | None = None
    wind_kmh: float | None = None
    precipitation_mm: float | None = None
    weather_code: int | None = None
    observed_at: str | None = None


class LocalGuideRead(BaseModel):
    trip_id: UUID
    countries: list[LocalGuideCountryRead] = Field(default_factory=list)
    cities_covered: list[str] = Field(default_factory=list)
    weather: list[GuideWeatherRead] = Field(default_factory=list)
    data_gaps: list[DataGapRead] = Field(default_factory=list)


# ------------------------------------------------------------ team members
OrgRole = Literal["OWNER", "ADMIN", "TRAVELER", "VIEWER"]


class MemberRead(BaseModel):
    user_id: UUID
    display_name: str
    email: str
    role: OrgRole
    joined_at: datetime


class MemberAdd(BaseModel):
    user_id: UUID
    role: OrgRole = "TRAVELER"


class MemberRoleUpdate(BaseModel):
    role: OrgRole


# ------------------------------------------------------------ trip outcome
class TripOutcomeVisitRead(BaseModel):
    status: str
    count: int


class TripOutcomeCostRead(BaseModel):
    total: float | None = None
    currency: str | None = None
    unknown_count: int = 0


class TripOpportunitySignalRead(BaseModel):
    kind: str
    kind_label: str
    text: str
    visit_id: str
    requires_confirmation: bool = True


class TripOutcomeMemoryRead(BaseModel):
    """出差结果里的复盘记忆引用：行程结束后由复盘 Worker 生成的摘要。"""

    kind: str
    summary: str


class TripOutcomeRead(BaseModel):
    """出差结果分析与客户机会复盘（PRD P1）。

    只读聚合：拜访结果、费用计划 vs 实际、纪要分类洞察与商机信号。
    所有"机会"都标注 requires_confirmation=True——不得当作已确认事实。
    """

    trip_id: UUID
    status: str
    window: dict[str, str] = Field(default_factory=dict)
    visits: dict[str, int] = Field(default_factory=dict)
    visit_labels: dict[str, str] = Field(default_factory=dict)
    record_outcomes: dict[str, int] = Field(default_factory=dict)
    record_outcome_labels: dict[str, str] = Field(default_factory=dict)
    plan_cost: TripOutcomeCostRead | None = None
    actual_cost: list[CurrencyTotalRead] = Field(default_factory=list)
    insights_by_kind: dict[str, int] = Field(default_factory=dict)
    insight_kind_labels: dict[str, str] = Field(default_factory=dict)
    opportunity_signals: list[TripOpportunitySignalRead] = Field(default_factory=list)
    memory_summaries: list[TripOutcomeMemoryRead] = Field(default_factory=list)


# ------------------------------------------------------------ action center
ActionCategory = Literal[
    "DECISION", "APPROVAL", "RUNNING", "FAILED", "PREPARATION", "COMPLETED"
]
ActionKind = Literal["SUGGESTION", "EXPENSE_REPORT", "JOB", "CRM_SYNC", "VISIT_FOLLOW_UP", "TRIP_CLOSEOUT"]


class ActionItemRead(BaseModel):
    category: ActionCategory
    kind: ActionKind
    entity_id: str
    title: str
    detail: str
    status: str
    severity: str | None = None
    trip_id: str | None = None
    trip_name: str | None = None
    action_hint: str
    available_actions: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ActionSectionRead(BaseModel):
    key: ActionCategory
    title: str
    items: list[ActionItemRead] = Field(default_factory=list)


class ActionCenterRead(BaseModel):
    generated_at: datetime
    total: int
    counts: dict[str, int] = Field(default_factory=dict)
    sections: list[ActionSectionRead] = Field(default_factory=list)


# ------------------------------------------------------------ travel policies
class TravelPolicyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    currency: str = Field(default="CNY", min_length=3, max_length=3)
    max_trip_budget: Decimal | None = Field(default=None, ge=0)
    max_plan_cost: Decimal | None = Field(default=None, ge=0)
    min_advance_booking_days: int | None = Field(default=None, ge=0)
    allowed_countries: list[str] = Field(default_factory=list)
    strict: bool = False
    enabled: bool = True


class TravelPolicyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    max_trip_budget: Decimal | None = Field(default=None, ge=0)
    max_plan_cost: Decimal | None = Field(default=None, ge=0)
    min_advance_booking_days: int | None = Field(default=None, ge=0)
    allowed_countries: list[str] | None = None
    strict: bool | None = None
    enabled: bool | None = None


class TravelPolicyRead(ORMModel):
    id: UUID
    name: str
    currency: str
    max_trip_budget: Decimal | None = None
    max_plan_cost: Decimal | None = None
    min_advance_booking_days: int | None = None
    allowed_countries: list[str] = Field(default_factory=list)
    strict: bool
    enabled: bool
    created_at: datetime
    updated_at: datetime


class PolicyViolationRead(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class PolicyCheckRead(BaseModel):
    policy_id: UUID | None = None
    policy_name: str | None = None
    strict: bool = False
    violations: list[PolicyViolationRead] = Field(default_factory=list)
    checked_at: datetime


# --------------------------------------------------------- expense reports
ExpenseReportStatus = Literal["DRAFT", "SUBMITTED", "APPROVED", "REJECTED"]


class ExpenseReportCreate(BaseModel):
    notes: str | None = None
    expense_ids: list[UUID] | None = None


class ExpenseReportItemRead(ORMModel):
    id: UUID
    report_id: UUID
    expense_id: UUID | None = None
    category: ExpenseCategory
    amount: Decimal
    currency: str
    occurred_on: date
    note: str | None = None
    receipt_ref: str | None = None
    receipt_asset_id: UUID | None = None
    receipt_sha256: str | None = None
    receipt_original_filename: str | None = None
    receipt_mime_type: str | None = None
    receipt_byte_length: int | None = None


class ExpenseReportRead(BaseModel):
    id: UUID
    trip_id: UUID
    status: ExpenseReportStatus
    notes: str | None = None
    submitted_at: datetime | None = None
    decided_at: datetime | None = None
    decided_by_user_id: UUID | None = None
    items: list[ExpenseReportItemRead] = Field(default_factory=list)
    totals: list[CurrencyTotalRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


CloseoutReadinessStatus = Literal[
    "BLOCKED",
    "READY_FOR_REVIEW",
    "REPORT_SUBMITTED",
    "REPORT_APPROVED",
    "READY_WITH_EXCEPTIONS",
]
CloseoutIssueSeverity = Literal["BLOCKER", "WARNING"]


class TripCloseoutIssueRead(BaseModel):
    id: str
    code: str
    severity: CloseoutIssueSeverity
    title: str
    detail: str
    source_type: str
    source_id: UUID
    action_hint: str


class TripCloseoutSummaryRead(BaseModel):
    visit_count: int
    visit_outcome_count: int
    visits_open_count: int
    visits_missing_outcome_count: int
    expense_count: int
    expenses_in_report_count: int
    expenses_not_in_report_count: int
    expenses_without_receipt_count: int
    receipt_asset_count: int
    receipt_asset_unavailable_count: int
    ocr_needs_review_count: int
    open_business_follow_up_count: int
    latest_report_id: UUID | None = None
    latest_report_status: ExpenseReportStatus | None = None
    blocker_count: int
    warning_count: int


class TripCloseoutReadinessRead(BaseModel):
    trip_id: UUID
    status: CloseoutReadinessStatus
    generated_at: datetime
    summary: TripCloseoutSummaryRead
    totals_by_currency: list[CurrencyTotalRead] = Field(default_factory=list)
    blockers: list[TripCloseoutIssueRead] = Field(default_factory=list)
    warnings: list[TripCloseoutIssueRead] = Field(default_factory=list)


class ExpenseReportDecisionRequest(BaseModel):
    note: str | None = None


# ------------------------------------------------------------- CRM sync
CrmSyncStatus = Literal["QUEUED", "PREPARED", "SUCCEEDED", "FAILED"]


class CrmSyncJobRead(ORMModel):
    id: UUID
    trip_id: UUID
    visit_id: UUID
    visit_record_id: UUID
    status: CrmSyncStatus
    job_id: UUID | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


# ------------------------------------------------------ market workspaces
MarketWorkspaceStatus = Literal["DRAFT", "ACTIVE", "PAUSED", "COMPLETED", "ARCHIVED"]
MarketContextExtractionStatus = Literal["NOT_STARTED", "PENDING", "COMPLETED", "FAILED"]
MarketQuestionSource = Literal["USER", "AGENT"]
MarketQuestionStatus = Literal["OPEN", "IN_PROGRESS", "ANSWERED", "BLOCKED", "CANCELLED"]
MarketActivityActor = Literal["USER", "SYSTEM"]


class MarketWorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    objective: str | None = Field(default=None, max_length=4000)
    initial_context: str | None = Field(default=None, max_length=20000)
    settings: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _non_blank_values(self) -> MarketWorkspaceCreate:
        if not self.name.strip():
            raise ValueError("工作空间名称不能为空")
        if self.initial_context is not None and not self.initial_context.strip():
            raise ValueError("初始业务背景不能为空白")
        return self


class MarketWorkspaceRead(ORMModel):
    id: UUID
    name: str
    objective: str | None
    status: MarketWorkspaceStatus
    owner_user_id: UUID | None
    current_context_version: int | None
    settings: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class MarketContextCreate(BaseModel):
    raw_input: str = Field(min_length=1, max_length=20000)
    adopt: bool = False

    @model_validator(mode="after")
    def _raw_input_not_blank(self) -> MarketContextCreate:
        if not self.raw_input.strip():
            raise ValueError("业务背景不能为空白")
        return self


class MarketContextRead(ORMModel):
    id: UUID
    workspace_id: UUID
    version: int
    raw_input: str
    extracted_context: dict[str, Any]
    user_overrides: dict[str, Any]
    unknowns: list[Any]
    provenance: dict[str, Any]
    extraction_status: MarketContextExtractionStatus
    extraction_prompt_id: str | None
    extraction_prompt_version: str | None
    is_current: bool
    created_by_user_id: UUID | None
    created_at: datetime


class MarketResearchQuestionCreate(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    rationale: str | None = Field(default=None, max_length=4000)
    priority: int = Field(default=3, ge=1, le=5)
    acceptance_criteria: list[Any] = Field(default_factory=list)
    parent_question_id: UUID | None = None

    @model_validator(mode="after")
    def _question_not_blank(self) -> MarketResearchQuestionCreate:
        if not self.question.strip():
            raise ValueError("研究问题不能为空白")
        return self


class MarketResearchQuestionRead(ORMModel):
    id: UUID
    workspace_id: UUID
    parent_question_id: UUID | None
    question: str
    rationale: str | None
    source: MarketQuestionSource
    priority: int
    status: MarketQuestionStatus
    acceptance_criteria: list[Any]
    expected_evidence: list[Any] = Field(default_factory=list)
    generated_by_run_id: UUID | None = None
    planner_question_key: str | None = None
    answer_summary: str | None
    blocked_reason: str | None
    created_by_user_id: UUID | None
    created_at: datetime
    updated_at: datetime


class MarketQuestionDependencyCreate(BaseModel):
    depends_on_question_id: UUID


class MarketQuestionDependencyRead(ORMModel):
    id: UUID
    question_id: UUID
    depends_on_question_id: UUID
    created_at: datetime


class MarketQuestionDependencyDeleted(BaseModel):
    dependency_id: UUID
    deleted: bool


class MarketResearchGraphNodeRead(BaseModel):
    id: UUID
    question: str
    rationale: str | None
    priority: int
    status: MarketQuestionStatus
    source: MarketQuestionSource
    parent_question_id: UUID | None
    generated_by_run_id: UUID | None
    planner_question_key: str | None
    acceptance_criteria: list[Any]
    expected_evidence: list[Any]
    depends_on_question_ids: list[UUID]
    waiting_on_question_ids: list[UUID]
    readiness: Literal[
        "READY",
        "WAITING",
        "BLOCKED",
        "ANSWERED",
        "CANCELLED",
        "QUEUED",
        "RUNNING",
        "WAITING_FOR_REVIEW",
        "WAITING_FOR_EVIDENCE",
        "WAITING_FOR_DEPENDENCY",
        "FAILED",
        "REJECTED",
    ]


class MarketResearchGraphEdgeRead(BaseModel):
    question_id: UUID
    depends_on_question_id: UUID


class MarketResearchGraphRead(BaseModel):
    workspace_id: UUID
    nodes: list[MarketResearchGraphNodeRead]
    edges: list[MarketResearchGraphEdgeRead]
    topological_order: list[UUID]
    ready_question_ids: list[UUID]
    cycle_detected: bool


class MarketResearchGraphExecutionCreate(BaseModel):
    max_concurrency: int | None = Field(default=None, ge=1, le=4)
    max_questions: int | None = Field(default=None, ge=1, le=12)
    max_duration_minutes: int | None = Field(default=None, ge=1, le=180)
    max_cost: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=4)


class MarketResearchGraphExecutionNodeRead(BaseModel):
    question_id: UUID
    state: Literal[
        "READY",
        "QUEUED",
        "RUNNING",
        "WAITING_FOR_REVIEW",
        "WAITING_FOR_EVIDENCE",
        "WAITING_FOR_DEPENDENCY",
        "ANSWERED",
        "FAILED",
        "REJECTED",
        "BLOCKED",
        "CANCELLED",
    ]
    latest_answer_id: UUID | None = None
    latest_answer_status: str | None = None
    job_id: UUID | None = None
    error: str | None = None


class MarketResearchGraphExecutionRead(BaseModel):
    execution_id: UUID
    workspace_id: UUID
    workflow_status: Literal["RUNNING", "COMPLETED", "FAILED", "REJECTED"]
    execution_state: Literal[
        "QUEUED",
        "READY",
        "RUNNING",
        "WAITING_FOR_REVIEW",
        "WAITING_FOR_EVIDENCE",
        "WAITING_FOR_DEPENDENCY",
        "COMPLETED",
        "PARTIAL",
        "FAILED",
        "CANCELLED",
        "BUDGET_EXHAUSTED",
        "TIMED_OUT",
    ]
    graph_digest: str
    max_concurrency: int
    max_questions: int
    max_duration_minutes: int
    max_cost: Decimal | None
    cost_estimate: Decimal | None
    cost_metering_available: bool
    dispatched_count: int
    completed_count: int
    active_count: int
    ready_count: int
    waiting_review_count: int
    waiting_evidence_count: int
    failed_count: int
    node_states: list[MarketResearchGraphExecutionNodeRead]
    stop_reason: str | None
    created_at: datetime
    started_at: datetime | None
    deadline_at: datetime
    finished_at: datetime | None


class MarketResearchGraphExecutionAccepted(BaseModel):
    execution_id: UUID
    job_id: UUID
    workflow_status: str
    reused: bool


class MarketResearchGraphExecutionCancelled(BaseModel):
    execution_id: UUID
    cancelled: bool
    cancelled_jobs: int
    running_jobs: int


class MarketResearchGraphQuestionRetryAccepted(BaseModel):
    execution_id: UUID
    question_id: UUID
    job_id: UUID
    reused: bool


class MarketActivityEventRead(ORMModel):
    id: UUID
    workspace_id: UUID
    actor_type: MarketActivityActor
    actor_user_id: UUID | None
    aggregate_type: str
    aggregate_id: UUID
    event_type: str
    payload: dict[str, Any]
    created_at: datetime


# ----------------------------------------------------------- market entities
MarketEntityStatus = Literal["CANDIDATE", "REVIEWED", "VERIFIED", "REJECTED", "MERGED"]
MarketWorkspaceEntityStatus = Literal["DISCOVERED", "RESEARCHING", "INCLUDED", "EXCLUDED"]
MarketEntityCustomerLinkStatus = Literal[
    "MATCH_CANDIDATE", "USER_CONFIRMED", "REJECTED", "CONFLICTED"
]


class MarketWorkspaceEntityCreate(BaseModel):
    existing_entity_id: UUID | None = None
    entity_kind: str | None = Field(default=None, min_length=1, max_length=80)
    display_name: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=5000)
    canonical_domain: str | None = Field(default=None, max_length=500)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    attributes: dict[str, Any] = Field(default_factory=dict)
    discovery_reason: str | None = Field(default=None, max_length=2000)
    relevance_note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _entity_values_not_blank(self) -> MarketWorkspaceEntityCreate:
        if self.existing_entity_id is not None:
            if any(
                value is not None
                for value in (
                    self.entity_kind,
                    self.display_name,
                    self.description,
                    self.canonical_domain,
                    self.country_code,
                )
            ) or self.attributes:
                raise ValueError("关联已有实体时，不允许覆盖实体主档字段")
        else:
            if not self.entity_kind or not self.entity_kind.strip():
                raise ValueError("新建实体必须提供实体类型")
            if not self.display_name or not self.display_name.strip():
                raise ValueError("新建实体必须提供实体名称")
            if self.country_code is not None:
                self.country_code = self.country_code.strip().upper()
        return self


class MarketEntityAliasRead(ORMModel):
    id: UUID
    alias_type: str
    raw_value: str
    normalized_value: str
    source_evidence_id: UUID | None
    created_at: datetime


class MarketEntityRead(ORMModel):
    id: UUID
    entity_kind: str
    display_name: str
    normalized_name: str
    description: str | None
    canonical_domain: str | None
    country_code: str | None
    attributes: dict[str, Any]
    status: MarketEntityStatus
    created_at: datetime
    updated_at: datetime
    aliases: list[MarketEntityAliasRead] = Field(default_factory=list)


class MarketWorkspaceEntityRead(BaseModel):
    entity: MarketEntityRead
    discovery_reason: str | None
    relevance_note: str | None
    status: MarketWorkspaceEntityStatus
    first_seen_at: datetime
    last_seen_at: datetime
    possible_duplicate_entity_ids: list[UUID] = Field(default_factory=list)


class MarketEntityCustomerLinkCreate(BaseModel):
    customer_id: UUID
    matching_basis: dict[str, Any] = Field(default_factory=dict)


MarketInsightStatus = Literal["DRAFT", "NEEDS_REVIEW", "ACCEPTED", "REJECTED", "CONFLICTED", "SUPERSEDED"]
MarketInsightConfidence = Literal["HIGH", "MEDIUM", "LOW", "INSUFFICIENT"]
MarketEvidenceVerification = Literal["UNVERIFIED", "PARTIALLY_VERIFIED", "VERIFIED", "DISPUTED", "REJECTED"]
MarketEvidenceRelation = Literal["SUPPORTS", "CONTRADICTS", "CONTEXT"]


class MarketEvidenceCreate(BaseModel):
    canonical_ref: str = Field(min_length=8, max_length=2000)
    content: str = Field(min_length=1, max_length=250000)
    kind: str = Field(default="WEB_SOURCE", min_length=1, max_length=80)
    source: str | None = Field(default=None, max_length=300)
    published_at: datetime | None = None
    observation: str | None = Field(default=None, max_length=10000)
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _non_blank_source_content(self) -> MarketEvidenceCreate:
        if not self.content.strip():
            raise ValueError("Evidence 原文内容不能为空")
        if not self.kind.strip():
            raise ValueError("Evidence kind 不能为空")
        return self


class MarketEvidenceRead(ORMModel):
    id: UUID
    subject_type: str
    subject_id: UUID
    kind: str
    ref: str
    source: str | None
    payload: dict[str, Any]
    retrieved_at: datetime | None
    published_at: datetime | None
    canonical_ref: str | None
    content_hash: str | None
    observation: str | None
    verification_status: MarketEvidenceVerification | None
    quality: dict[str, Any] | None
    created_at: datetime


class MarketEvidenceIntelligenceItem(BaseModel):
    evidence_id: UUID
    kind: str
    source: str | None
    canonical_ref: str | None
    source_host: str | None
    content_fingerprint: str | None
    fingerprint_source: Literal["STORED_HASH", "BODY_SHA256", "UNAVAILABLE"]
    verification_status: MarketEvidenceVerification
    published_at: datetime | None
    retrieved_at: datetime | None
    age_days: int | None
    origin_assessment: Literal[
        "DUPLICATE_CONTENT", "SAME_HOST", "HOST_IDENTIFIED", "HOST_UNKNOWN"
    ]
    duplicate_content_evidence_ids: list[UUID] = Field(default_factory=list)
    same_host_evidence_ids: list[UUID] = Field(default_factory=list)
    same_source_version_evidence_ids: list[UUID] = Field(default_factory=list)
    derived_page_evidence_ids: list[UUID] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


class MarketEvidenceIntelligenceRead(BaseModel):
    workspace_id: UUID
    generated_at: datetime
    total_evidence_count: int
    analyzed_evidence_count: int
    is_truncated: bool
    distinct_host_count: int
    duplicate_content_group_count: int
    duplicate_content_evidence_count: int
    multi_version_source_count: int
    multi_version_evidence_count: int
    unverified_count: int
    partially_verified_count: int
    verified_count: int
    disputed_count: int
    rejected_count: int
    missing_publication_date_count: int
    missing_content_hash_count: int
    unextracted_search_result_count: int
    items: list[MarketEvidenceIntelligenceItem] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class MarketSourceVerificationEvidenceRead(BaseModel):
    evidence_id: UUID
    relation_type: Literal["SUPPORTS", "CONTRADICTS", "CONTEXT"]
    source_host: str | None
    content_fingerprint: str | None
    fingerprint_source: Literal["STORED_HASH", "BODY_SHA256", "UNAVAILABLE"]
    content_changed_since_assessment: bool
    status_changed_since_assessment: bool
    source_version_changed: bool
    latest_source_evidence_id: UUID | None
    unextracted: bool
    issues: list[str] = Field(default_factory=list)


class MarketSourceVerificationClaimRead(BaseModel):
    claim_id: UUID
    claim_lineage_id: UUID
    claim_version: int
    statement: str
    claim_type: Literal["FACT", "ESTIMATE", "OPINION", "FORECAST", "INFERENCE"]
    claim_status: Literal[
        "NEEDS_REVIEW", "CONFLICTED", "INSUFFICIENT", "ACCEPTED", "REJECTED"
    ]
    verification_verdict: Literal[
        "PROVISIONAL_SUPPORT", "POTENTIAL_CONTRADICTION", "CONFLICTED", "INSUFFICIENT"
    ]
    supporting_evidence_count: int
    contradicting_evidence_count: int
    distinct_host_count: int
    distinct_content_count: int
    source_origin_state: Literal[
        "NO_SUPPORTING_EVIDENCE",
        "SINGLE_SOURCE_ONLY",
        "SAME_HOST_ONLY",
        "POSSIBLE_SYNDICATION",
        "MULTIPLE_HOSTS_UNCONFIRMED",
        "CONFLICTING_SIGNALS",
    ]
    changed_source_count: int
    status_changed_source_count: int
    unextracted_source_count: int
    evidence: list[MarketSourceVerificationEvidenceRead] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


class MarketSourceVerificationActionRead(BaseModel):
    action_id: str
    action_type: Literal[
        "EXTRACT_SOURCE_PAGE",
        "COMPLETE_SOURCE_METADATA",
        "INVESTIGATE_POSSIBLE_SYNDICATION",
        "REVIEW_SOURCE_VERSION_DRIFT",
        "REVERIFY_CLAIM_AFTER_SOURCE_CHANGE",
        "REVIEW_CONTRADICTORY_EVIDENCE",
        "SEEK_SEPARATELY_ORIGINATED_CORROBORATION",
    ]
    priority: Literal["HIGH", "MEDIUM", "LOW"]
    target_type: Literal["EVIDENCE", "CLAIM"]
    target_id: UUID
    evidence_ids: list[UUID] = Field(default_factory=list)
    claim_id: UUID | None = None
    title: str
    rationale: str
    research_question_id: UUID | None = None
    research_question_status: MarketQuestionStatus | None = None


class MarketSourceVerificationPlanRead(BaseModel):
    workspace_id: UUID
    generated_at: datetime
    total_evidence_count: int
    analyzed_evidence_count: int
    evidence_truncated: bool
    total_claim_count: int
    analyzed_claim_count: int
    claims_truncated: bool
    claims_needing_corroboration_count: int
    claims_with_contradictions_count: int
    claims_with_changed_sources_count: int
    action_count: int
    actions_truncated: bool
    claims: list[MarketSourceVerificationClaimRead] = Field(default_factory=list)
    actions: list[MarketSourceVerificationActionRead] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class MarketSourceVerificationTaskRead(BaseModel):
    action_id: str
    action_type: Literal[
        "EXTRACT_SOURCE_PAGE",
        "COMPLETE_SOURCE_METADATA",
        "INVESTIGATE_POSSIBLE_SYNDICATION",
        "REVIEW_SOURCE_VERSION_DRIFT",
        "REVERIFY_CLAIM_AFTER_SOURCE_CHANGE",
        "REVIEW_CONTRADICTORY_EVIDENCE",
        "SEEK_SEPARATELY_ORIGINATED_CORROBORATION",
    ]
    question_id: UUID
    question: str
    question_status: MarketQuestionStatus
    priority: Literal["HIGH", "MEDIUM", "LOW"]
    reused: bool
    depends_on_question_ids: list[UUID] = Field(default_factory=list)


class MarketSourceVerificationPlanApplicationRead(BaseModel):
    workspace_id: UUID
    plan_digest: str
    applied_at: datetime
    action_count: int
    created_question_count: int
    reused_question_count: int
    dependency_count: int
    tasks: list[MarketSourceVerificationTaskRead] = Field(default_factory=list)
    reused_application: bool = False


class MarketInsightEvidenceInput(BaseModel):
    evidence_id: UUID
    relation_type: MarketEvidenceRelation
    rationale: str | None = Field(default=None, max_length=2000)


class MarketInsightCreate(BaseModel):
    insight_key: str = Field(min_length=1, max_length=160)
    insight_type: str = Field(min_length=1, max_length=100)
    statement: str = Field(min_length=1, max_length=10000)
    assumptions: list[Any] = Field(default_factory=list)
    unknowns: list[Any] = Field(default_factory=list)
    implications: list[Any] = Field(default_factory=list)
    confidence_level: MarketInsightConfidence = "INSUFFICIENT"
    generated_by_run_id: UUID | None = None
    evidence: list[MarketInsightEvidenceInput] = Field(default_factory=list, max_length=100)
    insufficient_evidence_reason: str | None = Field(default=None, max_length=3000)

    @model_validator(mode="after")
    def _evidence_or_gap_reason(self) -> MarketInsightCreate:
        if not self.evidence and not (self.insufficient_evidence_reason or "").strip():
            raise ValueError("Insight 必须关联证据，或明确说明证据不足")
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("同一 Insight 不得重复关联相同 Evidence")
        if not self.insight_key.strip() or not self.insight_type.strip() or not self.statement.strip():
            raise ValueError("Insight key、类型和判断内容不能为空")
        return self


class MarketInsightEvidenceRead(BaseModel):
    relation_type: MarketEvidenceRelation
    rationale: str | None
    evidence: MarketEvidenceRead


class MarketInsightRead(BaseModel):
    id: UUID
    workspace_id: UUID
    version: int
    insight_key: str
    insight_type: str
    statement: str
    assumptions: list[Any]
    unknowns: list[Any]
    implications: list[Any]
    confidence_level: MarketInsightConfidence
    status: MarketInsightStatus
    generated_by_run_id: UUID | None
    supersedes_id: UUID | None
    insufficient_evidence_reason: str | None
    created_by_user_id: UUID | None
    reviewed_by_user_id: UUID | None
    reviewed_at: datetime | None
    review_note: str | None
    created_at: datetime
    updated_at: datetime
    evidence: list[MarketInsightEvidenceRead] = Field(default_factory=list)


class MarketInsightReview(BaseModel):
    review_note: str | None = Field(default=None, max_length=3000)

MarketQuestionAnswerStatus = Literal[
    "DRAFT", "NEEDS_REVIEW", "CONFLICTED", "ACCEPTED", "REJECTED", "SUPERSEDED"
]


class MarketQuestionAnswerEvidenceInput(BaseModel):
    evidence_id: UUID
    relation_type: MarketEvidenceRelation
    rationale: str | None = Field(default=None, max_length=2000)


class MarketQuestionAnswerCreate(BaseModel):
    answer_summary: str = Field(min_length=1, max_length=12000)
    assumptions: list[Any] = Field(default_factory=list)
    unknowns: list[Any] = Field(default_factory=list)
    confidence_level: MarketInsightConfidence = "INSUFFICIENT"
    generated_by_run_id: UUID | None = None
    evidence: list[MarketQuestionAnswerEvidenceInput] = Field(default_factory=list, max_length=100)
    evidence_gap: str | None = Field(default=None, max_length=3000)

    @model_validator(mode="after")
    def _answer_has_evidence_or_gap(self) -> MarketQuestionAnswerCreate:
        if not self.answer_summary.strip():
            raise ValueError("答案内容不能为空白")
        if not self.evidence and not (self.evidence_gap or "").strip():
            raise ValueError("答案必须关联证据，或明确说明证据缺口")
        relations = [(item.evidence_id, item.relation_type) for item in self.evidence]
        if len(relations) != len(set(relations)):
            raise ValueError("答案中不得重复添加相同的 Evidence 关系")
        return self


class MarketQuestionAnswerEvidenceRead(BaseModel):
    relation_type: MarketEvidenceRelation
    rationale: str | None
    evidence: MarketEvidenceRead


class MarketQuestionAnswerRead(BaseModel):
    id: UUID
    workspace_id: UUID
    question_id: UUID
    version: int
    answer_summary: str
    assumptions: list[Any]
    unknowns: list[Any]
    confidence_level: MarketInsightConfidence
    status: MarketQuestionAnswerStatus
    evidence_gap: str | None
    generated_by_run_id: UUID | None
    supersedes_id: UUID | None
    created_by_user_id: UUID | None
    reviewed_by_user_id: UUID | None
    reviewed_at: datetime | None
    review_note: str | None
    created_at: datetime
    evidence: list[MarketQuestionAnswerEvidenceRead] = Field(default_factory=list)


class MarketQuestionAnswerReview(BaseModel):
    review_note: str | None = Field(default=None, max_length=3000)

class MarketSourceExtractionAccepted(BaseModel):
    workspace_id: UUID
    source_evidence_id: UUID
    workflow_id: UUID
    job_id: UUID
    status: Literal["PENDING", "RUNNING", "COMPLETED"]
    reused: bool


class MarketSourceExtractionRead(BaseModel):
    workspace_id: UUID
    source_evidence_id: UUID
    workflow_id: UUID
    job_id: UUID | None
    job_status: str | None
    extraction_status: Literal["QUEUED", "RUNNING", "COMPLETED", "FAILED"]
    extracted_evidence_id: UUID | None
    agent_run_id: UUID | None
    failure_code: str | None
    failure_reason: str | None
    source_url: str | None
    extractor_version: str | None
    created_at: datetime
    updated_at: datetime


class MarketClaimEvidenceSourceRead(BaseModel):
    id: UUID
    kind: str
    source: str | None
    canonical_ref: str | None
    verification_status: str
    content_hash: str | None
    observation: str | None
    created_at: datetime


class MarketClaimEvidenceRead(BaseModel):
    evidence_id: UUID
    relation_type: Literal["SUPPORTS", "CONTRADICTS", "CONTEXT"]
    rationale: str | None
    source_host: str | None
    content_hash_snapshot: str | None
    verification_status_snapshot: str
    origin_assessment: Literal[
        "SINGLE_SOURCE",
        "SAME_HOST",
        "DUPLICATE_CONTENT",
        "DISTINCT_HOST_UNCONFIRMED",
    ]
    evidence: MarketClaimEvidenceSourceRead


class MarketClaimRead(BaseModel):
    id: UUID
    workspace_id: UUID
    answer_id: UUID
    verification_run_id: UUID
    claim_lineage_id: UUID
    version: int
    claim_key: str
    statement: str
    claim_type: Literal["FACT", "ESTIMATE", "OPINION", "FORECAST", "INFERENCE"]
    scope: dict[str, Any]
    reasoning: str | None
    verification_verdict: Literal[
        "PROVISIONAL_SUPPORT",
        "POTENTIAL_CONTRADICTION",
        "CONFLICTED",
        "INSUFFICIENT",
    ]
    confidence_level: Literal["MEDIUM", "LOW", "INSUFFICIENT"]
    status: Literal[
        "NEEDS_REVIEW", "CONFLICTED", "INSUFFICIENT", "ACCEPTED", "REJECTED", "SUPERSEDED"
    ]
    supporting_evidence_count: int
    contradicting_evidence_count: int
    verified_support_count: int
    verified_contradiction_count: int
    distinct_host_count: int
    confirmed_independent_source_count: int
    limitations: list[str]
    supersedes_id: UUID | None
    reviewed_by_user_id: UUID | None
    reviewed_at: datetime | None
    review_note: str | None
    created_at: datetime
    evidence: list[MarketClaimEvidenceRead] = Field(default_factory=list)


class MarketClaimLineageRead(BaseModel):
    workspace_id: UUID
    claim_lineage_id: UUID
    claim_key: str
    latest_version: int
    current_accepted_claim_id: UUID | None
    versions: list[MarketClaimRead] = Field(default_factory=list)

class MarketClaimImpactRead(BaseModel):
    claim_lineage_id: UUID
    latest_claim_id: UUID
    latest_claim_version: int
    latest_claim_status: Literal[
        "NEEDS_REVIEW", "CONFLICTED", "INSUFFICIENT", "ACCEPTED", "REJECTED", "SUPERSEDED"
    ]
    latest_verification_verdict: Literal[
        "PROVISIONAL_SUPPORT", "POTENTIAL_CONTRADICTION", "CONFLICTED", "INSUFFICIENT"
    ]
    current_accepted_claim_id: UUID | None
    consumer_type: Literal["INSIGHT", "OPPORTUNITY"]
    consumer_id: UUID
    consumer_title: str
    consumer_status: str
    relation_types: list[MarketEvidenceRelation]
    matched_evidence_count: int
    stale_evidence_count: int
    referenced_claim_versions: list[int]
    severity: Literal["HIGH", "MEDIUM"]
    reason_code: Literal[
        "LATEST_VERSION_CONTRADICTORY",
        "LATEST_VERSION_INSUFFICIENT",
        "LATEST_VERSION_PENDING_REVIEW",
        "CONSUMER_USES_OLDER_EVIDENCE_SNAPSHOT",
        "NO_ACCEPTED_CLAIM_VERSION",
    ]


class MarketClaimImpactReportRead(BaseModel):
    workspace_id: UUID
    generated_at: datetime
    analyzed_lineage_count: int
    total_lineage_count: int
    affected_consumer_count: int
    impact_count: int
    truncated: bool
    impacts: list[MarketClaimImpactRead] = Field(default_factory=list)


class MarketClaimVerificationAccepted(BaseModel):
    job_id: UUID | None
    workflow_id: UUID
    verification_run_id: UUID
    status: Literal["PENDING", "RUNNING", "COMPLETED"]
    evidence_count: int
    reused: bool


class MarketClaimVerificationRunRead(BaseModel):
    id: UUID
    workspace_id: UUID
    answer_id: UUID
    workflow_id: UUID
    agent_run_id: UUID | None
    job_id: UUID | None
    job_status: str | None
    status: Literal["QUEUED", "RUNNING", "COMPLETED", "FAILED"]
    prompt_version: str
    input_digest: str
    claim_count: int
    degraded: bool
    limitations: list[str]
    failure_reason: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    claims: list[MarketClaimRead] = Field(default_factory=list)


class MarketClaimReview(BaseModel):
    review_note: str = Field(min_length=1, max_length=3000)

    @model_validator(mode="after")
    def _review_note_not_blank(self) -> MarketClaimReview:
        if not self.review_note.strip():
            raise ValueError("人工审核主张必须填写审核说明")
        return self


class MarketQuestionAnswerGenerationCreate(BaseModel):
    evidence_ids: list[UUID] | None = Field(default=None, max_length=24)

    @model_validator(mode="after")
    def _unique_evidence_ids(self) -> MarketQuestionAnswerGenerationCreate:
        if self.evidence_ids is not None and len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("证据列表中存在重复 ID")
        return self


class MarketQuestionAnswerGenerationAccepted(BaseModel):
    job_id: UUID
    workflow_id: UUID
    status: Literal["PENDING", "RUNNING"]
    evidence_count: int
    reused: bool




MarketOpportunityStatus = Literal[
    "DISCOVERED", "RESEARCHING", "EVIDENCE_READY", "QUALIFICATION_PENDING",
    "QUALIFIED", "ACTION_PLANNED", "WON", "LOST", "DISQUALIFIED", "ON_HOLD",
]
MarketAssessmentConfidence = Literal["HIGH", "MEDIUM", "LOW", "INSUFFICIENT"]


class MarketOpportunityCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=10000)
    next_action: str | None = Field(default=None, max_length=2000)
    owner_user_id: UUID | None = None
    created_by_run_id: UUID | None = None

    @model_validator(mode="after")
    def _title_not_blank(self) -> MarketOpportunityCreate:
        if not self.title.strip():
            raise ValueError("机会标题不能为空白")
        return self


class MarketOpportunityRead(ORMModel):
    id: UUID
    workspace_id: UUID
    title: str
    description: str | None
    status: MarketOpportunityStatus
    next_action: str | None
    qualification_reason: str | None
    disqualification_reason: str | None
    assessment_version: int
    assessment_dimensions: dict[str, Any]
    assessment_summary: str | None
    assessment_confidence: MarketAssessmentConfidence
    created_by_run_id: UUID | None
    owner_user_id: UUID | None
    created_by_user_id: UUID | None
    qualified_by_user_id: UUID | None
    qualified_at: datetime | None
    outcome_recorded_by_user_id: UUID | None
    outcome_recorded_at: datetime | None
    outcome_note: str | None
    created_at: datetime
    updated_at: datetime


class MarketOpportunityEvidenceCreate(BaseModel):
    evidence_id: UUID
    relation_type: MarketEvidenceRelation
    rationale: str | None = Field(default=None, max_length=2000)


class MarketOpportunityEvidenceRead(BaseModel):
    relation_type: MarketEvidenceRelation
    rationale: str | None
    evidence: MarketEvidenceRead


class MarketOpportunityEntitiesAttach(BaseModel):
    entity_ids: list[UUID] = Field(min_length=1, max_length=100)
    relationship_note: str | None = Field(default=None, max_length=2000)


class MarketOpportunityAssessmentCreate(BaseModel):
    dimensions: dict[str, dict[str, Any]]
    summary: str | None = Field(default=None, max_length=5000)
    confidence_level: MarketAssessmentConfidence = "INSUFFICIENT"
    assessed_by_run_id: UUID | None = None

    @model_validator(mode="after")
    def _dimensions_present(self) -> MarketOpportunityAssessmentCreate:
        if not self.dimensions:
            raise ValueError("评估至少需要一个维度")
        return self


class MarketOpportunityAssessmentRead(ORMModel):
    id: UUID
    opportunity_id: UUID
    version: int
    dimensions: dict[str, Any]
    summary: str | None
    confidence_level: MarketAssessmentConfidence
    assessed_by_run_id: UUID | None
    assessed_by_user_id: UUID | None
    created_at: datetime


class MarketOpportunityDetailRead(BaseModel):
    opportunity: MarketOpportunityRead
    evidence: list[MarketOpportunityEvidenceRead]
    entities: list[MarketEntityRead]
    assessments: list[MarketOpportunityAssessmentRead]


class MarketOpportunityTransition(BaseModel):
    target_status: Literal[
        "RESEARCHING", "EVIDENCE_READY", "QUALIFICATION_PENDING",
        "ACTION_PLANNED", "ON_HOLD", "DISQUALIFIED",
    ]
    note: str | None = Field(default=None, max_length=3000)


class MarketOpportunityQualify(BaseModel):
    qualification_reason: str = Field(min_length=1, max_length=3000)

    @model_validator(mode="after")
    def _reason_not_blank(self) -> MarketOpportunityQualify:
        if not self.qualification_reason.strip():
            raise ValueError("确认机会资格必须提供理由")
        return self


class MarketOpportunityOutcome(BaseModel):
    outcome: Literal["WON", "LOST"]
    outcome_note: str = Field(min_length=1, max_length=5000)

    @model_validator(mode="after")
    def _note_not_blank(self) -> MarketOpportunityOutcome:
        if not self.outcome_note.strip():
            raise ValueError("记录结果必须提供业务事件或结果说明")
        return self


class MarketWorkspaceTripLinkCreate(BaseModel):
    trip_id: UUID
    link_note: str | None = Field(default=None, max_length=2000)


class MarketWorkspaceTripLinkRead(BaseModel):
    id: UUID
    workspace_id: UUID
    trip_id: UUID
    link_status: Literal["LINKED", "UNLINKED"]
    link_note: str | None
    linked_by_user_id: UUID | None
    linked_at: datetime
    unlinked_by_user_id: UUID | None
    unlinked_at: datetime | None
    trip_name: str | None
    trip_status: TripStatus | None
    window_start: datetime | None
    window_end: datetime | None
    countries: list[str]


class MarketOpportunityVisitLinkCreate(BaseModel):
    trip_id: UUID
    visit_id: UUID
    link_note: str | None = Field(default=None, max_length=2000)


class MarketOpportunityVisitUnlink(BaseModel):
    unlink_note: str | None = Field(default=None, max_length=2000)


class MarketOpportunityVisitCandidateRead(BaseModel):
    trip_id: UUID
    trip_name: str
    trip_status: str
    trip_window_start: datetime
    trip_window_end: datetime
    visit_id: UUID
    visit_title: str | None
    visit_objective: str | None
    visit_status: str
    customer_id: UUID | None
    customer_name: str | None
    place_id: UUID
    place_name: str
    city: str | None
    country_code: str | None
    latitude: float | None
    longitude: float | None
    timezone: str | None
    time_window_start: datetime | None
    time_window_end: datetime | None
    service_minutes: int
    priority: int
    business_value: Decimal
    movement_mode: str


class MarketOpportunityVisitLinkRead(MarketOpportunityVisitCandidateRead):
    id: UUID
    workspace_id: UUID
    opportunity_id: UUID
    link_status: Literal["LINKED", "UNLINKED"]
    link_note: str | None
    linked_by_user_id: UUID | None
    linked_at: datetime
    unlinked_by_user_id: UUID | None
    unlinked_at: datetime | None
    unlink_note: str | None


class MarketOpportunityExecutionRead(BaseModel):
    workspace_id: UUID
    opportunity_id: UUID
    opportunity_status: MarketOpportunityStatus
    can_link_visits: bool
    blocked_reason: str | None
    links: list[MarketOpportunityVisitLinkRead] = Field(default_factory=list)
    candidates: list[MarketOpportunityVisitCandidateRead] = Field(default_factory=list)


class MarketEntityCustomerLinkRead(ORMModel):
    id: UUID
    entity_id: UUID
    customer_id: UUID
    link_status: MarketEntityCustomerLinkStatus
    matching_basis: dict[str, Any]
    confirmed_by_user_id: UUID | None
    confirmed_at: datetime | None
    created_at: datetime
    updated_at: datetime


# -------------------------------------------------------------- flight segments
FlightBookingStatus = Literal["TENTATIVE", "BOOKED", "TICKETED", "CANCELLED"]
FlightSegmentSource = Literal["USER_ENTERED", "ITINERARY_IMPORT", "PROVIDER_IMPORT"]
FlightSegmentBindingStatus = Literal["ACTIVE", "RELEASED"]
FlightSegmentBindingState = Literal["UNLINKED", "CURRENT_PLAN", "STALE_PLAN_BINDING"]


class FlightSegmentCreate(BaseModel):
    """One actual flight segment; sensitive PNR / ticket numbers are intentionally excluded."""

    flight_designator: str = Field(min_length=2, max_length=12)
    departure_airport_iata: str = Field(min_length=3, max_length=3)
    arrival_airport_iata: str = Field(min_length=3, max_length=3)
    scheduled_departure_at: datetime
    scheduled_arrival_at: datetime
    departure_timezone: str = Field(min_length=1, max_length=64)
    arrival_timezone: str = Field(min_length=1, max_length=64)
    booking_status: FlightBookingStatus = "BOOKED"
    source: FlightSegmentSource = "USER_ENTERED"
    tracking_provider: str | None = Field(default=None, min_length=1, max_length=40)
    tracking_reference: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("flight_designator")
    @classmethod
    def _normalise_flight_designator(cls, value: str) -> str:
        normalized = value.strip().upper().replace(" ", "")
        if not 2 <= len(normalized) <= 12:
            raise ValueError("flight_designator 长度必须为 2 至 12")
        if not fullmatch(r"[A-Z0-9]+", normalized):
            raise ValueError("flight_designator 只能包含字母和数字")
        return normalized

    @field_validator("departure_airport_iata", "arrival_airport_iata")
    @classmethod
    def _normalise_airport_code(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not fullmatch(r"[A-Z]{3}", normalized):
            raise ValueError("机场代码必须是 3 位 IATA 字母代码")
        return normalized

    @field_validator("tracking_provider")
    @classmethod
    def _normalise_tracking_provider(cls, value: str | None) -> str | None:
        return value.strip().lower() if value is not None else None

    @field_validator("tracking_reference")
    @classmethod
    def _normalise_tracking_reference(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def _validate_schedule_and_tracking(self) -> FlightSegmentCreate:
        if self.departure_airport_iata == self.arrival_airport_iata:
            raise ValueError("departure_airport_iata 与 arrival_airport_iata 必须不同")
        for field_name, value in (
            ("scheduled_departure_at", self.scheduled_departure_at),
            ("scheduled_arrival_at", self.scheduled_arrival_at),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field_name} 必须包含时区偏移")
        if self.scheduled_arrival_at <= self.scheduled_departure_at:
            raise ValueError("scheduled_arrival_at 必须晚于 scheduled_departure_at")
        for field_name, zone_name in (
            ("departure_timezone", self.departure_timezone),
            ("arrival_timezone", self.arrival_timezone),
        ):
            try:
                ZoneInfo(zone_name)
            except ZoneInfoNotFoundError as exc:
                raise ValueError(f"{field_name} 必须是有效的 IANA 时区") from exc
        if (self.tracking_provider is None) != (self.tracking_reference is None):
            raise ValueError("tracking_provider 与 tracking_reference 必须同时提供或同时省略")
        return self


class FlightSegmentPlanBindingCreate(BaseModel):
    plan_travel_leg_id: UUID


class FlightSegmentPlanBindingRead(ORMModel):
    id: UUID
    plan_version_id: UUID
    plan_travel_leg_id: UUID
    status: FlightSegmentBindingStatus
    linked_by_user_id: UUID
    linked_at: datetime
    released_by_user_id: UUID | None = None
    released_at: datetime | None = None
    release_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class FlightSegmentRead(ORMModel):
    id: UUID
    trip_id: UUID
    flight_designator: str
    departure_airport_iata: str
    arrival_airport_iata: str
    scheduled_departure_at: datetime
    scheduled_arrival_at: datetime
    departure_timezone: str
    arrival_timezone: str
    booking_status: FlightBookingStatus
    source: FlightSegmentSource
    tracking_provider: str | None = None
    tracking_reference: str | None = None
    created_by_user_id: UUID | None = None
    created_at: datetime
    updated_at: datetime
    bindings: list[FlightSegmentPlanBindingRead] = Field(default_factory=list)
    binding_state: FlightSegmentBindingState = "UNLINKED"
