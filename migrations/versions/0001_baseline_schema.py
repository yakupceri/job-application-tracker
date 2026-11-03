"""Baseline schema: users / applications / candidate_jobs.

Revision ID: 0001_baseline_schema
Revises:
Create Date: 2026-11-03

This is the first Alembic migration introduced into a project that already
had these three tables created ad hoc (by ``tracker.init_db`` /
``jobs.init_candidate_jobs`` / the old ``app.py``'s own ``migrate_db``), so
every table/column here is created defensively -- only if it doesn't
already exist -- rather than assuming a blank database. A brand new
deployment gets the full schema from this one migration; an existing
production database just has the gaps (if any) filled in.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_baseline_schema"
down_revision = None
branch_labels = None
depends_on = None


def _existing_tables():
    conn = op.get_bind()
    return set(sa.inspect(conn).get_table_names())


def upgrade():
    tables = _existing_tables()

    if "users" not in tables:
        op.create_table(
            "users",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("username", sa.String(80), unique=True, nullable=False),
            sa.Column("password_hash", sa.String(255), nullable=False),
            sa.Column("created_at", sa.String(32), nullable=False),
        )

    if "applications" not in tables:
        op.create_table(
            "applications",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("company", sa.String(200), nullable=False),
            sa.Column("position", sa.String(200), nullable=False),
            sa.Column("applied_date", sa.String(10), nullable=False),
            sa.Column("channel", sa.String(50), nullable=False),
            sa.Column("note", sa.Text, nullable=True),
            sa.Column("status", sa.String(50), nullable=False),
            sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        )

    if "candidate_jobs" not in tables:
        op.create_table(
            "candidate_jobs",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("company", sa.String(200), nullable=False),
            sa.Column("position", sa.String(200), nullable=False),
            sa.Column("city", sa.String(200), nullable=True),
            sa.Column("link", sa.String(500), nullable=True),
            sa.Column("reason", sa.Text, nullable=True),
            sa.Column("tag", sa.String(50), nullable=False),
            sa.Column("found_date", sa.String(10), nullable=False),
            sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        )


def downgrade():
    # Deliberately not reversible: this baseline may be describing tables
    # that predate Alembic entirely, so dropping them here would delete
    # pre-existing user data rather than just undoing this migration.
    pass
