"""Workspace research graph orchestration and dependency invariants.

ResearchQuestion rows are the durable graph nodes. Dependency edges are separate
from parent/child presentation hierarchy and are always scoped to one Workspace.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import Conflict, NotFound, Unprocessable
from app.models.market import (
    MarketActivityEvent,
    MarketQuestionDependency,
    MarketResearchQuestion,
    MarketWorkspace,
)

MAX_GRAPH_NODES = 12
_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def normalize_research_graph(
    raw_graph: Any,
    raw_questions: Any,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Normalize untrusted planner output into a bounded, acyclic graph.

    Invalid or cyclic dependency declarations are not silently executed. The nodes
    are retained for review, unsafe edges are removed, and a warning is returned.
    """
    warnings: list[str] = []
    candidates = raw_graph if isinstance(raw_graph, list) else []
    if not candidates:
        candidates = [
            {"question": value}
            for value in raw_questions
            if isinstance(value, str) and value.strip()
        ] if isinstance(raw_questions, list) else []

    nodes: list[dict[str, Any]] = []
    used_keys: set[str] = set()
    for index, raw in enumerate(candidates[:MAX_GRAPH_NODES], start=1):
        if not isinstance(raw, dict):
            warnings.append(f"第 {index} 个研究节点不是对象，已忽略")
            continue
        question = raw.get("question")
        if not isinstance(question, str) or not question.strip():
            warnings.append(f"第 {index} 个研究节点缺少问题文本，已忽略")
            continue

        candidate_key = raw.get("key")
        key = candidate_key.strip() if isinstance(candidate_key, str) else ""
        if not _KEY_RE.fullmatch(key) or key in used_keys:
            key = f"q-{index:02d}"
            suffix = 1
            while key in used_keys:
                suffix += 1
                key = f"q-{index:02d}-{suffix}"
            if candidate_key:
                warnings.append(f"研究节点 key 无效或重复，已替换为 {key}")
        used_keys.add(key)

        raw_priority = raw.get("priority", 3)
        priority = raw_priority if isinstance(raw_priority, int) and not isinstance(raw_priority, bool) else 3
        priority = min(5, max(1, priority))

        def clean_strings(value: Any, limit: int, item_limit: int) -> list[str]:
            if not isinstance(value, list):
                return []
            result: list[str] = []
            seen: set[str] = set()
            for item in value:
                if not isinstance(item, str):
                    continue
                normalized = item.strip()[:item_limit]
                marker = normalized.casefold()
                if normalized and marker not in seen:
                    seen.add(marker)
                    result.append(normalized)
                if len(result) >= limit:
                    break
            return result

        rationale = raw.get("rationale")
        nodes.append(
            {
                "key": key,
                "question": question.strip()[:4000],
                "rationale": rationale.strip()[:4000] if isinstance(rationale, str) and rationale.strip() else None,
                "priority": priority,
                "acceptance_criteria": clean_strings(raw.get("acceptance_criteria"), 12, 500),
                "expected_evidence": clean_strings(raw.get("expected_evidence"), 12, 300),
                "depends_on_keys": clean_strings(raw.get("depends_on_keys"), MAX_GRAPH_NODES, 64),
            }
        )

    if not nodes:
        nodes = [
            {
                "key": f"q-{index:02d}",
                "question": question,
                "rationale": None,
                "priority": 3,
                "acceptance_criteria": [],
                "expected_evidence": [],
                "depends_on_keys": [],
            }
            for index, question in enumerate(
                [
                    "哪些可核验事实是完成用户目标所必需的？",
                    "哪些来源能够直接支持这些事实？",
                    "现有信息中有哪些缺口、时效问题或相互矛盾之处？",
                ],
                start=1,
            )
        ]
        warnings.append("规划器未返回有效研究节点，已生成通用待审阅问题")

    known = {node["key"] for node in nodes}
    for node in nodes:
        kept: list[str] = []
        for dependency in node["depends_on_keys"]:
            if dependency == node["key"]:
                warnings.append(f"{node['key']} 自我依赖已移除")
            elif dependency not in known:
                warnings.append(f"{node['key']} 引用了不存在的依赖节点 {dependency}，已移除")
            elif dependency not in kept:
                kept.append(dependency)
        node["depends_on_keys"] = kept

    # Kahn topological sort. If the model produced a cycle, preserve its questions
    # but drop every proposed edge; the user can add reviewed edges later.
    order_index = {node["key"]: index for index, node in enumerate(nodes)}
    indegree = {node["key"]: len(node["depends_on_keys"]) for node in nodes}
    dependents: dict[str, list[str]] = {node["key"]: [] for node in nodes}
    for node in nodes:
        for dependency in node["depends_on_keys"]:
            dependents[dependency].append(node["key"])
    ready = sorted(
        [key for key, degree in indegree.items() if degree == 0],
        key=order_index.__getitem__,
    )
    visited: list[str] = []
    while ready:
        key = ready.pop(0)
        visited.append(key)
        for dependent in sorted(dependents[key], key=order_index.__getitem__):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
                ready.sort(key=order_index.__getitem__)
    if len(visited) != len(nodes):
        warnings.append("规划器返回了循环依赖；所有自动依赖边已清除，需人工重新审阅")
        for node in nodes:
            node["depends_on_keys"] = []

    return nodes, warnings


