"""MarketEntity candidate discovery and explicit Customer-link use cases."""

from __future__ import annotations

import unicodedata
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.api.errors import BadRequest, Conflict, NotFound
from app.models.geo import Customer
from app.models.market import (
    MarketActivityEvent,
    MarketEntity,
    MarketEntityAlias,
    MarketEntityCustomerLink,
    MarketWorkspace,
    MarketWorkspaceEntity,
)


def normalize_name(value: str) -> str:
    """NFKC + casefold + collapsed whitespace; preserve display_name separately."""
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def normalize_domain(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    raw = value.strip()
    parsed = urlsplit(raw if "://" in raw else f"//{raw}")
    if parsed.username or parsed.password or parsed.port:
        raise BadRequest("主域名不能包含用户名、密码或端口")
    host = parsed.hostname
    if not host:
        raise BadRequest("主域名格式无效")
    try:
        canonical = host.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise BadRequest("主域名无法进行 IDNA 规范化") from exc
    if not canonical or any(not part for part in canonical.split(".")):
        raise BadRequest("主域名格式无效")
    return canonical[4:] if canonical.startswith("www.") else canonical


def _workspace_or_404(
    session: Session, organization_id: UUID | str, workspace_id: UUID
) -> MarketWorkspace:
    workspace = session.scalars(
        select(MarketWorkspace).where(
            MarketWorkspace.organization_id == str(organization_id),
            MarketWorkspace.id == workspace_id,
        )
    ).one_or_none()
    if workspace is None:
        raise NotFound("市场工作空间不存在", details={"workspace_id": str(workspace_id)})
    return workspace


def get_market_entity(
    session: Session, organization_id: UUID | str, entity_id: UUID
) -> MarketEntity | None:
    return session.scalars(
        select(MarketEntity).where(
            MarketEntity.organization_id == str(organization_id),
            MarketEntity.id == entity_id,
        )
    ).one_or_none()


def _ensure_alias(
    session: Session,
    organization_id: UUID | str,
    entity_id: UUID,
    *,
    alias_type: str,
    raw_value: str,
    normalized_value: str,
) -> None:
    existing = session.scalars(
        select(MarketEntityAlias).where(
            MarketEntityAlias.organization_id == str(organization_id),
            MarketEntityAlias.entity_id == entity_id,
            MarketEntityAlias.alias_type == alias_type,
            MarketEntityAlias.normalized_value == normalized_value,
        )
    ).one_or_none()
    if existing is None:
        session.add(
            MarketEntityAlias(
                organization_id=str(organization_id),
                entity_id=entity_id,
                alias_type=alias_type,
                raw_value=raw_value,
                normalized_value=normalized_value,
            )
        )


def _entity_with_aliases(
    session: Session, organization_id: UUID | str, entity: MarketEntity
) -> dict:
    aliases = session.scalars(
        select(MarketEntityAlias)
        .where(
            MarketEntityAlias.organization_id == str(organization_id),
            MarketEntityAlias.entity_id == entity.id,
        )
        .order_by(MarketEntityAlias.alias_type, MarketEntityAlias.raw_value)
    ).all()
    return {
        "id": entity.id,
        "entity_kind": entity.entity_kind,
        "display_name": entity.display_name,
        "normalized_name": entity.normalized_name,
        "description": entity.description,
        "canonical_domain": entity.canonical_domain,
        "country_code": entity.country_code,
        "attributes": entity.attributes,
        "status": entity.status,
        "created_at": entity.created_at,
        "updated_at": entity.updated_at,
        "aliases": [
            {
                "id": alias.id,
                "alias_type": alias.alias_type,
                "raw_value": alias.raw_value,
                "normalized_value": alias.normalized_value,
                "source_evidence_id": alias.source_evidence_id,
                "created_at": alias.created_at,
            }
            for alias in aliases
        ],
    }


def _record_workspace_entity_event(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    *,
    entity_id: UUID,
    actor_user_id: UUID,
    event_type: str,
    payload: dict | None = None,
) -> None:
    session.add(
        MarketActivityEvent(
            organization_id=str(organization_id),
            workspace_id=workspace_id,
            actor_type="USER",
            actor_user_id=actor_user_id,
            aggregate_type="market_entity",
            aggregate_id=entity_id,
            event_type=event_type,
            payload=payload or {},
        )
    )


def create_workspace_entity(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    *,
    entity_kind: str,
    display_name: str,
    description: str | None,
    canonical_domain: str | None,
    country_code: str | None,
    attributes: dict,
    discovery_reason: str | None,
    relevance_note: str | None,
    created_by_user_id: UUID,
) -> dict:
    _workspace_or_404(session, organization_id, workspace_id)
    normalized_name = normalize_name(display_name)
    if not normalized_name:
        raise BadRequest("实体名称不能为空")

    domain = normalize_domain(canonical_domain)
    country = country_code.upper() if country_code else None
    if country is not None and len(country) != 2:
        raise BadRequest("country_code 必须为 ISO alpha-2 格式")

    possible_duplicates: list[UUID] = []
    duplicate_stmt = select(MarketEntity.id).where(
        MarketEntity.organization_id == str(organization_id),
        MarketEntity.normalized_name == normalized_name,
    )
    if country:
        duplicate_stmt = duplicate_stmt.where(
            func.coalesce(MarketEntity.country_code, "") == country
        )
    else:
        duplicate_stmt = duplicate_stmt.where(MarketEntity.country_code.is_(None))
    possible_duplicates = list(session.scalars(duplicate_stmt).all())

    reused_by_domain = False
    entity: MarketEntity | None = None
    if domain:
        # Let PostgreSQL arbitrate concurrent creates against the partial unique index.
        inserted_id = session.scalar(
            pg_insert(MarketEntity)
            .values(
                organization_id=str(organization_id),
                entity_kind=entity_kind.strip(),
                display_name=display_name,
                normalized_name=normalized_name,
                description=description,
                canonical_domain=domain,
                country_code=country,
                attributes=attributes,
                status="CANDIDATE",
                created_by_user_id=created_by_user_id,
            )
            .on_conflict_do_nothing(
                index_elements=["organization_id", "canonical_domain"],
                index_where=text("canonical_domain IS NOT NULL"),
            )
            .returning(MarketEntity.id)
        )
        if inserted_id is None:
            entity = session.scalars(
                select(MarketEntity).where(
                    MarketEntity.organization_id == str(organization_id),
                    MarketEntity.canonical_domain == domain,
                )
            ).one_or_none()
            if entity is None:
                raise Conflict(
                    "实体主域名创建发生并发冲突，请重试",
                    details={"canonical_domain": domain},
                )
            reused_by_domain = True
        else:
            entity = session.scalars(
                select(MarketEntity).where(
                    MarketEntity.organization_id == str(organization_id),
                    MarketEntity.id == inserted_id,
                )
            ).one()
    else:
        entity = MarketEntity(
            organization_id=str(organization_id),
            entity_kind=entity_kind.strip(),
            display_name=display_name,
            normalized_name=normalized_name,
            description=description,
            country_code=country,
            attributes=attributes,
            status="CANDIDATE",
            created_by_user_id=created_by_user_id,
        )
        session.add(entity)
        session.flush()

    _ensure_alias(
        session,
        organization_id,
        entity.id,
        alias_type="NAME",
        raw_value=display_name,
        normalized_value=normalized_name,
    )
    if domain:
        _ensure_alias(
            session,
            organization_id,
            entity.id,
            alias_type="DOMAIN",
            raw_value=canonical_domain or domain,
            normalized_value=domain,
        )

    link = session.scalars(
        select(MarketWorkspaceEntity).where(
            MarketWorkspaceEntity.organization_id == str(organization_id),
            MarketWorkspaceEntity.workspace_id == workspace_id,
            MarketWorkspaceEntity.entity_id == entity.id,
        )
    ).one_or_none()
    if link is None:
        link = MarketWorkspaceEntity(
            organization_id=str(organization_id),
            workspace_id=workspace_id,
            entity_id=entity.id,
            discovery_reason=discovery_reason,
            relevance_note=relevance_note,
            status="DISCOVERED",
        )
        session.add(link)
        _record_workspace_entity_event(
            session,
            organization_id,
            workspace_id,
            entity_id=entity.id,
            actor_user_id=created_by_user_id,
            event_type="WORKSPACE_ENTITY_ADDED",
            payload={"entity_kind": entity.entity_kind},
        )
    else:
        link.last_seen_at = func.now()
        if link.discovery_reason is None and discovery_reason:
            link.discovery_reason = discovery_reason
        if link.relevance_note is None and relevance_note:
            link.relevance_note = relevance_note

    if not reused_by_domain:
        # This event is intentionally scoped to the Workspace where the candidate was added.
        _record_workspace_entity_event(
            session,
            organization_id,
            workspace_id,
            entity_id=entity.id,
            actor_user_id=created_by_user_id,
            event_type="MARKET_ENTITY_DISCOVERED",
            payload={"entity_kind": entity.entity_kind},
        )

    session.flush()
    return {
        "entity": _entity_with_aliases(session, organization_id, entity),
        "discovery_reason": link.discovery_reason,
        "relevance_note": link.relevance_note,
        "status": link.status,
        "first_seen_at": link.first_seen_at,
        "last_seen_at": link.last_seen_at,
        "possible_duplicate_entity_ids": [
            item for item in possible_duplicates if item != entity.id
        ],
        "reused_by_domain": reused_by_domain,
    }


def list_workspace_entities(
    session: Session,
    organization_id: UUID | str,
    workspace_id: UUID,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """Load workspace entities and aliases in two bounded queries (no per-row N+1)."""
    _workspace_or_404(session, organization_id, workspace_id)
    rows = session.execute(
        select(MarketWorkspaceEntity, MarketEntity)
        .join(
            MarketEntity,
            (MarketEntity.organization_id == MarketWorkspaceEntity.organization_id)
            & (MarketEntity.id == MarketWorkspaceEntity.entity_id),
        )
        .where(
            MarketWorkspaceEntity.organization_id == str(organization_id),
            MarketWorkspaceEntity.workspace_id == workspace_id,
        )
        .order_by(MarketWorkspaceEntity.first_seen_at.desc(), MarketWorkspaceEntity.id)
        .limit(limit)
        .offset(offset)
    ).all()

    entity_ids = [entity.id for _, entity in rows]
    alias_map: dict[UUID, list[dict]] = {entity_id: [] for entity_id in entity_ids}
    if entity_ids:
        aliases = session.scalars(
            select(MarketEntityAlias)
            .where(
                MarketEntityAlias.organization_id == str(organization_id),
                MarketEntityAlias.entity_id.in_(entity_ids),
            )
            .order_by(
                MarketEntityAlias.entity_id,
                MarketEntityAlias.alias_type,
                MarketEntityAlias.raw_value,
            )
        ).all()
        for alias in aliases:
            alias_map[alias.entity_id].append(
                {
                    "id": alias.id,
                    "alias_type": alias.alias_type,
                    "raw_value": alias.raw_value,
                    "normalized_value": alias.normalized_value,
                    "source_evidence_id": alias.source_evidence_id,
                    "created_at": alias.created_at,
                }
            )

    result: list[dict] = []
    for link, entity in rows:
        entity_payload = {
            "id": entity.id,
            "entity_kind": entity.entity_kind,
            "display_name": entity.display_name,
            "normalized_name": entity.normalized_name,
            "description": entity.description,
            "canonical_domain": entity.canonical_domain,
            "country_code": entity.country_code,
            "attributes": entity.attributes,
            "status": entity.status,
            "created_at": entity.created_at,
            "updated_at": entity.updated_at,
            "aliases": alias_map[entity.id],
        }
        result.append(
            {
                "entity": entity_payload,
                "discovery_reason": link.discovery_reason,
                "relevance_note": link.relevance_note,
                "status": link.status,
                "first_seen_at": link.first_seen_at,
                "last_seen_at": link.last_seen_at,
                "possible_duplicate_entity_ids": [],
                "reused_by_domain": False,
            }
        )
    return result


def _record_entity_customer_activity(
    session: Session,
    organization_id: UUID | str,
    entity_id: UUID,
    *,
    actor_user_id: UUID,
    event_type: str,
    payload: dict,
) -> None:
    workspace_ids = session.scalars(
        select(MarketWorkspaceEntity.workspace_id).where(
            MarketWorkspaceEntity.organization_id == str(organization_id),
            MarketWorkspaceEntity.entity_id == entity_id,
        )
    ).all()
    for workspace_id in workspace_ids:
        _record_workspace_entity_event(
            session,
            organization_id,
            workspace_id,
            entity_id=entity_id,
            actor_user_id=actor_user_id,
            event_type=event_type,
            payload=payload,
        )


def create_customer_link_candidate(
    session: Session,
    organization_id: UUID | str,
    entity_id: UUID,
    *,
    customer_id: UUID,
    matching_basis: dict,
    actor_user_id: UUID,
) -> MarketEntityCustomerLink:
    entity = get_market_entity(session, organization_id, entity_id)
    if entity is None:
        raise NotFound("市场候选实体不存在", details={"entity_id": str(entity_id)})
    customer = session.scalars(
        select(Customer).where(
            Customer.organization_id == str(organization_id),
            Customer.id == customer_id,
        )
    ).one_or_none()
    if customer is None:
        raise NotFound("客户不存在", details={"customer_id": str(customer_id)})

    existing = session.scalars(
        select(MarketEntityCustomerLink).where(
            MarketEntityCustomerLink.organization_id == str(organization_id),
            MarketEntityCustomerLink.entity_id == entity_id,
            MarketEntityCustomerLink.customer_id == customer_id,
        )
    ).one_or_none()
    if existing is not None:
        if existing.link_status in {"MATCH_CANDIDATE", "USER_CONFIRMED"}:
            return existing
        raise Conflict(
            "该实体与客户已有拒绝或冲突记录，不能自动重新建立匹配",
            details={"link_id": str(existing.id), "link_status": existing.link_status},
        )

    link = MarketEntityCustomerLink(
        organization_id=str(organization_id),
        entity_id=entity_id,
        customer_id=customer_id,
        link_status="MATCH_CANDIDATE",
        matching_basis=matching_basis,
    )
    session.add(link)
    session.flush()
    _record_entity_customer_activity(
        session,
        organization_id,
        entity_id,
        actor_user_id=actor_user_id,
        event_type="CUSTOMER_MATCH_CANDIDATE_CREATED",
        payload={"customer_id": str(customer_id), "link_id": str(link.id)},
    )
    session.flush()
    return link


def confirm_customer_link(
    session: Session,
    organization_id: UUID | str,
    entity_id: UUID,
    link_id: UUID,
    *,
    actor_user_id: UUID,
) -> MarketEntityCustomerLink:
    link = session.scalars(
        select(MarketEntityCustomerLink)
        .where(
            MarketEntityCustomerLink.organization_id == str(organization_id),
            MarketEntityCustomerLink.entity_id == entity_id,
            MarketEntityCustomerLink.id == link_id,
        )
        .with_for_update()
    ).one_or_none()
    if link is None:
        raise NotFound("客户匹配记录不存在", details={"link_id": str(link_id)})
    if link.link_status == "USER_CONFIRMED":
        return link
    if link.link_status != "MATCH_CANDIDATE":
        raise Conflict(
            "只有待确认的客户匹配建议可以确认",
            details={"link_id": str(link.id), "link_status": link.link_status},
        )

    link.link_status = "USER_CONFIRMED"
    link.confirmed_by_user_id = actor_user_id
    link.confirmed_at = datetime.now(UTC)
    session.flush()
    _record_entity_customer_activity(
        session,
        organization_id,
        entity_id,
        actor_user_id=actor_user_id,
        event_type="CUSTOMER_MATCH_USER_CONFIRMED",
        payload={"customer_id": str(link.customer_id), "link_id": str(link.id)},
    )
    session.flush()
    return link


__all__ = [
    "confirm_customer_link",
    "create_customer_link_candidate",
    "create_workspace_entity",
    "get_market_entity",
    "list_workspace_entities",
    "normalize_domain",
    "normalize_name",
]
