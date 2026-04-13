"""logger stream state persisted (last_stream_seen_at, last_stream_gap_at, last_ingest_error)

Revision ID: 0003_logger_stream_state
Revises: 0002_measurement_metrology
Create Date: 2026-04-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0003_logger_stream_state"
down_revision: str | None = "0002_measurement_metrology"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("loggers", sa.Column("last_stream_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("loggers", sa.Column("last_stream_gap_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("loggers", sa.Column("last_ingest_error", sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column("loggers", "last_ingest_error")
    op.drop_column("loggers", "last_stream_gap_at")
    op.drop_column("loggers", "last_stream_seen_at")
