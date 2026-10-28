"""0017_trip_hold_job_cancel — Trip 暂停状态与规划任务取消。

PRD MVP-1 §3.1 要求 `hold/resume/complete`，§4.3 要求任务可取消。状态机新增：

- ``trips.status = HOLD``：暂停中的出行不能生成新计划；恢复时根据是否存在生效计划回到
  ACTIVE/READY/DRAFT。
- ``background_jobs.status = CANCELLED``：仅 PENDING 任务可取消；RUNNING 已交给求解器，
  无法安全中断，返回 409。

Revision ID: 0017_trip_hold_job_cancel
Revises: 0016_workflow_evidence_sync
Create Date: 2026-10-28
"""

from __future__ import annotations

from alembic import op

revision = "0017_trip_hold_job_cancel"
down_revision = "0016_workflow_evidence_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE trips DROP CONSTRAINT IF EXISTS ck_trips_status_allowed")
    op.execute(
        """
        ALTER TABLE trips ADD CONSTRAINT ck_trips_status_allowed
        CHECK (status IN ('DRAFT','PLANNING','READY','ACTIVE','HOLD','COMPLETED','CANCELLED'))
        """
    )
    op.execute(
        "ALTER TABLE background_jobs DROP CONSTRAINT IF EXISTS ck_background_jobs_status_allowed"
    )
    op.execute(
        """
        ALTER TABLE background_jobs ADD CONSTRAINT ck_background_jobs_status_allowed
        CHECK (status IN ('PENDING','RUNNING','SUCCEEDED','FAILED','DEAD','CANCELLED'))
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE background_jobs DROP CONSTRAINT IF EXISTS ck_background_jobs_status_allowed")
    op.execute(
        """
        ALTER TABLE background_jobs ADD CONSTRAINT ck_background_jobs_status_allowed
        CHECK (status IN ('PENDING','RUNNING','SUCCEEDED','FAILED','DEAD'))
        """
    )
    op.execute("ALTER TABLE trips DROP CONSTRAINT IF EXISTS ck_trips_status_allowed")
    op.execute(
        """
        ALTER TABLE trips ADD CONSTRAINT ck_trips_status_allowed
        CHECK (status IN ('DRAFT','PLANNING','READY','ACTIVE','COMPLETED','CANCELLED'))
        """
    )
