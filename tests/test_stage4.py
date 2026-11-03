"""Tests for Stage 4: CSV import, demo mode, and the Gmail-connect route guards.

The Gmail OAuth/scan flow itself needs real Google credentials to exercise
end to end (see gmail_integration.py's own unit tests for the parsing/crypto
logic); here we only check that the routes behave correctly when the
feature is unconfigured/not connected, and that nothing leaks across users.
"""

from __future__ import annotations

import io

from conftest import login_user, register_user

from models import Application, User


def _signed_in_client(app, username, email):
    client = app.test_client()
    register_user(client, username, email)
    login_user(client, username, "abcd1234")
    return client


# ---------------------------------------------------------------------------
# CSV import
# ---------------------------------------------------------------------------


def test_csv_import_requires_login(client):
    resp = client.post("/profile/import/applications", data={})
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_csv_import_valid_rows(app):
    client = _signed_in_client(app, "csvuser", "csvuser@example.com")
    csv_content = (
        "company,position,applied_date,channel,note,status\n"
        "Acme,Backend,2026-01-01,email,,sent\n"
        "Beta,Frontend,2026-01-05,linkedin,Referans,interview\n"
    )
    data = {"csv_file": (io.BytesIO(csv_content.encode("utf-8")), "apps.csv")}
    resp = client.post("/profile/import/applications", data=data, content_type="multipart/form-data")
    assert resp.status_code == 302

    with app.app_context():
        user = User.query.filter_by(username="csvuser").first()
        rows = Application.query.filter_by(user_id=user.id).all()
        assert len(rows) == 2
        beta = [r for r in rows if r.company == "Beta"][0]
        assert beta.status == "interview"
        assert beta.note == "Referans"


def test_csv_import_skips_invalid_rows(app):
    client = _signed_in_client(app, "csvuser2", "csvuser2@example.com")
    csv_content = (
        "company,position,applied_date,channel,note,status\n"
        "Acme,Backend,2026-01-01,email,,sent\n"
        ",MissingCompany,2026-01-01,email,,sent\n"
        "BadDate,Dev,not-a-date,email,,sent\n"
        "BadChannel,Dev,2026-01-01,carrier-pigeon,,sent\n"
    )
    data = {"csv_file": (io.BytesIO(csv_content.encode("utf-8")), "apps.csv")}
    client.post("/profile/import/applications", data=data, content_type="multipart/form-data")

    with app.app_context():
        user = User.query.filter_by(username="csvuser2").first()
        rows = Application.query.filter_by(user_id=user.id).all()
        assert len(rows) == 1
        assert rows[0].company == "Acme"


def test_csv_import_no_file_shows_error(app):
    client = _signed_in_client(app, "csvuser3", "csvuser3@example.com")
    resp = client.post("/profile/import/applications", data={}, content_type="multipart/form-data")
    assert resp.status_code == 302
    # error is stashed in the session and rendered on the next /profile GET
    resp = client.get("/profile")
    assert "profile_import_no_file" not in resp.get_data(as_text=True)  # key is translated


def test_csv_import_only_affects_current_user(app):
    alice = _signed_in_client(app, "csvalice", "csvalice@example.com")
    bob = _signed_in_client(app, "csvbob", "csvbob@example.com")

    csv_content = "company,position,applied_date,channel,note,status\nAliceCo,Dev,2026-01-01,email,,sent\n"
    data = {"csv_file": (io.BytesIO(csv_content.encode("utf-8")), "apps.csv")}
    alice.post("/profile/import/applications", data=data, content_type="multipart/form-data")

    with app.app_context():
        bob_user = User.query.filter_by(username="csvbob").first()
        assert Application.query.filter_by(user_id=bob_user.id).count() == 0


# ---------------------------------------------------------------------------
# Demo mode
# ---------------------------------------------------------------------------


def test_demo_login_disabled_by_default(client):
    resp = client.post("/demo-login")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/login")
    with client.session_transaction() as sess:
        assert "_user_id" not in sess


def test_demo_login_creates_and_logs_in_demo_user(tmp_path):
    import app as app_module

    demo_app = app_module.create_app(
        {
            "PANEL_USERNAME": "owner2",
            "PANEL_PASSWORD": "ownerpass123",
            "SECRET_KEY": "test-secret-key-2",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'demo.db'}",
            "OWNER_EMAIL": "owner2@example.com",
            "DEMO_MODE": True,
        }
    )
    client = demo_app.test_client()
    resp = client.post("/demo-login")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/")

    with demo_app.app_context():
        demo_user = User.query.filter_by(is_demo=True).first()
        assert demo_user is not None
        assert Application.query.filter_by(user_id=demo_user.id).count() > 0

    dashboard = client.get("/")
    assert dashboard.status_code == 200


def test_demo_accounts_are_isolated_from_each_other(tmp_path):
    import app as app_module

    demo_app = app_module.create_app(
        {
            "PANEL_USERNAME": "owner3",
            "PANEL_PASSWORD": "ownerpass123",
            "SECRET_KEY": "test-secret-key-3",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'demo2.db'}",
            "OWNER_EMAIL": "owner3@example.com",
            "DEMO_MODE": True,
        }
    )
    client1 = demo_app.test_client()
    client2 = demo_app.test_client()
    client1.post("/demo-login")
    client2.post("/demo-login")

    with demo_app.app_context():
        demo_users = User.query.filter_by(is_demo=True).all()
        assert len(demo_users) == 2
        assert demo_users[0].id != demo_users[1].id


# ---------------------------------------------------------------------------
# Gmail route guards (no real Google credentials needed)
# ---------------------------------------------------------------------------


def test_gmail_connect_redirects_when_not_configured(app, monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    client = _signed_in_client(app, "gmailuser", "gmailuser@example.com")
    resp = client.get("/gmail/connect")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/profile")


def test_gmail_scan_redirects_when_not_connected(app):
    client = _signed_in_client(app, "gmailuser2", "gmailuser2@example.com")
    resp = client.get("/gmail/scan")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/profile")


def test_gmail_routes_require_login(client):
    for path in ("/gmail/connect", "/gmail/scan"):
        resp = client.get(path)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]
    resp = client.post("/gmail/disconnect")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]
