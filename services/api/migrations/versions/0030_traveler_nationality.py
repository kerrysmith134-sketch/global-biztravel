"""0030_traveler_nationality — 出差人国籍（PRD P0-7：结合证件/国籍核验入境可行性）。

PRD 要求"结合用户实际证件、国籍、签证和过境情况检查可行性"。此前入境评估只查离线目录，
完全不知道评估对象持哪国护照——同一个目的地对不同国籍结论可能相反，缺国籍必须显式提示
而不是默认。`traveler_profiles` 是事实记忆层（Semantic），国籍是典型稳定事实，放这里
并随版本化一起可回溯。

Revision ID: 0030_traveler_nationality
Revises: 0029_assistant_msg_updated_at
Create Date: 2026-11-02
"""

from __future__ import annotations

from alembic import op

revision = "0030_traveler_nationality"
down_revision = "0029_assistant_msg_updated_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE traveler_profiles "
        "ADD COLUMN nationality char(2)"
    )
    op.execute(
        "ALTER TABLE traveler_profiles "
        "ADD CONSTRAINT ck_traveler_profiles_nationality "
        "CHECK (nationality IS NULL OR nationality ~ '^[A-Z]{2}$')"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE traveler_profiles DROP CONSTRAINT IF EXISTS ck_traveler_profiles_nationality"
    )
    op.execute("ALTER TABLE traveler_profiles DROP COLUMN IF EXISTS nationality")
