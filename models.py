"""SQLAlchemy models for the multi-user web dashboard.

This is the data layer for ``app.py`` only. ``tracker.py``/``jobs.py`` keep
their own raw-``sqlite3`` functions for the standalone CLI tool and its test
suite (``tests/test_tracker.py``) -- those are untouched by this module.
The web app maps onto the *same* table names/columns (``applications``,
``candidate_jobs``, ``users``) so that data created by the CLI, by the old
raw-sqlite3 web app, and by this ORM all live in one consistent schema; only
their validation constants (``VALID_CHANNELS`` etc.) are reused here to
avoid duplicating the list of allowed values.

Passwords are hashed with Argon2 (via ``argon2-cffi``), not stored in any
reversible form. Email-verification and password-reset tokens are not
stored in the database at all: they're signed, time-limited tokens (see
``email_utils.py``) built from ``itsdangerous``, so there is nothing in the
database an attacker could steal to forge one.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerifyMismatchError
from flask_login import UserMixin
from werkzeug.security import check_password_hash as _werkzeug_check_password_hash

import jobs
import tracker
from extensions import db

_hasher = PasswordHasher()

# How many consecutive failed logins before a temporary lockout, and how
# long that lockout lasts. Kept here (not in app.py) so model-level helpers
# (User.is_locked / User.register_failed_login) can enforce it directly.
MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 15


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(200), nullable=False, default="")
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    target_position = db.Column(db.String(200), nullable=True)
    city_preference = db.Column(db.String(200), nullable=True)

    kvkk_accepted_at = db.Column(db.DateTime, nullable=True)
    email_verified = db.Column(db.Boolean, nullable=False, default=False)

    failed_login_count = db.Column(db.Integer, nullable=False, default=0)
    locked_until = db.Column(db.DateTime, nullable=True)

    is_demo = db.Column(db.Boolean, nullable=False, default=False)

    gmail_email = db.Column(db.String(255), nullable=True)
    gmail_refresh_token = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    @property
    def gmail_connected(self) -> bool:
        return self.gmail_refresh_token is not None

    applications = db.relationship(
        "Application", backref="user", cascade="all, delete-orphan", lazy="dynamic"
    )
    candidate_jobs = db.relationship(
        "CandidateJob", backref="user", cascade="all, delete-orphan", lazy="dynamic"
    )

    # -- password helpers --------------------------------------------------

    def set_password(self, raw_password: str) -> None:
        self.password_hash = _hasher.hash(raw_password)

    def check_password(self, raw_password: str) -> bool:
        try:
            ok = _hasher.verify(self.password_hash, raw_password)
        except VerifyMismatchError:
            return False
        except InvalidHash:
            # Account predates the Argon2 migration: its password_hash is
            # still in Werkzeug's pbkdf2/scrypt format. Verify against that
            # instead, and transparently upgrade to Argon2 on success so
            # this only ever runs once per account.
            if not _werkzeug_check_password_hash(self.password_hash, raw_password):
                return False
            self.set_password(raw_password)
            return True
        if ok and _hasher.check_needs_rehash(self.password_hash):
            # Argon2 parameters changed since this hash was created; quietly
            # upgrade it now that we have the plaintext in hand.
            self.set_password(raw_password)
        return ok

    # -- brute-force lockout -----------------------------------------------

    def is_locked(self) -> bool:
        return self.locked_until is not None and self.locked_until > datetime.utcnow()

    def register_failed_login(self) -> None:
        self.failed_login_count += 1
        if self.failed_login_count >= MAX_FAILED_LOGINS:
            self.locked_until = datetime.utcnow() + timedelta(minutes=LOCKOUT_MINUTES)

    def register_successful_login(self) -> None:
        self.failed_login_count = 0
        self.locked_until = None


class Application(db.Model):
    __tablename__ = "applications"

    id = db.Column(db.Integer, primary_key=True)
    company = db.Column(db.String(200), nullable=False)
    position = db.Column(db.String(200), nullable=False)
    applied_date = db.Column(db.String(10), nullable=False)
    channel = db.Column(db.String(50), nullable=False)
    note = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(50), nullable=False, default=tracker.DEFAULT_STATUS)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)


class CandidateJob(db.Model):
    __tablename__ = "candidate_jobs"

    id = db.Column(db.Integer, primary_key=True)
    company = db.Column(db.String(200), nullable=False)
    position = db.Column(db.String(200), nullable=False)
    city = db.Column(db.String(200), nullable=True)
    link = db.Column(db.String(500), nullable=True)
    reason = db.Column(db.Text, nullable=True)
    tag = db.Column(db.String(50), nullable=False)
    found_date = db.Column(db.String(10), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)


VALID_CHANNELS = tracker.VALID_CHANNELS
VALID_STATUSES = tracker.VALID_STATUSES
VALID_TAGS = jobs.VALID_TAGS
