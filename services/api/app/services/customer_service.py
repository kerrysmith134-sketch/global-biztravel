"""客户服务：客户建档、按外部编号去重、列表检索。

去重依据（第 4.4 章）：优先 ``external_ref``（客户在 CRM/ERP 里的编号），其次
``(name, country_code)``。刻意**不**用"名称模糊相似"来自动合并——把两个不同客户合并
比留下两条重复更糟，因为后续会议纪要与跟进任务会挂到错的客户身上。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.errors import Conflict
from app.api.schemas import CustomerCreate
from app.models.geo import Customer


def find_by_external_ref(
    session: Session, organization_id: UUID | str, external_ref: str | None
) -> Customer | None:
    if not external_ref:
        return None
    return session.scalars(
        select(Customer).where(
            Customer.organization_id == organization_id,
            Customer.external_ref == external_ref.strip(),
        )
    ).one_or_none()


def find_by_name(
    session: Session,
    organization_id: UUID | str,
    *,
    name: str,
    country_code: str | None = None,
) -> Customer | None:
    normalized = " ".join(name.split()).casefold()
    stmt = select(Customer).where(
        Customer.organization_id == organization_id,
        func.lower(func.btrim(Customer.name)) == normalized,
    )
    if country_code:
        stmt = stmt.where(func.coalesce(Customer.country_code, "") == country_code.upper())
    return session.scalars(stmt.order_by(Customer.created_at).limit(1)).one_or_none()


def create_customer(
    session: Session,
    organization_id: UUID | str,
    payload: CustomerCreate,
    *,
    owner_user_id: UUID | None = None,
) -> Customer:
    if payload.external_ref:
        clash = find_by_external_ref(session, organization_id, payload.external_ref)
        if clash is not None:
            raise Conflict(
                "该 external_ref 已存在",
                details={"external_ref": payload.external_ref, "existing_customer_id": str(clash.id)},
            )
    customer = Customer(
        organization_id=organization_id,
        name=payload.name.strip(),
        external_ref=payload.external_ref.strip() if payload.external_ref else None,
        tier=payload.tier,
        country_code=payload.country_code.upper() if payload.country_code else None,
        industry=payload.industry,
        primary_contact_name=payload.primary_contact_name,
        primary_contact_email=payload.primary_contact_email,
        primary_contact_phone=payload.primary_contact_phone,
        status=payload.status,
        notes=payload.notes,
        owner_user_id=owner_user_id,
    )
    session.add(customer)
    session.flush()
    return customer


def resolve_or_create_customer(
    session: Session,
    organization_id: UUID | str,
    *,
    name: str,
    country_code: str | None = None,
    external_ref: str | None = None,
    tier: str = "C",
    industry: str | None = None,
    primary_contact_name: str | None = None,
    primary_contact_email: str | None = None,
    primary_contact_phone: str | None = None,
    owner_user_id: UUID | None = None,
) -> tuple[Customer, bool]:
    """返回 ``(customer, reused)``。先按 external_ref，再按 name+country 复用。"""
    existing = find_by_external_ref(session, organization_id, external_ref) or find_by_name(
        session, organization_id, name=name, country_code=country_code
    )
    if existing is not None:
        # 复用而非覆盖：只补空缺字段，避免导入把人工维护的客户档案冲掉。
        if existing.external_ref is None and external_ref:
            existing.external_ref = external_ref.strip()
        if existing.primary_contact_email is None and primary_contact_email:
            existing.primary_contact_email = primary_contact_email
        if existing.primary_contact_name is None and primary_contact_name:
            existing.primary_contact_name = primary_contact_name
        if existing.industry is None and industry:
            existing.industry = industry
        return existing, True

    customer = Customer(
        organization_id=organization_id,
        name=name.strip(),
        external_ref=external_ref.strip() if external_ref else None,
        tier=tier,
        country_code=country_code.upper() if country_code else None,
        industry=industry,
        primary_contact_name=primary_contact_name,
        primary_contact_email=primary_contact_email,
        primary_contact_phone=primary_contact_phone,
        owner_user_id=owner_user_id,
    )
    session.add(customer)
    session.flush()
    return customer, False


def get_customer(
    session: Session, organization_id: UUID | str, customer_id: UUID
) -> Customer | None:
    return session.scalars(
        select(Customer).where(
            Customer.organization_id == organization_id, Customer.id == customer_id
        )
    ).one_or_none()


def list_customers(
    session: Session,
    organization_id: UUID | str,
    *,
    query: str | None = None,
    tier: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Customer]:
    stmt = select(Customer).where(Customer.organization_id == organization_id)
    if query:
        pattern = f"%{query.strip()}%"
        stmt = stmt.where(
            Customer.name.ilike(pattern) | func.coalesce(Customer.external_ref, "").ilike(pattern)
        )
    if tier:
        stmt = stmt.where(Customer.tier == tier.upper())
    stmt = stmt.order_by(Customer.name).limit(limit).offset(offset)
    return list(session.scalars(stmt).all())