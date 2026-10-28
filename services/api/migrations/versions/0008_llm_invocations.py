"""0008_llm_invocations — LLM 调用审计。

设计文档 v2.0 第 7.2 章 / 第 12.2 章（原文 ``0011_llm_invocations``，本仓库顺延为 0008）。

表结构刻意比"记一条日志"更重：它要能回答四类事后问题——

1. 这条解释/草稿由哪个模型、哪版 prompt、什么输入产生（``model`` / ``prompt_*`` / ``input_hash``）；
2. 成本与延迟是多少（``tokens_*`` / ``latency_ms`` / ``cost_estimate``）；
3. 是否降级、为什么（``degraded`` / ``error``）；
4. 是否来自重放缓存（``cached``）——缓存命中不该被算成模型成本。

RLS 与其他组织级表一致：应用角色非 owner，缺失租户 GUC 时读写全部失败。

Revision ID: 0008_llm_invocations
Revises: 0007_agent_memory
Create Date: 2026-10-28
"""

from __future__ import annotations

from alembic import op

from migrations import helpers

revision = "0008_llm_invocations"
down_revision = "0007_agent_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE llm_invocations (
            id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id    uuid NOT NULL,
            task               text NOT NULL,
            model              text NOT NULL,
            prompt_template_id text NOT NULL,
            prompt_version     text NOT NULL,
            input_hash         text NOT NULL,
            output_json        jsonb,
            tokens_in          integer NOT NULL DEFAULT 0,
            tokens_out         integer NOT NULL DEFAULT 0,
            latency_ms         integer NOT NULL DEFAULT 0,
            cost_estimate      numeric(12,6) NOT NULL DEFAULT 0,
            degraded           boolean NOT NULL DEFAULT false,
            cached             boolean NOT NULL DEFAULT false,
            error              text,
            trace_id           text,
            created_at         timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_llm_invocations_tokens_in_non_negative CHECK (tokens_in >= 0),
            CONSTRAINT ck_llm_invocations_tokens_out_non_negative CHECK (tokens_out >= 0),
            CONSTRAINT ck_llm_invocations_latency_non_negative CHECK (latency_ms >= 0),
            CONSTRAINT fk_llm_invocations_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX ix_llm_invocations_organization_id ON llm_invocations (organization_id)")
    op.execute(
        "CREATE INDEX ix_llm_invocations_org_task_created_at "
        "ON llm_invocations (organization_id, task, created_at)"
    )
    op.execute(
        "CREATE INDEX ix_llm_invocations_org_input_hash "
        "ON llm_invocations (organization_id, input_hash)"
    )

    helpers.enable_org_rls("llm_invocations", force=True)


def downgrade() -> None:
    helpers.disable_org_rls("llm_invocations")
    op.execute("DROP TABLE IF EXISTS llm_invocations")
