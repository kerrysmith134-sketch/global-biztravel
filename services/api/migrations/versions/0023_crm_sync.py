"""0023_crm_sync — 访后记录 CRM 同步任务。

真实 CRM 是外部系统；本表只保存"同步任务及其结果"。默认 file adapter 写本地待同步载荷并标
``PREPARED``，不伪造 ``SUCCEEDED``。

Revision ID: 0023_crm_sync
Revises: 0022_expense_reports
Create Date: 2026-10-28
"""

from __future__ import annotations

from alembic import op

from migrations import helpers

revision = "0023_crm_sync"
down_revision = "0022_expense_reports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE crm_sync_jobs (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            trip_id         uuid NOT NULL,
            visit_id        uuid NOT NULL,
            visit_record_id uuid NOT NULL,
            status          text NOT NULL DEFAULT 'QUEUED',
            job_id          uuid,
            payload         jsonb NOT NULL DEFAULT '{}',
            result          jsonb,
            error           text,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_crm_sync_jobs_org_record UNIQUE (organization_id, visit_record_id),
            CONSTRAINT ck_crm_sync_jobs_status_allowed
                CHECK (status IN ('QUEUED','PREPARED','SUCCEEDED','FAILED')),
            CONSTRAINT fk_crm_sync_jobs_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_crm_sync_jobs_trip_id_trips
                FOREIGN KEY (trip_id) REFERENCES trips (id) ON DELETE CASCADE,
            CONSTRAINT fk_crm_sync_jobs_visit_id_visits
                FOREIGN KEY (visit_id) REFERENCES visits (id) ON DELETE CASCADE,
            CONSTRAINT fk_crm_sync_jobs_visit_record_id_visit_records
                FOREIGN KEY (visit_record_id) REFERENCES visit_records (id) ON DELETE CASCADE,
            CONSTRAINT fk_crm_sync_jobs_job_id_background_jobs
                FOREIGN KEY (job_id) REFERENCES background_jobs (id) ON DELETE SET NULL
        )
        """
    )
    op.execute("CREATE INDEX ix_crm_sync_jobs_organization_id ON crm_sync_jobs (organization_id)")
    op.execute("CREATE INDEX ix_crm_sync_jobs_trip_id ON crm_sync_jobs (trip_id)")
    op.execute("CREATE INDEX ix_crm_sync_jobs_visit_id ON crm_sync_jobs (visit_id)")
    op.execute(
        "CREATE INDEX ix_crm_sync_jobs_visit_record_id ON crm_sync_jobs (visit_record_id)"
    )
    op.execute(
        "CREATE INDEX ix_crm_sync_jobs_org_status ON crm_sync_jobs (organization_id, status)"
    )
    helpers.ensure_updated_at_trigger("crm_sync_jobs")
    helpers.enable_org_rls("crm_sync_jobs", force=True)


def downgrade() -> None:
    helpers.disable_org_rls("crm_sync_jobs")
    op.execute("DROP TABLE IF EXISTS crm_sync_jobs")
