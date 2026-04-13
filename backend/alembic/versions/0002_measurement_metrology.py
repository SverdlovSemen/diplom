"""measurement metrology: out_of_range, cv_warnings_json

Revision ID: 0002_measurement_metrology
Revises: 0001_init
Create Date: 2026-04-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0002_measurement_metrology"
down_revision: str | None = "0001_init"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("measurements", sa.Column("out_of_range", sa.Boolean(), nullable=True))
    op.add_column("measurements", sa.Column("cv_warnings_json", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("measurements", "cv_warnings_json")
    op.drop_column("measurements", "out_of_range")
