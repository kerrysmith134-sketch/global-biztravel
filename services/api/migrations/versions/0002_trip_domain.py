"""0002_trip_domain — 地点 / 客户 / 出行 / 拜访 / 导入批次。

设计文档 v2.0 第 12.2 章迁移批次第 2 项。

本批同时落地"建表即授权"的租户隔离（见 ``helpers.enable_org_rls`` 与
``docs/decisions/ADR-0002-rls-per-migration.md``）：

- **业务表**（places/customers/customer_places/trips/visits/import_batches/import_rows）
  启用 ``ENABLE`` + ``FORCE ROW LEVEL SECURITY``，连表 owner 也受约束。
- 身份表（organizations/users/memberships）的隔离由 ``0001_core_identity`` 建表时启用
  （``ENABLE`` 但不 ``FORCE``）；本批不再重复声明，避免同一张表的策略有两个归属。

空间能力：``places.geog`` 是 ``geography(Point,4326)`` 派生列，由触发器
``trg_places_geog`` 从 ``lat``/``lon`` 维护，配 GIST 索引 ``ix_places_geog`` 供
"附近客户/路线距离"类查询使用。

Revision ID: 0002_trip_domain
Revises: 0001_core_identity
Create Date: 2026-10-28
"""

from __future__ import annotations

from alembic import op

from migrations import helpers

revision = "0002_trip_domain"
down_revision = "0001_core_identity"
branch_labels = None
depends_on = None

BUSINESS_TABLES = (
    "places",
    "customers",
    "customer_places",
    "trips",
    "visits",
    "import_batches",
    "import_rows",
)


