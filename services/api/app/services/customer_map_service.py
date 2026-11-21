"""Organization-level customer map query.

Returns one joined projection for the map rather than issuing one customer-place request
per customer. All reads are tenant-scoped and the projection intentionally excludes PII.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.geo import Customer, CustomerPlace, Place


def _filters(
    organization_id: UUID | str,
    *,
    query: str | None,
    tier: str | None,
    country_code: str | None,
    geocode_status: str | None,
):
    predicates = [
        Customer.organization_id == organization_id,
        CustomerPlace.organization_id == organization_id,
        Place.organization_id == organization_id,
    ]
    if tier:
        predicates.append(Customer.tier == tier.upper())
    if country_code:
        predicates.append(Place.country_code == country_code.upper())
    if geocode_status:
        predicates.append(Place.geocode_status == geocode_status.upper())
    if query and query.strip():
        pattern = f"%{query.strip()}%"
        predicates.append(
            or_(
                Customer.name.ilike(pattern),
                func.coalesce(Customer.external_ref, "").ilike(pattern),
                Place.name.ilike(pattern),
                func.coalesce(Place.city, "").ilike(pattern),
                func.coalesce(Place.address_line, "").ilike(pattern),
            )
        )
    return predicates


def list_customer_map_locations(
    session: Session,
    organization_id: UUID | str,
    *,
    query: str | None = None,
    tier: str | None = None,
    country_code: str | None = None,
    geocode_status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    predicates = _filters(
        organization_id,
        query=query,
        tier=tier,
        country_code=country_code,
        geocode_status=geocode_status,
    )
    base = (
        select(Customer, CustomerPlace, Place)
        .join(CustomerPlace, CustomerPlace.customer_id == Customer.id)
        .join(Place, Place.id == CustomerPlace.place_id)
        .where(*predicates)
    )

    rows = session.execute(
        base.order_by(
            Customer.country_code.asc().nulls_last(),
            Customer.name.asc(),
            CustomerPlace.is_primary.desc(),
            Place.name.asc(),
            Place.id.asc(),
        )
        .limit(limit)
        .offset(offset)
    ).all()

    locations_total = session.scalar(
        select(func.count(CustomerPlace.id))
        .select_from(CustomerPlace)
        .join(Customer, Customer.id == CustomerPlace.customer_id)
        .join(Place, Place.id == CustomerPlace.place_id)
        .where(*predicates)
    ) or 0
    locations_mapped = session.scalar(
        select(func.count(CustomerPlace.id))
        .select_from(CustomerPlace)
        .join(Customer, Customer.id == CustomerPlace.customer_id)
        .join(Place, Place.id == CustomerPlace.place_id)
        .where(
            *predicates,
            Place.lat.is_not(None),
            Place.lon.is_not(None),
        )
    ) or 0
    customers_with_locations = session.scalar(
        select(func.count(func.distinct(Customer.id)))
        .select_from(Customer)
        .join(CustomerPlace, CustomerPlace.customer_id == Customer.id)
        .join(Place, Place.id == CustomerPlace.place_id)
        .where(*predicates)
    ) or 0

    # Unlinked customer count is independent of map filters: it is a data-quality signal
    # for the whole organization and must not disappear when a map filter is selected.
    linked_customer_ids = select(CustomerPlace.customer_id).where(
        CustomerPlace.organization_id == organization_id
    )
    customers_without_locations = session.scalar(
        select(func.count(Customer.id)).where(
            Customer.organization_id == organization_id,
            Customer.id.not_in(linked_customer_ids),
        )
    ) or 0

    country_rows = session.execute(
        select(Place.country_code, func.count(CustomerPlace.id))
        .select_from(CustomerPlace)
        .join(Customer, Customer.id == CustomerPlace.customer_id)
        .join(Place, Place.id == CustomerPlace.place_id)
        .where(*predicates)
        .group_by(Place.country_code)
    ).all()
    countries = {
        (country or "UNKNOWN"): int(count)
        for country, count in country_rows
    }

    return {
        "rows": rows,
        "summary": {
            "customers_with_locations": int(customers_with_locations),
            "customers_without_locations": int(customers_without_locations),
            "locations_total": int(locations_total),
            "locations_mapped": int(locations_mapped),
            "locations_missing_coordinates": int(locations_total - locations_mapped),
            "countries": countries,
        },
    }
