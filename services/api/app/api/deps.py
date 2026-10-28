"""HTTP 依赖：身份上下文、租户会话、权限门与幂等键。

设计文档 v2.0 第 6.3 章。这里刻意只做 HTTP 语义，不做业务判断：

- **身份**：真实部署里由网关/OIDC 断言注入 ``X-Org-Id`` / ``X-User-Id``；本地开发允许直接传，
  因为它让"两个租户互相看不见"这条验收（AT-S01）可以在真实数据库上被真实跑出来。
  关掉 ``GBT_DEV_AUTH`` 后，缺失身份头一律 401。
- **成员资格**：身份头**不足以**获得访问权。每次请求都要确认 ``(org, user)`` 存在
  membership，并把角色映射成最低权限。否则任何人都能靠改一个请求头读别人公司数据。
- **会话**：每个请求一个会话，事务内已绑定租户 GUC；成功提交、异常回滚。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import BadRequest, Forbidden, Unauthorized
from app.config import get_settings
from app.db import AppSession, bind_org
from app.models.identity import Membership

#: 角色 → 能力等级。VIEWER 只读，TRAVELER 能改自己的出行内容，
#: ADMIN 能改组织级配置（客户档案、成员），OWNER 全部。
ROLE_RANK: dict[str, int] = {"VIEWER": 0, "TRAVELER": 1, "ADMIN": 2, "OWNER": 3}
ROLE_VIEWER, ROLE_TRAVELER, ROLE_ADMIN, ROLE_OWNER = 0, 1, 2, 3


@dataclass(frozen=True)
class RequestContext:
    """一次请求的身份上下文。``role`` 是已解析的成员角色。"""

    organization_id: UUID
    user_id: UUID
    role: str
    is_dev_auth: bool

    @property
    def rank(self) -> int:
        return ROLE_RANK.get(self.role, -1)


def _parse_uuid(value: str | None, *, header: str) -> UUID | None:
    if value is None or not value.strip():
        return None
    try:
        return UUID(value.strip())
    except ValueError as exc:
        raise Unauthorized(
            f"请求头 {header} 不是合法的 UUID", details={"header": header}
        ) from exc


def get_context(request: Request) -> RequestContext:
    """解析身份头。返回的上下文里角色已校验，路由不再重复查成员表。"""
    settings = get_settings()
    org_id = _parse_uuid(request.headers.get("X-Org-Id"), header="X-Org-Id")
    user_id = _parse_uuid(request.headers.get("X-User-Id"), header="X-User-Id")

    if org_id is None:
        if not settings.dev_auth:
            raise Unauthorized("缺少 X-Org-Id 请求头")
        org_id = settings.dev_org_id
    if user_id is None:
        if not settings.dev_auth:
            raise Unauthorized("缺少 X-User-Id 请求头")
        user_id = settings.dev_user_id

    session = AppSession()
    try:
        bind_org(session, org_id)
        membership = session.scalars(
            select(Membership).where(
                Membership.organization_id == org_id, Membership.user_id == user_id
            )
        ).one_or_none()
        if membership is None:
            raise Forbidden(
                "该用户不是此组织的成员",
                details={"organization_id": str(org_id), "user_id": str(user_id)},
            )
        request.state.org_id = org_id
        request.state.user_id = user_id
        return RequestContext(
            organization_id=org_id,
            user_id=user_id,
            role=membership.role,
            is_dev_auth=settings.dev_auth,
        )
    finally:
        session.close()


def require_role(context: RequestContext, minimum: int) -> None:
    if context.rank < minimum:
        raise Forbidden(
            "当前角色无权执行该操作",
            details={"role": context.role, "required_rank": minimum},
        )


def get_db(context: RequestContext, request: Request | None = None) -> Iterator[Session]:
    """请求级会话：事务内绑定租户 GUC，成功提交、异常回滚。

    会话会挂到 ``request.state.db_session``，由 :class:`app.api.transaction.CommitBeforeResponseMiddleware`
    在响应开始前提交——FastAPI 的 yield 依赖 teardown 发生在响应发送之后，不能依赖它保证
    "客户端看到 2xx 时数据已经落库"。
    """
    session = AppSession()
    bind_org(session, context.organization_id)
    if request is not None:
        request.state.db_session = session
    try:
        yield session
        if not session.info.get("skip_commit"):
            session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def idempotency_key(
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> str | None:
    """可选的幂等键。

    契约上写接口"接受"该头；本地开发不带也能用，但**带上就一定会被保证**：
    重放同一请求不会产生第二次副作用（见 :mod:`app.services.idempotency`）。
    """
    return idempotency_key.strip() if idempotency_key else None


def if_match_version(
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> int | None:
    """解析 ``If-Match`` 为整数版本令牌。``W/"3"``、``"3"``、``3`` 都接受。"""
    if if_match is None:
        return None
    token = if_match.strip().strip('"')
    if token.startswith("W/"):
        token = token[2:].strip().strip('"')
    try:
        return int(token)
    except ValueError as exc:
        raise BadRequest("If-Match 必须是整数版本号", details={"if_match": if_match}) from exc


# ------------------------------------------------------- FastAPI 注解别名
# 上面那些函数刻意写成"普通函数"（易测、不依赖框架）；这里用 Annotated 把它们接进
# FastAPI 的依赖解析，路由侧只写 `SessionDep` / `ContextDep` 即可，不再手写 Sub-dependency。
ContextDep = Annotated[RequestContext, Depends(get_context)]
IdempotencyKeyDep = Annotated[str | None, Depends(idempotency_key)]
IfMatchDep = Annotated[int | None, Depends(if_match_version)]


def session_dependency(request: Request, context: ContextDep) -> Iterator[Session]:
    """把请求级租户会话包装成 FastAPI 依赖。"""
    yield from get_db(context, request)


SessionDep = Annotated[Session, Depends(session_dependency)]