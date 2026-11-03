"""Tests for the /stats charts page added in Stage 3."""

from __future__ import annotations

from conftest import login_user, register_user


def _signed_in_client(app, username, email):
    client = app.test_client()
    register_user(client, username, email)
    login_user(client, username, "abcd1234")
    return client


def test_stats_requires_login(client):
    resp = client.get("/stats")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_stats_renders_with_no_data(app):
    client = _signed_in_client(app, "statsempty", "statsempty@example.com")
    resp = client.get("/stats")
    assert resp.status_code == 200
    assert "{{" not in resp.get_data(as_text=True)


def test_stats_counts_only_current_users_data(app):
    alice = _signed_in_client(app, "statsalice", "statsalice@example.com")
    bob = _signed_in_client(app, "statsbob", "statsbob@example.com")

    alice.post(
        "/applications",
        data={"company": "AliceCo", "position": "Dev", "applied_date": "2026-01-05", "channel": "email"},
    )
    alice.post(
        "/applications",
        data={"company": "AliceCo2", "position": "Dev", "applied_date": "2026-01-10", "channel": "linkedin"},
    )
    bob.post(
        "/applications",
        data={"company": "BobCo", "position": "Dev", "applied_date": "2026-01-05", "channel": "email"},
    )

    alice_html = alice.get("/stats").get_data(as_text=True)
    assert ">2<" in alice_html  # total_applications stat tile

    bob_html = bob.get("/stats").get_data(as_text=True)
    assert ">1<" in bob_html


def test_stats_response_rate_and_followups(app):
    client = _signed_in_client(app, "statsrate", "statsrate@example.com")
    client.post(
        "/applications",
        data={"company": "A", "position": "Dev", "applied_date": "2020-01-01", "channel": "email"},
    )
    client.post(
        "/applications",
        data={"company": "B", "position": "Dev", "applied_date": "2026-01-01", "channel": "email"},
    )
    with client.application.app_context():
        from extensions import db
        from models import Application

        a = Application.query.filter_by(company="B").first()
        a.status = "interview"
        db.session.commit()

    resp = client.get("/stats")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "%50" in body  # 1 of 2 applications reached interview/offer
