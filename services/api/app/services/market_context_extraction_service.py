"""Extract a versioned, user-provided business context through the shared LLM gateway.

The extraction is deliberately narrow: it organizes the supplied text and never performs
market research, invents facts, or writes to formal Customer / Trip records.
"""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import Conflict, NotFound
from app.llm.contracts import LLMRequest
from app.llm.gateway import get_gateway
from app.llm.prompts import (
    MARKET_CONTEXT_EXTRACT_PROMPT_VERSION,
    MARKET_CONTEXT_EXTRACT_SCHEMA,
    MARKET_CONTEXT_EXTRACT_SYSTEM,
    MARKET_CONTEXT_EXTRACT_TEMPLATE_ID,
)
from app.models.market import (
    MarketActivityEvent,
    MarketContextSnapshot,
    MarketWorkspace,
    MarketWorkspaceWorkflow,
)
from app.models.workflow import AgentRun, WorkflowInstance

AGENT_TYPE = "market_context_extraction"
WORKFLOW_TYPE = "market_context_extraction"
EXTRACTED_FIELDS = (
    "offerings",
    "business_models",
    "industries",
    "customer_segments",
    "value_propositions",
    "target_markets",
    "goals",
    "constraints",
)
MAX_ITEMS_PER_FIELD = 16
MAX_TEXT_LENGTH = 1200


def _text(value: Any, *, limit: int = MAX_TEXT_LENGTH) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned[:limit] if cleaned else None


def _normalise_items(
    raw_items: Any, raw_input: str
) -> tuple[list[dict[str, Any]], int]:
    if not isinstance(raw_items, list):
        return [], 0
    result: list[dict[str, Any]] = []
    rejected = 0
    for item in raw_items[:MAX_ITEMS_PER_FIELD]:
        if not isinstance(item, dict):
            rejected += 1
            continue
        label = _text(item.get("label"), limit=300)
        quote = _text(item.get("source_quote"), limit=600)
        # Requiring an exact quote prevents ungrounded model additions from being
        # silently promoted into structured user context.
        if not label or not quote or quote not in raw_input:
            rejected += 1
            continue
        confidence = item.get("confidence")
        try:
            confidence_value = max(0.0, min(float(confidence), 1.0))
        except (TypeError, ValueError):
            confidence_value = 0.5
        result.append(
            {
                "label": label,
                "normalized_label": _text(item.get("normalized_label"), limit=300),
                "description": _text(item.get("description")),
                "source_quote": quote,
                "confidence": confidence_value,
            }
        )
    if isinstance(raw_items, list) and len(raw_items) > MAX_ITEMS_PER_FIELD:
        rejected += len(raw_items) - MAX_ITEMS_PER_FIELD
    return result, rejected


def _normalise_text_list(value: Any, *, limit: int = 16) -> list[str]:
    if not isinstance(value, list):
        return []
    output: list[str] = []
    for item in value[:limit]:
        cleaned = _text(item)
        if cleaned:
            output.append(cleaned)
    return list(dict.fromkeys(output))


def _normalise_output(
    output: dict[str, Any], raw_input: str
) -> tuple[dict[str, Any], list[str], int]:
    extracted: dict[str, Any] = {}
    rejected = 0
    for field in EXTRACTED_FIELDS:
        items, dropped = _normalise_items(output.get(field), raw_input)
        extracted[field] = items
        rejected += dropped

    # Assumptions are explicitly kept separate from source-grounded categories.
    assumptions: list[str] = []
    for item in output.get("assumptions", [])[:16] if isinstance(output.get("assumptions"), list) else []:
        candidate = item.get("statement") if isinstance(item, dict) else item
        cleaned = _text(candidate)
        if cleaned:
            assumptions.append(cleaned)
    extracted["assumptions"] = list(dict.fromkeys(assumptions))
    unknowns = _normalise_text_list(output.get("unknowns"))
    if rejected:
        unknowns.append(
            f"{rejected} 个未能通过原文逐字引文校验的候选项已被丢弃。"
        )
    return extracted, list(dict.fromkeys(unknowns)), rejected


