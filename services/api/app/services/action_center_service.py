"""行动中心聚合服务（PRD S8-08 / 页面 06）。

不新增"待办"表：待办是已有事实的**视图**。建议、报销单、后台任务、CRM 同步各自是权威数据，
这里只按"用户现在要做什么"重新分组。这样任何一方状态变化都会立即反映，不会出现两份待办
互相对不上的问题。

分类与 PRD 对齐：

- ``DECISION``：待用户决定/批准的建议；
- ``APPROVAL``：已提交、等待审批的报销单；
- ``RUNNING``：排队或执行中的后台任务；
- ``FAILED``：终态失败、需要人看的任务；
- ``PREPARATION``：已准备但尚未完成外部确认（CRM 待导入文件）；
- ``COMPLETED``：最近完成的建议（仅保留短期窗口，避免行动中心退化成历史日志）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.crm import CrmSyncJob
from app.models.expense_reports import ExpenseReport, ExpenseReportItem
from app.models.proactive import ProactiveSuggestion
from app.models.reliability import BackgroundJob
from app.models.records import VisitFollowUp
from app.models.trip import Trip, Visit

CATEGORY_DECISION = "DECISION"
CATEGORY_APPROVAL = "APPROVAL"
CATEGORY_RUNNING = "RUNNING"
CATEGORY_FAILED = "FAILED"
CATEGORY_PREPARATION = "PREPARATION"
CATEGORY_COMPLETED = "COMPLETED"

CATEGORY_ORDER = (
    CATEGORY_DECISION,
    CATEGORY_APPROVAL,
    CATEGORY_FAILED,
    CATEGORY_RUNNING,
    CATEGORY_PREPARATION,
    CATEGORY_COMPLETED,
)

CATEGORY_TITLES = {
    CATEGORY_DECISION: "需要我决定",
    CATEGORY_APPROVAL: "等待我的批准",
    CATEGORY_RUNNING: "系统正在处理",
    CATEGORY_FAILED: "执行失败",
    CATEGORY_PREPARATION: "等待外部确认",
    CATEGORY_COMPLETED: "已完成",
}

COMPLETED_WINDOW = timedelta(days=7)
SECTION_LIMIT = 50

JOB_LABELS = {
    "plan_trip": "生成出行计划",
    "draft_minutes": "生成会议纪要",
    "crm_sync": "CRM 同步",
}

SEVERITY_RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


@dataclass
class ActionItem:
    category: str
    kind: str
    entity_id: str
    title: str
    detail: str
    status: str
    severity: str | None
    trip_id: str | None
    trip_name: str | None
    action_hint: str
    available_actions: list[str] = field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def sort_key(self) -> tuple[int, float]:
        return (
            SEVERITY_RANK.get(self.severity or "LOW", 3),
            -(self.updated_at or self.created_at or datetime.min.replace(tzinfo=UTC)).timestamp(),
        )


def _trip_names(session: Session, organization_id: str, ids: set[str]) -> dict[str, str]:
    if not ids:
        return {}
    rows = session.execute(
        select(Trip.id, Trip.name).where(
            Trip.organization_id == organization_id,
            Trip.id.in_([UUID(item) for item in ids]),
        )
    ).all()
    return {str(row[0]): row[1] for row in rows}


def _suggestion_items(
    session: Session, organization_id: str, trip_id: str | None, now: datetime
) -> tuple[list[ActionItem], list[ActionItem]]:
    pending_query = select(ProactiveSuggestion).where(
        ProactiveSuggestion.organization_id == organization_id,
        ProactiveSuggestion.status == "PENDING",
    )
    if trip_id:
        pending_query = pending_query.where(ProactiveSuggestion.trip_id == trip_id)
    pending = list(
        session.scalars(
            pending_query.order_by(ProactiveSuggestion.created_at.desc()).limit(SECTION_LIMIT)
        )
    )

    completed_query = select(ProactiveSuggestion).where(
        ProactiveSuggestion.organization_id == organization_id,
        ProactiveSuggestion.status == "EXECUTED",
        ProactiveSuggestion.executed_at >= now - COMPLETED_WINDOW,
    )
    if trip_id:
        completed_query = completed_query.where(ProactiveSuggestion.trip_id == trip_id)
    completed = list(
        session.scalars(
            completed_query.order_by(ProactiveSuggestion.executed_at.desc()).limit(5)
        )
    )

    def item(row: ProactiveSuggestion, category: str) -> ActionItem:
        return ActionItem(
            category=category,
            kind="SUGGESTION",
            entity_id=str(row.id),
            title=row.title,
            detail=row.rationale,
            status=row.status,
            severity=row.severity,
            trip_id=str(row.trip_id),
            trip_name=None,
            action_hint=(
                "批准后由业务服务执行准备动作"
                if row.requires_approval
                else "查看建议与证据后决定是否处理"
            ),
            available_actions=(
                ["APPROVE", "DISMISS", "OPEN_TRIP"]
                if category == CATEGORY_DECISION and row.requires_approval
                else ["OPEN_TRIP"]
            ),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    return (
        [item(row, CATEGORY_DECISION) for row in pending],
        [item(row, CATEGORY_COMPLETED) for row in completed],
    )


def _report_items(
    session: Session, organization_id: str, trip_id: str | None
) -> list[ActionItem]:
    query = select(ExpenseReport).where(
        ExpenseReport.organization_id == organization_id,
        ExpenseReport.status == "SUBMITTED",
    )
    if trip_id:
        query = query.where(ExpenseReport.trip_id == trip_id)
    reports = list(
        session.scalars(query.order_by(ExpenseReport.submitted_at.desc()).limit(SECTION_LIMIT))
    )
    if not reports:
        return []
    counts = dict(
        session.execute(
            select(ExpenseReportItem.report_id, func.count())
            .where(
                ExpenseReportItem.organization_id == organization_id,
                ExpenseReportItem.report_id.in_([row.id for row in reports]),
            )
            .group_by(ExpenseReportItem.report_id)
        ).all()
    )
    return [
        ActionItem(
            category=CATEGORY_APPROVAL,
            kind="EXPENSE_REPORT",
            entity_id=str(row.id),
            title="报销单待审批",
            detail=f"{int(counts.get(row.id, 0))} 项费用快照，提交后金额不再随源记录变化",
            status=row.status,
            severity="MEDIUM",
            trip_id=str(row.trip_id),
            trip_name=None,
            action_hint="审批前请核对费用明细与凭证",
            available_actions=["OPEN_TRIP"],
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in reports
    ]


def _job_items(
    session: Session, organization_id: str, trip_id: str | None
) -> tuple[list[ActionItem], list[ActionItem]]:
    query = select(BackgroundJob).where(
        BackgroundJob.organization_id == organization_id,
        BackgroundJob.status.in_(("PENDING", "RUNNING", "FAILED", "DEAD")),
        BackgroundJob.job_type != "crm_sync",
    )
    jobs = list(
        session.scalars(query.order_by(BackgroundJob.created_at.desc()).limit(SECTION_LIMIT * 2))
    )
    if not jobs:
        return [], []

    visit_ids = [
        UUID(str(row.payload["visit_id"]))
        for row in jobs
        if not row.payload.get("trip_id") and row.payload.get("visit_id")
    ]
    visit_trips: dict[str, str] = {}
    if visit_ids:
        visit_trips = {
            str(visit_id): str(trip)
            for visit_id, trip in session.execute(
                select(Visit.id, Visit.trip_id).where(
                    Visit.organization_id == organization_id,
                    Visit.id.in_(visit_ids),
                )
            ).all()
        }

    running: list[ActionItem] = []
    failed: list[ActionItem] = []
    for row in jobs:
        raw_trip = row.payload.get("trip_id")
        resolved_trip = str(raw_trip) if raw_trip else visit_trips.get(str(row.payload.get("visit_id")))
        if trip_id and resolved_trip != trip_id:
            continue
        label = JOB_LABELS.get(row.job_type, row.job_type)
        is_failed = row.status in ("FAILED", "DEAD")
        target = failed if is_failed else running
        target.append(
            ActionItem(
                category=CATEGORY_FAILED if is_failed else CATEGORY_RUNNING,
                kind="JOB",
                entity_id=str(row.id),
                title=label,
                detail=(
                    f"第 {row.attempts} 次尝试失败：{row.last_error or '未记录原因'}"
                    if is_failed
                    else f"任务已排队/执行中（第 {row.attempts} 次尝试）"
                ),
                status=row.status,
                severity="HIGH" if is_failed else "LOW",
                trip_id=resolved_trip,
                trip_name=None,
                action_hint="失败任务需要人工判断后重试或修复输入",
                available_actions=["OPEN_TRIP"] if resolved_trip else [],
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
        )
    return running, failed


def _crm_items(
    session: Session, organization_id: str, trip_id: str | None
) -> tuple[list[ActionItem], list[ActionItem]]:
    query = select(CrmSyncJob).where(
        CrmSyncJob.organization_id == organization_id,
        CrmSyncJob.status.in_(("PREPARED", "FAILED")),
    )
    if trip_id:
        query = query.where(CrmSyncJob.trip_id == trip_id)
    rows = list(
        session.scalars(query.order_by(CrmSyncJob.created_at.desc()).limit(SECTION_LIMIT))
    )

    prepared: list[ActionItem] = []
    failed: list[ActionItem] = []
    for row in rows:
        if row.status == "PREPARED":
            detail = (row.result or {}).get("detail") or "待导入文件已生成，尚未收到外部 CRM 回执"
            prepared.append(
                ActionItem(
                    category=CATEGORY_PREPARATION,
                    kind="CRM_SYNC",
                    entity_id=str(row.id),
                    title="客户拜访记录待导入 CRM",
                    detail=str(detail),
                    status=row.status,
                    severity="LOW",
                    trip_id=str(row.trip_id),
                    trip_name=None,
                    action_hint="文件适配器只生成待导入文件，不代表外部系统已接收",
                    available_actions=["OPEN_TRIP"],
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
            )
        else:
            failed.append(
                ActionItem(
                    category=CATEGORY_FAILED,
                    kind="CRM_SYNC",
                    entity_id=str(row.id),
                    title="CRM 同步失败",
                    detail=row.error or "未记录失败原因",
                    status=row.status,
                    severity="MEDIUM",
                    trip_id=str(row.trip_id),
                    trip_name=None,
                    action_hint="检查 CRM 配置或稍后重试",
                    available_actions=["OPEN_TRIP"],
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
            )
    return prepared, failed



def _visit_follow_up_items(
    session: Session, organization_id: str, trip_id: str | None, now: datetime
) -> tuple[list[ActionItem], list[ActionItem]]:
    base = select(VisitFollowUp).where(
        VisitFollowUp.organization_id == organization_id,
    )
    if trip_id:
        base = base.where(VisitFollowUp.trip_id == trip_id)
    active_rows = list(
        session.scalars(
            base.where(VisitFollowUp.status.in_(("OPEN", "IN_PROGRESS")))
            .order_by(VisitFollowUp.updated_at.desc())
            .limit(SECTION_LIMIT)
        )
    )
    completed_rows = list(
        session.scalars(
            base.where(
                VisitFollowUp.status == "COMPLETED",
                VisitFollowUp.completed_at >= now - COMPLETED_WINDOW,
            )
            .order_by(VisitFollowUp.completed_at.desc())
            .limit(SECTION_LIMIT)
        )
    )

    def item(row: VisitFollowUp, category: str) -> ActionItem:
        due = f" · 截止 {row.due_date}" if row.due_date else ""
        state = "进行中" if row.status == "IN_PROGRESS" else (
            "已完成" if row.status == "COMPLETED" else "待开始"
        )
        return ActionItem(
            category=category,
            kind="VISIT_FOLLOW_UP",
            entity_id=str(row.id),
            title=row.title,
            detail=f"{state}{due}",
            status=row.status,
            severity="LOW",
            trip_id=str(row.trip_id),
            trip_name=None,
            action_hint="打开对应行程，在原拜访记录中更新进度；不会自动写入 CRM",
            available_actions=["OPEN_TRIP"],
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    return (
        [item(row, CATEGORY_PREPARATION) for row in active_rows],
        [item(row, CATEGORY_COMPLETED) for row in completed_rows],
    )


def build_action_center(
    session: Session,
    organization_id: UUID | str,
    *,
    trip_id: UUID | str | None = None,
) -> dict:
    org = str(organization_id)
    trip_filter = str(trip_id) if trip_id else None
    now = datetime.now(UTC)

    suggestions, completed_suggestions = _suggestion_items(session, org, trip_filter, now)
    active_follow_ups, completed_follow_ups = _visit_follow_up_items(
        session, org, trip_filter, now
    )
    reports = _report_items(session, org, trip_filter)
    running_jobs, failed_jobs = _job_items(session, org, trip_filter)
    prepared_crm, failed_crm = _crm_items(session, org, trip_filter)

    groups: dict[str, list[ActionItem]] = {
        CATEGORY_DECISION: suggestions,
        CATEGORY_APPROVAL: reports,
        CATEGORY_RUNNING: running_jobs,
        CATEGORY_FAILED: failed_jobs + failed_crm,
        CATEGORY_PREPARATION: prepared_crm + active_follow_ups,
        CATEGORY_COMPLETED: completed_suggestions + completed_follow_ups,
    }
    for items in groups.values():
        items.sort(key=lambda item: item.sort_key())

    trip_names = _trip_names(
        session,
        org,
        {item.trip_id for items in groups.values() for item in items if item.trip_id},
    )
    for items in groups.values():
        for item in items:
            if item.trip_id:
                item.trip_name = trip_names.get(item.trip_id)

    sections = [
        {
            "key": key,
            "title": CATEGORY_TITLES[key],
            "items": [
                {
                    "category": item.category,
                    "kind": item.kind,
                    "entity_id": item.entity_id,
                    "title": item.title,
                    "detail": item.detail,
                    "status": item.status,
                    "severity": item.severity,
                    "trip_id": item.trip_id,
                    "trip_name": item.trip_name,
                    "action_hint": item.action_hint,
                    "available_actions": item.available_actions,
                    "created_at": item.created_at,
                    "updated_at": item.updated_at,
                }
                for item in groups[key][:SECTION_LIMIT]
            ],
        }
        for key in CATEGORY_ORDER
    ]
    counts = {key: len(groups[key]) for key in CATEGORY_ORDER}
    return {
        "generated_at": now,
        "total": sum(counts.values()),
        "counts": counts,
        "sections": sections,
    }


__all__ = [
    "CATEGORY_ORDER",
    "CATEGORY_TITLES",
    "build_action_center",
]
