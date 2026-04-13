"""logger schedule mode + image retention policy

Revision ID: 0004_logger_schedule_retention
Revises: 0003_logger_stream_state
Create Date: 2026-04-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0004_logger_schedule_retention"
down_revision: str | None = "0003_logger_stream_state"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    capture_mode = sa.Enum("continuous", "schedule", name="capture_mode")
    capture_mode.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "loggers",
        sa.Column("capture_mode", capture_mode, nullable=False, server_default="continuous"),
    )
    op.add_column("loggers", sa.Column("schedule_start_hour_utc", sa.Integer(), nullable=True))
    op.add_column("loggers", sa.Column("schedule_end_hour_utc", sa.Integer(), nullable=True))
    op.add_column("loggers", sa.Column("image_retention_days", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("loggers", "image_retention_days")
    op.drop_column("loggers", "schedule_end_hour_utc")
    op.drop_column("loggers", "schedule_start_hour_utc")
    op.drop_column("loggers", "capture_mode")
    op.execute("DROP TYPE IF EXISTS capture_mode")
