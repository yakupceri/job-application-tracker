"""Pytest suite for tracker.py.

Every test uses a fresh in-memory SQLite database (via
``tracker.get_connection(":memory:")``) so the real ``applications.db`` in
the project root is never touched or depended on.
"""

from datetime import date, timedelta

import pytest

import tracker


@pytest.fixture
def conn():
    """A fresh in-memory database connection with schema already created."""
    connection = tracker.get_connection(":memory:")
    yield connection
    connection.close()


# ---------------------------------------------------------------------------
# add_application
# ---------------------------------------------------------------------------


def test_add_application_success_defaults_status_to_sent(conn):
    app_id = tracker.add_application(
        conn,
        company="Acme Corp",
        position="Backend Engineer",
        applied_date="2024-01-15",
        channel="email",
    )

    assert app_id == 1

    rows = tracker.list_applications(conn)
    assert len(rows) == 1
    row = rows[0]
    assert row["company"] == "Acme Corp"
    assert row["position"] == "Backend Engineer"
    assert row["applied_date"] == "2024-01-15"
    assert row["channel"] == "email"
    assert row["status"] == "sent"


def test_add_application_rejects_invalid_channel(conn):
    with pytest.raises(ValueError):
        tracker.add_application(
            conn,
            company="Acme Corp",
            position="Backend Engineer",
            applied_date="2024-01-15",
            channel="carrier-pigeon",
        )

    # Nothing should have been inserted.
    assert tracker.list_applications(conn) == []


def test_add_application_rejects_invalid_date_format(conn):
    with pytest.raises(ValueError):
        tracker.add_application(
            conn,
            company="Acme Corp",
            position="Backend Engineer",
            applied_date="15-01-2024",  # wrong format, not ISO
            channel="email",
        )

    assert tracker.list_applications(conn) == []


# ---------------------------------------------------------------------------
# list_applications
# ---------------------------------------------------------------------------


def test_list_applications_sorted_by_applied_date_ascending(conn):
    tracker.add_application(
        conn, company="C", position="P1", applied_date="2024-03-01", channel="email"
    )
    tracker.add_application(
        conn, company="A", position="P2", applied_date="2024-01-01", channel="linkedin"
    )
    tracker.add_application(
        conn,
        company="B",
        position="P3",
        applied_date="2024-02-01",
        channel="kariyer.net",
    )

    rows = tracker.list_applications(conn)
    dates = [row["applied_date"] for row in rows]

    assert dates == ["2024-01-01", "2024-02-01", "2024-03-01"]
    assert [row["company"] for row in rows] == ["A", "B", "C"]


# ---------------------------------------------------------------------------
# update_status
# ---------------------------------------------------------------------------


def test_update_status_success(conn):
    app_id = tracker.add_application(
        conn, company="Acme", position="Dev", applied_date="2024-01-01", channel="email"
    )

    tracker.update_status(conn, app_id, "interview")

    rows = tracker.list_applications(conn)
    assert rows[0]["status"] == "interview"


def test_update_status_rejects_invalid_status(conn):
    app_id = tracker.add_application(
        conn, company="Acme", position="Dev", applied_date="2024-01-01", channel="email"
    )

    with pytest.raises(ValueError):
        tracker.update_status(conn, app_id, "ghosted")

    # Status should remain unchanged.
    rows = tracker.list_applications(conn)
    assert rows[0]["status"] == "sent"


def test_update_status_errors_on_nonexistent_id(conn):
    with pytest.raises(ValueError):
        tracker.update_status(conn, 999, "interview")


# ---------------------------------------------------------------------------
# get_followups
# ---------------------------------------------------------------------------


def test_get_followups_returns_only_old_sent_applications(conn):
    fixed_today = date(2024, 6, 15)

    old_date = (fixed_today - timedelta(days=10)).isoformat()  # > 7 days old
    recent_date = (fixed_today - timedelta(days=3)).isoformat()  # <= 7 days old

    old_id = tracker.add_application(
        conn, company="OldSent", position="P", applied_date=old_date, channel="email"
    )
    tracker.add_application(
        conn, company="RecentSent", position="P", applied_date=recent_date, channel="email"
    )

    followups = tracker.get_followups(conn, today=fixed_today)

    assert len(followups) == 1
    assert followups[0]["id"] == old_id
    assert followups[0]["company"] == "OldSent"


def test_get_followups_excludes_non_sent_and_boundary_cases(conn):
    fixed_today = date(2024, 6, 15)

    old_date = (fixed_today - timedelta(days=10)).isoformat()  # > 7 days, old enough
    exactly_seven_days = (fixed_today - timedelta(days=7)).isoformat()  # == 7, excluded

    # Old but status is not "sent" -> must be excluded even though it's old.
    old_but_rejected_id = tracker.add_application(
        conn,
        company="OldRejected",
        position="P",
        applied_date=old_date,
        channel="email",
    )
    tracker.update_status(conn, old_but_rejected_id, "rejected")

    # Exactly 7 days old and still "sent" -> spec says "more than 7 days", so
    # exactly 7 days must NOT appear.
    tracker.add_application(
        conn,
        company="ExactlySevenDays",
        position="P",
        applied_date=exactly_seven_days,
        channel="email",
    )

    # A genuinely qualifying row, to make sure the query isn't just empty.
    qualifying_date = (fixed_today - timedelta(days=8)).isoformat()
    qualifying_id = tracker.add_application(
        conn,
        company="Qualifies",
        position="P",
        applied_date=qualifying_date,
        channel="email",
    )

    followups = tracker.get_followups(conn, today=fixed_today)
    followup_ids = [row["id"] for row in followups]

    assert old_but_rejected_id not in followup_ids
    assert qualifying_id in followup_ids
    assert len(followups) == 1
