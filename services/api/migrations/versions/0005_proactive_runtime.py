"""0005_proactive_runtime — 主动式运行时：触发 / 建议 / 审计 / 审批。

设计文档 v2.0 第 8 章与第 12.2 章迁移批次（原文编号 ``0009_proactive_runtime``；
本仓库因前四批合并而顺延为 ``0005``，与 ``0003``/``0004`` 的说明一致）。

本批只解决"感知 → 提案 → 人审 → 受控执行 → 审计"这条闭环的**持久化**：

1. ``proactive_triggers`` —— 触发源四分类的统一落点，按
   ``(organization_id, trip_id, type, rule_id, dedupe_key)`` 唯一，重复扫描不会重复登记。
2. ``proactive_suggestions`` —— 建议本体。``push_status`` 与 ``status`` 分离，使"入库但没推送"
   （低置信度 / 冷却期 / 每日上限）可解释；``(organization_id, trip_id, suggestion_type,
   dedupe_key)`` 唯一，保证同一去重键只产生一条建议（AT-P02）。
3. ``suggestion_audit`` —— 生命周期每一步一条审计（执行人/系统 + 结构化细节）。
4. ``approvals`` —— 通用人审记录。设计文档 §8.5 要求执行前必须有"人、时间、决策"，
   放在独立表里而不是复用建议行的三个字段，是为了后续审批矩阵能直接复用。

RLS 按 ADR-0002 的「建表即授权」方式在本迁移内完成：四张表全部
``ENABLE`` + ``FORCE ROW LEVEL SECURITY``，应用角色 ``gbt_app`` 非 owner，隔离必然生效。

Revision ID: 0005_proactive_runtime
Revises: 0004_planning_reliability
Create Date: 2026-10-28
"""

from __future__ import annotations

from alembic import op

from migrations import helpers

revision = "0005_proactive_runtime"
down_revision = "0004_planning_reliability"
branch_labels = None
depends_on = None

BUSINESS_TABLES = (
    "proactive_triggers",
    "proactive_suggestions",
    "suggestion_audit",
    "approvals",
)


