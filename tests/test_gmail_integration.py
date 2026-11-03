"""Unit tests for gmail_integration.py's pure parsing/crypto helpers.

None of these touch the network or Google's API -- they exercise the
heuristics (channel inference, company-name guessing, sender-header
parsing) and the token-at-rest encryption directly.
"""

from __future__ import annotations

import os

import gmail_integration as gi


def test_gmail_oauth_configured_false_by_default(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    assert gi.gmail_oauth_configured() is False


def test_gmail_oauth_configured_true_when_both_set(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "x")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "y")
    assert gi.gmail_oauth_configured() is True


def test_infer_channel_linkedin():
    assert gi._infer_channel("jobs-noreply@linkedin.com") == "linkedin"


def test_infer_channel_kariyer():
    assert gi._infer_channel("no-reply@kariyer.net") == "kariyer.net"


def test_infer_channel_default_email():
    assert gi._infer_channel("hr@acme-corp.com") == "email"


def test_parse_sender_with_display_name():
    name, addr = gi._parse_sender('"Acme Corp Recruiting" <recruiting@acme.com>')
    assert name == "Acme Corp Recruiting"
    assert addr == "recruiting@acme.com"


def test_parse_sender_without_display_name():
    name, addr = gi._parse_sender("recruiting@acme.com")
    assert name == "recruiting@acme.com"
    assert addr == "recruiting@acme.com"


def test_infer_company_from_subject_pattern():
    company = gi._infer_company("Your application to Acme Corp", "Acme Corp Recruiting")
    assert company == "Acme Corp"


def test_infer_company_falls_back_to_sender_name():
    company = gi._infer_company("Thanks for reaching out!", "Acme Corp Recruiting")
    assert company == "Acme Corp Recruiting"


def test_token_encrypt_decrypt_roundtrip():
    secret_key = "some-very-secret-key"
    token = "1//0gExampleRefreshToken"
    encrypted = gi.encrypt_refresh_token(secret_key, token)
    assert encrypted != token
    assert gi.decrypt_refresh_token(secret_key, encrypted) == token


def test_token_decrypt_wrong_key_returns_none():
    encrypted = gi.encrypt_refresh_token("key-one", "a-token")
    assert gi.decrypt_refresh_token("key-two", encrypted) is None
