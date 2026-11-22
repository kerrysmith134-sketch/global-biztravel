"""Apply source-verification findings to the existing Workspace Research DAG.

The application is explicit and idempotent: it creates auditable Agent-generated
research questions and dependency edges, but never starts paid model execution or
approves answers/claims automatically.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.api.errors import Conflict, NotFound
from app.models.market import (
    MarketActivityEvent,
    MarketClaim,
    MarketResearchQuestion,
    MarketWorkspace,
    MarketWorkspaceWorkflow,
)
from app.models.workflow import AgentRun, WorkflowInstance
from app.services import (
    market_research_dag_service,
    market_research_graph_service,
    market_source_verification_service,
)

WORKFLOW_TYPE = "market_source_verification_plan_application"
AGENT_TYPE = "MARKET_SOURCE_VERIFICATION_PLANNER"


def _plan_digest(plan: dict[str, Any]) -> str:
    """Ignore generated_at and current task-state enrichment; hash only plan semantics."""
    normalized_actions = [
        {
            key: action.get(key)
            for key in (
                "action_id",
                "action_type",
                "priority",
                "target_type",
                "target_id",
                "evidence_ids",
                "claim_id",
                "title",
                "rationale",
            )
        }
        for action in plan.get("actions", [])
    ]
    canonical = json.dumps(
        {"workspace_id": str(plan["workspace_id"]), "actions": normalized_actions},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(canonical.encode()).hexdigest()


def _question_text(
    action: dict[str, Any],
    claim: MarketClaim | None,
) -> tuple[str, list[str], list[str]]:
    action_type = str(action["action_type"])
    evidence_ids = [str(item) for item in action.get("evidence_ids", [])]
    evidence_reference = (
        "关联 Evidence ID：" + "、".join(evidence_ids[:12])
        if evidence_ids
        else "当前没有可用的关联 Evidence，请先说明证据缺口。"
    )
    claim_statement = claim.statement if claim is not None else None

    guidance: dict[str, str] = {
        "EXTRACT_SOURCE_PAGE": (
            "围绕指定来源提取可追溯的网页正文。请核对页面 URL 与抓取时间，"
            "正文必须来自页面本身，不能把搜索摘要当作网页正文；若内容已变化，"
            "应保留新版本及其来源关系。"
        ),
        "COMPLETE_SOURCE_METADATA": (
            "检查来源 URL、出版/发布日期、抓取时间、来源主体和内容指纹。"
            "缺失信息应明确标为未知，不得推测或补造。"
        ),
        "INVESTIGATE_POSSIBLE_SYNDICATION": (
            "调查相同内容在不同主机上的发布关系，尽可能找到原始发布者、"
            "转载声明、联合发布关系或可追溯的出版方信息。不同域名或相同正文"
            "本身都不能证明来源独立。"
        ),
        "REVIEW_SOURCE_VERSION_DRIFT": (
            "比较同一规范化 URL 的不同内容版本，列出实质变更、版本时间和"
            "可能影响的结论；不得只因抓取时间较新就认定新版本更权威。"
        ),
        "REVERIFY_CLAIM_AFTER_SOURCE_CHANGE": (
            "基于当前可用版本重新核对主张，逐条指出支持、反驳和仍未知的部分。"
            "必须说明旧快照与新证据之间的差异。"
        ),
        "REVIEW_CONTRADICTORY_EVIDENCE": (
            "逐条比较支持与反驳材料的主体、地域、时间范围、定义和原文。"
            "说明冲突能否解释、哪些部分仍不能得出结论，禁止按来源数量投票。"
        ),
        "SEEK_SEPARATELY_ORIGINATED_CORROBORATION": (
            "寻找形成过程可分别核实的补充材料，优先考虑原始监管文件、"
            "统计/年报、正式公告或直接访谈记录，并说明其与已有来源的关系。"
        ),
    }
    instruction = guidance.get(action_type, "完成该来源核验任务，并明确报告证据和未知项。")
    parts = [str(action.get("title") or "来源核验任务"), instruction]
    if claim_statement:
        parts.append(f"待核验主张：{claim_statement}")
    parts.append(evidence_reference)
    question = "\n\n".join(parts)[:4000]

    acceptance = [
        "结论必须关联当前 Workspace 中可追溯的 Evidence，列出对应来源或 Evidence ID。",
        "区分直接证据、推断和未知事项；缺失信息不得猜测。",
        "如存在支持与反驳材料，必须分别解释其范围与差异。",
        "答案只能作为待审核研究草稿，不得自动采纳研究答案或市场主张。",
    ]
    expected = [
        "可追溯的来源 URL、原文/摘录和 Evidence 记录",
        "发布日期与抓取时间；无法确认时明确说明",
        "证据与主张之间的支持、反驳或上下文关系",
    ]
    return question, acceptance, expected


def _serialize_task(
    action: dict[str, Any],
    question: MarketResearchQuestion,
    *,
    reused: bool,
    dependencies: list[UUID],
) -> dict[str, Any]:
    return {
        "action_id": action["action_id"],
        "action_type": action["action_type"],
        "question_id": question.id,
        "question": question.question,
        "question_status": question.status,
        "priority": action["priority"],
        "reused": reused,
        "depends_on_question_ids": sorted(set(dependencies), key=str),
    }


def apply_plan(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    *,
    actor_user_id: UUID,
) -> dict[str, Any]:
    """Create DAG questions from the latest plan. No LLM dispatch is performed here."""
    workspace = session.scalars(
        select(MarketWorkspace)
        .where(
            MarketWorkspace.organization_id == organization_id,
            MarketWorkspace.id == workspace_id,
        )
        .with_for_update()
    ).one_or_none()
    if workspace is None:
        raise NotFound("市场工作空间不存在", details={"workspace_id": str(workspace_id)})

    active_execution = session.scalars(
        select(WorkflowInstance)
        .join(
            MarketWorkspaceWorkflow,
            and_(
                MarketWorkspaceWorkflow.organization_id == WorkflowInstance.organization_id,
                MarketWorkspaceWorkflow.workflow_id == WorkflowInstance.id,
            ),
        )
        .where(
            WorkflowInstance.organization_id == organization_id,
            WorkflowInstance.workflow_type == market_research_dag_service.WORKFLOW_TYPE,
            WorkflowInstance.status == "RUNNING",
            MarketWorkspaceWorkflow.workspace_id == workspace_id,
        )
        .with_for_update()
    ).first()
    if active_execution is not None:
        raise Conflict(
            "当前研究图仍在执行，完成或取消后再同步新的核验任务",
            code="MARKET_RESEARCH_DAG_ALREADY_RUNNING",
            details={"execution_id": str(active_execution.id)},
        )

    plan = market_source_verification_service.build_plan(
        session, organization_id, workspace_id
    )
    actions: list[dict[str, Any]] = list(plan.get("actions", []))
    digest = _plan_digest(plan)
    if not actions:
        return {
            "workspace_id": workspace_id,
            "plan_digest": digest,
            "applied_at": datetime.now(UTC),
            "action_count": 0,
            "created_question_count": 0,
            "reused_question_count": 0,
            "dependency_count": 0,
            "tasks": [],
            "reused_application": False,
        }

    linked_workflow_ids = list(
        session.scalars(
            select(MarketWorkspaceWorkflow.workflow_id).where(
                MarketWorkspaceWorkflow.organization_id == organization_id,
                MarketWorkspaceWorkflow.workspace_id == workspace_id,
            )
        ).all()
    )
    linked_workflows = (
        list(
            session.scalars(
                select(WorkflowInstance)
                .where(
                    WorkflowInstance.organization_id == organization_id,
                    WorkflowInstance.id.in_(linked_workflow_ids),
                    WorkflowInstance.workflow_type == WORKFLOW_TYPE,
                )
                .order_by(WorkflowInstance.created_at.desc(), WorkflowInstance.id)
            ).all()
        )
        if linked_workflow_ids
        else []
    )
    for existing_workflow in linked_workflows:
        existing_payload = existing_workflow.payload or {}
        if existing_payload.get("plan_digest") != digest:
            continue
        stored_tasks = existing_payload.get("tasks")
        if not isinstance(stored_tasks, list):
            continue
        task_ids = [
            UUID(str(item["question_id"]))
            for item in stored_tasks
            if isinstance(item, dict) and item.get("question_id")
        ]
        questions = (
            list(
                session.scalars(
                    select(MarketResearchQuestion).where(
                        MarketResearchQuestion.organization_id == organization_id,
                        MarketResearchQuestion.workspace_id == workspace_id,
                        MarketResearchQuestion.id.in_(task_ids),
                    )
                ).all()
            )
            if task_ids
            else []
        )
        question_by_id = {row.id: row for row in questions}
        # A cancelled task is not a valid terminal application: permit an explicit
        # re-application to create a replacement question for the same stable action.
        if any(row.status == "CANCELLED" for row in questions):
            continue
        tasks: list[dict[str, Any]] = []
        for stored in stored_tasks:
            if not isinstance(stored, dict):
                continue
            try:
                question = question_by_id[UUID(str(stored["question_id"]))]
            except (KeyError, TypeError, ValueError):
                continue
            action = next(
                (row for row in actions if row["action_id"] == stored.get("action_id")),
                None,
            )
            if action is None:
                continue
            dependency_ids = [
                UUID(str(item))
                for item in stored.get("depends_on_question_ids", [])
            ]
            tasks.append(
                _serialize_task(
                    action,
                    question,
                    reused=True,
                    dependencies=dependency_ids,
                )
            )
        return {
            "workspace_id": workspace_id,
            "plan_digest": digest,
            "applied_at": existing_payload.get("applied_at") or existing_workflow.created_at,
            "action_count": len(tasks),
            "created_question_count": 0,
            "reused_question_count": len(tasks),
            "dependency_count": sum(len(row["depends_on_question_ids"]) for row in tasks),
            "tasks": tasks,
            "reused_application": True,
        }

    action_ids = [str(action["action_id"]) for action in actions]
    existing_questions = list(
        session.scalars(
            select(MarketResearchQuestion)
            .join(
                AgentRun,
                and_(
                    AgentRun.organization_id == MarketResearchQuestion.organization_id,
                    AgentRun.id == MarketResearchQuestion.generated_by_run_id,
                ),
            )
            .where(
                MarketResearchQuestion.organization_id == organization_id,
                MarketResearchQuestion.workspace_id == workspace_id,
                MarketResearchQuestion.planner_question_key.in_(action_ids),
                AgentRun.agent_type == AGENT_TYPE,
            )
            .order_by(
                MarketResearchQuestion.updated_at.desc(),
                MarketResearchQuestion.created_at.desc(),
                MarketResearchQuestion.id,
            )
        ).all()
    )
    existing_by_action: dict[str, MarketResearchQuestion] = {}
    for question in existing_questions:
        if question.planner_question_key:
            existing_by_action.setdefault(question.planner_question_key, question)

    now = datetime.now(UTC)
    workflow = WorkflowInstance(
        organization_id=organization_id,
        workflow_type=WORKFLOW_TYPE,
        status="COMPLETED",
        payload={
            "workspace_id": str(workspace_id),
            "plan_digest": digest,
            "action_ids": action_ids,
            "applied_at": now.isoformat(),
            "tasks": [],
        },
        created_by_user_id=actor_user_id,
    )
    session.add(workflow)
    session.flush()
    session.add(
        MarketWorkspaceWorkflow(
            organization_id=organization_id,
            workspace_id=workspace_id,
            workflow_id=workflow.id,
            linked_by_user_id=actor_user_id,
        )
    )
    agent_run = AgentRun(
        organization_id=organization_id,
        workflow_id=workflow.id,
        agent_type=AGENT_TYPE,
        status="SUCCEEDED",
        input={"workspace_id": str(workspace_id), "plan_digest": digest, "action_ids": action_ids},
        output={},
        started_at=now,
        finished_at=now,
    )
    session.add(agent_run)
    session.flush()

    claim_ids = [UUID(str(item["claim_id"])) for item in actions if item.get("claim_id")]
    claims = (
        list(
            session.scalars(
                select(MarketClaim).where(
                    MarketClaim.organization_id == organization_id,
                    MarketClaim.workspace_id == workspace_id,
                    MarketClaim.id.in_(claim_ids),
                )
            ).all()
        )
        if claim_ids
        else []
    )
    claim_by_id = {row.id: row for row in claims}

    question_by_action: dict[str, MarketResearchQuestion] = {}
    created_count = 0
    reused_count = 0
    for action in actions:
        action_id = str(action["action_id"])
        existing = existing_by_action.get(action_id)
        if existing is not None and existing.status != "CANCELLED":
            question_by_action[action_id] = existing
            reused_count += 1
            continue

        claim = claim_by_id.get(UUID(str(action["claim_id"]))) if action.get("claim_id") else None
        question_text, acceptance, expected = _question_text(action, claim)
        priority = {"HIGH": 1, "MEDIUM": 2, "LOW": 3}.get(str(action["priority"]), 3)
        item = MarketResearchQuestion(
            organization_id=organization_id,
            workspace_id=workspace_id,
            parent_question_id=None,
            question=question_text,
            rationale=str(action.get("rationale") or action.get("title") or "来源核验计划生成的研究任务"),
            source="AGENT",
            priority=priority,
            status="OPEN",
            acceptance_criteria=acceptance,
            expected_evidence=expected,
            generated_by_run_id=agent_run.id,
            planner_question_key=action_id,
            created_by_user_id=actor_user_id,
        )
        session.add(item)
        session.flush()
        question_by_action[action_id] = item
        created_count += 1
        session.add(
            MarketActivityEvent(
                organization_id=organization_id,
                workspace_id=workspace_id,
                actor_type="USER",
                actor_user_id=actor_user_id,
                aggregate_type="market_research_question",
                aggregate_id=item.id,
                event_type="SOURCE_VERIFICATION_TASK_CREATED",
                payload={
                    "action_id": action_id,
                    "action_type": action["action_type"],
                    "source_verification_run_id": str(agent_run.id),
                    "evidence_ids": [str(value) for value in action.get("evidence_ids", [])],
                },
            )
        )

    # Claim-level verification tasks depend on all source-level tasks that touch
    # one of their evidence records. This gives the DAG a real evidence-first order.
    evidence_action_questions: dict[str, set[UUID]] = {}
    for action in actions:
        if action.get("target_type") != "EVIDENCE":
            continue
        question = question_by_action[str(action["action_id"])]
        for evidence_id in action.get("evidence_ids", []):
            evidence_action_questions.setdefault(str(evidence_id), set()).add(question.id)

    dependency_count = 0
    dependencies_by_action: dict[str, list[UUID]] = {}
    for action in actions:
        if action.get("target_type") != "CLAIM":
            continue
        question = question_by_action[str(action["action_id"])]
        task_dependency_ids: set[UUID] = set()
        for evidence_id in action.get("evidence_ids", []):
            task_dependency_ids.update(evidence_action_questions.get(str(evidence_id), set()))
        task_dependency_ids.discard(question.id)
        for dependency_id in sorted(task_dependency_ids, key=str):
            _, reused = market_research_graph_service.create_question_dependency(
                session,
                organization_id,
                workspace_id,
                question.id,
                dependency_id,
                actor_user_id=actor_user_id,
            )
            if not reused:
                dependency_count += 1
        dependencies_by_action[str(action["action_id"])] = sorted(task_dependency_ids, key=str)

    tasks = [
        _serialize_task(
            action,
            question_by_action[str(action["action_id"])],
            reused=str(action["action_id"]) in existing_by_action
            and existing_by_action[str(action["action_id"])].status != "CANCELLED",
            dependencies=dependencies_by_action.get(str(action["action_id"]), []),
        )
        for action in actions
    ]
    output = {
        "plan_digest": digest,
        "created_question_count": created_count,
        "reused_question_count": reused_count,
        "dependency_count": dependency_count,
        "tasks": [
            {
                **row,
                "question_id": str(row["question_id"]),
                "depends_on_question_ids": [str(value) for value in row["depends_on_question_ids"]],
            }
            for row in tasks
        ],
    }
    agent_run.output = output
    workflow.payload = {
        **(workflow.payload or {}),
        "tasks": output["tasks"],
        "created_question_count": created_count,
        "reused_question_count": reused_count,
        "dependency_count": dependency_count,
    }
    session.add(
        MarketActivityEvent(
            organization_id=organization_id,
            workspace_id=workspace_id,
            actor_type="USER",
            actor_user_id=actor_user_id,
            aggregate_type="market_source_verification_plan",
            aggregate_id=workflow.id,
            event_type="SOURCE_VERIFICATION_PLAN_APPLIED",
            payload={
                "plan_digest": digest,
                "action_count": len(actions),
                "created_question_count": created_count,
                "reused_question_count": reused_count,
                "dependency_count": dependency_count,
            },
        )
    )
    session.flush()
    return {
        "workspace_id": workspace_id,
        "plan_digest": digest,
        "applied_at": now,
        "action_count": len(actions),
        "created_question_count": created_count,
        "reused_question_count": reused_count,
        "dependency_count": dependency_count,
        "tasks": tasks,
        "reused_application": False,
    }
