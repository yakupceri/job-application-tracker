"""Email verification / password reset tokens, and optional SMTP sending.

Tokens are signed+timed with ``itsdangerous`` rather than stored in the
database -- nothing to leak, and they expire on their own. Each token also
embeds a short fingerprint of the user's current password hash, so a
password-reset token (or a stale verification token for an email that has
since changed) is automatically invalidated the moment the underlying value
it certifies changes.

SMTP is entirely optional, controlled via ``.env``:
``MAIL_SERVER``/``MAIL_PORT``/``MAIL_USERNAME``/``MAIL_PASSWORD``/
``MAIL_USE_TLS``/``MAIL_DEFAULT_SENDER``. When ``MAIL_SERVER`` is unset,
``mail_is_configured()`` returns False and ``send_email`` is a no-op that
returns False -- callers use this to disable email verification / "forgot
password" in the UI while leaving the rest of the app working normally.
"""

from __future__ import annotations

import hashlib
import os
import smtplib
from email.message import EmailMessage

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

EMAIL_VERIFY_MAX_AGE = 60 * 60 * 24  # 24 hours
PASSWORD_RESET_MAX_AGE = 60 * 60  # 1 hour


def _serializer(secret_key: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key, salt="job-tracker-email-token")


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def make_token(secret_key: str, user_id: int, purpose: str, binding_value: str) -> str:
    """Create a signed token for ``purpose`` (e.g. "verify-email", "reset-password").

    ``binding_value`` is whatever must stay unchanged for the token to still
    be valid (the user's email for verification, their password hash for a
    reset link) -- only its fingerprint is embedded, never the raw value.
    """
    serializer = _serializer(secret_key)
    return serializer.dumps(
        {"user_id": user_id, "purpose": purpose, "fp": _fingerprint(binding_value)}
    )


def read_token(
    secret_key: str, token: str, purpose: str, max_age: int, binding_value: str
) -> int | None:
    """Return the ``user_id`` encoded in ``token`` if valid, else ``None``."""
    serializer = _serializer(secret_key)
    try:
        data = serializer.loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    if data.get("purpose") != purpose:
        return None
    if data.get("fp") != _fingerprint(binding_value):
        return None
    return data.get("user_id")


def mail_is_configured() -> bool:
    return bool(os.environ.get("MAIL_SERVER"))


def send_email(to_addr: str, subject: str, body: str) -> bool:
    """Send a plain-text email via SMTP. Returns False if SMTP isn't configured."""
    if not mail_is_configured():
        return False

    server = os.environ["MAIL_SERVER"]
    port = int(os.environ.get("MAIL_PORT", "587"))
    username = os.environ.get("MAIL_USERNAME")
    password = os.environ.get("MAIL_PASSWORD")
    use_tls = os.environ.get("MAIL_USE_TLS", "true").lower() not in ("false", "0", "no")
    sender = os.environ.get("MAIL_DEFAULT_SENDER") or username or "no-reply@localhost"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_addr
    msg.set_content(body)

    with smtplib.SMTP(server, port, timeout=10) as smtp:
        if use_tls:
            smtp.starttls()
        if username and password:
            smtp.login(username, password)
        smtp.send_message(msg)
    return True


# ---------------------------------------------------------------------------
# Password strength (server-side; the register form also runs a client-side
# meter for live feedback, but this is the rule that's actually enforced)
# ---------------------------------------------------------------------------

MIN_PASSWORD_LENGTH = 8


def password_error(password: str) -> str | None:
    """Return a human-readable error if ``password`` fails the policy, else None."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"too_short"
    has_letter = any(c.isalpha() for c in password)
    has_digit = any(c.isdigit() for c in password)
    if not (has_letter and has_digit):
        return "needs_letter_and_digit"
    return None
