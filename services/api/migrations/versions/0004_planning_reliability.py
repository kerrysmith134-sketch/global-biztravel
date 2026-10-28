"""0004_planning_reliability — 计划版本 + 后台任务/Outbox。

设计文档 v2.0 第 10 章（可靠性规范）与第 12.2 章迁移批次。

本批一次补齐三件事，因为它们必须**同事务**才能保证"计划已生成"与"任务已成功"不会分叉：

1. ``plan_versions`` / ``plan_stops`` —— 不可变的计划快照（内容寻址的 ``problem_hash``；
   同一 ``trip_id`` 至多一份 ``ACTIVE``，由部分唯一索引强制）。
2. ``background_jobs`` —— 可恢复的异步任务队列（``SKIP LOCKED`` 抢占 + 租约 + 退避 + 终态）。
3. ``outbox_events`` / ``outbox_deliveries`` —— 事务性发件箱：业务写入与事件写入同一次提交，
   消费者按 ``(event_id, consumer)`` 去重，因此重复投递不会产生第二次副作用。

同时补上 ``0002`` 遗留的一处伏笔：``trips.active_plan_version_id`` 的外键指向
``plan_versions.id``（``ON DELETE SET NULL``：计划版本被清理时 Trip 不应连带消失）。

修订顺序说明：设计文档把计划表放在 0008–0013，本仓库因 RLS 拆分序号不同，
**以实际序号 0004 为准**（与 0003 的说明一致）。

Revision ID: 0004_planning_reliability
Revises: 0003_reliability_core
Create Date: 2026-10-28
"""

from __future__ import annotations

from alembic import op

from migrations import helpers

revision = "0004_planning_reliability"
down_revision = "0003_reliability_core"
branch_labels = None
depends_on = None

BUSINESS_TABLES = (
    "background_jobs",
    "plan_versions",
    "plan_stops",
    "outbox_events",
    "outbox_deliveries",
)


