"""add digital_segment gauge_type

Revision ID: 0005_gauge_type_digital_segment
Revises: 0004_logger_schedule_retention
Create Date: 2026-04-21
"""

from __future__ import annotations

from alembic import op

revision: str = "0005_gauge_type_digital_segment"
down_revision: str | None = "0004_logger_schedule_retention"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE gauge_type ADD VALUE IF NOT EXISTS 'digital_segment'")


def downgrade() -> None:
    # PostgreSQL enum value removal is non-trivial and unsafe if rows use it.
    # Keep downgrade as no-op for this enum extension.
    pass