def upgrade() -> None:
    # ---------------------------------------------------------------- places
    op.execute(
        """
        CREATE TABLE places (
            id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id    uuid NOT NULL,
            name               text NOT NULL,
            address_line       text,
            city               text,
            region             text,
            country_code       char(2),
            timezone           text,
            lat                double precision,
            lon                double precision,
            geog               geography(Point, 4326),
            geocode_status     text NOT NULL DEFAULT 'MISSING',
            geocode_source     text,
            geocode_confidence numeric(4, 3),
            created_at         timestamptz NOT NULL DEFAULT now(),
            updated_at         timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_places_lat_range CHECK (lat IS NULL OR (lat >= -90 AND lat <= 90)),
            CONSTRAINT ck_places_lon_range CHECK (lon IS NULL OR (lon >= -180 AND lon <= 180)),
            CONSTRAINT ck_places_geocode_status_allowed
                CHECK (geocode_status IN ('MISSING', 'RESOLVED', 'FAILED')),
            CONSTRAINT fk_places_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX ix_places_organization_id ON places (organization_id)")
    op.execute(
        "CREATE INDEX ix_places_organization_id_country_code ON places (organization_id, country_code)"
    )
    op.execute("CREATE INDEX ix_places_geog ON places USING GIST (geog)")

    # ------------------------------------------------------------- customers
    op.execute(
        """
        CREATE TABLE customers (
            id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id       uuid NOT NULL,
            name                  text NOT NULL,
            external_ref          text,
            tier                  text NOT NULL DEFAULT 'C',
            country_code          char(2),
            industry              text,
            primary_contact_name  text,
            primary_contact_email text,
            primary_contact_phone text,
            status                text NOT NULL DEFAULT 'ACTIVE',
            notes                 text,
            owner_user_id         uuid,
            created_at            timestamptz NOT NULL DEFAULT now(),
            updated_at            timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_customers_tier_allowed CHECK (tier IN ('A', 'B', 'C')),
            CONSTRAINT ck_customers_status_allowed
                CHECK (status IN ('ACTIVE', 'INACTIVE', 'PROSPECT')),
            CONSTRAINT fk_customers_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_customers_owner_user_id_users
                FOREIGN KEY (owner_user_id) REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute("CREATE INDEX ix_customers_organization_id ON customers (organization_id)")
    op.execute("CREATE INDEX ix_customers_owner_user_id ON customers (owner_user_id)")
    op.execute("CREATE INDEX ix_customers_organization_id_name ON customers (organization_id, name)")
    # 外部系统编号在**组织内**唯一；为空的行不参与唯一性（避免"未编号客户"互相冲突）。
    op.execute(
        "CREATE UNIQUE INDEX uq_customers_organization_id_external_ref "
        "ON customers (organization_id, external_ref) WHERE external_ref IS NOT NULL"
    )

    # -------------------------------------------------------- customer_places
    op.execute(
        """
        CREATE TABLE customer_places (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            customer_id     uuid NOT NULL,
            place_id        uuid NOT NULL,
            label           text,
            is_primary      boolean NOT NULL DEFAULT false,
            created_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_customer_places_customer_id_place_id UNIQUE (customer_id, place_id),
            CONSTRAINT fk_customer_places_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_customer_places_customer_id_customers
                FOREIGN KEY (customer_id) REFERENCES customers (id) ON DELETE CASCADE,
            CONSTRAINT fk_customer_places_place_id_places
                FOREIGN KEY (place_id) REFERENCES places (id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX ix_customer_places_organization_id ON customer_places (organization_id)")
    op.execute("CREATE INDEX ix_customer_places_customer_id ON customer_places (customer_id)")
    op.execute("CREATE INDEX ix_customer_places_place_id ON customer_places (place_id)")

    # ----------------------------------------------------------------- trips
    op.execute(
        """
        CREATE TABLE trips (
            id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id        uuid NOT NULL,
            name                   text NOT NULL,
            purpose                text,
            owner_user_id          uuid,
            origin_place_id        uuid,
            home_timezone          text NOT NULL DEFAULT 'Asia/Shanghai',
            currency               text NOT NULL DEFAULT 'CNY',
            window_start           timestamptz NOT NULL,
            window_end             timestamptz NOT NULL,
            countries              text[] NOT NULL DEFAULT '{}',
            status                 text NOT NULL DEFAULT 'DRAFT',
            budget_amount          numeric(14, 2),
            version                integer NOT NULL DEFAULT 1,
            -- 指向 0003_planning 的 plan_versions；本批只建列，外键在 0003 补齐。
            active_plan_version_id uuid,
            created_at             timestamptz NOT NULL DEFAULT now(),
            updated_at             timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_trips_window_order CHECK (window_end > window_start),
            CONSTRAINT ck_trips_status_allowed
                CHECK (status IN ('DRAFT','PLANNING','READY','ACTIVE','COMPLETED','CANCELLED')),
            CONSTRAINT ck_trips_version_positive CHECK (version >= 1),
            CONSTRAINT fk_trips_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_trips_owner_user_id_users
                FOREIGN KEY (owner_user_id) REFERENCES users (id) ON DELETE SET NULL,
            CONSTRAINT fk_trips_origin_place_id_places
                FOREIGN KEY (origin_place_id) REFERENCES places (id) ON DELETE SET NULL
        )
        """
    )
    op.execute("CREATE INDEX ix_trips_organization_id ON trips (organization_id)")
    op.execute("CREATE INDEX ix_trips_owner_user_id ON trips (owner_user_id)")
    op.execute("CREATE INDEX ix_trips_organization_id_status ON trips (organization_id, status)")

    # ---------------------------------------------------------------- visits
    op.execute(
        """
        CREATE TABLE visits (
            id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id   uuid NOT NULL,
            trip_id           uuid NOT NULL,
            customer_id       uuid,
            place_id          uuid NOT NULL,
            title             text,
            service_minutes   integer NOT NULL DEFAULT 60,
            time_window_start timestamptz,
            time_window_end   timestamptz,
            priority          smallint NOT NULL DEFAULT 2,
            business_value    numeric(14, 2) NOT NULL DEFAULT 0,
            movement_mode     text NOT NULL DEFAULT 'GROUND',
            is_immovable      boolean NOT NULL DEFAULT false,
            status            text NOT NULL DEFAULT 'PLANNED',
            client_updated_at timestamptz,
            created_at        timestamptz NOT NULL DEFAULT now(),
            updated_at        timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_visits_service_minutes_positive CHECK (service_minutes > 0),
            CONSTRAINT ck_visits_priority_range CHECK (priority BETWEEN 1 AND 3),
            CONSTRAINT ck_visits_business_value_non_negative CHECK (business_value >= 0),
            CONSTRAINT ck_visits_movement_mode_allowed
                CHECK (movement_mode IN ('GROUND','RAIL','AIR','SEA')),
            CONSTRAINT ck_visits_status_allowed
                CHECK (status IN ('PLANNED','SCHEDULED','DONE','MISSED','CANCELLED')),
            CONSTRAINT ck_visits_time_window_order
                CHECK (time_window_start IS NULL OR time_window_end IS NULL
                       OR time_window_end > time_window_start),
            CONSTRAINT fk_visits_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_visits_trip_id_trips
                FOREIGN KEY (trip_id) REFERENCES trips (id) ON DELETE CASCADE,
            CONSTRAINT fk_visits_customer_id_customers
                FOREIGN KEY (customer_id) REFERENCES customers (id) ON DELETE SET NULL,
            -- RESTRICT：地点是拜访的物理前提，删掉地点会让行程失去意义，必须显式处理。
            CONSTRAINT fk_visits_place_id_places
                FOREIGN KEY (place_id) REFERENCES places (id) ON DELETE RESTRICT
        )
        """
    )
    op.execute("CREATE INDEX ix_visits_organization_id ON visits (organization_id)")
    op.execute("CREATE INDEX ix_visits_trip_id ON visits (trip_id)")
    op.execute("CREATE INDEX ix_visits_place_id ON visits (place_id)")
    op.execute("CREATE INDEX ix_visits_organization_id_status ON visits (organization_id, status)")

    # -------------------------------------------------------- import_batches
    op.execute(
        """
        CREATE TABLE import_batches (
            id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id    uuid NOT NULL,
            trip_id            uuid NOT NULL,
            filename           text NOT NULL,
            file_sha256        text NOT NULL,
            status             text NOT NULL DEFAULT 'PARSED',
            total_rows         integer NOT NULL DEFAULT 0,
            valid_rows         integer NOT NULL DEFAULT 0,
            invalid_rows       integer NOT NULL DEFAULT 0,
            duplicate_rows     integer NOT NULL DEFAULT 0,
            report             jsonb NOT NULL DEFAULT '{}',
            created_by_user_id uuid,
            parsed_at          timestamptz NOT NULL DEFAULT now(),
            confirmed_at       timestamptz,
            created_at         timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_import_batches_status_allowed
                CHECK (status IN ('PARSED','CONFIRMED','DISCARDED','FAILED')),
            CONSTRAINT fk_import_batches_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_import_batches_trip_id_trips
                FOREIGN KEY (trip_id) REFERENCES trips (id) ON DELETE CASCADE,
            CONSTRAINT fk_import_batches_created_by_user_id_users
                FOREIGN KEY (created_by_user_id) REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute("CREATE INDEX ix_import_batches_organization_id ON import_batches (organization_id)")
    op.execute("CREATE INDEX ix_import_batches_trip_id ON import_batches (trip_id)")
    op.execute(
        "CREATE INDEX ix_import_batches_trip_id_created_at ON import_batches (trip_id, created_at)"
    )

    # ----------------------------------------------------------- import_rows
    op.execute(
        """
        CREATE TABLE import_rows (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            batch_id        uuid NOT NULL,
            row_number      integer NOT NULL,
            raw             jsonb NOT NULL,
            normalized      jsonb,
            status          text NOT NULL DEFAULT 'VALID',
            issues          jsonb NOT NULL DEFAULT '[]',
            customer_id     uuid,
            place_id        uuid,
            visit_id        uuid,
            CONSTRAINT uq_import_rows_batch_id_row_number UNIQUE (batch_id, row_number),
            CONSTRAINT ck_import_rows_status_allowed
                CHECK (status IN ('VALID','INVALID','DUPLICATE','ACCEPTED','REJECTED','SKIPPED')),
            CONSTRAINT fk_import_rows_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_import_rows_batch_id_import_batches
                FOREIGN KEY (batch_id) REFERENCES import_batches (id) ON DELETE CASCADE,
            CONSTRAINT fk_import_rows_customer_id_customers
                FOREIGN KEY (customer_id) REFERENCES customers (id) ON DELETE SET NULL,
            CONSTRAINT fk_import_rows_place_id_places
                FOREIGN KEY (place_id) REFERENCES places (id) ON DELETE SET NULL,
            CONSTRAINT fk_import_rows_visit_id_visits
                FOREIGN KEY (visit_id) REFERENCES visits (id) ON DELETE SET NULL
        )
        """
    )
    op.execute("CREATE INDEX ix_import_rows_organization_id ON import_rows (organization_id)")
    op.execute("CREATE INDEX ix_import_rows_batch_id ON import_rows (batch_id)")
    op.execute("CREATE INDEX ix_import_rows_batch_id_row_number ON import_rows (batch_id, row_number)")

    # -------------------------------------------------------------- 派生列与触发器
    helpers.ensure_places_geog_trigger()
    helpers.ensure_updated_at_trigger("places", "customers", "trips", "visits")

    # ------------------------------------------------------------------- RLS
    # 只处理本批新建的业务表；身份三表的隔离归 0001 管。
    for table in BUSINESS_TABLES:
        helpers.enable_org_rls(table, force=True)


def downgrade() -> None:
    # 先摘除本批表的策略，再按依赖倒序删表。
    for table in BUSINESS_TABLES:
        helpers.disable_org_rls(table)

    op.execute("DROP TABLE IF EXISTS import_rows")
    op.execute("DROP TABLE IF EXISTS import_batches")
    op.execute("DROP TABLE IF EXISTS visits")
    op.execute("DROP TABLE IF EXISTS trips")
    op.execute("DROP TABLE IF EXISTS customer_places")
    op.execute("DROP TABLE IF EXISTS customers")
    op.execute("DROP TABLE IF EXISTS places")

    # 删除表时其 BEFORE 触发器一并消失，函数此刻才无依赖者。
    op.execute("DROP FUNCTION IF EXISTS sync_place_geog()")
    # ``set_updated_at()`` 由 0001 创建，而 organizations/users 本批不删，若在这里
    # 删除会撞上"其它表仍依赖该函数"而令 downgrade 失败；交给 0001 的 downgrade 收拾。
