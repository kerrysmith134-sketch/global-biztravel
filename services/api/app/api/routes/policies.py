"""差旅政策接口（企业协作 / PRD 方向六）。"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status
from sqlalchemy import select

from app.api.deps import ROLE_ADMIN, ROLE_VIEWER, ContextDep, SessionDep, require_role
from app.api.schemas import (
    PolicyCheckRead,
    PolicyViolationRead,
    TravelPolicyCreate,
    TravelPolicyRead,
    TravelPolicyUpdate,
)
from app.models.planning import PlanVersion
from app.services import policy_service, trip_service

router = APIRouter(tags=["policies"])


@router.get(
    "/travel-policies",
    response_model=list[TravelPolicyRead],
    summary="差旅政策列表",
)
def list_policies(context: ContextDep, session: SessionDep):
    require_role(context, ROLE_VIEWER)
    return [
        TravelPolicyRead.model_validate(row)
        for row in policy_service.list_policies(session, context.organization_id)
    ]


@router.post(
    "/travel-policies",
    response_model=TravelPolicyRead,
    status_code=status.HTTP_201_CREATED,
    summary="创建差旅政策（ADMIN）",
)
def create_policy(
    payload: TravelPolicyCreate, context: ContextDep, session: SessionDep
):
    require_role(context, ROLE_ADMIN)
    row = policy_service.create_policy(session, context.organization_id, payload)
    return TravelPolicyRead.model_validate(row)


@router.patch(
    "/travel-policies/{policy_id}",
    response_model=TravelPolicyRead,
    summary="更新差旅政策（ADMIN）",
)
def update_policy(
    policy_id: UUID,
    payload: TravelPolicyUpdate,
    context: ContextDep,
    session: SessionDep,
):
    require_role(context, ROLE_ADMIN)
    row = policy_service.update_policy(
        session, context.organization_id, policy_id, payload
    )
    return TravelPolicyRead.model_validate(row)


@router.delete(
    "/travel-policies/{policy_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除差旅政策（ADMIN）",
)
def delete_policy(policy_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_ADMIN)
    policy_service.delete_policy(session, context.organization_id, policy_id)


@router.get(
    "/trips/{trip_id}/policy-check",
    response_model=PolicyCheckRead,
    summary="按当前政策检查出行/生效计划",
)
def check_trip_policy(trip_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_VIEWER)
    trip = trip_service.require_trip(session, context.organization_id, trip_id)
    plan = session.scalars(
        select(PlanVersion).where(
            PlanVersion.organization_id == str(context.organization_id),
            PlanVersion.trip_id == trip.id,
            PlanVersion.status == "ACTIVE",
        )
    ).one_or_none()
    evaluation = policy_service.evaluate(
        session,
        context.organization_id,
        trip,
        cost_total=plan.cost_total if plan is not None else None,
        cost_currency=plan.cost_currency if plan is not None else None,
        cost_unknown_count=plan.cost_unknown_count if plan is not None else 0,
    )
    return PolicyCheckRead(
        policy_id=evaluation.policy_id,
        policy_name=evaluation.policy_name,
        strict=evaluation.strict,
        violations=[PolicyViolationRead(**item) for item in policy_service.evaluation_payload(evaluation)],
        checked_at=evaluation.checked_at,
    )
