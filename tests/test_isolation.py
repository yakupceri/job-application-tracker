"""Cross-account data isolation tests.

Each registered user must only ever see/modify their own applications and
candidate jobs -- via the dashboard, via direct POSTs to another user's
record ids, and via CSV export. These tests use two separate logged-in
clients (alice/bob) against the *same* app/database and assert one never
leaks into the other.
"""

from __future__ import annotations

from conftest import login_user, register_user

from models import Application, CandidateJob, User


def _signed_in_client(app, username, email):
    client = app.test_client()
    register_user(client, username, email)
    login_user(client, username, "abcd1234")
    return client


def test_applications_isolated_between_users(app):
    alice = _signed_in_client(app, "alice", "alice@example.com")
    bob = _signed_in_client(app, "bob", "bob@example.com")

    alice.post(
        "/applications",
        data={"company": "AliceCo", "position": "Dev", "applied_date": "2026-01-01", "channel": "email"},
    )
    bob.post(
        "/applications",
        data={"company": "BobCo", "position": "Dev", "applied_date": "2026-01-01", "channel": "email"},
    )

    alice_page = alice.get("/").get_data(as_text=True)
    bob_page = bob.get("/").get_data(as_text=True)

    assert "AliceCo" in alice_page and "BobCo" not in alice_page
    assert "BobCo" in bob_page and "AliceCo" not in bob_page


def test_cannot_modify_another_users_application_by_id(app):
    alice = _signed_in_client(app, "alice2", "alice2@example.com")
    bob = _signed_in_client(app, "bob2", "bob2@example.com")

    bob.post(
        "/applications",
        data={"company": "BobCo", "position": "Dev", "applied_date": "2026-01-01", "channel": "email"},
    )
    with app.app_context():
        bob_app_id = Application.query.filter_by(company="BobCo").first().id

    resp = alice.post(f"/applications/{bob_app_id}/status", data={"status": "interview"})
    assert resp.status_code == 404

    resp = alice.post(f"/applications/{bob_app_id}/delete")
    assert resp.status_code == 404

    with app.app_context():
        bob_app = Application.query.get(bob_app_id)
        assert bob_app is not None
        assert bob_app.status == "sent"


def test_cannot_apply_to_another_users_candidate_job(app):
    alice = _signed_in_client(app, "alice3", "alice3@example.com")
    bob = _signed_in_client(app, "bob3", "bob3@example.com")

    with app.app_context():
        bob_user = User.query.filter_by(username="bob3").first()
        from extensions import db

        db.session.add(
            CandidateJob(
                company="SecretCo",
                position="Engineer",
                city="Istanbul",
                link="https://example.com/job",
                reason="great fit",
                tag="Çok uygun",
                found_date="2026-01-01",
                user_id=bob_user.id,
            )
        )
        db.session.commit()
        candidate_id = CandidateJob.query.filter_by(company="SecretCo").first().id

    resp = alice.post(f"/apply/{candidate_id}")
    assert resp.status_code == 404

    alice_page = alice.get("/").get_data(as_text=True)
    assert "SecretCo" not in alice_page

    with app.app_context():
        assert CandidateJob.query.filter_by(company="SecretCo").first() is not None
        assert Application.query.filter_by(company="SecretCo").first() is None


def test_csv_export_only_contains_own_data(app):
    alice = _signed_in_client(app, "alice4", "alice4@example.com")
    bob = _signed_in_client(app, "bob4", "bob4@example.com")

    alice.post(
        "/applications",
        data={"company": "AliceCo", "position": "Dev", "applied_date": "2026-01-01", "channel": "email"},
    )
    bob.post(
        "/applications",
        data={"company": "BobCo", "position": "Dev", "applied_date": "2026-01-01", "channel": "email"},
    )

    alice_csv = alice.get("/profile/export/applications.csv").get_data(as_text=True)
    assert "AliceCo" in alice_csv
    assert "BobCo" not in alice_csv


def test_deleting_account_removes_only_that_users_data(app):
    alice = _signed_in_client(app, "alice5", "alice5@example.com")
    bob = _signed_in_client(app, "bob5", "bob5@example.com")

    alice.post(
        "/applications",
        data={"company": "AliceCo5", "position": "Dev", "applied_date": "2026-01-01", "channel": "email"},
    )
    bob.post(
        "/applications",
        data={"company": "BobCo5", "position": "Dev", "applied_date": "2026-01-01", "channel": "email"},
    )

    alice.post("/profile/delete", data={"password": "abcd1234"})

    with app.app_context():
        assert User.query.filter_by(username="alice5").first() is None
        assert Application.query.filter_by(company="AliceCo5").first() is None
        assert Application.query.filter_by(company="BobCo5").first() is not None
        assert User.query.filter_by(username="bob5").first() is not None
