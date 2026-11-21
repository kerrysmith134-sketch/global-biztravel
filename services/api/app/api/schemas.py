"""API 契约的 Pydantic 模型（设计文档 v2.0 第 5 章）。

约定：
- 所有枚举以**字符串字面量**暴露，与数据库 CHECK 约束一致；客户端做分支判断时依赖它。
- ``from_attributes=True`` 让模型可直接由 ORM 对象构造，避免在路由里手写字段搬运。
- 分页统一 ``{"items": [...], "next_cursor": null}``。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


class CustomerMapSummaryRead(BaseModel):
    customers_with_locations: int = 0
    customers_without_locations: int = 0
    locations_total: int = 0
    locations_mapped: int = 0
    locations_missing_coordinates: int = 0
    countries: dict[str, int] = Field(default_factory=dict)


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
JobType = Literal["plan_trip", "draft_minutes", "crm_sync", "fx_rate_sync"]
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
    """按 callsign 拉取航迹（OpenSky，免钥公开源，ADR-0034）；信号挂到出行。"""

    trip_id: UUID
    callsign: str = Field(min_length=1, max_length=20)


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
    aircraft: int
    signals_ingested: int
    items: list[FlightPullItemRead] = Field(default_factory=list)


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


class ExpenseRead(ORMModel):
    id: UUID
    trip_id: UUID
    category: ExpenseCategory
    amount: Decimal
    currency: str
    occurred_on: date
    note: str | None = None
    receipt_ref: str | None = None
    created_at: datetime


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
ActionKind = Literal["SUGGESTION", "EXPENSE_REPORT", "JOB", "CRM_SYNC"]


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
