"""historical gauge_type migration kept for alembic chain integrity

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
    # Keep historical revision available so existing databases with alembic_version=0005
    # can start successfully after digital_segment removal from application code.
    op.execute("ALTER TYPE gauge_type ADD VALUE IF NOT EXISTS 'digital_segment'")


def downgrade() -> None:
    # Enum value removal is unsafe in generic downgrade flow.
    pass
