"""Add demo-account flag and Gmail OAuth fields to users.

Revision ID: 0003_add_demo_and_gmail_fields
Revises: 0002_add_user_profile_fields
Create Date: 2026-11-04

Adds: is_demo (flags throwaway "try the demo" accounts so they can be swept
up on a schedule), gmail_email (the connected Gmail address, shown on the
profile page), gmail_refresh_token (Fernet-encrypted at rest -- see
gmail_integration.py; never stored or displayed in plain text).

Every ADD COLUMN is defensive (checked against the table's current columns
first), same convention as 0002.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_add_demo_and_gmail_fields"
down_revision = "0002_add_user_profile_fields"
branch_labels = None
depends_on = None


def _existing_columns(table: str) -> set[str]:
    conn = op.get_bind()
    return {col["name"] for col in sa.inspect(conn).get_columns(table)}


def upgrade():
    existing = _existing_columns("users")

    with op.batch_alter_table("users", schema=None) as batch_op:
        if "is_demo" not in existing:
            batch_op.add_column(
                sa.Column("is_demo", sa.Boolean, nullable=False, server_default=sa.false())
            )
        if "gmail_email" not in existing:
            batch_op.add_column(sa.Column("gmail_email", sa.String(255), nullable=True))
        if "gmail_refresh_token" not in existing:
            batch_op.add_column(sa.Column("gmail_refresh_token", sa.Text, nullable=True))


def downgrade():
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("gmail_refresh_token")
        batch_op.drop_column("gmail_email")
        batch_op.drop_column("is_demo")
