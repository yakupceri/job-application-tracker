"""Gmail OAuth connection + best-effort parsing of job-application emails.

Each user connects their *own* Gmail account from the profile page (standard
OAuth "Sign in with Google"-style consent screen, never a password). We only
ever request the read-only ``gmail.readonly`` scope, and only ever read --
never send, delete, or modify anything in the user's mailbox.

This module is deliberately network-call-free at import time so it's safe
to import from app.py/tests without any Google credentials configured; every
function that actually talks to Google takes the caller-supplied
credentials/config explicitly.

Setup (documented in README.md): a Google Cloud project with the Gmail API
enabled and an OAuth 2.0 Client ID (type "Web application"), its client
id/secret in ``GOOGLE_CLIENT_ID``/``GOOGLE_CLIENT_SECRET``, and
``GOOGLE_REDIRECT_URI`` set to ``<APP_BASE_URL>/gmail/callback``. If these
aren't set, ``gmail_oauth_configured()`` returns False and the whole feature
stays hidden in the UI -- same "optional, degrades cleanly" pattern as the
SMTP email features.
"""

from __future__ import annotations

import base64
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# How many of the most recent matching messages to pull per scan. Kept small
# deliberately: this is a "find candidates, let the user review" tool, not a
# full mailbox import, and it keeps Gmail API quota usage trivial.
MAX_RESULTS = 30

# A single Gmail search query covering common ATS/job-board senders and
# application-confirmation subject lines, in Turkish and English. Best
# effort by design -- every result is a *suggestion* the user edits and
# confirms before anything is saved, never auto-imported.
SEARCH_QUERY = (
    '(subject:("your application" OR "application received" OR "we received your application" '
    'OR "başvurunuz" OR "başvurunuzu aldık" OR "application to") '
    'OR from:(linkedin.com OR kariyer.net OR indeed.com OR glassdoor.com OR lever.co '
    'OR greenhouse.io OR workday.com OR smartrecruiters.com))'
)


def gmail_oauth_configured() -> bool:
    return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))


def _client_config(redirect_uri: str) -> dict:
    return {
        "web": {
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }


def build_auth_url(redirect_uri: str, state: str) -> str:
    """Return the Google consent-screen URL to send the user to."""
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_config(_client_config(redirect_uri), scopes=SCOPES, state=state)
    flow.redirect_uri = redirect_uri
    auth_url, _ = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent"
    )
    return auth_url


def exchange_code_for_credentials(redirect_uri: str, state: str, authorization_response: str):
    """Exchange the callback's ``code`` for credentials (with a refresh token)."""
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_config(_client_config(redirect_uri), scopes=SCOPES, state=state)
    flow.redirect_uri = redirect_uri
    flow.fetch_token(authorization_response=authorization_response)
    return flow.credentials


def credentials_from_refresh_token(refresh_token: str):
    from google.oauth2.credentials import Credentials

    return Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        scopes=SCOPES,
    )


def fetch_connected_email(credentials) -> str:
    from googleapiclient.discovery import build

    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    profile = service.users().getProfile(userId="me").execute()
    return profile["emailAddress"]


# ---------------------------------------------------------------------------
# Token encryption at rest (Fernet, key derived from SECRET_KEY)
# ---------------------------------------------------------------------------


def _fernet(secret_key: str) -> Fernet:
    # Fernet needs a 32-byte urlsafe-base64 key; derive one deterministically
    # from the app's SECRET_KEY so no extra key needs to be managed/rotated.
    import hashlib

    digest = hashlib.sha256(secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_refresh_token(secret_key: str, refresh_token: str) -> str:
    return _fernet(secret_key).encrypt(refresh_token.encode("utf-8")).decode("utf-8")


def decrypt_refresh_token(secret_key: str, encrypted: str) -> str | None:
    try:
        return _fernet(secret_key).decrypt(encrypted.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return None


# ---------------------------------------------------------------------------
# Searching + best-effort parsing
# ---------------------------------------------------------------------------


@dataclass
class EmailCandidate:
    message_id: str
    company: str
    position: str
    applied_date: str  # ISO YYYY-MM-DD
    channel: str
    subject: str
    sender: str


_CHANNEL_BY_DOMAIN = (
    ("linkedin.com", "linkedin"),
    ("kariyer.net", "kariyer.net"),
)

# "Your application to Acme Corp" / "Acme Corp için başvurunuz alındı" style
# subject patterns. Best effort: anything that doesn't match falls back to
# the sender's display name as the company guess, position left blank for
# the user to fill in on the review screen.
_SUBJECT_PATTERNS = [
    re.compile(r"application (?:to|for .*? at)\s+(?P<company>.+)", re.IGNORECASE),
    re.compile(r"your application to\s+(?P<company>.+)", re.IGNORECASE),
    re.compile(r"^(?P<company>.+?)\s+(?:için başvurunuz|başvurunuzu)", re.IGNORECASE),
]


def _infer_channel(sender: str) -> str:
    lowered = sender.lower()
    for domain, channel in _CHANNEL_BY_DOMAIN:
        if domain in lowered:
            return channel
    return "email"


def _infer_company(subject: str, sender_name: str) -> str:
    for pattern in _SUBJECT_PATTERNS:
        match = pattern.search(subject)
        if match:
            return match.group("company").strip(" .-–—")
    return sender_name


def _parse_sender(raw_from: str) -> tuple[str, str]:
    """Return (display_name, email_address) from a raw ``From:`` header."""
    match = re.match(r'^"?([^"<]*)"?\s*<(.+)>$', raw_from.strip())
    if match:
        name, addr = match.group(1).strip(), match.group(2).strip()
        return name or addr, addr
    return raw_from, raw_from


def search_application_emails(credentials) -> list[EmailCandidate]:
    """Query Gmail for likely application emails and parse each into a candidate."""
    from googleapiclient.discovery import build

    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    results = (
        service.users()
        .messages()
        .list(userId="me", q=SEARCH_QUERY, maxResults=MAX_RESULTS)
        .execute()
    )
    message_refs = results.get("messages", [])

    candidates: list[EmailCandidate] = []
    for ref in message_refs:
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=ref["id"], format="metadata", metadataHeaders=["From", "Subject", "Date"])
            .execute()
        )
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        subject = headers.get("Subject", "")
        raw_from = headers.get("From", "")
        sender_name, sender_addr = _parse_sender(raw_from)

        internal_date_ms = int(msg.get("internalDate", "0"))
        applied_date = (
            datetime.fromtimestamp(internal_date_ms / 1000, tz=timezone.utc).date().isoformat()
            if internal_date_ms
            else ""
        )

        candidates.append(
            EmailCandidate(
                message_id=ref["id"],
                company=_infer_company(subject, sender_name),
                position="",
                applied_date=applied_date,
                channel=_infer_channel(sender_addr),
                subject=subject,
                sender=sender_addr,
            )
        )
    return candidates
