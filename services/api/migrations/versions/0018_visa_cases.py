"""0018_visa_cases — 签证案件与入境评估。

PRD 方向二：签证不是一次性问答，而是"评估要求 → 材料清单 → 申请进度 → 出发前检查"。
本迁移落两张表，均由 RLS 强制组织隔离；不存护照号，只允许护照有效期用于时效检查。

Revision ID: 0018_visa_cases
Revises: 0017_trip_hold_job_cancel
Create Date: 2026-10-28
"""

from __future__ import annotations

from alembic import op

from migrations import helpers

revision = "0018_visa_cases"
down_revision = "0017_trip_hold_job_cancel"
branch_labels = None
depends_on = None

TABLES = ("visa_cases", "entry_assessments")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE visa_cases (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id     uuid NOT NULL,
            trip_id             uuid NOT NULL,
            country_code        char(2) NOT NULL,
            status              text NOT NULL DEFAULT 'NEW',
            checklist           jsonb NOT NULL DEFAULT '[]',
            notes               text,
            passport_expires_at date,
            confidence          double precision NOT NULL DEFAULT 0.6,
            submitted_at        timestamptz,
            decision_at         timestamptz,
            created_by_user_id  uuid,
            created_at          timestamptz NOT NULL DEFAULT now(),
            updated_at          timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_visa_cases_org_trip_country
                UNIQUE (organization_id, trip_id, country_code),
            CONSTRAINT ck_visa_cases_status_allowed
                CHECK (status IN ('NEW','PREPARING','SUBMITTED','APPROVED','REJECTED','CANCELLED')),
            CONSTRAINT ck_visa_cases_confidence_range CHECK (confidence >= 0 AND confidence <= 1),
            CONSTRAINT fk_visa_cases_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_visa_cases_trip_id_trips
                FOREIGN KEY (trip_id) REFERENCES trips (id) ON DELETE CASCADE,
            CONSTRAINT fk_visa_cases_created_by_user_id_users
                FOREIGN KEY (created_by_user_id) REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute("CREATE INDEX ix_visa_cases_organization_id ON visa_cases (organization_id)")
    op.execute("CREATE INDEX ix_visa_cases_trip_id ON visa_cases (trip_id)")
    op.execute("CREATE INDEX ix_visa_cases_org_status ON visa_cases (organization_id, status)")

    op.execute(
        """
        CREATE TABLE entry_assessments (
            id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id       uuid NOT NULL,
            trip_id               uuid NOT NULL,
            country_code          char(2) NOT NULL,
            summary               text NOT NULL,
            source                text NOT NULL,
            source_version        text NOT NULL,
            confidence            double precision NOT NULL DEFAULT 0.6,
            manual_review_required boolean NOT NULL DEFAULT true,
            assessed_at           timestamptz NOT NULL,
            created_at            timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_entry_assessments_org_trip_country_version
                UNIQUE (organization_id, trip_id, country_code, source_version),
            CONSTRAINT ck_entry_assessments_confidence_range CHECK (confidence >= 0 AND confidence <= 1),
            CONSTRAINT fk_entry_assessments_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_entry_assessments_trip_id_trips
                FOREIGN KEY (trip_id) REFERENCES trips (id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX ix_entry_assessments_organization_id ON entry_assessments (organization_id)")
    op.execute("CREATE INDEX ix_entry_assessments_trip_id ON entry_assessments (trip_id)")
    op.execute(
        "CREATE INDEX ix_entry_assessments_org_trip ON entry_assessments (organization_id, trip_id)"
    )

    helpers.ensure_updated_at_trigger("visa_cases")
    for table in TABLES:
        helpers.enable_org_rls(table, force=True)


def downgrade() -> None:
    for table in TABLES:
        helpers.disable_org_rls(table)
    op.execute("DROP TABLE IF EXISTS entry_assessments")
    op.execute("DROP TABLE IF EXISTS visa_cases")