def extract_context_snapshot(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    context_id: UUID,
    *,
    actor_user_id: UUID,
) -> MarketContextSnapshot:
    """Extract one immutable context version; user overrides always win."""
    workspace = session.scalars(
        select(MarketWorkspace)
        .where(
            MarketWorkspace.organization_id == organization_id,
            MarketWorkspace.id == workspace_id,
        )
        .with_for_update()
    ).one_or_none()
    if workspace is None:
        raise NotFound(
            "市场工作空间不存在", details={"workspace_id": str(workspace_id)}
        )

    snapshot = session.scalars(
        select(MarketContextSnapshot)
        .where(
            MarketContextSnapshot.organization_id == organization_id,
            MarketContextSnapshot.workspace_id == workspace_id,
            MarketContextSnapshot.id == context_id,
        )
        .with_for_update()
    ).one_or_none()
    if snapshot is None:
        raise NotFound(
            "业务上下文版本不存在", details={"context_id": str(context_id)}
        )
    if not snapshot.raw_input.strip():
        raise Conflict("原始业务背景为空，无法执行上下文提取")
    if (
        snapshot.extraction_status == "COMPLETED"
        and snapshot.extraction_prompt_id == MARKET_CONTEXT_EXTRACT_TEMPLATE_ID
        and snapshot.extraction_prompt_version == MARKET_CONTEXT_EXTRACT_PROMPT_VERSION
    ):
        return snapshot
    if snapshot.extraction_status == "PENDING":
        raise Conflict("该上下文版本已有提取运行，暂时不能重复启动")

    input_digest = sha256(snapshot.raw_input.encode("utf-8")).hexdigest()
    workflow = WorkflowInstance(
        organization_id=organization_id,
        workflow_type=WORKFLOW_TYPE,
        status="RUNNING",
        payload={
            "workspace_id": str(workspace_id),
            "context_snapshot_id": str(context_id),
            "context_version": snapshot.version,
            "input_sha256": input_digest,
            "prompt_template_id": MARKET_CONTEXT_EXTRACT_TEMPLATE_ID,
            "prompt_version": MARKET_CONTEXT_EXTRACT_PROMPT_VERSION,
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
    run = AgentRun(
        organization_id=organization_id,
        workflow_id=workflow.id,
        agent_type=AGENT_TYPE,
        status="RUNNING",
        input={
            "workspace_id": str(workspace_id),
            "context_snapshot_id": str(context_id),
            "context_version": snapshot.version,
            "input_sha256": input_digest,
            "input_character_count": len(snapshot.raw_input),
            "prompt_template_id": MARKET_CONTEXT_EXTRACT_TEMPLATE_ID,
            "prompt_version": MARKET_CONTEXT_EXTRACT_PROMPT_VERSION,
        },
    )
    session.add(run)
    snapshot.extraction_status = "PENDING"
    snapshot.extraction_prompt_id = MARKET_CONTEXT_EXTRACT_TEMPLATE_ID
    snapshot.extraction_prompt_version = MARKET_CONTEXT_EXTRACT_PROMPT_VERSION
    session.flush()

    fallback = {
        **{field: [] for field in EXTRACTED_FIELDS},
        "assumptions": [],
        "unknowns": ["模型服务不可用，尚未完成上下文提取。"],
    }
    response = get_gateway().complete(
        session,
        organization_id,
        LLMRequest(
            task="market_context_extract",
            prompt_template_id=MARKET_CONTEXT_EXTRACT_TEMPLATE_ID,
            prompt_version=MARKET_CONTEXT_EXTRACT_PROMPT_VERSION,
            variables={"raw_input": snapshot.raw_input},
            schema=MARKET_CONTEXT_EXTRACT_SCHEMA,
            fallback=fallback,
            system=MARKET_CONTEXT_EXTRACT_SYSTEM,
            max_tokens=1400,
        ),
    )

    run.finished_at = datetime.now(UTC)
    audit = {
        "agent_run_id": str(run.id),
        "workflow_id": str(workflow.id),
        "llm_invocation_id": str(response.invocation_id) if response.invocation_id else None,
        "model": response.model,
        "degraded": response.degraded,
        "input_sha256": input_digest,
        "prompt_template_id": MARKET_CONTEXT_EXTRACT_TEMPLATE_ID,
        "prompt_version": MARKET_CONTEXT_EXTRACT_PROMPT_VERSION,
    }
    provenance = dict(snapshot.provenance or {})
    provenance["extraction"] = audit

    if response.degraded:
        snapshot.extraction_status = "FAILED"
        provenance["extraction"]["status"] = "FAILED"
        provenance["extraction"]["error"] = response.error or "LLM Gateway degraded"
        snapshot.provenance = provenance
        run.status = "FAILED"
        run.error = (response.error or "LLM Gateway degraded")[:2000]
        run.output = {"status": "FAILED", "input_sha256": input_digest}
        workflow.status = "FAILED"
        workflow.payload = {**(workflow.payload or {}), "extraction_status": "FAILED"}
        event_type = "CONTEXT_EXTRACTION_FAILED"
    else:
        extracted, unknowns, _rejected = _normalise_output(
            response.output, snapshot.raw_input
        )
        user_overrides = dict(snapshot.user_overrides or {})
        for field in EXTRACTED_FIELDS + ("assumptions",):
            if field in user_overrides:
                extracted[field] = user_overrides[field]
                provenance[field] = {"source": "USER_EDIT"}
            else:
                provenance[field] = {
                    "source": "MODEL",
                    "prompt_id": MARKET_CONTEXT_EXTRACT_TEMPLATE_ID,
                    "prompt_version": MARKET_CONTEXT_EXTRACT_PROMPT_VERSION,
                    "input_sha256": input_digest,
                }
        snapshot.extracted_context = extracted
        snapshot.unknowns = unknowns
        snapshot.provenance = provenance
        snapshot.extraction_status = "COMPLETED"
        run.status = "SUCCEEDED"
        run.output = {
            "status": "COMPLETED",
            "input_sha256": input_digest,
            "field_item_counts": {
                key: len(value) for key, value in extracted.items() if isinstance(value, list)
            },
            "unknown_count": len(unknowns),
        }
        workflow.status = "COMPLETED"
        workflow.payload = {**(workflow.payload or {}), "extraction_status": "COMPLETED"}
        event_type = "CONTEXT_EXTRACTION_COMPLETED"

    session.add(
        MarketActivityEvent(
            organization_id=organization_id,
            workspace_id=workspace_id,
            actor_type="USER",
            actor_user_id=actor_user_id,
            aggregate_type="market_context_snapshot",
            aggregate_id=snapshot.id,
            event_type=event_type,
            payload={
                "context_version": snapshot.version,
                "workflow_id": str(workflow.id),
                "agent_run_id": str(run.id),
                "input_sha256": input_digest,
                "degraded": response.degraded,
            },
        )
    )
    session.flush()
    return snapshot


__all__ = [
    "AGENT_TYPE",
    "WORKFLOW_TYPE",
    "extract_context_snapshot",
]
