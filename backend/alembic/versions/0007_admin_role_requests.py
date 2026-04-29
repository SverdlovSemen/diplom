"""add admin role requests table

Revision ID: 0007_admin_role_requests
Revises: 0006_drop_digital_segment
Create Date: 2026-04-29
"""

from __future__ import annotations

from alembic import op

revision: str = "0007_admin_role_requests"
down_revision: str | None = "0006_drop_digital_segment"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            CREATE TYPE admin_role_request_status AS ENUM ('pending', 'approved', 'rejected');
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END
        $$;
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_role_requests (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            status admin_role_request_status DEFAULT 'pending' NOT NULL,
            reviewed_by UUID REFERENCES users (id) ON DELETE SET NULL,
            review_comment VARCHAR(500),
            reviewed_at TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_admin_role_requests_user_id
        ON admin_role_requests (user_id)
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ix_admin_role_requests_user_pending_unique
        ON admin_role_requests (user_id)
        WHERE status = 'pending'
        """
    )
    op.alter_column("admin_role_requests", "status", server_default=None)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_admin_role_requests_user_pending_unique")
    op.execute("DROP INDEX IF EXISTS ix_admin_role_requests_user_id")
    op.execute("DROP TABLE IF EXISTS admin_role_requests")
    op.execute("DROP TYPE IF EXISTS admin_role_request_status")
