"""0007_agent_memory — Agent 记忆与复盘（事实 / 过程 / 情景）。

设计文档 v2.0 第 9 章：

1. ``traveler_profiles``（事实记忆）：版本化偏好，旧版以 ``valid_until`` 失活而不是被覆盖。
2. ``plan_attempt_log``（过程记忆）：规划尝试与否决记录，供下次同场景复用。
3. ``trip_memory_summaries``（情景记忆）：行程摘要 + ``pgvector`` 向量列；向量与业务行同库
   同事务，避免跨系统写非事务导致的脏记忆。

``CREATE EXTENSION vector`` 由 ``infra/docker/initdb/01-bootstrap.sql`` 在初始化时完成，
与 PostGIS 同属实例级前置条件；迁移只使用、不创建（干净库必须从 initdb 起）。

Revision ID: 0007_agent_memory
Revises: 0006_visit_records
Create Date: 2026-10-28
"""

from __future__ import annotations

from alembic import op

from migrations import helpers

revision = "0007_agent_memory"
down_revision = "0006_visit_records"
branch_labels = None
depends_on = None

TABLES = ("traveler_profiles", "plan_attempt_log", "trip_memory_summaries")


def upgrade() -> None:
    # ------------------------------------------------------- traveler_profiles
    op.execute(
        """
        CREATE TABLE traveler_profiles (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            user_id         uuid NOT NULL,
            version         integer NOT NULL DEFAULT 1,
            preferences     jsonb NOT NULL DEFAULT '{}',
            notes           text,
            confidence      double precision NOT NULL DEFAULT 1,
            valid_from      timestamptz NOT NULL DEFAULT now(),
            valid_until     timestamptz,
            superseded_by   uuid,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_traveler_profiles_org_user_version
                UNIQUE (organization_id, user_id, version),
            CONSTRAINT ck_traveler_profiles_version_positive CHECK (version >= 1),
            CONSTRAINT ck_traveler_profiles_confidence_range
                CHECK (confidence >= 0 AND confidence <= 1),
            CONSTRAINT fk_traveler_profiles_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_traveler_profiles_user_id_users
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_traveler_profiles_organization_id ON traveler_profiles (organization_id)"
    )
    op.execute("CREATE INDEX ix_traveler_profiles_user_id ON traveler_profiles (user_id)")
    op.execute(
        "CREATE INDEX ix_traveler_profiles_org_user_valid "
        "ON traveler_profiles (organization_id, user_id, valid_until)"
    )

    # --------------------------------------------------------- plan_attempt_log
    op.execute(
        """
        CREATE TABLE plan_attempt_log (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            trip_id         uuid NOT NULL,
            plan_version_id uuid,
            attempt_type    text NOT NULL,
            outcome         text NOT NULL,
            reason          text,
            details         jsonb NOT NULL DEFAULT '{}',
            created_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_plan_attempt_log_attempt_type_allowed
                CHECK (attempt_type IN ('PLAN_GENERATED','PLAN_REJECTED',
                                        'PLAN_ACTIVATED','PLAN_SUPERSEDED')),
            CONSTRAINT fk_plan_attempt_log_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_plan_attempt_log_trip_id_trips
                FOREIGN KEY (trip_id) REFERENCES trips (id) ON DELETE CASCADE,
            CONSTRAINT fk_plan_attempt_log_plan_version_id_plan_versions
                FOREIGN KEY (plan_version_id) REFERENCES plan_versions (id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_plan_attempt_log_organization_id ON plan_attempt_log (organization_id)"
    )
    op.execute("CREATE INDEX ix_plan_attempt_log_trip_id ON plan_attempt_log (trip_id)")
    op.execute(
        "CREATE INDEX ix_plan_attempt_log_org_trip_created_at "
        "ON plan_attempt_log (organization_id, trip_id, created_at)"
    )

    # ---------------------------------------------------- trip_memory_summaries
    op.execute(
        """
        CREATE TABLE trip_memory_summaries (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            trip_id         uuid,
            customer_id     uuid,
            kind            text NOT NULL,
            summary         text NOT NULL,
            decisions       jsonb NOT NULL DEFAULT '[]',
            follow_ups      jsonb NOT NULL DEFAULT '[]',
            lessons         jsonb NOT NULL DEFAULT '[]',
            embedding       vector(256),
            confidence      double precision NOT NULL DEFAULT 0.8,
            valid_from      timestamptz NOT NULL DEFAULT now(),
            valid_until     timestamptz,
            superseded_by   uuid,
            source_ref      text,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_trip_memory_summaries_kind_allowed
                CHECK (kind IN ('TRIP_REVIEW','CUSTOMER_NOTE','PREFERENCE')),
            CONSTRAINT ck_trip_memory_summaries_confidence_range
                CHECK (confidence >= 0 AND confidence <= 1),
            CONSTRAINT fk_trip_memory_summaries_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_trip_memory_summaries_trip_id_trips
                FOREIGN KEY (trip_id) REFERENCES trips (id) ON DELETE CASCADE,
            CONSTRAINT fk_trip_memory_summaries_customer_id_customers
                FOREIGN KEY (customer_id) REFERENCES customers (id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_trip_memory_summaries_organization_id "
        "ON trip_memory_summaries (organization_id)"
    )
    op.execute("CREATE INDEX ix_trip_memory_summaries_trip_id ON trip_memory_summaries (trip_id)")
    op.execute(
        "CREATE INDEX ix_trip_memory_summaries_org_trip "
        "ON trip_memory_summaries (organization_id, trip_id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_trip_memory_summaries_org_source_ref "
        "ON trip_memory_summaries (organization_id, source_ref) "
        "WHERE source_ref IS NOT NULL"
    )

    helpers.ensure_updated_at_trigger("traveler_profiles", "trip_memory_summaries")

    for table in TABLES:
        helpers.enable_org_rls(table, force=True)


def downgrade() -> None:
    for table in TABLES:
        helpers.disable_org_rls(table)

    op.execute("DROP TABLE IF EXISTS trip_memory_summaries")
    op.execute("DROP TABLE IF EXISTS plan_attempt_log")
    op.execute("DROP TABLE IF EXISTS traveler_profiles")
