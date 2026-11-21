"""任务处理器：``job_type`` → 可执行函数。

处理器契约（与 ``job_service`` 的分工）：

- 收到一个**已经处于 ``RUNNING``、且租约属于当前 Worker** 的任务（由 ``main`` 负责抢占）。
- 只在当前会话里写数据，**不提交事务**——提交点由 ``main`` 决定，这样"处理器留下的副作用"
  与"任务状态结算"能在一个事务里一起落库。
- 拿不出结果时抛 ``TaskFailed``。``retryable=True`` 表示"这次是环境问题，重试有意义"
  （``main`` 会回滚本事务）；``retryable=False`` 表示"重试必然重复同一个错误"
  （``main`` 会**保留**本事务已经写的副作用再结算，用于"先把失败事件发出去，再把任务标终态"）。

为什么要区分是否保留副作用：计划不可行时我们**必须**把 ``plan.not_feasible`` 事件留在库里，
否则消费者永远不会把 Trip 从 ``PLANNING`` 拨回 ``DRAFT``，用户界面上那次出行就永远停在
"正在规划中"。既然结果本身是确定的失败，就没必要为了回滚而丢掉这条通知。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session
from travel_planner import SolverFailed, plan_trip

from app.api.errors import ApiError
from app.config import get_settings
from app.integrations.crm import CrmUnavailable
from app.integrations.fx import FxMarketUnavailable
from app.integrations.research_search import (
    ResearchSearchUnavailable,
    get_research_search_provider,
)
from app.models.reliability import BackgroundJob
from app.models.trip import Trip
from app.models.workflow import AgentRun, WorkflowInstance
from app.services import (
    crm_service,
    evidence_service,
    fx_market_service,
    job_service,
    memory_service,
    minutes_service,
    outbox_service,
    planning_service,
)


class TaskFailed(RuntimeError):
    """任务处理失败。``retryable`` 决定是"换个时间再试"还是"到此为止"。"""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.message = message
        self.retryable = retryable


TaskHandler = Callable[[Session, BackgroundJob], dict[str, Any]]


def handle_plan_trip(session: Session, job: BackgroundJob) -> dict[str, Any]:
    """为一次出行求解并落一份候选计划版本。

    失败路径统一走 ``_reject``：先发 ``plan.not_feasible`` 事件（并把 Trip 拨回 ``DRAFT``
    的责任交给消费者），再抛 ``TaskFailed(retryable=False)``。三种失败共用一条路径：
    输入不可用（缺坐标、窗口冲突）、求解器双双失败、引擎判定不可行。
    """
    settings = get_settings()
    raw_trip_id = (job.payload or {}).get("trip_id")
    if not raw_trip_id:
        raise TaskFailed("任务载荷缺少 trip_id", retryable=False)
    try:
        trip_id = UUID(str(raw_trip_id))
    except ValueError as exc:
        raise TaskFailed(f"任务载荷中的 trip_id 不是合法 UUID：{raw_trip_id}", retryable=False) from exc

    trip = session.get(Trip, trip_id)
    if trip is None:
        raise TaskFailed("出行不存在（可能已被删除）", retryable=False)

    try:
        build = planning_service.build_problem(session, job.organization_id, trip)
    except ApiError as exc:
        _reject(session, trip, reason=exc.message, code=exc.code)
        raise TaskFailed(exc.message, retryable=False) from exc

    try:
        result = plan_trip(
            build.problem,
            solver=planning_service.solver_kind_for(settings.planning_solver),
            explanation_ref=f"job:{job.id}",
        )
    except SolverFailed as exc:
        _reject(session, trip, reason=str(exc), code="SOLVER_FAILED")
        raise TaskFailed(str(exc), retryable=False) from exc

    if not result.validation.feasible:
        failures = ", ".join(f"{check.rule}: {check.detail}" for check in result.validation.failures)
        _reject(session, trip, reason=f"独立验证器判定计划不可行（{failures}）", code="PLAN_NOT_FEASIBLE")
        raise TaskFailed("独立验证器判定计划不可行", retryable=False)

    version = planning_service.persist_plan(
        session,
        job.organization_id,
        trip,
        build,
        result,
        created_by_user_id=job.created_by_user_id,
        job_id=job.id,
    )

    # 已排入计划的拜访转为 SCHEDULED，表示"这次行程里它有了确定的位置"。
    planning_service.mark_visit_status(session, result.placed_visit_ids, status="SCHEDULED")

    # 过程记忆：记下"这次算过、结果如何"。失败路径由 _reject 记另一条。
    memory_service.log_attempt(
        session,
        job.organization_id,
        trip.id,
        attempt_type=memory_service.ATTEMPT_GENERATED,
        outcome="FEASIBLE",
        details={
            "solver": result.solver.value,
            "degraded": result.degraded,
            "objective_value": result.objective_value,
            "placed": len(result.placed_visit_ids),
            "unplaced": len(result.unplaced),
            "job_id": str(job.id),
        },
        plan_version_id=version.id,
    )

    # 事件带上 plan_version_id：消费者据此判断"这份计划是否已经被激活过"。
    # dedupe_key 用 job.id：同一个任务被重跑（例如租约过期后重排）不会产生第二条同义事件。
    outbox_service.emit(
        session,
        job.organization_id,
        aggregate_type=outbox_service.AGGREGATE_TRIP,
        aggregate_id=trip.id,
        event_type=outbox_service.EVENT_PLAN_VERSION_CREATED,
        payload={
            "trip_id": str(trip.id),
            "plan_version_id": str(version.id),
            "version_number": version.version_number,
            "solver": result.solver.value,
            "degraded": result.degraded,
            "objective_value": result.objective_value,
            "job_id": str(job.id),
        },
        consumers=(outbox_service.CONSUMER_TRIP_STATUS_SYNC,),
        dedupe_key=f"plan-version-created:{job.id}",
    )
    session.flush()

    return {
        "plan_version_id": str(version.id),
        "version_number": version.version_number,
        "solver": result.solver.value,
        "objective_value": result.objective_value,
        "degraded": result.degraded,
        "placed_visits": [str(vid) for vid in result.placed_visit_ids],
        "unplaced": [
            {"visit_id": str(item["visit_id"]), "reason": str(item["reason"]), "source": str(item["source"])}
            for item in version.unplaced
        ],
    }


def _reject(session: Session, trip: Trip, *, reason: str, code: str) -> None:
    """记下"这次规划没能产出计划"，并立刻 flush（``retryable=False`` 时不回滚，见模块文档）。"""
    memory_service.log_attempt(
        session,
        trip.organization_id,
        trip.id,
        attempt_type=memory_service.ATTEMPT_REJECTED,
        outcome=code,
        reason=reason,
    )
    outbox_service.emit(
        session,
        trip.organization_id,
        aggregate_type=outbox_service.AGGREGATE_TRIP,
        aggregate_id=trip.id,
        event_type=outbox_service.EVENT_PLAN_NOT_FEASIBLE,
        payload={"trip_id": str(trip.id), "code": code, "reason": reason},
        consumers=(outbox_service.CONSUMER_TRIP_STATUS_SYNC,),
        dedupe_key=f"plan-not-feasible:{trip.id}:{trip.version}",
    )
    session.flush()


def handle_draft_minutes(session: Session, job: BackgroundJob) -> dict[str, Any]:
    """为一条现场记录生成会议纪要草稿（异步任务，PRD §4.1 summary-jobs）。"""
    visit_id = (job.payload or {}).get("visit_id")
    if not visit_id:
        raise TaskFailed("任务载荷缺少 visit_id", retryable=False)
    try:
        draft = minutes_service.draft_minutes(session, job.organization_id, visit_id)
    except ApiError as exc:
        raise TaskFailed(exc.message, retryable=False) from exc
    return {
        "draft_id": str(draft.invocation_id),
        "llm_invocation_id": str(draft.invocation_id),
        "title": draft.title,
        "summary": draft.summary,
        "decisions": draft.decisions,
        "follow_up_tasks": draft.follow_up_tasks,
        "degraded": draft.degraded,
    }


def handle_crm_sync(session: Session, job: BackgroundJob) -> dict[str, Any]:
    """把一条拜访记录整理并交给 CRM 适配器。默认 file adapter 只标 PREPARED。"""
    crm_id = (job.payload or {}).get("crm_sync_id")
    if not crm_id:
        raise TaskFailed("任务载荷缺少 crm_sync_id", retryable=False)
    try:
        crm_job = crm_service.get_job(session, job.organization_id, crm_id)
        return crm_service.execute_sync(session, crm_job)
    except CrmUnavailable as exc:
        raise TaskFailed(str(exc), retryable=False) from exc
    except ApiError as exc:
        raise TaskFailed(exc.message, retryable=False) from exc


def handle_fx_sync(session: Session, job: BackgroundJob) -> dict[str, Any]:
    """把公开行情源的最新快照写进组织 `fx_rates`（ADR-0033）。

    未配置行情源/网络不可用都按**不可重试失败**结算：行情是日期性快照，
    下次手动同步自然拿到新数据，自动重试只会重复同一个错误。
    """
    payload = job.payload or {}
    base = payload.get("base_currency")
    symbols = payload.get("symbols")
    if not base or not isinstance(symbols, list) or not symbols:
        raise TaskFailed("任务载荷缺少 base_currency 或 symbols", retryable=False)
    try:
        summary = fx_market_service.sync_rates(
            session,
            job.organization_id,
            base_currency=str(base),
            symbols=[str(item) for item in symbols],
        )
    except FxMarketUnavailable as exc:
        raise TaskFailed(str(exc), retryable=False) from exc
    except ApiError as exc:
        raise TaskFailed(exc.message, retryable=False) from exc
    return {"status": "SUCCEEDED", **summary}


def handle_research_execute(session: Session, job: BackgroundJob) -> dict[str, Any]:
    """Execute only the queries reviewed in the user's ResearchPlan.

    Search hits are persisted as unverified snippets with source URLs and collection times.
    This task does not synthesize conclusions or claim fact verification.
    """
    raw_plan_id = (job.payload or {}).get("plan_id")
    if not raw_plan_id:
        raise TaskFailed("研究任务载荷缺少 plan_id", retryable=False)
    try:
        plan_id = UUID(str(raw_plan_id))
    except ValueError as exc:
        raise TaskFailed("研究任务的 plan_id 不是合法 UUID", retryable=False) from exc

    workflow = session.scalars(
        select(WorkflowInstance).where(
            WorkflowInstance.organization_id == str(job.organization_id),
            WorkflowInstance.id == plan_id,
            WorkflowInstance.workflow_type == "research_plan",
        ).with_for_update()
    ).one_or_none()
    if workflow is None:
        raise TaskFailed("研究计划不存在", retryable=False)
    if workflow.job_id is not None and workflow.job_id != job.id:
        raise TaskFailed("研究任务已被更新的任务取代", retryable=False)

    payload = dict(workflow.payload or {})
    plan = payload.get("plan") or {}
    if not bool(plan.get("external_research_needed")):
        raise TaskFailed("研究计划没有外部检索意图", retryable=False)
    queries = plan.get("search_queries")
    if not isinstance(queries, list):
        raise TaskFailed("研究计划的 search_queries 格式无效", retryable=False)
    settings = get_settings()
    queries = [
        item.strip()[:500]
        for item in queries[: max(1, settings.research_search_max_queries)]
        if isinstance(item, str) and item.strip()
    ]
    if not queries:
        raise TaskFailed("研究计划没有可执行的搜索查询", retryable=False)

    provider = get_research_search_provider()
    if not provider.configured:
        raise TaskFailed("研究搜索 Provider 已取消配置", retryable=False)

    now = datetime.now(UTC)
    run = AgentRun(
        organization_id=str(job.organization_id),
        workflow_id=workflow.id,
        agent_type="research_executor",
        status="RUNNING",
        input={
            "plan_id": str(workflow.id),
            "query_count": len(queries),
            "query_sha256": [
                __import__("hashlib").sha256(item.encode("utf-8")).hexdigest()
                for item in queries
            ],
            "provider": provider.name,
        },
    )
    session.add(run)
    session.flush()

    unique_results: dict[str, Any] = {}
    language = payload.get("locale")
    language = language if isinstance(language, str) and language.strip() else None
    try:
        for query in queries:
            for result in provider.search(query, language=language):
                unique_results.setdefault(result.url, (query, result))
    except ResearchSearchUnavailable as exc:
        run.status = "FAILED"
        run.error = str(exc)[:4000]
        run.finished_at = datetime.now(UTC)
        # Temporary network/provider failures should use the existing queue retry policy.
        raise TaskFailed(str(exc), retryable=True) from exc

    collected_at = datetime.now(UTC)
    evidence_items = []
    for query, result in unique_results.values():
        evidence_items.append({
            "kind": "WEB_SEARCH_RESULT",
            "ref": result.url,
            "source": result.provider,
            "payload": {
                "url": result.url,
                "title": result.title,
                "snippet": result.snippet,
                "query": query,
                "engine": result.engine,
                "published_at": result.published_at,
                "evidence_state": "UNVERIFIED_SEARCH_RESULT",
            },
            "retrieved_at": collected_at.isoformat(),
        })
    evidence_service.record_many(
        session,
        job.organization_id,
        subject_type="RESEARCH_PLAN",
        subject_id=workflow.id,
        items=evidence_items,
    )
    existing_evidence = evidence_service.list_for_subject(
        session,
        job.organization_id,
        subject_type="RESEARCH_PLAN",
        subject_id=workflow.id,
    )
    web_evidence_count = sum(1 for item in existing_evidence if item.kind == "WEB_SEARCH_RESULT")

    run.output = {
        "provider": provider.name,
        "query_count": len(queries),
        "new_result_count": len(evidence_items),
        "total_web_evidence_count": web_evidence_count,
        "verification_status": "NOT_VERIFIED",
        "retrieved_at": collected_at.isoformat(),
    }
    run.status = "SUCCEEDED"
    run.finished_at = collected_at
    payload["retrieval_status"] = "COMPLETED"
    payload["retrieval_error"] = None
    payload["results_count"] = web_evidence_count
    payload["retrieval_provider"] = provider.name
    payload["retrieved_at"] = collected_at.isoformat()
    payload["retrieval_query_count"] = len(queries)
    workflow.payload = payload
    workflow.status = "COMPLETED"
    session.flush()

    return {
        "plan_id": str(workflow.id),
        "retrieval_status": "COMPLETED",
        "provider": provider.name,
        "query_count": len(queries),
        "new_result_count": len(evidence_items),
        "results_count": web_evidence_count,
        "verification_status": "NOT_VERIFIED",
    }


#: 任务处理器注册表。新增 ``job_type`` 时必须同时在这里登记，否则任务会以
#: "没有注册处理器"直接终态失败（这是刻意的：静默丢弃任务比报错更糟）。
TASK_HANDLERS: Mapping[str, TaskHandler] = {
    job_service.JOB_TYPE_PLAN_TRIP: handle_plan_trip,
    job_service.JOB_TYPE_DRAFT_MINUTES: handle_draft_minutes,
    job_service.JOB_TYPE_CRM_SYNC: handle_crm_sync,
    job_service.JOB_TYPE_FX_SYNC: handle_fx_sync,
    job_service.JOB_TYPE_RESEARCH_EXECUTE: handle_research_execute,
}


__all__ = [
    "TASK_HANDLERS",
    "TaskFailed",
    "TaskHandler",
    "handle_crm_sync",
    "handle_draft_minutes",
    "handle_fx_sync",
    "handle_plan_trip",
    "handle_research_execute",
]