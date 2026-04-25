"""drop digital_segment from gauge_type enum

Revision ID: 0006_drop_digital_segment
Revises: 0005_gauge_type_digital_segment
Create Date: 2026-04-25
"""

from __future__ import annotations

from alembic import op

revision: str = "0006_drop_digital_segment"
down_revision: str | None = "0005_gauge_type_digital_segment"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Normalize legacy rows first.
    op.execute(
        """
        UPDATE loggers
        SET gauge_type = 'digital'
        WHERE gauge_type::text = 'digital_segment'
        """
    )

    # Rebuild enum without digital_segment.
    op.execute("ALTER TYPE gauge_type RENAME TO gauge_type_old")
    op.execute("CREATE TYPE gauge_type AS ENUM ('analog', 'digital')")
    op.execute(
        """
        ALTER TABLE loggers
        ALTER COLUMN gauge_type TYPE gauge_type
        USING gauge_type::text::gauge_type
        """
    )
    op.execute("DROP TYPE gauge_type_old")


def downgrade() -> None:
    op.execute("ALTER TYPE gauge_type RENAME TO gauge_type_old")
    op.execute("CREATE TYPE gauge_type AS ENUM ('analog', 'digital', 'digital_segment')")
    op.execute(
        """
        ALTER TABLE loggers
        ALTER COLUMN gauge_type TYPE gauge_type
        USING gauge_type::text::gauge_type
        """
    )
    op.execute("DROP TYPE gauge_type_old")