def persist_planner_graph(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    *,
    workflow_id: UUID,
    agent_run_id: UUID,
    created_by_user_id: UUID,
    nodes: list[dict[str, Any]],
) -> list[UUID]:
    """Materialize one Planner output into immutable run-attributed question nodes."""
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

    # A run may be materialized only once. This also makes accidental internal retries safe.
    existing = session.scalars(
        select(MarketResearchQuestion).where(
            MarketResearchQuestion.organization_id == organization_id,
            MarketResearchQuestion.workspace_id == workspace_id,
            MarketResearchQuestion.generated_by_run_id == agent_run_id,
        )
    ).all()
    if existing:
        by_key = {row.planner_question_key: row.id for row in existing}
        if len(by_key) == len(nodes) and all(node["key"] in by_key for node in nodes):
            return [by_key[node["key"]] for node in nodes]
        raise Conflict(
            "同一 Planner 运行已存在不完整的问题图，拒绝重复写入",
            code="RESEARCH_GRAPH_PARTIAL_REPLAY",
        )

    questions_by_key: dict[str, MarketResearchQuestion] = {}
    for node in nodes:
        row = MarketResearchQuestion(
            organization_id=organization_id,
            workspace_id=workspace_id,
            parent_question_id=None,
            question=node["question"],
            rationale=node.get("rationale"),
            source="AGENT",
            priority=node["priority"],
            status="OPEN",
            acceptance_criteria=node.get("acceptance_criteria") or [],
            expected_evidence=node.get("expected_evidence") or [],
            generated_by_run_id=agent_run_id,
            planner_question_key=node["key"],
            created_by_user_id=created_by_user_id,
        )
        session.add(row)
        questions_by_key[node["key"]] = row
    session.flush()

    edge_payload: list[dict[str, str]] = []
    for node in nodes:
        question = questions_by_key[node["key"]]
        for dependency_key in node.get("depends_on_keys", []):
            dependency = questions_by_key.get(dependency_key)
            if dependency is None:
                continue
            session.add(
                MarketQuestionDependency(
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    question_id=question.id,
                    depends_on_question_id=dependency.id,
                )
            )
            edge_payload.append(
                {
                    "question_key": node["key"],
                    "depends_on_key": dependency_key,
                }
            )
    session.add(
        MarketActivityEvent(
            organization_id=organization_id,
            workspace_id=workspace_id,
            actor_type="USER",
            actor_user_id=created_by_user_id,
            aggregate_type="research_question_graph",
            aggregate_id=workflow_id,
            event_type="RESEARCH_QUESTION_GRAPH_GENERATED",
            payload={
                "planner_run_id": str(agent_run_id),
                "node_count": len(questions_by_key),
                "edge_count": len(edge_payload),
                "question_ids": {key: str(row.id) for key, row in questions_by_key.items()},
                "edges": edge_payload,
            },
        )
    )
    session.flush()
    return [questions_by_key[node["key"]].id for node in nodes]


def _require_workspace(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    *,
    lock: bool = False,
) -> MarketWorkspace:
    statement = select(MarketWorkspace).where(
        MarketWorkspace.organization_id == organization_id,
        MarketWorkspace.id == workspace_id,
    )
    if lock:
        statement = statement.with_for_update()
    workspace = session.scalars(statement).one_or_none()
    if workspace is None:
        raise NotFound("市场工作空间不存在", details={"workspace_id": str(workspace_id)})
    return workspace


