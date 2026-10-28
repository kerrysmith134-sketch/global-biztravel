"""客户档案接口。

客户是"组织级"资源而不是"出行级"资源：同一个客户会在多次出行里被拜访，档案要连续。
因此导入确认时是**复用**（只补空缺字段）而不是覆盖——避免一次导入把人工维护的
联系方式、行业、分级冲掉（见 :func:`app.services.customer_service.resolve_or_create_customer`）。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import ROLE_ADMIN, ROLE_VIEWER, ContextDep, IdempotencyKeyDep, SessionDep, require_role
from app.api.errors import NotFound
from app.api.idempotent import IdempotentWrite
from app.api.pagination import clamp_limit, decode_cursor
from app.api.pagination import next_cursor as page_cursor
from app.api.schemas import CustomerCreate, CustomerRead, Page
from app.services import customer_service

router = APIRouter(tags=["customers"])

CUSTOMERS_ENDPOINT = "POST /v1/customers"


@router.get("/customers", response_model=Page[CustomerRead], summary="客户列表")
def list_customers(
    context: ContextDep,
    session: SessionDep,
    query: str | None = Query(default=None, description="按名称或外部编号模糊检索"),
    tier: str | None = Query(default=None, description="按客户分级过滤：A/B/C"),
    limit: int | None = Query(default=None, ge=1),
    cursor: str | None = Query(default=None),
):
    require_role(context, ROLE_VIEWER)
    page_size = clamp_limit(limit)
    offset = decode_cursor(cursor)
    customers = customer_service.list_customers(
        session, context.organization_id, query=query, tier=tier, limit=page_size, offset=offset
    )
    items = [CustomerRead.model_validate(customer) for customer in customers]
    return Page[CustomerRead](items=items, next_cursor=page_cursor(customers, offset, page_size))


@router.post(
    "/customers",
    response_model=CustomerRead,
    status_code=status.HTTP_201_CREATED,
    summary="创建客户",
)
def create_customer(
    payload: CustomerCreate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    """``external_ref`` 已存在时返回 409 —— 客户档案不允许同编号两条。"""
    require_role(context, ROLE_ADMIN)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=CUSTOMERS_ENDPOINT,
        key=idempotency_key,
        payload=payload.model_dump(mode="json"),
    )
    # 先检查、后执行：重放请求不得再建第二份客户档案（external_ref 唯一，会直接 409 之外还多脏数据）。
    if (replay := guard.begin()) is not None:
        return replay
    customer = customer_service.create_customer(
        session, context.organization_id, payload, owner_user_id=context.user_id
    )
    body = CustomerRead.model_validate(customer)
    return guard.finish(status_code=status.HTTP_201_CREATED, body=body)


@router.get("/customers/{customer_id}", response_model=CustomerRead, summary="客户详情")
def get_customer(customer_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_VIEWER)
    customer = customer_service.get_customer(session, context.organization_id, customer_id)
    if customer is None:
        raise NotFound("客户不存在", details={"customer_id": str(customer_id)})
    return CustomerRead.model_validate(customer)