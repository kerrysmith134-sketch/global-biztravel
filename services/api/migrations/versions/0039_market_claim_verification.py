"""0039_market_claim_verification — atomic claims and claim-level evidence snapshots."""

from alembic import op

from migrations import helpers

revision = "0039_market_claim_verification"
down_revision = "0038_market_evidence_derivations"
branch_labels = None
depends_on = None

TABLES = (
    "market_claim_verification_runs",
    "market_claims",
    "market_claim_evidence",
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE market_claim_verification_runs (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            answer_id uuid NOT NULL,
            workflow_id uuid NOT NULL,
            agent_run_id uuid,
            status text NOT NULL DEFAULT 'QUEUED',
            prompt_version text NOT NULL,
            input_digest text NOT NULL,
            claim_count integer NOT NULL DEFAULT 0,
            degraded boolean NOT NULL DEFAULT false,
            limitations jsonb NOT NULL DEFAULT '[]',
            failure_reason text,
            started_at timestamptz,
            completed_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_market_claim_verification_runs_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_market_claim_verification_run_workspace
                FOREIGN KEY (organization_id, workspace_id)
                REFERENCES market_workspaces (organization_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_market_claim_verification_run_answer
                FOREIGN KEY (organization_id, workspace_id, answer_id)
                REFERENCES market_research_question_answers (organization_id, workspace_id, id)
                ON DELETE RESTRICT,
            CONSTRAINT fk_market_claim_verification_run_workflow
                FOREIGN KEY (organization_id, workspace_id, workflow_id)
                REFERENCES market_workspace_workflows (organization_id, workspace_id, workflow_id)
                ON DELETE CASCADE,
            CONSTRAINT fk_market_claim_verification_run_agent_run
                FOREIGN KEY (organization_id, agent_run_id)
                REFERENCES agent_runs (organization_id, id) ON DELETE RESTRICT,
            CONSTRAINT uq_market_claim_verification_runs_org_workspace_id
                UNIQUE (organization_id, workspace_id, id),
            CONSTRAINT uq_market_claim_verification_runs_workflow
                UNIQUE (organization_id, workspace_id, workflow_id),
            CONSTRAINT ck_market_claim_verification_runs_status_allowed
                CHECK (status IN ('QUEUED','RUNNING','COMPLETED','FAILED')),
            CONSTRAINT ck_market_claim_verification_runs_input_digest_length
                CHECK (length(input_digest) = 64),
            CONSTRAINT ck_market_claim_verification_runs_claim_count_nonnegative
                CHECK (claim_count >= 0)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_market_claim_verification_runs_organization_id "
        "ON market_claim_verification_runs (organization_id)"
    )
    op.execute(
        "CREATE INDEX ix_market_claim_verification_runs_org_answer_created "
        "ON market_claim_verification_runs (organization_id, workspace_id, answer_id, created_at)"
    )

    op.execute(
        """
        CREATE TABLE market_claims (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            answer_id uuid NOT NULL,
            verification_run_id uuid NOT NULL,
            generated_by_run_id uuid NOT NULL,
            version integer NOT NULL,
            claim_key text NOT NULL,
            statement text NOT NULL,
            claim_type text NOT NULL,
            scope jsonb NOT NULL DEFAULT '{}',
            reasoning text,
            verification_verdict text NOT NULL,
            confidence_level text NOT NULL,
            status text NOT NULL DEFAULT 'NEEDS_REVIEW',
            supporting_evidence_count integer NOT NULL DEFAULT 0,
            contradicting_evidence_count integer NOT NULL DEFAULT 0,
            verified_support_count integer NOT NULL DEFAULT 0,
            verified_contradiction_count integer NOT NULL DEFAULT 0,
            distinct_host_count integer NOT NULL DEFAULT 0,
            confirmed_independent_source_count integer NOT NULL DEFAULT 0,
            limitations jsonb NOT NULL DEFAULT '[]',
            supersedes_id uuid,
            reviewed_by_user_id uuid,
            reviewed_at timestamptz,
            review_note text,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_market_claims_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_market_claim_workspace
                FOREIGN KEY (organization_id, workspace_id)
                REFERENCES market_workspaces (organization_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_market_claim_answer
                FOREIGN KEY (organization_id, workspace_id, answer_id)
                REFERENCES market_research_question_answers (organization_id, workspace_id, id)
                ON DELETE RESTRICT,
            CONSTRAINT fk_market_claim_verification_run
                FOREIGN KEY (organization_id, workspace_id, verification_run_id)
                REFERENCES market_claim_verification_runs (organization_id, workspace_id, id)
                ON DELETE CASCADE,
            CONSTRAINT fk_market_claim_agent_run
                FOREIGN KEY (organization_id, generated_by_run_id)
                REFERENCES agent_runs (organization_id, id) ON DELETE RESTRICT,
            CONSTRAINT fk_market_claim_supersedes_same_workspace
                FOREIGN KEY (organization_id, workspace_id, supersedes_id)
                REFERENCES market_claims (organization_id, workspace_id, id)
                ON DELETE NO ACTION,
            CONSTRAINT fk_market_claims_reviewed_by_user_id_users
                FOREIGN KEY (reviewed_by_user_id) REFERENCES users (id) ON DELETE RESTRICT,
            CONSTRAINT uq_market_claims_org_workspace_id
                UNIQUE (organization_id, workspace_id, id),
            CONSTRAINT uq_market_claims_answer_key_version
                UNIQUE (organization_id, workspace_id, answer_id, claim_key, version),
            CONSTRAINT ck_market_claims_version_positive CHECK (version >= 1),
            CONSTRAINT ck_market_claims_claim_key_length CHECK (length(claim_key) = 64),
            CONSTRAINT ck_market_claims_statement_not_blank CHECK (length(btrim(statement)) > 0),
            CONSTRAINT ck_market_claims_claim_type_allowed
                CHECK (claim_type IN ('FACT','ESTIMATE','OPINION','FORECAST','INFERENCE')),
            CONSTRAINT ck_market_claims_status_allowed
                CHECK (status IN ('NEEDS_REVIEW','CONFLICTED','INSUFFICIENT','ACCEPTED','REJECTED','SUPERSEDED')),
            CONSTRAINT ck_market_claims_verification_verdict_allowed
                CHECK (verification_verdict IN ('PROVISIONAL_SUPPORT','POTENTIAL_CONTRADICTION','CONFLICTED','INSUFFICIENT')),
            CONSTRAINT ck_market_claims_confidence_allowed
                CHECK (confidence_level IN ('MEDIUM','LOW','INSUFFICIENT')),
            CONSTRAINT ck_market_claims_support_count_nonnegative CHECK (supporting_evidence_count >= 0),
            CONSTRAINT ck_market_claims_contradict_count_nonnegative CHECK (contradicting_evidence_count >= 0),
            CONSTRAINT ck_market_claims_verified_support_count_nonnegative CHECK (verified_support_count >= 0),
            CONSTRAINT ck_market_claims_verified_contradiction_count_nonnegative CHECK (verified_contradiction_count >= 0),
            CONSTRAINT ck_market_claims_distinct_host_count_nonnegative CHECK (distinct_host_count >= 0),
            CONSTRAINT ck_market_claims_no_automated_independence_confirmation
                CHECK (confirmed_independent_source_count = 0),
            CONSTRAINT ck_market_claims_terminal_review_has_audit_reason
                CHECK (
                    status NOT IN ('ACCEPTED','REJECTED') OR
                    (reviewed_by_user_id IS NOT NULL AND reviewed_at IS NOT NULL
                     AND length(btrim(coalesce(review_note, ''))) > 0)
                ),
            CONSTRAINT ck_market_claims_accepted_requires_supported_claim
                CHECK (
                    status <> 'ACCEPTED' OR
                    (verification_verdict = 'PROVISIONAL_SUPPORT'
                     AND supporting_evidence_count >= 1
                     AND contradicting_evidence_count = 0
                     AND reviewed_by_user_id IS NOT NULL
                     AND reviewed_at IS NOT NULL
                     AND length(btrim(coalesce(review_note, ''))) > 0)
                ),
            CONSTRAINT ck_market_claims_supersedes_not_self
                CHECK (supersedes_id IS NULL OR supersedes_id <> id)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_market_claims_organization_id ON market_claims (organization_id)"
    )
    op.execute(
        "CREATE INDEX ix_market_claims_org_workspace_answer "
        "ON market_claims (organization_id, workspace_id, answer_id, created_at)"
    )
    op.execute(
        "CREATE INDEX ix_market_claims_org_verification_run "
        "ON market_claims (organization_id, verification_run_id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_market_claims_one_accepted_per_key "
        "ON market_claims (organization_id, workspace_id, answer_id, claim_key) "
        "WHERE status = 'ACCEPTED'"
    )

    op.execute(
        """
        CREATE TABLE market_claim_evidence (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            claim_id uuid NOT NULL,
            evidence_id uuid NOT NULL,
            assessed_by_run_id uuid NOT NULL,
            relation_type text NOT NULL,
            rationale text,
            source_host text,
            content_hash_snapshot text,
            verification_status_snapshot text NOT NULL,
            origin_assessment text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_market_claim_evidence_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_market_claim_evidence_claim
                FOREIGN KEY (organization_id, workspace_id, claim_id)
                REFERENCES market_claims (organization_id, workspace_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_market_claim_evidence_evidence
                FOREIGN KEY (organization_id, evidence_id)
                REFERENCES evidence (organization_id, id) ON DELETE RESTRICT,
            CONSTRAINT fk_market_claim_evidence_agent_run
                FOREIGN KEY (organization_id, assessed_by_run_id)
                REFERENCES agent_runs (organization_id, id) ON DELETE RESTRICT,
            CONSTRAINT uq_market_claim_evidence_relation
                UNIQUE (organization_id, claim_id, evidence_id),
            CONSTRAINT ck_market_claim_evidence_relation_type_allowed
                CHECK (relation_type IN ('SUPPORTS','CONTRADICTS','CONTEXT')),
            CONSTRAINT ck_market_claim_evidence_origin_assessment_allowed
                CHECK (origin_assessment IN ('SINGLE_SOURCE','SAME_HOST','DUPLICATE_CONTENT','DISTINCT_HOST_UNCONFIRMED'))
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_market_claim_evidence_organization_id ON market_claim_evidence (organization_id)"
    )
    op.execute(
        "CREATE INDEX ix_market_claim_evidence_org_claim "
        "ON market_claim_evidence (organization_id, workspace_id, claim_id)"
    )
    op.execute(
        "CREATE INDEX ix_market_claim_evidence_org_evidence "
        "ON market_claim_evidence (organization_id, evidence_id)"
    )

    for table in TABLES:
        helpers.enable_org_rls(table, force=True)

    if helpers.role_exists("gbt_app"):
        # Verification inputs and run identity are immutable; only execution lifecycle fields
        # may change as the durable worker advances the run.
        op.execute(
            "REVOKE UPDATE, DELETE, TRUNCATE ON market_claim_verification_runs FROM gbt_app"
        )
        op.execute(
            "GRANT UPDATE (agent_run_id, status, claim_count, degraded, limitations, "
            "failure_reason, started_at, completed_at) "
            "ON market_claim_verification_runs TO gbt_app"
        )
        # Assertion content and its evidence mapping are immutable for the application role.
        # Only the human-review state/audit columns may change after a claim is created.
        op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON market_claims FROM gbt_app")
        op.execute(
            "GRANT UPDATE (status, reviewed_by_user_id, reviewed_at, review_note) "
            "ON market_claims TO gbt_app"
        )
        # Claim/evidence links are provenance snapshots: application writes append-only.
        op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON market_claim_evidence FROM gbt_app")


def downgrade() -> None:
    for table in reversed(TABLES):
        helpers.disable_org_rls(table)
    op.execute("DROP TABLE IF EXISTS market_claim_evidence")
    op.execute("DROP TABLE IF EXISTS market_claims")
    op.execute("DROP TABLE IF EXISTS market_claim_verification_runs")