def upgrade() -> None:
    # ------------------------------------------------------- background_jobs
    # 先建任务表：``plan_versions.job_id`` 要引用它。
    op.execute(
        """
        CREATE TABLE background_jobs (
            id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id    uuid NOT NULL,
            job_type           text NOT NULL,
            status             text NOT NULL DEFAULT 'PENDING',
            payload            jsonb NOT NULL DEFAULT '{}',
            result             jsonb,
            attempts           integer NOT NULL DEFAULT 0,
            max_attempts       integer NOT NULL DEFAULT 5,
            available_at       timestamptz NOT NULL DEFAULT now(),
            locked_at          timestamptz,
            locked_by          text,
            lease_expires_at   timestamptz,
            heartbeat_at       timestamptz,
            last_error         text,
            dedupe_key         text,
            created_by_user_id uuid,
            started_at         timestamptz,
            finished_at        timestamptz,
            created_at         timestamptz NOT NULL DEFAULT now(),
            updated_at         timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_background_jobs_status_allowed
                CHECK (status IN ('PENDING','RUNNING','SUCCEEDED','FAILED','DEAD')),
            CONSTRAINT ck_background_jobs_attempts_non_negative CHECK (attempts >= 0),
            CONSTRAINT ck_background_jobs_max_attempts_positive CHECK (max_attempts >= 1),
            CONSTRAINT fk_background_jobs_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_background_jobs_created_by_user_id_users
                FOREIGN KEY (created_by_user_id) REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute("CREATE INDEX ix_background_jobs_organization_id ON background_jobs (organization_id)")
    # 队列主索引：Worker 的 claim 只需扫 (status, available_at) 前缀。
    op.execute(
        "CREATE INDEX ix_background_jobs_status_available_at ON background_jobs (status, available_at)"
    )
    # 幂等入队：同一租户同一 dedupe_key 只能存在一条在途任务；历史终态任务不参与唯一性。
    op.execute(
        "CREATE UNIQUE INDEX uq_background_jobs_organization_id_dedupe_key "
        "ON background_jobs (organization_id, dedupe_key) "
        "WHERE dedupe_key IS NOT NULL AND status IN ('PENDING','RUNNING')"
    )

    # --------------------------------------------------------- plan_versions
    op.execute(
        """
        CREATE TABLE plan_versions (
            id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id    uuid NOT NULL,
            trip_id            uuid NOT NULL,
            version_number     integer NOT NULL,
            status             text NOT NULL DEFAULT 'DRAFT',
            problem_hash       text NOT NULL,
            solver             text NOT NULL,
            objective_value    double precision NOT NULL DEFAULT 0,
            degraded           boolean NOT NULL DEFAULT false,
            trip_version       integer NOT NULL,
            validation         jsonb NOT NULL DEFAULT '{}',
            unplaced           jsonb NOT NULL DEFAULT '[]',
            explanation_ref    text,
            job_id             uuid,
            created_by_user_id uuid,
            activated_at       timestamptz,
            superseded_at      timestamptz,
            created_at         timestamptz NOT NULL DEFAULT now(),
            updated_at         timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_plan_versions_trip_id_version_number UNIQUE (trip_id, version_number),
            CONSTRAINT ck_plan_versions_status_allowed
                CHECK (status IN ('DRAFT','ACTIVE','SUPERSEDED')),
            CONSTRAINT ck_plan_versions_solver_allowed
                CHECK (solver IN ('ortools','heuristic')),
            CONSTRAINT ck_plan_versions_version_number_positive CHECK (version_number >= 1),
            CONSTRAINT ck_plan_versions_trip_version_positive CHECK (trip_version >= 1),
            CONSTRAINT fk_plan_versions_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_plan_versions_trip_id_trips
                FOREIGN KEY (trip_id) REFERENCES trips (id) ON DELETE CASCADE,
            CONSTRAINT fk_plan_versions_job_id_background_jobs
                FOREIGN KEY (job_id) REFERENCES background_jobs (id) ON DELETE SET NULL,
            CONSTRAINT fk_plan_versions_created_by_user_id_users
                FOREIGN KEY (created_by_user_id) REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute("CREATE INDEX ix_plan_versions_organization_id ON plan_versions (organization_id)")
    op.execute("CREATE INDEX ix_plan_versions_trip_id ON plan_versions (trip_id)")
    op.execute("CREATE INDEX ix_plan_versions_job_id ON plan_versions (job_id)")
    # 每个出行至多一份生效计划。用部分唯一索引而不是应用层判断：并发激活时数据库说了算。
    op.execute(
        "CREATE UNIQUE INDEX uq_plan_versions_trip_id_active "
        "ON plan_versions (trip_id) WHERE status = 'ACTIVE'"
    )

    # ------------------------------------------------------------ plan_stops
    op.execute(
        """
        CREATE TABLE plan_stops (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            plan_version_id uuid NOT NULL,
            visit_id        uuid NOT NULL,
            day_index       integer NOT NULL,
            sequence        integer NOT NULL,
            day_date        date NOT NULL,
            arrive_at       timestamptz NOT NULL,
            depart_at       timestamptz NOT NULL,
            travel_minutes  integer NOT NULL DEFAULT 0,
            service_minutes integer NOT NULL DEFAULT 0,
            created_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_plan_stops_plan_version_id_day_index_sequence
                UNIQUE (plan_version_id, day_index, sequence),
            CONSTRAINT uq_plan_stops_plan_version_id_visit_id
                UNIQUE (plan_version_id, visit_id),
            CONSTRAINT ck_plan_stops_day_index_non_negative CHECK (day_index >= 0),
            CONSTRAINT ck_plan_stops_sequence_positive CHECK (sequence >= 1),
            CONSTRAINT ck_plan_stops_travel_minutes_non_negative CHECK (travel_minutes >= 0),
            CONSTRAINT ck_plan_stops_service_minutes_non_negative CHECK (service_minutes >= 0),
            CONSTRAINT ck_plan_stops_depart_after_arrive CHECK (depart_at >= arrive_at),
            CONSTRAINT fk_plan_stops_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_plan_stops_plan_version_id_plan_versions
                FOREIGN KEY (plan_version_id) REFERENCES plan_versions (id) ON DELETE CASCADE,
            CONSTRAINT fk_plan_stops_visit_id_visits
                FOREIGN KEY (visit_id) REFERENCES visits (id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX ix_plan_stops_organization_id ON plan_stops (organization_id)")
    op.execute("CREATE INDEX ix_plan_stops_plan_version_id ON plan_stops (plan_version_id)")
    op.execute("CREATE INDEX ix_plan_stops_visit_id ON plan_stops (visit_id)")

    # ---------------------------------------------------------- outbox_events
    op.execute(
        """
        CREATE TABLE outbox_events (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            aggregate_type  text NOT NULL,
            aggregate_id    uuid NOT NULL,
            event_type      text NOT NULL,
            payload         jsonb NOT NULL DEFAULT '{}',
            status          text NOT NULL DEFAULT 'PENDING',
            attempts        integer NOT NULL DEFAULT 0,
            max_attempts    integer NOT NULL DEFAULT 5,
            next_attempt_at timestamptz NOT NULL DEFAULT now(),
            last_error      text,
            processed_at    timestamptz,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_outbox_events_status_allowed
                CHECK (status IN ('PENDING','PROCESSING','DONE','DEAD')),
            CONSTRAINT ck_outbox_events_attempts_non_negative CHECK (attempts >= 0),
            CONSTRAINT fk_outbox_events_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX ix_outbox_events_organization_id ON outbox_events (organization_id)")
    op.execute(
        "CREATE INDEX ix_outbox_events_aggregate_id ON outbox_events (aggregate_type, aggregate_id)"
    )
    op.execute(
        "CREATE INDEX ix_outbox_events_status_next_attempt_at "
        "ON outbox_events (status, next_attempt_at)"
    )

    # ------------------------------------------------------ outbox_deliveries
    op.execute(
        """
        CREATE TABLE outbox_deliveries (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            event_id        uuid NOT NULL,
            consumer        text NOT NULL,
            status          text NOT NULL DEFAULT 'PENDING',
            attempts        integer NOT NULL DEFAULT 0,
            last_error      text,
            delivered_at    timestamptz,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_outbox_deliveries_event_id_consumer UNIQUE (event_id, consumer),
            CONSTRAINT ck_outbox_deliveries_status_allowed
                CHECK (status IN ('PENDING','DONE','FAILED')),
            CONSTRAINT ck_outbox_deliveries_attempts_non_negative CHECK (attempts >= 0),
            CONSTRAINT fk_outbox_deliveries_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_outbox_deliveries_event_id_outbox_events
                FOREIGN KEY (event_id) REFERENCES outbox_events (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_outbox_deliveries_organization_id ON outbox_deliveries (organization_id)"
    )
    op.execute("CREATE INDEX ix_outbox_deliveries_event_id ON outbox_deliveries (event_id)")
    op.execute("CREATE INDEX ix_outbox_deliveries_status ON outbox_deliveries (status)")

    # ------------------------------------------- 补 0002 遗留的跨批外键与索引
    op.execute(
        "ALTER TABLE trips ADD CONSTRAINT fk_trips_active_plan_version_id_plan_versions "
        "FOREIGN KEY (active_plan_version_id) REFERENCES plan_versions (id) ON DELETE SET NULL"
    )
    op.execute("CREATE INDEX ix_trips_active_plan_version_id ON trips (active_plan_version_id)")

    # -------------------------------------------------------------- 触发器
    helpers.ensure_updated_at_trigger(
        "background_jobs", "plan_versions", "outbox_events", "outbox_deliveries"
    )

    # ------------------------------------------------------------------- RLS
    for table in BUSINESS_TABLES:
        helpers.enable_org_rls(table, force=True)


def downgrade() -> None:
    # 先摘策略，再按依赖倒序删表；本批新增的是"指向旧表"的外键，必须先摘掉。
    for table in BUSINESS_TABLES:
        helpers.disable_org_rls(table)

    op.execute("DROP INDEX IF EXISTS ix_trips_active_plan_version_id")
    op.execute(
        "ALTER TABLE trips DROP CONSTRAINT IF EXISTS fk_trips_active_plan_version_id_plan_versions"
    )

    op.execute("DROP TABLE IF EXISTS outbox_deliveries")
    op.execute("DROP TABLE IF EXISTS outbox_events")
    op.execute("DROP TABLE IF EXISTS plan_stops")
    op.execute("DROP TABLE IF EXISTS plan_versions")
    op.execute("DROP TABLE IF EXISTS background_jobs")
    # ``set_updated_at()`` 由 0001 创建且被旧表继续依赖，交给 0001 的 downgrade 收拾。