"""0025_visit_objective — 拜访目标（拜访前工作台）。

Revision ID: 0025_visit_objective
Revises: 0024_fx_rates
Create Date: 2026-10-28
"""

from __future__ import annotations

from alembic import op

revision = "0025_visit_objective"
down_revision = "0024_fx_rates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE visits ADD COLUMN objective text")


def downgrade() -> None:
    op.execute("ALTER TABLE visits DROP COLUMN IF EXISTS objective")
