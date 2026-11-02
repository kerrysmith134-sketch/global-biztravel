"""团队协作服务（PRD P1：更完整的企业审批、权限和团队协作）。

只做成员列表与角色管理——不建新表（复用 ``memberships``），不引入邀请流程。
角色四层：OWNER > ADMIN > TRAVELER > VIEWER（与 :mod:`app.api.deps` 的权限位一致）。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.errors import NotFound, Unprocessable
from app.models.identity import Membership, User

ROLES = ("OWNER", "ADMIN", "TRAVELER", "VIEWER")


def list_members(
    session: Session, organization_id: UUID | str
) -> list[tuple[Membership, User]]:
    return list(
        session.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.organization_id == str(organization_id))
            .order_by(Membership.created_at)
        ).all()
    )


def _require_membership(
    session: Session, organization_id: UUID | str, user_id: UUID | str
) -> Membership:
    row = session.scalars(
        select(Membership).where(
            Membership.organization_id == str(organization_id),
            Membership.user_id == str(user_id),
        )
    ).one_or_none()
    if row is None:
        raise NotFound("成员不存在", details={"user_id": str(user_id)})
    return row


def _owner_count(
    session: Session, organization_id: UUID | str, *, exclude_user: UUID | str | None = None
) -> int:
    stmt = (
        select(func.count())
        .select_from(Membership)
        .where(
            Membership.organization_id == str(organization_id),
            Membership.role == "OWNER",
        )
    )
    if exclude_user:
        stmt = stmt.where(Membership.user_id != str(exclude_user))
    return int(session.scalar(stmt) or 0)


def add_member(
    session: Session,
    organization_id: UUID | str,
    *,
    user_id: UUID | str,
    role: str,
) -> Membership:
    """把已有用户加入组织；重复加入幂等返回现有成员。"""
    user = session.get(User, UUID(str(user_id)))
    if user is None:
        raise NotFound("用户不存在", details={"user_id": str(user_id)})
    existing = session.scalars(
        select(Membership).where(
            Membership.organization_id == str(organization_id),
            Membership.user_id == str(user_id),
        )
    ).one_or_none()
    if existing is not None:
        return existing
    link = Membership(organization_id=str(organization_id), user_id=str(user_id), role=role)
    session.add(link)
    session.flush()
    return link


def update_role(
    session: Session,
    organization_id: UUID | str,
    *,
    user_id: UUID | str,
    new_role: str,
) -> Membership:
    membership = _require_membership(session, organization_id, user_id)
    if membership.role == "OWNER" and new_role != "OWNER":
        if _owner_count(session, organization_id) <= 1:
            raise Unprocessable("不能降级最后一位 OWNER")
    membership.role = new_role
    session.flush()
    return membership


def remove_member(session: Session, organization_id: UUID | str, *, user_id: UUID | str) -> None:
    membership = _require_membership(session, organization_id, user_id)
    if membership.role == "OWNER" and _owner_count(session, organization_id) <= 1:
        raise Unprocessable("不能移除最后一位 OWNER")
    session.delete(membership)
    session.flush()


__all__ = ["ROLES", "add_member", "list_members", "remove_member", "update_role"]
