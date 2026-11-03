"""Add multi-user profile/account-security fields to users.

Revision ID: 0002_add_user_profile_fields
Revises: 0001_baseline_schema
Create Date: 2026-11-03

Adds: full_name, email (unique), target_position, city_preference,
kvkk_accepted_at, email_verified, failed_login_count, locked_until.

Every ``ADD COLUMN`` here is defensive (checked against the table's current
columns first) so this is safe to run against a database that already has
some/all of these columns -- e.g. one created fresh by
``models.py``/``db.create_all()`` before this migration ever ran.

Data backfill for pre-existing rows (added before any of these columns
existed): the account named ``PANEL_USERNAME`` -- the project owner's own
login, created by the pre-user-accounts version of this app -- gets its
email/full name from the ``OWNER_EMAIL``/``OWNER_FULL_NAME`` environment
variables (defaulting to the owner's known address so this works with no
extra setup). Any *other* pre-existing row (a user who registered through
the old plain username/password form, before email existed at all) is
grandfathered in with a placeholder ``<username>@legacy.local`` email and
``email_verified=True`` -- they can correct it from the profile page after
logging in; nothing about their data is lost or blocks login.
"""

from __future__ import annotations

import os
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "0002_add_user_profile_fields"
down_revision = "0001_baseline_schema"
branch_labels = None
depends_on = None


def _existing_columns(table: str) -> set[str]:
    conn = op.get_bind()
    return {col["name"] for col in sa.inspect(conn).get_columns(table)}


def upgrade():
    existing = _existing_columns("users")

    with op.batch_alter_table("users", schema=None) as batch_op:
        if "full_name" not in existing:
            batch_op.add_column(sa.Column("full_name", sa.String(200), nullable=True))
        if "email" not in existing:
            batch_op.add_column(sa.Column("email", sa.String(255), nullable=True))
        if "target_position" not in existing:
            batch_op.add_column(sa.Column("target_position", sa.String(200), nullable=True))
        if "city_preference" not in existing:
            batch_op.add_column(sa.Column("city_preference", sa.String(200), nullable=True))
        if "kvkk_accepted_at" not in existing:
            batch_op.add_column(sa.Column("kvkk_accepted_at", sa.DateTime, nullable=True))
        if "email_verified" not in existing:
            batch_op.add_column(
                sa.Column(
                    "email_verified", sa.Boolean, nullable=False, server_default=sa.false()
                )
            )
        if "failed_login_count" not in existing:
            batch_op.add_column(
                sa.Column("failed_login_count", sa.Integer, nullable=False, server_default="0")
            )
        if "locked_until" not in existing:
            batch_op.add_column(sa.Column("locked_until", sa.DateTime, nullable=True))

    # --- backfill rows that predate these columns -------------------------
    conn = op.get_bind()
    users = sa.table(
        "users",
        sa.column("id", sa.Integer),
        sa.column("username", sa.String),
        sa.column("full_name", sa.String),
        sa.column("email", sa.String),
        sa.column("kvkk_accepted_at", sa.DateTime),
        sa.column("email_verified", sa.Boolean),
    )

    owner_username = os.environ.get("PANEL_USERNAME")
    owner_email = os.environ.get("OWNER_EMAIL", "yakupceri24@gmail.com")
    owner_full_name = os.environ.get("OWNER_FULL_NAME") or owner_username

    now = datetime.utcnow()
    rows = conn.execute(sa.select(users.c.id, users.c.username, users.c.email)).fetchall()
    for row in rows:
        if row.email:
            continue
        if owner_username and row.username == owner_username:
            new_email, new_full_name = owner_email, owner_full_name
        else:
            new_email, new_full_name = f"{row.username}@legacy.local", row.username
        conn.execute(
            users.update()
            .where(users.c.id == row.id)
            .values(
                email=new_email,
                full_name=new_full_name,
                kvkk_accepted_at=now,
                email_verified=True,
            )
        )

    # --- now that every row has a value, enforce NOT NULL + uniqueness ----
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column("full_name", existing_type=sa.String(200), nullable=False)
        batch_op.alter_column("email", existing_type=sa.String(255), nullable=False)
        batch_op.create_unique_constraint("uq_users_email", ["email"])


def downgrade():
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_constraint("uq_users_email", type_="unique")
        batch_op.drop_column("locked_until")
        batch_op.drop_column("failed_login_count")
        batch_op.drop_column("email_verified")
        batch_op.drop_column("kvkk_accepted_at")
        batch_op.drop_column("city_preference")
        batch_op.drop_column("target_position")
        batch_op.drop_column("email")
        batch_op.drop_column("full_name")