def upgrade() -> None:
    # ----------------------------------------------------- proactive_triggers
    op.execute(
        """
        CREATE TABLE proactive_triggers (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            trip_id         uuid NOT NULL,
            type            text NOT NULL,
            rule_id         text NOT NULL,
            dedupe_key      text NOT NULL,
            payload         jsonb NOT NULL DEFAULT '{}',
            occurred_at     timestamptz NOT NULL DEFAULT clock_timestamp(),
            status          text NOT NULL DEFAULT 'DETECTED',
            suggestion_id   uuid,
            detail          jsonb NOT NULL DEFAULT '{}',
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_proactive_triggers_org_trip_rule_dedupe
                UNIQUE (organization_id, trip_id, type, rule_id, dedupe_key),
            CONSTRAINT ck_proactive_triggers_type_allowed
                CHECK (type IN ('SCHEDULED','EVENT','STATE','EXTERNAL')),
            CONSTRAINT ck_proactive_triggers_status_allowed
                CHECK (status IN ('DETECTED','EVALUATED','PROPOSED','SUPPRESSED','DEDUPED','FAILED')),
            CONSTRAINT fk_proactive_triggers_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_proactive_triggers_trip_id_trips
                FOREIGN KEY (trip_id) REFERENCES trips (id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX ix_proactive_triggers_organization_id ON proactive_triggers (organization_id)")
    op.execute("CREATE INDEX ix_proactive_triggers_trip_id ON proactive_triggers (trip_id)")

    # -------------------------------------------------- proactive_suggestions
    op.execute(
        """
        CREATE TABLE proactive_suggestions (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id     uuid NOT NULL,
            trip_id             uuid NOT NULL,
            source_trigger_id   uuid,
            suggestion_type     text NOT NULL,
            status              text NOT NULL DEFAULT 'PENDING',
            title               text NOT NULL,
            rationale           text NOT NULL,
            evidence            jsonb NOT NULL DEFAULT '[]',
            proposed_action     jsonb NOT NULL DEFAULT '{}',
            confidence          double precision NOT NULL DEFAULT 0,
            requires_approval   boolean NOT NULL DEFAULT true,
            severity            text NOT NULL DEFAULT 'MEDIUM',
            dedupe_key          text NOT NULL,
            push_status         text NOT NULL DEFAULT 'PENDING',
            push_reason         text,
            notified_at         timestamptz,
            feedback_penalty    double precision NOT NULL DEFAULT 0,
            approved_by_user_id uuid,
            approved_at         timestamptz,
            executed_at         timestamptz,
            execution_result    jsonb,
            dismissed_at        timestamptz,
            dismissed_reason    text,
            created_at          timestamptz NOT NULL DEFAULT now(),
            updated_at          timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_proactive_suggestions_org_trip_type_dedupe
                UNIQUE (organization_id, trip_id, suggestion_type, dedupe_key),
            CONSTRAINT ck_proactive_suggestions_status_allowed
                CHECK (status IN ('PENDING','SUPPRESSED','APPROVED','EXECUTED','DISMISSED','EXPIRED')),
            CONSTRAINT ck_proactive_suggestions_push_status_allowed
                CHECK (push_status IN ('PENDING','PUSHED','WITHHELD')),
            CONSTRAINT ck_proactive_suggestions_severity_allowed
                CHECK (severity IN ('LOW','MEDIUM','HIGH')),
            CONSTRAINT ck_proactive_suggestions_confidence_range
                CHECK (confidence >= 0 AND confidence <= 1),
            CONSTRAINT ck_proactive_suggestions_penalty_range
                CHECK (feedback_penalty >= 0 AND feedback_penalty <= 1),
            CONSTRAINT fk_proactive_suggestions_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_proactive_suggestions_trip_id_trips
                FOREIGN KEY (trip_id) REFERENCES trips (id) ON DELETE CASCADE,
            CONSTRAINT fk_proactive_suggestions_source_trigger_id_proactive_triggers
                FOREIGN KEY (source_trigger_id) REFERENCES proactive_triggers (id) ON DELETE SET NULL,
            CONSTRAINT fk_proactive_suggestions_approved_by_user_id_users
                FOREIGN KEY (approved_by_user_id) REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_proactive_suggestions_organization_id "
        "ON proactive_suggestions (organization_id)"
    )
    op.execute("CREATE INDEX ix_proactive_suggestions_trip_id ON proactive_suggestions (trip_id)")
    op.execute(
        "CREATE INDEX ix_proactive_suggestions_source_trigger_id "
        "ON proactive_suggestions (source_trigger_id)"
    )
    op.execute(
        "CREATE INDEX ix_proactive_suggestions_org_status_created_at "
        "ON proactive_suggestions (organization_id, status, created_at)"
    )
    op.execute(
        "CREATE INDEX ix_proactive_suggestions_org_trip_created_at "
        "ON proactive_suggestions (organization_id, trip_id, created_at)"
    )

    # ------------------------------------------------------ suggestion_audit
    op.execute(
        """
        CREATE TABLE suggestion_audit (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            suggestion_id   uuid NOT NULL,
            action          text NOT NULL,
            actor_type      text NOT NULL DEFAULT 'SYSTEM',
            actor_user_id   uuid,
            detail          jsonb NOT NULL DEFAULT '{}',
            created_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_suggestion_audit_action_allowed
                CHECK (action IN ('DETECTED','EVALUATED','PROPOSED','SUPPRESSED','APPROVED',
                                  'EXECUTED','DISMISSED','AUDITED','LEARNED')),
            CONSTRAINT ck_suggestion_audit_actor_type_allowed
                CHECK (actor_type IN ('SYSTEM','USER')),
            CONSTRAINT fk_suggestion_audit_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_suggestion_audit_suggestion_id_proactive_suggestions
                FOREIGN KEY (suggestion_id) REFERENCES proactive_suggestions (id) ON DELETE CASCADE,
            CONSTRAINT fk_suggestion_audit_actor_user_id_users
                FOREIGN KEY (actor_user_id) REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute("CREATE INDEX ix_suggestion_audit_organization_id ON suggestion_audit (organization_id)")
    op.execute(
        "CREATE INDEX ix_suggestion_audit_suggestion_id_created_at "
        "ON suggestion_audit (suggestion_id, created_at)"
    )

    # -------------------------------------------------------------- approvals
    op.execute(
        """
        CREATE TABLE approvals (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id     uuid NOT NULL,
            subject_type        text NOT NULL,
            subject_id          uuid NOT NULL,
            decision            text NOT NULL,
            decided_by_user_id  uuid,
            decided_at          timestamptz NOT NULL DEFAULT clock_timestamp(),
            note                text,
            created_at          timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_approvals_org_subject
                UNIQUE (organization_id, subject_type, subject_id),
            CONSTRAINT ck_approvals_decision_allowed
                CHECK (decision IN ('APPROVED','REJECTED')),
            CONSTRAINT fk_approvals_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_approvals_decided_by_user_id_users
                FOREIGN KEY (decided_by_user_id) REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute("CREATE INDEX ix_approvals_organization_id ON approvals (organization_id)")
    op.execute("CREATE INDEX ix_approvals_subject ON approvals (subject_type, subject_id)")

    helpers.ensure_updated_at_trigger("proactive_triggers", "proactive_suggestions")

    for table in BUSINESS_TABLES:
        helpers.enable_org_rls(table, force=True)


def downgrade() -> None:
    for table in BUSINESS_TABLES:
        helpers.disable_org_rls(table)

    op.execute("DROP TABLE IF EXISTS approvals")
    op.execute("DROP TABLE IF EXISTS suggestion_audit")
    op.execute("DROP TABLE IF EXISTS proactive_suggestions")
    op.execute("DROP TABLE IF EXISTS proactive_triggers")
