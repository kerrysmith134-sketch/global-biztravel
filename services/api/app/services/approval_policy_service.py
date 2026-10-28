"""审批策略服务：按建议风险等级计算最低批准角色并执行（设计文档 N1）。"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import Forbidden
from app.models.approval import ApprovalPolicy

ROLE_RANK = {"VIEWER": 0, "TRAVELER": 1, "ADMIN": 2, "OWNER": 3}
SEVERITY_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
SUBJECT_SUGGESTION = "proactive_suggestion"


@dataclass(frozen=True)
class ApprovalRequirement:
    required_role: str | None
    required_approvals: int


def requirement(
    session: Session,
    organization_id: UUID | str,
    *,
    severity: str,
    subject_type: str = SUBJECT_SUGGESTION,
) -> ApprovalRequirement:
    """返回适用于该风险等级的最低角色与会签人数；无匹配策略 ⇒ 不额外限制。"""
    rows = list(
        session.scalars(
            select(ApprovalPolicy).where(
                ApprovalPolicy.organization_id == str(organization_id),
                ApprovalPolicy.subject_type == subject_type,
                ApprovalPolicy.enabled.is_(True),
            )
        )
    )
    severity_rank = SEVERITY_RANK.get(severity.upper(), 2)
    matching = [
        policy
        for policy in rows
        if SEVERITY_RANK.get(policy.min_severity, 0) <= severity_rank
    ]
    if not matching:
        return ApprovalRequirement(None, 1)
    best = max(
        matching,
        key=lambda policy: (
            ROLE_RANK.get(policy.required_role, 0),
            policy.required_approvals,
        ),
    )
    return ApprovalRequirement(best.required_role, max(best.required_approvals, 1))


def required_role(
    session: Session,
    organization_id: UUID | str,
    *,
    severity: str,
    subject_type: str = SUBJECT_SUGGESTION,
) -> str | None:
    return requirement(
        session, organization_id, severity=severity, subject_type=subject_type
    ).required_role


def enforce(
    session: Session,
    organization_id: UUID | str,
    *,
    severity: str,
    actor_role: str | None,
) -> int:
    """校验角色并返回所需会签人数（无匹配策略/无角色信息时至少 1）。"""
    req = requirement(session, organization_id, severity=severity)
    if req.required_role is None or actor_role is None:
        return req.required_approvals
    if ROLE_RANK.get(actor_role, -1) < ROLE_RANK.get(req.required_role, 0):
        raise Forbidden(
            "该建议的风险等级需要更高角色批准",
            details={
                "required_role": req.required_role,
                "actor_role": actor_role,
                "severity": severity,
            },
        )
    return req.required_approvals


def list_policies(
    session: Session, organization_id: UUID | str
) -> list[ApprovalPolicy]:
    return list(
        session.scalars(
            select(ApprovalPolicy)
            .where(ApprovalPolicy.organization_id == str(organization_id))
            .order_by(ApprovalPolicy.min_severity)
        )
    )


__all__ = [
    "ApprovalRequirement",
    "ROLE_RANK",
    "SEVERITY_RANK",
    "SUBJECT_SUGGESTION",
    "enforce",
    "list_policies",
    "required_role",
    "requirement",
]
