"""Organization-level customer map query.

Returns one joined projection for the map rather than issuing one customer-place request
per customer. All reads are tenant-scoped and the projection intentionally excludes PII.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import and_, case, func, or_, select
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
        predicates.append(func.coalesce(Place.country_code, Customer.country_code) == country_code.upper())
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

    # Prefer the actual linked-place country; fall back to the customer's declared country.
    # A customer with multiple sites in one country is counted once in that country's customer total.
    country_key = func.coalesce(Place.country_code, Customer.country_code, "UNKNOWN")
    country_rows = session.execute(
        select(country_key.label("country_code"), func.count(CustomerPlace.id))
        .select_from(CustomerPlace)
        .join(Customer, Customer.id == CustomerPlace.customer_id)
        .join(Place, Place.id == CustomerPlace.place_id)
        .where(*predicates)
        .group_by(country_key)
    ).all()
    countries = {(country or "UNKNOWN"): int(count) for country, count in country_rows}

    coverage_rows = session.execute(
        select(
            country_key.label("country_code"),
            func.count(func.distinct(Customer.id)).label("customers_total"),
            func.count(CustomerPlace.id).label("locations_total"),
            func.count(case((and_(Place.lat.is_not(None), Place.lon.is_not(None)), CustomerPlace.id)))
                .label("locations_mapped"),
            func.count(func.distinct(case((Customer.tier == "A", Customer.id)))).label("tier_a_customers"),
            func.count(func.distinct(case((Customer.tier == "B", Customer.id)))).label("tier_b_customers"),
            func.count(func.distinct(case((Customer.tier == "C", Customer.id)))).label("tier_c_customers"),
            func.count(func.distinct(case((Customer.status == "ACTIVE", Customer.id)))).label("active_customers"),
            func.count(func.distinct(case((Customer.status == "PROSPECT", Customer.id)))).label("prospect_customers"),
            func.count(func.distinct(case((Customer.status == "INACTIVE", Customer.id)))).label("inactive_customers"),
        )
        .select_from(CustomerPlace)
        .join(Customer, Customer.id == CustomerPlace.customer_id)
        .join(Place, Place.id == CustomerPlace.place_id)
        .where(*predicates)
        .group_by(country_key)
        .order_by(country_key.asc())
    ).all()
    country_stats = [
        {
            "country_code": row.country_code,
            "customers_total": int(row.customers_total or 0),
            "tier_a_customers": int(row.tier_a_customers or 0),
            "tier_b_customers": int(row.tier_b_customers or 0),
            "tier_c_customers": int(row.tier_c_customers or 0),
            "active_customers": int(row.active_customers or 0),
            "prospect_customers": int(row.prospect_customers or 0),
            "inactive_customers": int(row.inactive_customers or 0),
            "locations_total": int(row.locations_total or 0),
            "locations_mapped": int(row.locations_mapped or 0),
        }
        for row in coverage_rows
    ]

    return {
        "rows": rows,
        "summary": {
            "customers_with_locations": int(customers_with_locations),
            "customers_without_locations": int(customers_without_locations),
            "locations_total": int(locations_total),
            "locations_mapped": int(locations_mapped),
            "locations_missing_coordinates": int(locations_total - locations_mapped),
            "countries": countries,
            "country_stats": country_stats,
        },
    }
