"""Tests for the Excel-style grid endpoints added in Stage 2: inline cell
edits on applications, JSON-mode responses, and dismissing a candidate job.
"""

from __future__ import annotations

from conftest import login_user, register_user

from models import Application, CandidateJob, User


def _signed_in_client(app, username, email):
    client = app.test_client()
    register_user(client, username, email)
    login_user(client, username, "abcd1234")
    return client


def _fetch_post(client, url, json_body):
    return client.post(url, json=json_body, headers={"X-Requested-With": "fetch"})


def test_inline_update_company(app):
    client = _signed_in_client(app, "griduser", "griduser@example.com")
    client.post(
        "/applications",
        data={"company": "Old", "position": "Dev", "applied_date": "2026-01-01", "channel": "email"},
    )
    with app.app_context():
        app_id = Application.query.filter_by(company="Old").first().id

    resp = _fetch_post(client, f"/applications/{app_id}/update", {"field": "company", "value": "New Co"})
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True, "value": "New Co"}
    with app.app_context():
        assert Application.query.get(app_id).company == "New Co"


def test_inline_update_rejects_empty_company(app):
    client = _signed_in_client(app, "griduser2", "griduser2@example.com")
    client.post(
        "/applications",
        data={"company": "Old", "position": "Dev", "applied_date": "2026-01-01", "channel": "email"},
    )
    with app.app_context():
        app_id = Application.query.filter_by(company="Old").first().id

    resp = _fetch_post(client, f"/applications/{app_id}/update", {"field": "company", "value": "   "})
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False
    with app.app_context():
        assert Application.query.get(app_id).company == "Old"


def test_inline_update_rejects_invalid_date(app):
    client = _signed_in_client(app, "griduser3", "griduser3@example.com")
    client.post(
        "/applications",
        data={"company": "Old", "position": "Dev", "applied_date": "2026-01-01", "channel": "email"},
    )
    with app.app_context():
        app_id = Application.query.filter_by(company="Old").first().id

    resp = _fetch_post(client, f"/applications/{app_id}/update", {"field": "applied_date", "value": "not-a-date"})
    assert resp.status_code == 400


def test_inline_update_rejects_invalid_channel(app):
    client = _signed_in_client(app, "griduser4", "griduser4@example.com")
    client.post(
        "/applications",
        data={"company": "Old", "position": "Dev", "applied_date": "2026-01-01", "channel": "email"},
    )
    with app.app_context():
        app_id = Application.query.filter_by(company="Old").first().id

    resp = _fetch_post(client, f"/applications/{app_id}/update", {"field": "channel", "value": "carrier-pigeon"})
    assert resp.status_code == 400


def test_inline_update_rejects_unknown_field(app):
    client = _signed_in_client(app, "griduser5", "griduser5@example.com")
    client.post(
        "/applications",
        data={"company": "Old", "position": "Dev", "applied_date": "2026-01-01", "channel": "email"},
    )
    with app.app_context():
        app_id = Application.query.filter_by(company="Old").first().id

    resp = _fetch_post(client, f"/applications/{app_id}/update", {"field": "status", "value": "offer"})
    assert resp.status_code == 400


def test_cannot_inline_update_another_users_application(app):
    alice = _signed_in_client(app, "gridalice", "gridalice@example.com")
    bob = _signed_in_client(app, "gridbob", "gridbob@example.com")

    bob.post(
        "/applications",
        data={"company": "BobCo", "position": "Dev", "applied_date": "2026-01-01", "channel": "email"},
    )
    with app.app_context():
        bob_app_id = Application.query.filter_by(company="BobCo").first().id

    resp = _fetch_post(alice, f"/applications/{bob_app_id}/update", {"field": "company", "value": "Hacked"})
    assert resp.status_code == 404
    with app.app_context():
        assert Application.query.get(bob_app_id).company == "BobCo"


def test_create_application_json_mode_returns_application(app):
    client = _signed_in_client(app, "gridadd", "gridadd@example.com")
    resp = client.post(
        "/applications",
        data={"company": "NewCo", "position": "Dev", "applied_date": "2026-01-01", "channel": "email"},
        headers={"X-Requested-With": "fetch"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["application"]["company"] == "NewCo"


def test_dismiss_candidate_job(app):
    client = _signed_in_client(app, "griddismiss", "griddismiss@example.com")
    with app.app_context():
        from extensions import db

        user = User.query.filter_by(username="griddismiss").first()
        db.session.add(
            CandidateJob(
                company="LeadCo", position="Eng", city="Izmir", link=None,
                reason=None, tag="Uygun", found_date="2026-01-01", user_id=user.id,
            )
        )
        db.session.commit()
        job_id = CandidateJob.query.filter_by(company="LeadCo").first().id

    resp = _fetch_post(client, f"/jobs/{job_id}/delete", {})
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}
    with app.app_context():
        assert CandidateJob.query.get(job_id) is None


def test_cannot_dismiss_another_users_candidate_job(app):
    alice = _signed_in_client(app, "gridalice2", "gridalice2@example.com")
    bob = _signed_in_client(app, "gridbob2", "gridbob2@example.com")

    with app.app_context():
        from extensions import db

        bob_user = User.query.filter_by(username="gridbob2").first()
        db.session.add(
            CandidateJob(
                company="BobLead", position="Eng", city="Izmir", link=None,
                reason=None, tag="Uygun", found_date="2026-01-01", user_id=bob_user.id,
            )
        )
        db.session.commit()
        job_id = CandidateJob.query.filter_by(company="BobLead").first().id

    resp = _fetch_post(alice, f"/jobs/{job_id}/delete", {})
    assert resp.status_code == 404
    with app.app_context():
        assert CandidateJob.query.get(job_id) is not None
