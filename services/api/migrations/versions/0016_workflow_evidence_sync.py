"""0016_workflow_evidence_sync — 工作流、AgentRun、Evidence、SyncOperation、集成连接、外部动作。

PRD MVP-1 迁移 0005/0006 与设计文档 §12.1 明确保留的持久化模型。此前这些能力部分被
JSON 字段或临时处理替代；本迁移把它们落成可查询、可审计、受 RLS 约束的表。

Revision ID: 0016_workflow_evidence_sync
Revises: 0015_plan_travel_legs
Create Date: 2026-10-28
"""

from __future__ import annotations

from alembic import op

from migrations import helpers

revision = "0016_workflow_evidence_sync"
down_revision = "0015_plan_travel_legs"
branch_labels = None
depends_on = None

TABLES = (
    "workflow_instances",
    "agent_runs",
    "evidence",
    "sync_operations",
    "integration_connections",
    "external_actions",
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE workflow_instances (
            id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id    uuid NOT NULL,
            trip_id            uuid,
            workflow_type      text NOT NULL,
            status             text NOT NULL DEFAULT 'ACCEPTED',
            payload            jsonb NOT NULL DEFAULT '{}',
            job_id             uuid,
            created_by_user_id uuid,
            created_at         timestamptz NOT NULL DEFAULT now(),
            updated_at         timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_workflow_instances_status_allowed
                CHECK (status IN ('ACCEPTED','RUNNING','COMPLETED','FAILED','REJECTED')),
            CONSTRAINT fk_workflow_instances_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_workflow_instances_trip_id_trips
                FOREIGN KEY (trip_id) REFERENCES trips (id) ON DELETE CASCADE,
            CONSTRAINT fk_workflow_instances_job_id_background_jobs
                FOREIGN KEY (job_id) REFERENCES background_jobs (id) ON DELETE SET NULL,
            CONSTRAINT fk_workflow_instances_created_by_user_id_users
                FOREIGN KEY (created_by_user_id) REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_workflow_instances_organization_id ON workflow_instances (organization_id)"
    )
    op.execute("CREATE INDEX ix_workflow_instances_trip_id ON workflow_instances (trip_id)")

    op.execute(
        """
        CREATE TABLE agent_runs (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            workflow_id     uuid,
            agent_type      text NOT NULL,
            status          text NOT NULL DEFAULT 'RUNNING',
            input           jsonb NOT NULL DEFAULT '{}',
            output          jsonb,
            error           text,
            started_at      timestamptz NOT NULL DEFAULT now(),
            finished_at     timestamptz,
            created_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_agent_runs_status_allowed
                CHECK (status IN ('RUNNING','SUCCEEDED','FAILED')),
            CONSTRAINT fk_agent_runs_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_agent_runs_workflow_id_workflow_instances
                FOREIGN KEY (workflow_id) REFERENCES workflow_instances (id) ON DELETE SET NULL
        )
        """
    )
    op.execute("CREATE INDEX ix_agent_runs_organization_id ON agent_runs (organization_id)")
    op.execute("CREATE INDEX ix_agent_runs_workflow_id ON agent_runs (workflow_id)")

    op.execute(
        """
        CREATE TABLE evidence (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            subject_type    text NOT NULL,
            subject_id      uuid NOT NULL,
            kind            text NOT NULL,
            ref             text NOT NULL,
            source          text,
            payload         jsonb NOT NULL DEFAULT '{}',
            retrieved_at    timestamptz,
            created_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_evidence_org_subject_kind_ref
                UNIQUE (organization_id, subject_type, subject_id, kind, ref),
            CONSTRAINT fk_evidence_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX ix_evidence_organization_id ON evidence (organization_id)")
    op.execute(
        "CREATE INDEX ix_evidence_org_subject ON evidence (organization_id, subject_type, subject_id)"
    )

    op.execute(
        """
        CREATE TABLE sync_operations (
            id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id   uuid NOT NULL,
            trip_id           uuid,
            device_id         text,
            operation_id      text NOT NULL,
            operation_type    text NOT NULL,
            payload           jsonb NOT NULL DEFAULT '{}',
            status            text NOT NULL,
            result            jsonb NOT NULL DEFAULT '{}',
            client_created_at timestamptz,
            created_at        timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_sync_operations_org_operation UNIQUE (organization_id, operation_id),
            CONSTRAINT ck_sync_operations_status_allowed
                CHECK (status IN ('APPLIED','CONFLICT','REJECTED')),
            CONSTRAINT fk_sync_operations_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_sync_operations_trip_id_trips
                FOREIGN KEY (trip_id) REFERENCES trips (id) ON DELETE SET NULL
        )
        """
    )
    op.execute("CREATE INDEX ix_sync_operations_organization_id ON sync_operations (organization_id)")
    op.execute("CREATE INDEX ix_sync_operations_trip_id ON sync_operations (trip_id)")
    op.execute(
        "CREATE INDEX ix_sync_operations_org_trip ON sync_operations (organization_id, trip_id)"
    )

    op.execute(
        """
        CREATE TABLE integration_connections (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            provider        text NOT NULL,
            capability      text NOT NULL,
            status          text NOT NULL DEFAULT 'DISABLED',
            config          jsonb NOT NULL DEFAULT '{}',
            last_synced_at  timestamptz,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_integration_connections_org_provider_capability
                UNIQUE (organization_id, provider, capability),
            CONSTRAINT ck_integration_connections_status_allowed
                CHECK (status IN ('ACTIVE','DISABLED','DEGRADED')),
            CONSTRAINT fk_integration_connections_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_integration_connections_organization_id "
        "ON integration_connections (organization_id)"
    )

    op.execute(
        """
        CREATE TABLE external_actions (
            id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id      uuid NOT NULL,
            subject_type         text NOT NULL,
            subject_id           uuid NOT NULL,
            action_type          text NOT NULL,
            status               text NOT NULL DEFAULT 'PREPARED',
            request              jsonb NOT NULL DEFAULT '{}',
            response             jsonb,
            requires_approval    boolean NOT NULL DEFAULT true,
            approved_by_user_id  uuid,
            executed_at          timestamptz,
            created_at           timestamptz NOT NULL DEFAULT now(),
            updated_at           timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_external_actions_status_allowed
                CHECK (status IN ('PREPARED','APPROVED','EXECUTED','FAILED')),
            CONSTRAINT fk_external_actions_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_external_actions_approved_by_user_id_users
                FOREIGN KEY (approved_by_user_id) REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute("CREATE INDEX ix_external_actions_organization_id ON external_actions (organization_id)")
    op.execute(
        "CREATE INDEX ix_external_actions_org_subject "
        "ON external_actions (organization_id, subject_type, subject_id)"
    )

    helpers.ensure_updated_at_trigger(
        "workflow_instances", "integration_connections", "external_actions"
    )
    for table in TABLES:
        helpers.enable_org_rls(table, force=True)


def downgrade() -> None:
    for table in TABLES:
        helpers.disable_org_rls(table)
    op.execute("DROP TABLE IF EXISTS external_actions")
    op.execute("DROP TABLE IF EXISTS integration_connections")
    op.execute("DROP TABLE IF EXISTS sync_operations")
    op.execute("DROP TABLE IF EXISTS evidence")
    op.execute("DROP TABLE IF EXISTS agent_runs")
    op.execute("DROP TABLE IF EXISTS workflow_instances")
