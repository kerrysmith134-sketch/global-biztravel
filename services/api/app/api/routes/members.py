"""团队协作接口（PRD P1）。"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import ROLE_ADMIN, ROLE_VIEWER, ContextDep, SessionDep, require_role
from app.api.errors import Unprocessable
from app.api.schemas import MemberAdd, MemberRead, MemberRoleUpdate
from app.services import member_service

router = APIRouter(tags=["members"])


def _read(row: tuple) -> MemberRead:
    membership, user = row
    return MemberRead(
        user_id=user.id,
        display_name=user.display_name,
        email=user.email,
        role=membership.role,
        joined_at=membership.created_at,
    )


@router.get("/members", response_model=list[MemberRead], summary="组织成员列表")
def list_members(context: ContextDep, session: SessionDep):
    require_role(context, ROLE_VIEWER)
    return [_read(row) for row in member_service.list_members(session, context.organization_id)]


@router.post("/members", response_model=MemberRead, status_code=status.HTTP_201_CREATED, summary="添加成员（需用户已存在）")
def add_member(payload: MemberAdd, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_ADMIN)
    if payload.user_id == context.user_id:
        raise Unprocessable("不能添加自己")
    membership = member_service.add_member(
        session, context.organization_id, user_id=str(payload.user_id), role=payload.role
    )
    user = membership.user_id
    row = next(r for r in member_service.list_members(session, context.organization_id) if str(r[0].user_id) == str(user))
    return _read(row)


@router.patch("/members/{user_id}", response_model=MemberRead, summary="变更成员角色")
def update_role(
    user_id: UUID,
    payload: MemberRoleUpdate,
    context: ContextDep,
    session: SessionDep,
):
    require_role(context, ROLE_ADMIN)
    if payload.role == "OWNER" and context.role != "OWNER":
        raise Unprocessable("只有 OWNER 能提升为 OWNER")
    member_service.update_role(
        session, context.organization_id, user_id=str(user_id), new_role=payload.role
    )
    row = next(r for r in member_service.list_members(session, context.organization_id) if str(r[0].user_id) == str(user_id))
    return _read(row)


@router.delete("/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT, summary="移除成员")
def remove_member(user_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_ADMIN)
    member_service.remove_member(session, context.organization_id, user_id=str(user_id))