def list_question_dependencies(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    question_id: UUID,
) -> list[MarketQuestionDependency]:
    _require_workspace(session, organization_id, workspace_id)
    question = session.scalars(
        select(MarketResearchQuestion).where(
            MarketResearchQuestion.organization_id == organization_id,
            MarketResearchQuestion.workspace_id == workspace_id,
            MarketResearchQuestion.id == question_id,
        )
    ).one_or_none()
    if question is None:
        raise NotFound("研究问题不存在", details={"question_id": str(question_id)})
    return list(
        session.scalars(
            select(MarketQuestionDependency)
            .where(
                MarketQuestionDependency.organization_id == organization_id,
                MarketQuestionDependency.workspace_id == workspace_id,
                MarketQuestionDependency.question_id == question_id,
            )
            .order_by(MarketQuestionDependency.created_at, MarketQuestionDependency.id)
        ).all()
    )


def _would_create_cycle(
    edges: list[MarketQuestionDependency],
    question_id: UUID,
    depends_on_question_id: UUID,
) -> bool:
    adjacency: dict[UUID, list[UUID]] = {}
    for edge in edges:
        adjacency.setdefault(edge.question_id, []).append(edge.depends_on_question_id)
    pending = [depends_on_question_id]
    visited: set[UUID] = set()
    while pending:
        current = pending.pop()
        if current == question_id:
            return True
        if current in visited:
            continue
        visited.add(current)
        pending.extend(adjacency.get(current, []))
    return False


def create_question_dependency(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    question_id: UUID,
    depends_on_question_id: UUID,
    *,
    actor_user_id: UUID,
) -> tuple[MarketQuestionDependency, bool]:
    _require_workspace(session, organization_id, workspace_id, lock=True)
    ids = {question_id, depends_on_question_id}
    questions = session.scalars(
        select(MarketResearchQuestion).where(
            MarketResearchQuestion.organization_id == organization_id,
            MarketResearchQuestion.workspace_id == workspace_id,
            MarketResearchQuestion.id.in_(ids),
        )
    ).all()
    if len(questions) != 2:
        raise NotFound(
            "当前工作空间内未找到两个依赖问题",
            details={"question_id": str(question_id), "depends_on_question_id": str(depends_on_question_id)},
        )
    existing = session.scalars(
        select(MarketQuestionDependency).where(
            MarketQuestionDependency.organization_id == organization_id,
            MarketQuestionDependency.workspace_id == workspace_id,
            MarketQuestionDependency.question_id == question_id,
            MarketQuestionDependency.depends_on_question_id == depends_on_question_id,
        )
    ).one_or_none()
    if existing is not None:
        return existing, True

    edges = list(
        session.scalars(
            select(MarketQuestionDependency).where(
                MarketQuestionDependency.organization_id == organization_id,
                MarketQuestionDependency.workspace_id == workspace_id,
            )
        ).all()
    )
    if _would_create_cycle(edges, question_id, depends_on_question_id):
        raise Conflict(
            "该依赖会形成循环，已拒绝保存",
            code="RESEARCH_DEPENDENCY_CYCLE",
            details={
                "question_id": str(question_id),
                "depends_on_question_id": str(depends_on_question_id),
            },
        )

    edge = MarketQuestionDependency(
        organization_id=organization_id,
        workspace_id=workspace_id,
        question_id=question_id,
        depends_on_question_id=depends_on_question_id,
    )
    session.add(edge)
    session.flush()
    session.add(
        MarketActivityEvent(
            organization_id=organization_id,
            workspace_id=workspace_id,
            actor_type="USER",
            actor_user_id=actor_user_id,
            aggregate_type="market_question_dependency",
            aggregate_id=edge.id,
            event_type="RESEARCH_QUESTION_DEPENDENCY_CREATED",
            payload={
                "question_id": str(question_id),
                "depends_on_question_id": str(depends_on_question_id),
            },
        )
    )
    session.flush()
    return edge, False


