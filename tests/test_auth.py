"""Auth/account tests for the Flask web app: registration, login rules,
brute-force lockout, profile updates, and account deletion.
"""

from __future__ import annotations

from conftest import login_user, register_user

from models import User


def test_register_requires_kvkk_acceptance(client):
    resp = register_user(client, "nokvkk", "nokvkk@example.com", kvkk_accept=False)
    assert resp.status_code == 200
    assert "KVKK" in resp.get_data(as_text=True) or "Privacy Notice" in resp.get_data(as_text=True)


def test_register_rejects_weak_password(client):
    resp = register_user(client, "weakpw", "weakpw@example.com", password="short1", password2="short1")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "8" in body  # too-short message mentions the minimum length


def test_register_rejects_password_without_digit(client):
    resp = register_user(client, "nodigitpw", "nodigit@example.com", password="onlyletters", password2="onlyletters")
    assert resp.status_code == 200


def test_register_rejects_mismatched_passwords(client):
    resp = register_user(client, "mismatch", "mismatch@example.com", password="abcd1234", password2="abcd9999")
    assert resp.status_code == 200


def test_register_rejects_duplicate_username(client, app):
    register_user(client, "dup1", "dup1@example.com")
    resp = register_user(client, "dup1", "dup2@example.com")
    assert resp.status_code == 200
    with app.app_context():
        assert User.query.filter_by(username="dup1").count() == 1


def test_register_rejects_duplicate_email(client, app):
    register_user(client, "userA", "shared@example.com")
    resp = register_user(client, "userB", "shared@example.com")
    assert resp.status_code == 200
    with app.app_context():
        assert User.query.filter_by(email="shared@example.com").count() == 1


def test_password_stored_hashed_not_plaintext(client, app):
    register_user(client, "hashcheck", "hashcheck@example.com", password="abcd1234")
    with app.app_context():
        user = User.query.filter_by(username="hashcheck").first()
        assert user.password_hash != "abcd1234"
        assert "abcd1234" not in user.password_hash
        assert user.password_hash.startswith("$argon2")


def test_login_with_username_or_email(client):
    register_user(client, "flex", "flex@example.com", password="abcd1234")

    resp = login_user(client, "flex", "abcd1234")
    assert resp.status_code == 302
    client.get("/logout")

    resp = login_user(client, "flex@example.com", "abcd1234")
    assert resp.status_code == 302


def test_login_fails_with_wrong_password(client):
    register_user(client, "wrongpw", "wrongpw@example.com", password="abcd1234")
    resp = login_user(client, "wrongpw", "nope1234", remember=False)
    assert resp.status_code == 200
    assert "login_error" not in resp.get_data(as_text=True)  # key is translated, not leaked raw


def test_brute_force_lockout_after_max_attempts(client, app):
    register_user(client, "lockme", "lockme@example.com", password="abcd1234")

    for _ in range(5):
        login_user(client, "lockme", "wrongpass")

    with app.app_context():
        user = User.query.filter_by(username="lockme").first()
        assert user.is_locked()

    # Even the *correct* password is rejected while locked.
    resp = login_user(client, "lockme", "abcd1234")
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    with client.session_transaction() as sess:
        assert "_user_id" not in sess


def test_successful_login_resets_failed_count(client, app):
    register_user(client, "resetme", "resetme@example.com", password="abcd1234")
    login_user(client, "resetme", "wrongpass")
    login_user(client, "resetme", "abcd1234")
    with app.app_context():
        user = User.query.filter_by(username="resetme").first()
        assert user.failed_login_count == 0
        assert not user.is_locked()


def test_profile_requires_login(client):
    resp = client.get("/profile")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_profile_update_changes_info(client, app):
    register_user(client, "profileuser", "profileuser@example.com", password="abcd1234")
    login_user(client, "profileuser", "abcd1234")

    client.post(
        "/profile/update",
        data={
            "full_name": "New Name",
            "email": "newemail@example.com",
            "target_position": "AI",
            "city_preference": "Ankara",
        },
    )
    with app.app_context():
        user = User.query.filter_by(username="profileuser").first()
        assert user.full_name == "New Name"
        assert user.email == "newemail@example.com"
        assert user.target_position == "AI"


def test_profile_password_change_requires_current_password(client, app):
    register_user(client, "pwchange", "pwchange@example.com", password="abcd1234")
    login_user(client, "pwchange", "abcd1234")

    client.post(
        "/profile/password",
        data={"current_password": "WRONG", "new_password": "newpass123", "new_password2": "newpass123"},
    )
    with app.app_context():
        user = User.query.filter_by(username="pwchange").first()
        assert user.check_password("abcd1234")  # unchanged

    client.post(
        "/profile/password",
        data={"current_password": "abcd1234", "new_password": "newpass123", "new_password2": "newpass123"},
    )
    with app.app_context():
        user = User.query.filter_by(username="pwchange").first()
        assert user.check_password("newpass123")


def test_profile_delete_requires_correct_password(client, app):
    register_user(client, "deleteme", "deleteme@example.com", password="abcd1234")
    login_user(client, "deleteme", "abcd1234")

    client.post("/profile/delete", data={"password": "WRONG"})
    with app.app_context():
        assert User.query.filter_by(username="deleteme").first() is not None

    client.post("/profile/delete", data={"password": "abcd1234"})
    with app.app_context():
        assert User.query.filter_by(username="deleteme").first() is None


def test_owner_account_bootstrapped_on_startup(app):
    with app.app_context():
        owner = User.query.filter_by(username="owner").first()
        assert owner is not None
        assert owner.email == "owner@example.com"
        assert owner.check_password("ownerpass123")
