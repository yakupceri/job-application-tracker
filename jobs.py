"""Candidate job listings for the job application tracker.

This module adds a second table, ``candidate_jobs``, to the same SQLite
database used by ``tracker.py``. It holds job postings the owner has not
applied to yet but is considering ("Başvurabileceğim ilanlar" in the web
dashboard).

Core functions here follow the same conventions as ``tracker.py``: plain
functions that take an explicit ``conn`` (a ``sqlite3.Connection``), raise
``ValueError`` on bad input, and use parameterized SQL only. No Flask or
``print`` calls live in this module -- it is importable and testable on its
own.

Mapping candidate jobs into ``applications`` (see ``mark_candidate_applied``):
``tracker.add_application`` requires a ``channel`` from
``tracker.VALID_CHANNELS`` ("email", "kariyer.net", "linkedin"), but a
candidate job's ``link`` can point anywhere. We infer the channel from the
link's domain: a link containing "linkedin" maps to "linkedin", a link
containing "kariyer.net" maps to "kariyer.net", and anything else defaults to
"email" -- with the real source link preserved in the application's ``note``
field so no information is lost.
"""

from __future__ import annotations

import sqlite3
from datetime import date

import tracker

VALID_TAGS = ("Çok uygun", "Uygun", "Zorlama")

CANDIDATE_JOBS_SCHEMA = """
CREATE TABLE IF NOT EXISTS candidate_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL,
    position TEXT NOT NULL,
    city TEXT,
    link TEXT,
    reason TEXT,
    tag TEXT NOT NULL,
    found_date TEXT NOT NULL
)
"""

# Used to order rows best-first in list_candidate_jobs.
_TAG_ORDER_SQL = """
    CASE tag
        WHEN 'Çok uygun' THEN 0
        WHEN 'Uygun' THEN 1
        WHEN 'Zorlama' THEN 2
        ELSE 3
    END
"""


def init_candidate_jobs(conn: sqlite3.Connection) -> None:
    """Create the ``candidate_jobs`` table if it does not already exist."""
    conn.execute(CANDIDATE_JOBS_SCHEMA)
    conn.commit()


def add_candidate_job(
    conn: sqlite3.Connection,
    company: str,
    position: str,
    city: str,
    link: str,
    reason: str,
    tag: str,
    found_date: str | None = None,
) -> int:
    """Insert a new candidate job row and return its new id.

    Raises ``ValueError`` if ``tag`` is not one of ``VALID_TAGS``. If
    ``found_date`` is omitted, today's ISO date is used.
    """
    if tag not in VALID_TAGS:
        raise ValueError(f"Invalid tag '{tag}': expected one of {VALID_TAGS}")

    if link and not link.lower().startswith(("http://", "https://")):
        raise ValueError(f"Invalid link '{link}': must start with http:// or https://")

    if found_date is None:
        found_date = date.today().isoformat()

    cursor = conn.execute(
        """
        INSERT INTO candidate_jobs (company, position, city, link, reason, tag, found_date)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (company, position, city, link, reason, tag, found_date),
    )
    conn.commit()
    return cursor.lastrowid


def list_candidate_jobs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return all candidate jobs, best tag first.

    Order is "Çok uygun", then "Uygun", then "Zorlama" (via a SQL CASE
    expression), and by ``found_date`` ascending within each tag.
    """
    cursor = conn.execute(
        f"""
        SELECT id, company, position, city, link, reason, tag, found_date
        FROM candidate_jobs
        ORDER BY {_TAG_ORDER_SQL}, found_date ASC
        """
    )
    return cursor.fetchall()


def clear_candidate_jobs(conn: sqlite3.Connection) -> None:
    """Delete all rows from ``candidate_jobs`` (e.g. before refilling)."""
    conn.execute("DELETE FROM candidate_jobs")
    conn.commit()


def _infer_channel(link: str | None) -> str:
    """Map a candidate job's link domain to a valid applications channel."""
    if link:
        lowered = link.lower()
        if "linkedin" in lowered:
            return "linkedin"
        if "kariyer.net" in lowered:
            return "kariyer.net"
    return "email"


def mark_candidate_applied(conn: sqlite3.Connection, candidate_id: int) -> int:
    """Move a candidate job into ``applications`` and remove it from the list.

    Looks up ``candidate_id`` in ``candidate_jobs``, inserts a matching row
    into ``applications`` via ``tracker.add_application`` (today's date,
    channel inferred from the link's domain via ``_infer_channel``, and the
    original link/reason preserved in the note), then deletes the candidate
    row. Returns the new application id. Raises ``ValueError`` if no
    candidate job with ``candidate_id`` exists.
    """
    row = conn.execute(
        "SELECT * FROM candidate_jobs WHERE id = ?", (candidate_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"No candidate job found with id {candidate_id}")

    channel = _infer_channel(row["link"])
    note_parts = []
    if row["link"]:
        note_parts.append(f"Source: {row['link']}")
    if row["reason"]:
        note_parts.append(row["reason"])
    note = " | ".join(note_parts) or None

    app_id = tracker.add_application(
        conn,
        company=row["company"],
        position=row["position"],
        applied_date=date.today().isoformat(),
        channel=channel,
        note=note,
    )

    conn.execute("DELETE FROM candidate_jobs WHERE id = ?", (candidate_id,))
    conn.commit()
    return app_id