def delete_question_dependency(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    question_id: UUID,
    dependency_id: UUID,
    *,
    actor_user_id: UUID,
) -> UUID:
    _require_workspace(session, organization_id, workspace_id, lock=True)
    edge = session.scalars(
        select(MarketQuestionDependency)
        .where(
            MarketQuestionDependency.organization_id == organization_id,
            MarketQuestionDependency.workspace_id == workspace_id,
            MarketQuestionDependency.question_id == question_id,
            MarketQuestionDependency.id == dependency_id,
        )
        .with_for_update()
    ).one_or_none()
    if edge is None:
        raise NotFound("研究问题依赖关系不存在", details={"dependency_id": str(dependency_id)})
    edge_id = edge.id
    depends_on_id = edge.depends_on_question_id
    session.delete(edge)
    session.flush()
    session.add(
        MarketActivityEvent(
            organization_id=organization_id,
            workspace_id=workspace_id,
            actor_type="USER",
            actor_user_id=actor_user_id,
            aggregate_type="market_question_dependency",
            aggregate_id=edge_id,
            event_type="RESEARCH_QUESTION_DEPENDENCY_REMOVED",
            payload={
                "question_id": str(question_id),
                "depends_on_question_id": str(depends_on_id),
            },
        )
    )
    session.flush()
    return edge_id


def get_research_graph(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
) -> dict[str, Any]:
    _require_workspace(session, organization_id, workspace_id)
    questions = list(
        session.scalars(
            select(MarketResearchQuestion)
            .where(
                MarketResearchQuestion.organization_id == organization_id,
                MarketResearchQuestion.workspace_id == workspace_id,
            )
            .order_by(MarketResearchQuestion.priority, MarketResearchQuestion.created_at, MarketResearchQuestion.id)
        ).all()
    )
    edges = list(
        session.scalars(
            select(MarketQuestionDependency)
            .where(
                MarketQuestionDependency.organization_id == organization_id,
                MarketQuestionDependency.workspace_id == workspace_id,
            )
            .order_by(MarketQuestionDependency.created_at, MarketQuestionDependency.id)
        ).all()
    )
    question_by_id = {row.id: row for row in questions}
    dependencies: dict[UUID, list[UUID]] = {row.id: [] for row in questions}
    dependents: dict[UUID, list[UUID]] = {row.id: [] for row in questions}
    for edge in edges:
        if edge.question_id in dependencies and edge.depends_on_question_id in question_by_id:
            dependencies[edge.question_id].append(edge.depends_on_question_id)
            dependents[edge.depends_on_question_id].append(edge.question_id)

    indegree = {question_id: len(deps) for question_id, deps in dependencies.items()}
    priority_index = {
        row.id: (row.priority, row.created_at, str(row.id))
        for row in questions
    }
    ready = sorted(
        [question_id for question_id, degree in indegree.items() if degree == 0],
        key=priority_index.__getitem__,
    )
    topological_order: list[UUID] = []
    while ready:
        current = ready.pop(0)
        topological_order.append(current)
        for dependent in dependents[current]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
                ready.sort(key=priority_index.__getitem__)
    cycle_detected = len(topological_order) != len(questions)
    if cycle_detected:
        topological_order = []

    nodes: list[dict[str, Any]] = []
    ready_ids: list[UUID] = []
    for question in questions:
        deps = dependencies[question.id]
        waiting_on = [
            dependency_id
            for dependency_id in deps
            if question_by_id[dependency_id].status != "ANSWERED"
        ]
        if question.status == "ANSWERED":
            readiness = "ANSWERED"
        elif question.status == "CANCELLED":
            readiness = "CANCELLED"
        elif question.status == "BLOCKED" or any(
            question_by_id[item].status in {"BLOCKED", "CANCELLED"} for item in deps
        ):
            readiness = "BLOCKED"
        elif waiting_on:
            readiness = "WAITING"
        else:
            readiness = "READY"
            ready_ids.append(question.id)
        nodes.append(
            {
                "id": question.id,
                "question": question.question,
                "rationale": question.rationale,
                "priority": question.priority,
                "status": question.status,
                "source": question.source,
                "parent_question_id": question.parent_question_id,
                "generated_by_run_id": question.generated_by_run_id,
                "planner_question_key": question.planner_question_key,
                "acceptance_criteria": question.acceptance_criteria or [],
                "expected_evidence": question.expected_evidence or [],
                "depends_on_question_ids": deps,
                "waiting_on_question_ids": waiting_on,
                "readiness": readiness,
            }
        )

    return {
        "workspace_id": workspace_id,
        "nodes": nodes,
        "edges": [
            {
                "question_id": edge.question_id,
                "depends_on_question_id": edge.depends_on_question_id,
            }
            for edge in edges
        ],
        "topological_order": topological_order,
        "ready_question_ids": ready_ids,
        "cycle_detected": cycle_detected,
    }
