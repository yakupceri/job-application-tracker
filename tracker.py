"""Job Application Tracker.

A small command-line tool for tracking job applications (company, position,
date applied, channel, status, notes) stored in a local SQLite database.

The module is intentionally split into two layers:

1. Core logic (``get_connection``, ``init_db``, ``add_application``,
   ``list_applications``, ``update_status``, ``get_followups``) -- plain
   functions that take an explicit database connection (or path) and return
   plain Python data. These are what tests should import and call directly.
2. CLI layer (``build_parser``, ``main`` and the ``cmd_*`` helpers) -- thin
   argparse plumbing that parses arguments, calls into the core layer, and
   prints results / handles errors for a human at a terminal.

Only the Python standard library is used: ``sqlite3``, ``argparse`` and
``datetime``.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

DEFAULT_DB_PATH = str(Path(__file__).resolve().parent / "applications.db")

VALID_CHANNELS = ("email", "kariyer.net", "linkedin")
VALID_STATUSES = ("sent", "followed-up", "interview", "rejected", "offer")
DEFAULT_STATUS = "sent"
FOLLOWUP_THRESHOLD_DAYS = 7

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL,
    position TEXT NOT NULL,
    applied_date TEXT NOT NULL,
    channel TEXT NOT NULL,
    note TEXT,
    status TEXT NOT NULL,
    user_id INTEGER
)
"""


# ---------------------------------------------------------------------------
# Core logic (importable, DB-path/connection driven, no argparse/print here)
# ---------------------------------------------------------------------------


def get_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open (creating if needed) a SQLite connection at ``db_path``.

    ``db_path`` may also be ``":memory:"`` for an in-memory database, which
    is convenient for tests. The schema is created automatically.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create the ``applications`` table if it does not already exist."""
    conn.execute(SCHEMA)
    conn.commit()


def is_valid_date(date_str: str) -> bool:
    """Return True if ``date_str`` is a valid ISO date (YYYY-MM-DD)."""
    try:
        date.fromisoformat(date_str)
        return True
    except (ValueError, TypeError):
        return False


def add_application(
    conn: sqlite3.Connection,
    company: str,
    position: str,
    applied_date: str,
    channel: str,
    note: str | None = None,
    status: str = DEFAULT_STATUS,
    user_id: int | None = None,
) -> int:
    """Insert a new application row and return its new id.

    Raises ``ValueError`` if ``applied_date`` is not a valid ISO date or if
    ``channel``/``status`` are not among the allowed values.

    ``user_id`` is optional and only used by the multi-user web app. The
    terminal CLI never passes it, so rows it creates keep ``user_id`` as
    ``NULL``, preserving the tool's original single-user behavior.
    """
    if not is_valid_date(applied_date):
        raise ValueError(
            f"Invalid date '{applied_date}': expected format YYYY-MM-DD"
        )
    if channel not in VALID_CHANNELS:
        raise ValueError(
            f"Invalid channel '{channel}': expected one of {VALID_CHANNELS}"
        )
    if status not in VALID_STATUSES:
        raise ValueError(
            f"Invalid status '{status}': expected one of {VALID_STATUSES}"
        )

    cursor = conn.execute(
        """
        INSERT INTO applications (company, position, applied_date, channel, note, status, user_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (company, position, applied_date, channel, note, status, user_id),
    )
    conn.commit()
    return cursor.lastrowid


def list_applications(
    conn: sqlite3.Connection, user_id: int | None = None
) -> list[sqlite3.Row]:
    """Return applications sorted by ``applied_date`` ascending.

    When ``user_id`` is given (the web app's usage), only that user's rows
    are returned. When omitted (the terminal CLI's usage), every row is
    returned regardless of owner, exactly as before ``user_id`` existed.
    """
    if user_id is None:
        cursor = conn.execute(
            "SELECT id, company, position, applied_date, channel, note, status, user_id "
            "FROM applications ORDER BY applied_date ASC"
        )
    else:
        cursor = conn.execute(
            "SELECT id, company, position, applied_date, channel, note, status, user_id "
            "FROM applications WHERE user_id = ? ORDER BY applied_date ASC",
            (user_id,),
        )
    return cursor.fetchall()


def update_status(
    conn: sqlite3.Connection,
    app_id: int,
    new_status: str,
    user_id: int | None = None,
) -> None:
    """Update the status of the application with id ``app_id``.

    Raises ``ValueError`` if ``new_status`` is invalid or if no matching
    application exists. When ``user_id`` is given, the update (and the
    existence check) is restricted to that user's own row.
    """
    if new_status not in VALID_STATUSES:
        raise ValueError(
            f"Invalid status '{new_status}': expected one of {VALID_STATUSES}"
        )

    if user_id is None:
        existing = conn.execute(
            "SELECT id FROM applications WHERE id = ?", (app_id,)
        ).fetchone()
    else:
        existing = conn.execute(
            "SELECT id FROM applications WHERE id = ? AND user_id = ?",
            (app_id, user_id),
        ).fetchone()
    if existing is None:
        raise ValueError(f"No application found with id {app_id}")

    if user_id is None:
        conn.execute(
            "UPDATE applications SET status = ? WHERE id = ?", (new_status, app_id)
        )
    else:
        conn.execute(
            "UPDATE applications SET status = ? WHERE id = ? AND user_id = ?",
            (new_status, app_id, user_id),
        )
    conn.commit()


