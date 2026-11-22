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

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from travel_planner import SolverFailed, plan_trip

from app.api.errors import ApiError
from app.config import get_settings
from app.integrations.crm import CrmUnavailable
from app.integrations.fx import FxMarketUnavailable
from app.integrations.research_page import ResearchPageFetchError, get_research_page_provider
from app.integrations.research_search import (
    ResearchSearchUnavailable,
    get_research_search_provider,
)
from app.llm.contracts import LLMRequest
from app.llm.gateway import get_gateway
from app.llm.prompts import (
    MARKET_CLAIM_VERIFY_PROMPT_VERSION,
    MARKET_CLAIM_VERIFY_SCHEMA,
    MARKET_CLAIM_VERIFY_SYSTEM,
    MARKET_CLAIM_VERIFY_TEMPLATE_ID,
    MARKET_QUESTION_ANSWER_PROMPT_VERSION,
    MARKET_QUESTION_ANSWER_SCHEMA,
    MARKET_QUESTION_ANSWER_SYSTEM,
    MARKET_QUESTION_ANSWER_TEMPLATE_ID,
)
from app.models.market import (
    MarketActivityEvent,
    MarketClaim,
    MarketClaimEvidence,
    MarketClaimVerificationRun,
    MarketEvidenceDerivation,
    MarketResearchQuestion,
    MarketResearchQuestionAnswer,
    MarketResearchQuestionAnswerEvidence,
    MarketWorkspaceWorkflow,
)
from app.models.reliability import BackgroundJob
from app.models.trip import Trip
from app.models.workflow import AgentRun, Evidence, WorkflowInstance
from app.services import (
    crm_service,
    evidence_service,
    fx_market_service,
    job_service,
    market_claim_verification_service,
    market_insight_service,
    market_question_answer_agent_service,
    market_question_answer_service,
    market_research_dag_service,
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



def _parse_research_published_at(value: Any) -> datetime | None:
    """Parse a source publication timestamp conservatively; unknown stays NULL."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


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
    target_question_id: UUID | None = None
    if payload.get("target_question_id"):
        try:
            target_question_id = UUID(str(payload["target_question_id"]))
            target_workspace_id = UUID(str(payload["workspace_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise TaskFailed("问题级 ResearchPlan 的目标引用无效", retryable=False) from exc
        target_question = session.scalars(
            select(MarketResearchQuestion).where(
                MarketResearchQuestion.organization_id == str(job.organization_id),
                MarketResearchQuestion.workspace_id == target_workspace_id,
                MarketResearchQuestion.id == target_question_id,
            ).with_for_update()
        ).one_or_none()
        if target_question is None or target_question.status in {"BLOCKED", "CANCELLED"}:
            reason = "目标研究问题已不存在、阻塞或取消，检索未启动"
            payload["retrieval_status"] = "FAILED"
            payload["retrieval_error"] = reason
            workflow.payload = payload
            workflow.status = "FAILED"
            session.flush()
            raise TaskFailed(reason, retryable=False)
    if not bool(plan.get("external_research_needed")):
        raise TaskFailed("研究计划没有外部检索意图", retryable=False)
    queries = payload.get("reviewed_search_queries")
    if not isinstance(queries, list):
        raise TaskFailed("研究计划缺少用户确认的搜索查询", retryable=False)
    queries = [
        item.strip()[:500]
        for item in queries
        if isinstance(item, str) and item.strip()
    ]
    if not queries or len(queries) > 12:
        raise TaskFailed("用户确认的搜索查询数量不符合约束", retryable=False)

    reviewed_digest = sha256("\n".join(queries).encode("utf-8")).hexdigest()
    expected_workflow_digest = payload.get("reviewed_search_queries_sha256")
    expected_job_digest = (job.payload or {}).get("query_digest")
    if (
        reviewed_digest != expected_workflow_digest
        or reviewed_digest != expected_job_digest
    ):
        raise TaskFailed("用户确认的搜索查询哈希校验失败", retryable=False)

    provider = get_research_search_provider()
    if not provider.configured:
        raise TaskFailed("研究搜索 Provider 已取消配置", retryable=False)

    run = AgentRun(
        organization_id=str(job.organization_id),
        workflow_id=workflow.id,
        agent_type="research_executor",
        status="RUNNING",
        input={
            "plan_id": str(workflow.id),
            "query_count": len(queries),
            "query_sha256": [
                sha256(item.encode("utf-8")).hexdigest()
                for item in queries
            ],
            "provider": provider.name,
        },
    )
    session.add(run)
    session.flush()

    unique_results: dict[str, tuple[Any, list[str]]] = {}
    language = payload.get("locale")
    language = language if isinstance(language, str) and language.strip() else None
    try:
        for query in queries:
            for result in provider.search(query, language=language):
                existing = unique_results.get(result.url)
                if existing is None:
                    unique_results[result.url] = (result, [query])
                elif query not in existing[1]:
                    existing[1].append(query)
    except ResearchSearchUnavailable as exc:
        run.status = "FAILED"
        run.error = str(exc)[:4000]
        run.finished_at = datetime.now(UTC)
        # Temporary network/provider failures should use the existing queue retry policy.
        raise TaskFailed(str(exc), retryable=True) from exc

    if target_question_id is not None:
        target_workspace_id = UUID(str(payload["workspace_id"]))
        target_question = session.scalars(
            select(MarketResearchQuestion).where(
                MarketResearchQuestion.organization_id == str(job.organization_id),
                MarketResearchQuestion.workspace_id == target_workspace_id,
                MarketResearchQuestion.id == target_question_id,
            ).with_for_update()
        ).one_or_none()
        if target_question is None or target_question.status in {"BLOCKED", "CANCELLED"}:
            # Provider work may already have completed, but a cancelled node must
            # not receive new Evidence after the user stopped that research target.
            reason = "目标研究问题在检索期间已阻塞或取消；来源未写入 Workspace"
            run.status = "FAILED"
            run.error = reason
            run.finished_at = datetime.now(UTC)
            payload["retrieval_status"] = "FAILED"
            payload["retrieval_error"] = reason
            workflow.payload = payload
            workflow.status = "FAILED"
            session.flush()
            raise TaskFailed(reason, retryable=False)

    collected_at = datetime.now(UTC)
    workspace_ids = list(
        session.scalars(
            select(MarketWorkspaceWorkflow.workspace_id).where(
                MarketWorkspaceWorkflow.organization_id == job.organization_id,
                MarketWorkspaceWorkflow.workflow_id == workflow.id,
            )
        ).all()
    )
    evidence_items = []
    if workspace_ids:
        # A Workspace owns the deduplicated source/content version. The link table
        # separately retains which ResearchPlan and reviewed query discovered it.
        for workspace_id in workspace_ids:
            for result, matched_queries in unique_results.values():
                title = result.title.strip()
                snippet = result.snippet.strip()
                content = "\n\n".join(part for part in (title, snippet) if part)
                if not content:
                    continue
                evidence = market_insight_service.record_market_evidence(
                    session,
                    job.organization_id,
                    workspace_id,
                    canonical_ref=result.url,
                    content=content,
                    kind="WEB_SEARCH_RESULT",
                    source=result.provider,
                    published_at=_parse_research_published_at(result.published_at),
                    observation=snippet or title,
                    verification_status="UNVERIFIED",
                    quality=None,
                    payload={
                        "url": result.url,
                        "title": result.title,
                        "snippet": result.snippet,
                        "query": matched_queries[0],
                        "matched_queries": matched_queries,
                        "engine": result.engine,
                        "published_at_raw": result.published_at,
                        "evidence_state": "UNVERIFIED_SEARCH_RESULT",
                        "content_kind": "search_result_snippet",
                        "research_plan_id": str(workflow.id),
                        "question_scope_managed": bool(payload.get("target_question_id")),
                        "target_question_id": payload.get("target_question_id"),
                        "agent_run_id": str(run.id),
                        "retrieved_at": collected_at.isoformat(),
                    },
                    actor_user_id=None,
                )
                for query in matched_queries:
                    market_insight_service.link_research_plan_evidence(
                        session,
                        job.organization_id,
                        workspace_id,
                        workflow.id,
                        evidence.id,
                        search_query=query,
                        collected_by_run_id=run.id,
                        question_id=(
                            UUID(str(payload["target_question_id"]))
                            if payload.get("target_question_id")
                            else None
                        ),
                    )
                evidence_items.append(
                    {
                        "evidence_id": str(evidence.id),
                        "workspace_id": str(workspace_id),
                        "canonical_ref": evidence.canonical_ref,
                        "content_hash": evidence.content_hash,
                    }
                )
        linked_evidence = market_insight_service.list_research_plan_linked_evidence(
            session, job.organization_id, workflow.id
        )
        web_evidence_count = sum(
            1 for item in linked_evidence if item.kind == "WEB_SEARCH_RESULT"
        )
        evidence_storage_mode = "WORKSPACE_VERSIONED"
    else:
        # Standalone ResearchPlans retain their original subject-scoped Evidence behavior.
        for result, matched_queries in unique_results.values():
            evidence_items.append({
                "kind": "WEB_SEARCH_RESULT",
                "ref": result.url,
                "source": result.provider,
                "payload": {
                    "url": result.url,
                    "title": result.title,
                    "snippet": result.snippet,
                    "query": matched_queries[0],
                    "matched_queries": matched_queries,
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
        web_evidence_count = sum(
            1 for item in existing_evidence if item.kind == "WEB_SEARCH_RESULT"
        )
        evidence_storage_mode = "RESEARCH_PLAN_COMPAT"


    run.output = {
        "provider": provider.name,
        "query_count": len(queries),
        "new_result_count": len(evidence_items),
        "total_web_evidence_count": web_evidence_count,
        "verification_status": "NOT_VERIFIED",
        "evidence_storage_mode": evidence_storage_mode,
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
    payload["evidence_storage_mode"] = evidence_storage_mode
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
        "evidence_storage_mode": evidence_storage_mode,
    }


def handle_market_research_dag_schedule(
    session: Session, job: BackgroundJob
) -> dict[str, Any]:
    """Advance a durable Workspace research DAG through the shared Worker queue."""
    raw_execution_id = (job.payload or {}).get("execution_workflow_id")
    try:
        execution_id = UUID(str(raw_execution_id))
    except (TypeError, ValueError) as exc:
        raise TaskFailed("研究图调度任务缺少合法 execution_workflow_id", retryable=False) from exc
    try:
        return market_research_dag_service.run_scheduler(
            session,
            job.organization_id,
            execution_id,
        )
    except ApiError as exc:
        raise TaskFailed(exc.message, retryable=False) from exc


def _answer_text_list(value: Any, *, limit: int = 12, item_limit: int = 1200) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value[:limit]:
        if isinstance(item, str) and item.strip():
            result.append(item.strip()[:item_limit])
    return result


def handle_market_question_answer_draft(
    session: Session, job: BackgroundJob
) -> dict[str, Any]:
    """Draft an evidence-cited answer; never accept it or mutate the question status."""
    payload = job.payload or {}
    try:
        workspace_id = UUID(str(payload["workspace_id"]))
        question_id = UUID(str(payload["question_id"]))
        workflow_id = UUID(str(payload["workflow_id"]))
        raw_evidence_ids = payload.get("evidence_ids") or []
        evidence_ids = [UUID(str(item)) for item in raw_evidence_ids]
    except (KeyError, TypeError, ValueError) as exc:
        raise TaskFailed("研究问题答案任务载荷无效", retryable=False) from exc

    workflow = session.scalars(
        select(WorkflowInstance).where(
            WorkflowInstance.organization_id == str(job.organization_id),
            WorkflowInstance.id == workflow_id,
            WorkflowInstance.workflow_type == market_question_answer_agent_service.WORKFLOW_TYPE,
        ).with_for_update()
    ).one_or_none()
    if workflow is None:
        raise TaskFailed("研究问题答案 Workflow 不存在", retryable=False)
    if workflow.job_id is not None and workflow.job_id != job.id:
        raise TaskFailed("研究问题答案任务已被更新的任务取代", retryable=False)

    dag_execution_id = (payload.get("dag_execution_workflow_id") or "").strip()
    if dag_execution_id:
        try:
            parsed_execution_id = UUID(dag_execution_id)
        except ValueError as exc:
            raise TaskFailed("答案任务包含无效的 DAG execution ID", retryable=False) from exc
        parent_execution = session.scalars(
            select(WorkflowInstance).where(
                WorkflowInstance.organization_id == str(job.organization_id),
                WorkflowInstance.id == parsed_execution_id,
                WorkflowInstance.workflow_type == market_research_dag_service.WORKFLOW_TYPE,
            )
        ).one_or_none()
        if parent_execution is None or parent_execution.status != "RUNNING":
            # A task may have been claimed just before its parent execution was
            # cancelled or stopped. Persist a truthful child state and do not start
            # a new provider call after the stop boundary.
            workflow.status = "REJECTED"
            workflow.payload = {
                **(workflow.payload or {}),
                "generation_status": "CANCELLED",
                "generation_error": "父研究图执行已停止，未启动模型调用",
            }
            session.flush()
            return {
                "workspace_id": str(workspace_id),
                "question_id": str(question_id),
                "workflow_id": str(workflow.id),
                "cancelled": True,
                "reason": "PARENT_EXECUTION_STOPPED",
            }

    try:
        question, evidence_rows = (
            market_question_answer_agent_service.load_answer_draft_inputs(
                session,
                job.organization_id,
                workspace_id,
                question_id,
                evidence_ids,
                workflow_id,
            )
        )
    except ApiError as exc:
        workflow.status = "FAILED"
        workflow.payload = {
            **(workflow.payload or {}),
            "generation_status": "FAILED",
            "generation_error": exc.message[:2000],
        }
        session.flush()
        raise TaskFailed(exc.message, retryable=False) from exc

    evidence_context: list[dict[str, Any]] = []
    evidence_by_id: dict[str, Any] = {}
    for row in evidence_rows:
        body = str((row.payload or {}).get("content") or "").strip()
        source_ref = row.canonical_ref or (row.payload or {}).get("url") or row.ref
        item = {
            "evidence_id": str(row.id),
            "source_ref": str(source_ref)[:2000],
            "kind": row.kind,
            "source": row.source,
            "published_at": row.published_at.isoformat() if row.published_at else None,
            "verification_status": row.verification_status or "UNVERIFIED",
            "quality": row.quality or {},
            "content": body[:4000],
            "content_truncated": len(body) > 4000,
        }
        evidence_context.append(item)
        evidence_by_id[str(row.id)] = row

    variables = {
        "workspace_id": str(workspace_id),
        "question_id": str(question_id),
        "question": question.question,
        "question_rationale": question.rationale,
        "acceptance_criteria": question.acceptance_criteria or [],
        "evidence": evidence_context,
        "evidence_count": len(evidence_context),
    }
    run = AgentRun(
        organization_id=str(job.organization_id),
        workflow_id=workflow.id,
        agent_type="MARKET_QUESTION_ANSWER",
        status="RUNNING",
        input={
            "job_id": str(job.id),
            "workspace_id": str(workspace_id),
            "question_id": str(question_id),
            "evidence_ids": [str(item) for item in evidence_ids],
            "prompt_template_id": MARKET_QUESTION_ANSWER_TEMPLATE_ID,
            "prompt_version": MARKET_QUESTION_ANSWER_PROMPT_VERSION,
        },
    )
    session.add(run)
    session.flush()

    fallback: dict[str, Any] = {
        "answer_summary": (
            "当前工作空间尚无可引用的来源材料，无法形成有证据支持的答案。"
            if not evidence_context
            else "当前模型服务未能可靠生成答案；现有来源材料仍需人工分析和核验。"
        ),
        "assumptions": [],
        "unknowns": (
            ["尚未采集或关联可供分析的来源证据。"]
            if not evidence_context
            else ["模型生成未成功；本次没有形成可审核的证据判断。"]
        ),
        "confidence_level": "INSUFFICIENT",
        "evidence_assessments": [],
        "evidence_gap": (
            "当前没有可引用的 Evidence。请先采集或关联来源材料，再重新生成答案。"
            if not evidence_context
            else "LLM Gateway 已降级或输出不可用；答案未由模型可靠生成。"
        ),
    }
    request = LLMRequest(
        task="market_question_answer_draft",
        prompt_template_id=MARKET_QUESTION_ANSWER_TEMPLATE_ID,
        prompt_version=MARKET_QUESTION_ANSWER_PROMPT_VERSION,
        variables=variables,
        schema=MARKET_QUESTION_ANSWER_SCHEMA,
        fallback=fallback,
        system=MARKET_QUESTION_ANSWER_SYSTEM,
        max_tokens=1800,
        temperature=0.1,
    )
    try:
        response = get_gateway().complete(
            session,
            job.organization_id,
            request,
            trace_id=str(job.id),
        )
    except Exception as exc:  # Gateway unexpected failure should retry with the job lease policy.
        raise TaskFailed(
            f"研究问题答案 LLM Gateway 调用失败：{type(exc).__name__}: {exc}",
            retryable=True,
        ) from exc

    output = response.output or fallback
    if response.degraded or not evidence_context:
        output = fallback
    answer_summary = output.get("answer_summary")
    if not isinstance(answer_summary, str) or not answer_summary.strip():
        output = fallback
        answer_summary = fallback["answer_summary"]

    assumptions = _answer_text_list(output.get("assumptions"))
    unknowns = _answer_text_list(output.get("unknowns"))
    confidence = str(output.get("confidence_level") or "INSUFFICIENT").upper()
    if confidence not in {"HIGH", "MEDIUM", "LOW", "INSUFFICIENT"}:
        confidence = "INSUFFICIENT"

    answer_evidence: list[dict[str, Any]] = []
    excluded_citations = 0
    assessments = output.get("evidence_assessments")
    if isinstance(assessments, list):
        seen: set[tuple[UUID, str]] = set()
        for assessment in assessments[:50]:
            if not isinstance(assessment, dict):
                continue
            try:
                evidence_id = UUID(str(assessment.get("evidence_id")))
            except (TypeError, ValueError):
                excluded_citations += 1
                continue
            relation = str(assessment.get("relation_type") or "").upper()
            key = (evidence_id, relation)
            if str(evidence_id) not in evidence_by_id or relation not in {
                "SUPPORTS",
                "CONTRADICTS",
                "CONTEXT",
            } or key in seen:
                excluded_citations += 1
                continue
            seen.add(key)
            rationale = assessment.get("rationale")
            answer_evidence.append(
                {
                    "evidence_id": evidence_id,
                    "relation_type": relation,
                    "rationale": (
                        rationale.strip()[:2000]
                        if isinstance(rationale, str) and rationale.strip()
                        else None
                    ),
                }
            )

    evidence_gap = output.get("evidence_gap")
    evidence_gap = (
        evidence_gap.strip()[:3000]
        if isinstance(evidence_gap, str) and evidence_gap.strip()
        else None
    )
    if excluded_citations:
        unknowns.append(
            f"模型输出中有 {excluded_citations} 条引用未能映射到本次提供的 Evidence，已排除。"
        )
    if not answer_evidence and evidence_gap is None:
        evidence_gap = "模型未能建立答案主张与可用 Evidence 之间的明确映射。"
    if not answer_evidence and not evidence_gap:
        evidence_gap = "当前没有可用于采纳答案的证据关联。"

    # Unverified source snippets can never support a HIGH-confidence answer.
    has_unverified = any(
        (row.verification_status or "UNVERIFIED") != "VERIFIED"
        for row in evidence_rows
    )
    if has_unverified and confidence == "HIGH":
        confidence = "MEDIUM"
    if not any(item["relation_type"] == "SUPPORTS" for item in answer_evidence):
        confidence = "INSUFFICIENT"
        if evidence_gap is None:
            evidence_gap = (
                "本次答案没有建立任何 SUPPORTS 证据关系；当前材料不足以支撑直接结论。"
            )

    answer = market_question_answer_service.create_answer(
        session,
        job.organization_id,
        workspace_id,
        question_id,
        answer_summary=answer_summary.strip()[:12000],
        assumptions=assumptions,
        unknowns=unknowns,
        confidence_level=confidence,
        generated_by_run_id=run.id,
        evidence=answer_evidence,
        evidence_gap=evidence_gap,
        created_by_user_id=None,
    )
    run.status = "SUCCEEDED"
    run.output = {
        "answer_id": str(answer["id"]),
        "answer_version": answer["version"],
        "llm_invocation_id": str(response.invocation_id),
        "model": response.model,
        "degraded": response.degraded,
        "cached": response.cached,
        "selected_evidence_count": len(evidence_context),
        "linked_evidence_count": len(answer_evidence),
        "excluded_citation_count": excluded_citations,
        "confidence_level": confidence,
        "answer_status": answer["status"],
    }
    run.finished_at = datetime.now(UTC)
    workflow.payload = {
        **(workflow.payload or {}),
        "generation_status": "COMPLETED",
        "answer_id": str(answer["id"]),
        "answer_version": answer["version"],
        "agent_run_id": str(run.id),
        "llm_invocation_id": str(response.invocation_id),
        "degraded": response.degraded,
    }
    workflow.status = "COMPLETED"
    session.flush()
    return {
        "workspace_id": str(workspace_id),
        "question_id": str(question_id),
        "workflow_id": str(workflow.id),
        "agent_run_id": str(run.id),
        "answer_id": str(answer["id"]),
        "answer_version": answer["version"],
        "answer_status": answer["status"],
        "llm_invocation_id": str(response.invocation_id),
        "degraded": response.degraded,
        "linked_evidence_count": len(answer_evidence),
    }


def _claim_source_host(row: Evidence) -> str | None:
    reference = row.canonical_ref or row.ref
    try:
        host = urlsplit(reference).hostname
    except ValueError:
        return None
    return host.casefold().rstrip(".") if host else None


def _claim_evidence_digest(row: Evidence) -> str:
    if row.content_hash:
        return row.content_hash.casefold()
    content = str((row.payload or {}).get("content") or "")
    return sha256(content.encode("utf-8")).hexdigest()
def handle_market_claim_verification(
    session: Session, job: BackgroundJob
) -> dict[str, Any]:
    """Extract atomic claims and map each one to only the selected answer Evidence."""
    payload = job.payload or {}
    try:
        workspace_id = UUID(str(payload["workspace_id"]))
        answer_id = UUID(str(payload["answer_id"]))
        workflow_id = UUID(str(payload["workflow_id"]))
        verification_run_id = UUID(str(payload["verification_run_id"]))
        evidence_ids = [UUID(str(item)) for item in payload.get("evidence_ids") or []]
    except (KeyError, TypeError, ValueError) as exc:
        raise TaskFailed("主张核验任务载荷无效", retryable=False) from exc

    workflow = session.scalars(
        select(WorkflowInstance).where(
            WorkflowInstance.organization_id == job.organization_id,
            WorkflowInstance.id == workflow_id,
            WorkflowInstance.workflow_type == market_claim_verification_service.WORKFLOW_TYPE,
        ).with_for_update()
    ).one_or_none()
    if workflow is None:
        raise TaskFailed("主张核验 Workflow 不存在", retryable=False)
    if workflow.job_id is not None and workflow.job_id != job.id:
        raise TaskFailed("主张核验任务已被更新的任务取代", retryable=False)

    verification_run = session.scalars(
        select(MarketClaimVerificationRun).where(
            MarketClaimVerificationRun.organization_id == job.organization_id,
            MarketClaimVerificationRun.workspace_id == workspace_id,
            MarketClaimVerificationRun.id == verification_run_id,
            MarketClaimVerificationRun.workflow_id == workflow_id,
        ).with_for_update()
    ).one_or_none()
    if verification_run is None:
        raise TaskFailed("主张核验运行记录不存在", retryable=False)

    answer = session.scalars(
        select(MarketResearchQuestionAnswer)
        .where(
            MarketResearchQuestionAnswer.organization_id == job.organization_id,
            MarketResearchQuestionAnswer.workspace_id == workspace_id,
            MarketResearchQuestionAnswer.id == answer_id,
        )
        .with_for_update()
    ).one_or_none()
    if answer is None:
        raise TaskFailed("研究答案版本不存在", retryable=False)
    if answer.status in {"REJECTED", "SUPERSEDED"}:
        raise TaskFailed("研究答案版本已驳回或已被替代，不能继续核验", retryable=False)

    answer_links = session.scalars(
        select(MarketResearchQuestionAnswerEvidence).where(
            MarketResearchQuestionAnswerEvidence.organization_id == job.organization_id,
            MarketResearchQuestionAnswerEvidence.workspace_id == workspace_id,
            MarketResearchQuestionAnswerEvidence.answer_id == answer_id,
        )
    ).all()
    linked_ids = {row.evidence_id for row in answer_links}
    if not evidence_ids or len(evidence_ids) != len(set(evidence_ids)):
        raise TaskFailed("主张核验必须提供非空且不重复的 Evidence ID 列表", retryable=False)
    if not set(evidence_ids).issubset(linked_ids):
        raise TaskFailed("主张核验输入包含未关联到该答案版本的 Evidence", retryable=False)

    evidence_rows = list(
        session.scalars(
            select(Evidence).where(
                Evidence.organization_id == job.organization_id,
                Evidence.id.in_(evidence_ids),
            )
        ).all()
    )
    by_id = {row.id: row for row in evidence_rows}
    if len(by_id) != len(evidence_ids):
        raise TaskFailed("主张核验所引用的 Evidence 已删除或不可用", retryable=False)
    linked_workflow_ids = set(
        session.scalars(
            select(MarketWorkspaceWorkflow.workflow_id).where(
                MarketWorkspaceWorkflow.organization_id == job.organization_id,
                MarketWorkspaceWorkflow.workspace_id == workspace_id,
            )
        ).all()
    )
    for evidence_row in by_id.values():
        valid_scope = (
            evidence_row.subject_type == "market_workspace"
            and evidence_row.subject_id == workspace_id
        ) or (
            evidence_row.subject_type == "RESEARCH_PLAN"
            and evidence_row.subject_id in linked_workflow_ids
        )
        if not valid_scope:
            raise TaskFailed(
                "主张核验拒绝使用不属于当前 Workspace 或其 ResearchPlan 的 Evidence",
                retryable=False,
            )
    ordered_evidence = [by_id[item] for item in evidence_ids]
    content_evidence = [
        row for row in ordered_evidence
        if isinstance((row.payload or {}).get("content"), str)
        and (row.payload or {}).get("content", "").strip()
    ]
    if len(content_evidence) != len(ordered_evidence):
        raise TaskFailed("主张核验输入中有 Evidence 缺少正文或摘要", retryable=False)
    actual_digest = market_claim_verification_service.compute_input_digest(
        answer, content_evidence
    )
    if actual_digest != verification_run.input_digest:
        raise TaskFailed(
            "入队后的答案或 Evidence 内容/状态发生变化，输入快照校验失败",
            retryable=False,
        )

    now = datetime.now(UTC)
    verification_run.status = "RUNNING"
    verification_run.started_at = verification_run.started_at or now
    verification_run.failure_reason = None
    workflow.status = "RUNNING"
    workflow.payload = {
        **(workflow.payload or {}),
        "verification_status": "RUNNING",
        "started_at": now.isoformat(),
    }

    evidence_context: list[dict[str, Any]] = []
    evidence_by_id: dict[str, Evidence] = {}
    for row in content_evidence:
        body = str((row.payload or {}).get("content") or "").strip()
        evidence_context.append(
            {
                "evidence_id": str(row.id),
                "source_ref": str(row.canonical_ref or row.ref)[:2000],
                "source_host": _claim_source_host(row),
                "kind": row.kind,
                "source": row.source,
                "published_at": row.published_at.isoformat() if row.published_at else None,
                "verification_status": row.verification_status or "UNVERIFIED",
                "content_hash": row.content_hash,
                "content": body[:4000],
                "content_truncated": len(body) > 4000,
            }
        )
        evidence_by_id[str(row.id)] = row

    run = AgentRun(
        organization_id=job.organization_id,
        workflow_id=workflow.id,
        agent_type="MARKET_CLAIM_VERIFICATION",
        status="RUNNING",
        input={
            "job_id": str(job.id),
            "workspace_id": str(workspace_id),
            "answer_id": str(answer_id),
            "answer_version": answer.version,
            "verification_run_id": str(verification_run.id),
            "evidence_ids": [str(row.id) for row in content_evidence],
            "prompt_template_id": MARKET_CLAIM_VERIFY_TEMPLATE_ID,
            "prompt_version": MARKET_CLAIM_VERIFY_PROMPT_VERSION,
            "input_digest": verification_run.input_digest,
        },
    )
    session.add(run)
    session.flush()
    verification_run.agent_run_id = run.id

    fallback: dict[str, Any] = {
        "claims": [],
        "limitations": [
            "LLM Gateway 未能可靠完成主张提取；本次未生成可审核的原子主张。",
            "网页 Evidence 的来源独立性和事实真实性未由本次运行确认。",
        ],
    }
    variables = {
        "workspace_id": str(workspace_id),
        "answer_id": str(answer_id),
        "answer_version": answer.version,
        "answer_summary": answer.answer_summary,
        "answer_assumptions": answer.assumptions or [],
        "answer_unknowns": answer.unknowns or [],
        "evidence": evidence_context,
        "evidence_count": len(evidence_context),
    }
    request = LLMRequest(
        task="market_claim_verify",
        prompt_template_id=MARKET_CLAIM_VERIFY_TEMPLATE_ID,
        prompt_version=MARKET_CLAIM_VERIFY_PROMPT_VERSION,
        variables=variables,
        schema=MARKET_CLAIM_VERIFY_SCHEMA,
        fallback=fallback,
        system=MARKET_CLAIM_VERIFY_SYSTEM,
        max_tokens=2600,
        temperature=0.1,
    )
    try:
        response = get_gateway().complete(
            session,
            job.organization_id,
            request,
            trace_id=str(job.id),
        )
    except Exception as exc:  # Gateway failure should use the shared job retry policy.
        raise TaskFailed(
            f"主张核验 LLM Gateway 调用失败：{type(exc).__name__}: {exc}",
            retryable=True,
        ) from exc

    output = response.output or fallback
    if response.degraded:
        output = fallback
    raw_claims = output.get("claims")
    raw_claims = raw_claims if isinstance(raw_claims, list) else []
    raw_limitations = output.get("limitations")
    limitations = [
        item.strip()[:1000]
        for item in raw_limitations[:30]
        if isinstance(item, str) and item.strip()
    ] if isinstance(raw_limitations, list) else []
    if response.degraded:
        limitations.extend(fallback["limitations"])

    created_claims: list[MarketClaim] = []
    seen_claim_keys: set[str] = set()
    excluded_claim_count = 0
    excluded_assessment_count = 0

    if not response.degraded:
        for candidate in raw_claims[:30]:
            if not isinstance(candidate, dict):
                excluded_claim_count += 1
                continue
            statement_value = candidate.get("claim_text")
            if not isinstance(statement_value, str):
                excluded_claim_count += 1
                continue
            statement = " ".join(statement_value.split()).strip()
            if not statement or len(statement) > 2000:
                excluded_claim_count += 1
                continue
            normalized = statement.casefold()

            claim_type = str(candidate.get("claim_type") or "INFERENCE").upper()
            if claim_type not in {"FACT", "ESTIMATE", "OPINION", "FORECAST", "INFERENCE"}:
                claim_type = "INFERENCE"
            raw_scope = candidate.get("scope")
            scope: dict[str, Any] = raw_scope if isinstance(raw_scope, dict) else {}
            try:
                scope_json = json.dumps(
                    scope, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
                )
                if len(scope_json) > 8000:
                    raise ValueError("scope too large")
            except (TypeError, ValueError):
                scope = {}
                scope_json = "{}"
            claim_key_material = json.dumps(
                {
                    "statement": normalized,
                    "claim_type": claim_type,
                    "scope": json.loads(scope_json),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            claim_key = sha256(claim_key_material.encode("utf-8")).hexdigest()
            if claim_key in seen_claim_keys:
                excluded_claim_count += 1
                continue
            seen_claim_keys.add(claim_key)

            reasoning = candidate.get("reasoning")
            reasoning = reasoning.strip()[:2000] if isinstance(reasoning, str) and reasoning.strip() else None

            raw_assessments = candidate.get("evidence_assessments")
            assessments = raw_assessments if isinstance(raw_assessments, list) else []
            valid_assessments: list[tuple[Evidence, str, str | None]] = []
            seen_evidence_ids: set[UUID] = set()
            for assessment in assessments[:50]:
                if not isinstance(assessment, dict):
                    excluded_assessment_count += 1
                    continue
                try:
                    evidence_id = UUID(str(assessment.get("evidence_id")))
                except (TypeError, ValueError):
                    excluded_assessment_count += 1
                    continue
                relation = str(assessment.get("relation_type") or "").upper()
                evidence_row = by_id.get(evidence_id)
                if (
                    evidence_row is None
                    or evidence_id in seen_evidence_ids
                    or relation not in {"SUPPORTS", "CONTRADICTS", "CONTEXT"}
                    or (
                        evidence_row.verification_status == "REJECTED"
                        and relation != "CONTEXT"
                    )
                ):
                    excluded_assessment_count += 1
                    continue
                seen_evidence_ids.add(evidence_id)
                rationale_value = assessment.get("rationale")
                rationale = (
                    rationale_value.strip()[:2000]
                    if isinstance(rationale_value, str) and rationale_value.strip()
                    else None
                )
                valid_assessments.append((evidence_row, relation, rationale))

            support_rows = [item[0] for item in valid_assessments if item[1] == "SUPPORTS"]
            contradict_rows = [item[0] for item in valid_assessments if item[1] == "CONTRADICTS"]
            linked_rows = [item[0] for item in valid_assessments]
            linked_hosts = {
                host for host in (_claim_source_host(row) for row in linked_rows) if host
            }
            support_hosts = {
                host for host in (_claim_source_host(row) for row in support_rows) if host
            }
            linked_distinct_hosts = len(linked_hosts)
            distinct_hosts = len(support_hosts)
            claim_host_counts: dict[str, int] = {}
            claim_hash_counts: dict[str, int] = {}
            for evidence_row in linked_rows:
                linked_host = _claim_source_host(evidence_row)
                if linked_host:
                    claim_host_counts[linked_host] = claim_host_counts.get(linked_host, 0) + 1
                linked_digest = _claim_evidence_digest(evidence_row)
                claim_hash_counts[linked_digest] = claim_hash_counts.get(linked_digest, 0) + 1
            support_content_digests = {
                _claim_evidence_digest(row) for row in support_rows
            }
            verified_support_count = sum(
                1 for row in support_rows if row.verification_status == "VERIFIED"
            )
            verified_contradiction_count = sum(
                1 for row in contradict_rows if row.verification_status == "VERIFIED"
            )

            if support_rows and contradict_rows:
                verdict = "CONFLICTED"
                confidence = "INSUFFICIENT"
                status_value = "CONFLICTED"
            elif support_rows:
                verdict = "PROVISIONAL_SUPPORT"
                confidence = (
                    "MEDIUM"
                    if (
                        verified_support_count == len(support_rows)
                        and distinct_hosts >= 2
                        and len(support_content_digests) == len(support_rows)
                    )
                    else "LOW"
                )
                status_value = "NEEDS_REVIEW"
            elif contradict_rows:
                verdict = "POTENTIAL_CONTRADICTION"
                confidence = "LOW"
                status_value = "NEEDS_REVIEW"
            else:
                verdict = "INSUFFICIENT"
                confidence = "INSUFFICIENT"
                status_value = "INSUFFICIENT"

            claim_limitations: list[str] = []
            unverified_count = sum(
                1 for row in linked_rows
                if (row.verification_status or "UNVERIFIED") != "VERIFIED"
            )
            if unverified_count:
                claim_limitations.append(
                    f"{unverified_count} 条关联 Evidence 尚未达到 VERIFIED 状态。"
                )
            if linked_distinct_hosts > 1:
                claim_limitations.append(
                    "检测到多个来源主机，但主机不同不能证明出版方或来源彼此独立。"
                )
            if len(support_content_digests) < len(support_rows):
                claim_limitations.append(
                    "支持该主张的 Evidence 存在相同内容指纹；重复内容不得作为额外佐证。"
                )
            linked_hashes = [row.content_hash for row in linked_rows if row.content_hash]
            if len(linked_hashes) != len(set(linked_hashes)):
                claim_limitations.append("检测到内容哈希重复的来源，不能重复计为独立佐证。")
            if not valid_assessments:
                claim_limitations.append("模型未能映射到任何可用的输入 Evidence。")
            if confidence == "MEDIUM":
                claim_limitations.append(
                    "中置信度仅表示输入 Evidence 状态与主机数量达到最低门槛，不代表已确认来源独立。"
                )
            claim_limitations.append(
                "本次 Agent 结果是待审核的证据映射，不是事实认证；confirmed_independent_source_count 固定为 0。"
            )

            latest = session.scalars(
                select(MarketClaim)
                .where(
                    MarketClaim.organization_id == job.organization_id,
                    MarketClaim.workspace_id == workspace_id,
                    MarketClaim.answer_id == answer_id,
                    MarketClaim.claim_key == claim_key,
                )
                .order_by(MarketClaim.version.desc())
                .limit(1)
                .with_for_update()
            ).one_or_none()
            version = latest.version + 1 if latest is not None else 1
            claim = MarketClaim(
                organization_id=job.organization_id,
                workspace_id=workspace_id,
                answer_id=answer_id,
                verification_run_id=verification_run.id,
                generated_by_run_id=run.id,
                version=version,
                claim_key=claim_key,
                statement=statement,
                claim_type=claim_type,
                scope=scope,
                reasoning=reasoning,
                verification_verdict=verdict,
                confidence_level=confidence,
                status=status_value,
                supporting_evidence_count=len(support_rows),
                contradicting_evidence_count=len(contradict_rows),
                verified_support_count=verified_support_count,
                verified_contradiction_count=verified_contradiction_count,
                distinct_host_count=distinct_hosts,
                confirmed_independent_source_count=0,
                limitations=claim_limitations,
                supersedes_id=latest.id if latest is not None else None,
            )
            session.add(claim)
            session.flush()
            if latest is not None and latest.status in {"NEEDS_REVIEW", "CONFLICTED", "INSUFFICIENT"}:
                latest.status = "SUPERSEDED"
                session.flush()

            for evidence_row, relation, rationale in valid_assessments:
                host = _claim_source_host(evidence_row)
                digest = _claim_evidence_digest(evidence_row)
                if claim_hash_counts.get(digest, 0) > 1:
                    origin_assessment = "DUPLICATE_CONTENT"
                elif host and claim_host_counts.get(host, 0) > 1:
                    origin_assessment = "SAME_HOST"
                elif linked_distinct_hosts > 1:
                    origin_assessment = "DISTINCT_HOST_UNCONFIRMED"
                else:
                    origin_assessment = "SINGLE_SOURCE"
                session.add(
                    MarketClaimEvidence(
                        organization_id=job.organization_id,
                        workspace_id=workspace_id,
                        claim_id=claim.id,
                        evidence_id=evidence_row.id,
                        assessed_by_run_id=run.id,
                        relation_type=relation,
                        rationale=rationale,
                        source_host=host,
                        content_hash_snapshot=digest,
                        verification_status_snapshot=evidence_row.verification_status or "UNVERIFIED",
                        origin_assessment=origin_assessment,
                    )
                )
            session.flush()
            created_claims.append(claim)

    if excluded_claim_count:
        limitations.append(f"已排除 {excluded_claim_count} 条格式无效或重复的主张。")
    if excluded_assessment_count:
        limitations.append(
            f"已排除 {excluded_assessment_count} 条无法映射到本次输入 Evidence 的关系。"
        )
    if not created_claims and not limitations:
        limitations.append("模型没有提取出可单独核验的原子主张。")

    verification_run.claim_count = len(created_claims)
    verification_run.degraded = response.degraded
    verification_run.limitations = limitations
    verification_run.status = "COMPLETED"
    verification_run.completed_at = datetime.now(UTC)
    run.status = "SUCCEEDED"
    run.finished_at = datetime.now(UTC)
    run.output = {
        "verification_run_id": str(verification_run.id),
        "answer_id": str(answer_id),
        "claim_count": len(created_claims),
        "claim_ids": [str(item.id) for item in created_claims],
        "excluded_claim_count": excluded_claim_count,
        "excluded_assessment_count": excluded_assessment_count,
        "degraded": response.degraded,
        "llm_invocation_id": str(response.invocation_id),
        "model": response.model,
        "cached": response.cached,
        "automatic_independence_confirmations": 0,
    }
    workflow.status = "COMPLETED"
    workflow.payload = {
        **(workflow.payload or {}),
        "verification_status": "COMPLETED",
        "verification_run_id": str(verification_run.id),
        "agent_run_id": str(run.id),
        "claim_count": len(created_claims),
        "degraded": response.degraded,
        "completed_at": verification_run.completed_at.isoformat(),
    }
    session.add(
        MarketActivityEvent(
            organization_id=job.organization_id,
            workspace_id=workspace_id,
            actor_type="SYSTEM",
            actor_user_id=None,
            aggregate_type="market_claim_verification",
            aggregate_id=verification_run.id,
            event_type="MARKET_CLAIM_VERIFICATION_COMPLETED",
            payload={
                "answer_id": str(answer_id),
                "claim_count": len(created_claims),
                "degraded": response.degraded,
                "automatic_independence_confirmations": 0,
            },
        )
    )
    session.flush()
    return {
        "workspace_id": str(workspace_id),
        "answer_id": str(answer_id),
        "workflow_id": str(workflow.id),
        "verification_run_id": str(verification_run.id),
        "agent_run_id": str(run.id),
        "claim_count": len(created_claims),
        "degraded": response.degraded,
    }


def handle_market_source_page_extraction(
    session: Session, job: BackgroundJob
) -> dict[str, Any]:
    """Extract a user-selected public page into a new immutable, still-unverified Evidence."""
    payload = job.payload or {}
    try:
        workspace_id = UUID(str(payload["workspace_id"]))
        workflow_id = UUID(str(payload["workflow_id"]))
        source_evidence_id = UUID(str(payload["source_evidence_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise TaskFailed("来源网页提取任务载荷无效", retryable=False) from exc

    workflow = session.scalars(
        select(WorkflowInstance).where(
            WorkflowInstance.organization_id == str(job.organization_id),
            WorkflowInstance.id == workflow_id,
            WorkflowInstance.workflow_type == "market_source_extraction",
        ).with_for_update()
    ).one_or_none()
    if workflow is None:
        raise TaskFailed("来源网页提取 Workflow 不存在", retryable=False)
    if workflow.job_id is not None and workflow.job_id != job.id:
        raise TaskFailed("来源网页提取任务已被更新的任务取代", retryable=False)

    workspace_link = session.scalars(
        select(MarketWorkspaceWorkflow).where(
            MarketWorkspaceWorkflow.organization_id == job.organization_id,
            MarketWorkspaceWorkflow.workspace_id == workspace_id,
            MarketWorkspaceWorkflow.workflow_id == workflow_id,
        )
    ).one_or_none()
    if workspace_link is None:
        workflow.status = "FAILED"
        workflow.payload = {
            **(workflow.payload or {}),
            "extraction_status": "FAILED",
            "failure_code": "WORKSPACE_LINK_MISSING",
            "failure_reason": "提取 Workflow 不属于指定 Workspace",
        }
        session.flush()
        raise TaskFailed("来源网页提取 Workflow 不属于指定 Workspace", retryable=False)

    source = session.scalars(
        select(Evidence).where(
            Evidence.organization_id == job.organization_id,
            Evidence.id == source_evidence_id,
            Evidence.subject_type == "market_workspace",
            Evidence.subject_id == workspace_id,
        )
    ).one_or_none()
    if source is None or source.kind != "WEB_SEARCH_RESULT" or not source.canonical_ref:
        workflow.status = "FAILED"
        workflow.payload = {
            **(workflow.payload or {}),
            "extraction_status": "FAILED",
            "failure_code": "SOURCE_NOT_ELIGIBLE",
            "failure_reason": "来源不存在、不是搜索结果或缺少来源 URL",
        }
        session.flush()
        raise TaskFailed("仅允许提取当前 Workspace 中具备 URL 的 WEB_SEARCH_RESULT", retryable=False)
    if source.verification_status == "REJECTED":
        workflow.status = "FAILED"
        workflow.payload = {
            **(workflow.payload or {}),
            "extraction_status": "FAILED",
            "failure_code": "SOURCE_REJECTED",
            "failure_reason": "该搜索来源已被驳回",
        }
        session.flush()
        raise TaskFailed("已被驳回的搜索来源不能提取", retryable=False)

    run = AgentRun(
        organization_id=str(job.organization_id),
        workflow_id=workflow.id,
        agent_type="market_source_page_extractor",
        status="RUNNING",
        input={
            "job_id": str(job.id),
            "workspace_id": str(workspace_id),
            "workflow_id": str(workflow.id),
            "source_evidence_id": str(source.id),
            "source_content_hash": source.content_hash,
            "extractor_version": (workflow.payload or {}).get("extractor_version"),
        },
    )
    session.add(run)
    session.flush()

    provider = get_research_page_provider()
    try:
        page = provider.fetch(source.canonical_ref)
    except ResearchPageFetchError as exc:
        if exc.retryable:
            raise TaskFailed(str(exc), retryable=True) from exc
        now = datetime.now(UTC)
        run.status = "FAILED"
        run.error = f"{exc.code}: {exc}"[:4000]
        run.output = {"failure_code": exc.code, "retryable": False}
        run.finished_at = now
        workflow.status = "FAILED"
        workflow.payload = {
            **(workflow.payload or {}),
            "extraction_status": "FAILED",
            "failure_code": exc.code,
            "failure_reason": str(exc)[:2000],
            "agent_run_id": str(run.id),
        }
        session.add(
            MarketActivityEvent(
                organization_id=job.organization_id,
                workspace_id=workspace_id,
                actor_type="SYSTEM",
                actor_user_id=None,
                aggregate_type="market_source_extraction",
                aggregate_id=workflow.id,
                event_type="MARKET_SOURCE_EXTRACTION_FAILED",
                payload={
                    "source_evidence_id": str(source.id),
                    "agent_run_id": str(run.id),
                    "failure_code": exc.code,
                },
            )
        )
        session.flush()
        raise TaskFailed(str(exc), retryable=False) from exc

    extracted = market_insight_service.record_market_evidence(
        session,
        job.organization_id,
        workspace_id,
        canonical_ref=page.final_url,
        content=page.excerpt,
        content_hash_override=page.content_hash,
        kind="WEB_PAGE_EXTRACT",
        source=provider.name,
        published_at=page.published_at,
        observation=page.excerpt[:1200],
        verification_status="UNVERIFIED",
        quality=None,
        payload={
            "requested_url": page.requested_url,
            "final_url": page.final_url,
            "title": page.title,
            "description": page.description,
            "author": page.author,
            "published_at_raw": page.published_at_raw,
            "observed_canonical_url": page.observed_canonical_url,
            "language": page.language,
            "content_type": page.content_type,
            "content_hash_scope": "normalized_full_extracted_text",
            "content_chars": page.content_chars,
            "stored_excerpt_chars": len(page.excerpt),
            "excerpt_truncated": page.content_chars > len(page.excerpt),
            "extractor_version": page.extractor_version,
            "derived_from_evidence_id": str(source.id),
            "question_scope_managed": bool((source.payload or {}).get("question_scope_managed")),
            "target_question_id": (source.payload or {}).get("target_question_id"),
            "extraction_workflow_id": str(workflow.id),
            "extraction_agent_run_id": str(run.id),
            "retrieved_at": page.retrieved_at.isoformat(),
            "content": page.excerpt,
        },
        actor_user_id=None,
    )

    # If a task is replayed after a worker-side retry, the lineage edge remains unique.
    session.execute(
        pg_insert(MarketEvidenceDerivation.__table__)
        .values(
            organization_id=UUID(str(job.organization_id)),
            workspace_id=workspace_id,
            source_evidence_id=source.id,
            derived_evidence_id=extracted.id,
            workflow_id=workflow.id,
            agent_run_id=run.id,
            derivation_type="WEB_PAGE_TEXT_EXTRACTION",
            extractor_version=page.extractor_version,
            source_url=page.final_url,
            content_hash=page.content_hash,
        )
        .on_conflict_do_nothing(constraint="uq_market_evidence_derivation")
    )
    market_insight_service.inherit_research_plan_evidence_links(
        session,
        job.organization_id,
        workspace_id,
        source.id,
        extracted.id,
        extraction_workflow_id=workflow.id,
        extraction_agent_run_id=run.id,
    )

    now = datetime.now(UTC)
    run.status = "SUCCEEDED"
    run.output = {
        "source_evidence_id": str(source.id),
        "extracted_evidence_id": str(extracted.id),
        "final_url": page.final_url,
        "content_hash": page.content_hash,
        "content_chars": page.content_chars,
        "stored_excerpt_chars": len(page.excerpt),
        "verification_status": "UNVERIFIED",
        "extractor_version": page.extractor_version,
    }
    run.finished_at = now
    workflow.status = "COMPLETED"
    workflow.payload = {
        **(workflow.payload or {}),
        "extraction_status": "COMPLETED",
        "extracted_evidence_id": str(extracted.id),
        "agent_run_id": str(run.id),
        "final_url": page.final_url,
        "content_hash": page.content_hash,
        "content_chars": page.content_chars,
        "stored_excerpt_chars": len(page.excerpt),
        "verification_status": "UNVERIFIED",
        "retrieved_at": page.retrieved_at.isoformat(),
        "completed_at": now.isoformat(),
    }
    session.add(
        MarketActivityEvent(
            organization_id=job.organization_id,
            workspace_id=workspace_id,
            actor_type="SYSTEM",
            actor_user_id=None,
            aggregate_type="market_source_extraction",
            aggregate_id=workflow.id,
            event_type="MARKET_SOURCE_PAGE_EXTRACTED",
            payload={
                "source_evidence_id": str(source.id),
                "extracted_evidence_id": str(extracted.id),
                "content_hash": page.content_hash,
                "verification_status": "UNVERIFIED",
            },
        )
    )
    session.flush()
    return {
        "workspace_id": str(workspace_id),
        "source_evidence_id": str(source.id),
        "workflow_id": str(workflow.id),
        "agent_run_id": str(run.id),
        "extracted_evidence_id": str(extracted.id),
        "content_hash": page.content_hash,
        "verification_status": "UNVERIFIED",
    }



#: 任务处理器注册表。新增 ``job_type`` 时必须同时在这里登记，否则任务会以
#: "没有注册处理器"直接终态失败（这是刻意的：静默丢弃任务比报错更糟）。
TASK_HANDLERS: Mapping[str, TaskHandler] = {
    job_service.JOB_TYPE_PLAN_TRIP: handle_plan_trip,
    job_service.JOB_TYPE_DRAFT_MINUTES: handle_draft_minutes,
    job_service.JOB_TYPE_CRM_SYNC: handle_crm_sync,
    job_service.JOB_TYPE_FX_SYNC: handle_fx_sync,
    job_service.JOB_TYPE_RESEARCH_EXECUTE: handle_research_execute,
    job_service.JOB_TYPE_MARKET_QUESTION_ANSWER: handle_market_question_answer_draft,
    job_service.JOB_TYPE_MARKET_RESEARCH_DAG_SCHEDULE: handle_market_research_dag_schedule,
    job_service.JOB_TYPE_MARKET_PAGE_EXTRACT: handle_market_source_page_extraction,
    job_service.JOB_TYPE_MARKET_CLAIM_VERIFY: handle_market_claim_verification,
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
    "handle_market_question_answer_draft",
    "handle_market_research_dag_schedule",
    "handle_market_source_page_extraction",
    "handle_market_claim_verification",
]