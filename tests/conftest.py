"""Shared pytest fixtures for the Flask web app's tests.

Each test gets its own throwaway SQLite file (never ``:memory:`` -- the app
uses Alembic's ``upgrade()`` to build the schema, and a fresh engine
connection per checkout would otherwise see an empty in-memory database).
The file is created in ``tmp_path`` so pytest cleans it up automatically.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# app.py's module-level `app = create_app()` (needed so `gunicorn app:app`
# works unmodified) runs at import time and requires these to be set. The
# per-test `app` fixture below immediately creates its own fully-isolated
# instance via create_app(test_config=...), so these placeholder values are
# only ever used for that one throwaway module-level instance.
os.environ.setdefault("PANEL_USERNAME", "import-time-placeholder")
os.environ.setdefault("PANEL_PASSWORD", "import-time-placeholder-pass")
os.environ.setdefault("SECRET_KEY", "import-time-placeholder-secret")
os.environ.setdefault("DB_PATH", ":memory:")

import app as app_module  # noqa: E402


@pytest.fixture
def app(tmp_path):
    db_path = tmp_path / "test_app.db"
    flask_app = app_module.create_app(
        {
            "PANEL_USERNAME": "owner",
            "PANEL_PASSWORD": "ownerpass123",
            "SECRET_KEY": "test-secret-key",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
            "OWNER_EMAIL": "owner@example.com",
        }
    )
    flask_app.config["TESTING"] = True
    yield flask_app


@pytest.fixture
def client(app):
    return app.test_client()


def register_user(client, username, email, **overrides):
    data = {
        "full_name": overrides.get("full_name", username),
        "email": email,
        "username": username,
        "password": overrides.get("password", "abcd1234"),
        "password2": overrides.get("password2", overrides.get("password", "abcd1234")),
        "target_position": overrides.get("target_position", "backend"),
        "city_preference": overrides.get("city_preference", "Istanbul"),
        "kvkk_accept": "on" if overrides.get("kvkk_accept", True) else "",
    }
    return client.post("/register", data=data)


def login_user(client, identifier, password, remember=False):
    data = {"identifier": identifier, "password": password}
    if remember:
        data["remember"] = "on"
    return client.post("/login", data=data)
