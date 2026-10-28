"""运维管理接口（设计文档第 11.4 章 / 第 15 章）。"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, status
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.api.deps import ROLE_ADMIN, ContextDep, SessionDep, require_role
from app.api.schemas import (
    ApprovalPolicyCreate,
    ApprovalPolicyRead,
    RetentionReportRead,
)
from app.models.approval import ApprovalPolicy
from app.services import approval_policy_service, retention_service

router = APIRouter(tags=["admin"])


@router.post(
    "/admin:retention",
    response_model=RetentionReportRead,
    status_code=status.HTTP_200_OK,
    summary="立即执行一次留存脱敏（过期笔记/记忆清空正文）",
)
def run_retention(context: ContextDep, session: SessionDep):
    require_role(context, ROLE_ADMIN)
    report = retention_service.apply_retention(session, context.organization_id)
    return RetentionReportRead(
        records_redacted=report.records_redacted,
        summaries_redacted=report.summaries_redacted,
        cutoff=report.cutoff if isinstance(report.cutoff, datetime) else None,
    )


@router.get(
    "/approval-policies",
    response_model=list[ApprovalPolicyRead],
    summary="审批策略列表",
)
def list_approval_policies(context: ContextDep, session: SessionDep):
    require_role(context, ROLE_ADMIN)
    return [
        ApprovalPolicyRead.model_validate(item)
        for item in approval_policy_service.list_policies(session, context.organization_id)
    ]


@router.post(
    "/approval-policies",
    response_model=ApprovalPolicyRead,
    status_code=status.HTTP_201_CREATED,
    summary="创建/更新一条审批策略（按 subject + 最低风险等级唯一）",
)
def upsert_approval_policy(
    payload: ApprovalPolicyCreate, context: ContextDep, session: SessionDep
):
    require_role(context, ROLE_ADMIN)
    statement = (
        pg_insert(ApprovalPolicy.__table__)
        .values(
            organization_id=str(context.organization_id),
            subject_type=payload.subject_type,
            min_severity=payload.min_severity,
            required_role=payload.required_role,
            enabled=payload.enabled,
        )
        .on_conflict_do_update(
            constraint="uq_approval_policies_org_subject_severity",
            set_={"required_role": payload.required_role, "enabled": payload.enabled},
        )
        .returning(ApprovalPolicy.__table__.c.id)
    )
    policy_id = session.execute(statement).scalar_one()
    policy = session.get(ApprovalPolicy, policy_id)
    return ApprovalPolicyRead.model_validate(policy)


@router.delete(
    "/approval-policies/{policy_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除审批策略",
)
def delete_approval_policy(policy_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_ADMIN)
    session.execute(
        delete(ApprovalPolicy).where(
            ApprovalPolicy.organization_id == str(context.organization_id),
            ApprovalPolicy.id == str(policy_id),
        )
    )