def delete_application(
    conn: sqlite3.Connection, app_id: int, user_id: int | None = None
) -> None:
    """Delete the application with id ``app_id``.

    Raises ``ValueError`` if no matching application exists. When
    ``user_id`` is given, deletion is restricted to that user's own row.
    """
    if user_id is None:
        existing = conn.execute(
            "SELECT id FROM applications WHERE id = ?", (app_id,)
        ).fetchone()
    else:
        existing = conn.execute(
            "SELECT id FROM applications WHERE id = ? AND user_id = ?",
            (app_id, user_id),
        ).fetchone()
    if existing is None:
        raise ValueError(f"No application found with id {app_id}")

    if user_id is None:
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
    else:
        conn.execute(
            "DELETE FROM applications WHERE id = ? AND user_id = ?",
            (app_id, user_id),
        )
    conn.commit()


def get_followups(
    conn: sqlite3.Connection,
    today: date | None = None,
    user_id: int | None = None,
) -> list[sqlite3.Row]:
    """Return applications still 'sent' and applied more than 7 days ago.

    ``today`` can be injected for deterministic testing; defaults to
    ``date.today()``. When ``user_id`` is given, only that user's rows are
    considered; when omitted, all rows are (the CLI's original behavior).
    """
    if today is None:
        today = date.today()

    if user_id is None:
        cursor = conn.execute(
            "SELECT id, company, position, applied_date, channel, note, status "
            "FROM applications WHERE status = ? ORDER BY applied_date ASC",
            (DEFAULT_STATUS,),
        )
    else:
        cursor = conn.execute(
            "SELECT id, company, position, applied_date, channel, note, status "
            "FROM applications WHERE status = ? AND user_id = ? ORDER BY applied_date ASC",
            (DEFAULT_STATUS, user_id),
        )
    results = []
    for row in cursor.fetchall():
        applied = date.fromisoformat(row["applied_date"])
        if (today - applied).days > FOLLOWUP_THRESHOLD_DAYS:
            results.append(row)
    return results


# ---------------------------------------------------------------------------
# CLI layer
# ---------------------------------------------------------------------------


def _format_row(row: sqlite3.Row) -> str:
    return (
        f"[{row['id']}] {row['company']} - {row['position']} "
        f"| applied: {row['applied_date']} | channel: {row['channel']} "
        f"| status: {row['status']} | note: {row['note'] or ''}"
    )


def cmd_add(args: argparse.Namespace) -> int:
    conn = get_connection(args.db_path)
    try:
        app_id = add_application(
            conn,
            company=args.company,
            position=args.position,
            applied_date=args.date,
            channel=args.channel,
            note=args.note,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Added application #{app_id} for {args.company} - {args.position}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    conn = get_connection(args.db_path)
    rows = list_applications(conn)
    if not rows:
        print("No applications found.")
        return 0
    for row in rows:
        print(_format_row(row))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    conn = get_connection(args.db_path)
    try:
        update_status(conn, args.id, args.new_status)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Updated application #{args.id} to status '{args.new_status}'")
    return 0


def cmd_followup(args: argparse.Namespace) -> int:
    conn = get_connection(args.db_path)
    rows = get_followups(conn)
    if not rows:
        print("No applications need a follow-up right now.")
        return 0
    print("Applications needing follow-up (sent more than 7 days ago):")
    for row in rows:
        print(_format_row(row))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tracker.py", description="Track your job applications."
    )
    parser.add_argument(
        "--db-path",
        default=DEFAULT_DB_PATH,
        help="Path to the SQLite database file (default: applications.db)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="Add a new application")
    add_parser.add_argument("--company", required=True)
    add_parser.add_argument("--position", required=True)
    add_parser.add_argument("--date", required=True, help="Applied date, YYYY-MM-DD")
    add_parser.add_argument("--channel", required=True, choices=VALID_CHANNELS)
    add_parser.add_argument("--note", default=None)
    add_parser.set_defaults(func=cmd_add)

    list_parser = subparsers.add_parser("list", help="List all applications")
    list_parser.set_defaults(func=cmd_list)

    status_parser = subparsers.add_parser(
        "status", help="Update the status of an application"
    )
    status_parser.add_argument("id", type=int)
    status_parser.add_argument("new_status", choices=VALID_STATUSES)
    status_parser.set_defaults(func=cmd_status)

    followup_parser = subparsers.add_parser(
        "followup", help="Show applications that need a follow-up"
    )
    followup_parser.set_defaults(func=cmd_followup)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
