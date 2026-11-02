# job-application-tracker

A simple command-line tool for tracking job applications: which company and
position you applied to, when, through which channel, and what the current
status is. Data is stored locally in a SQLite database file.

Built with Ruflo AI agents.

## Requirements

- Python 3 (standard library only: `sqlite3`, `argparse`, `datetime`). No
  third-party packages are needed to run the tool.
- Running the test suite (written by a separate contributor) additionally
  requires `pytest`: `pip install pytest`.

## Storage

Data is stored in `applications.db`, a SQLite file created automatically in
the project folder the first time you run any command. This file is not
committed to git (see `.gitignore`).

## Usage

Run all commands with `python tracker.py <command> ...` from the project
root.

### Add an application

```bash
python tracker.py add --company "Acme Corp" --position "Backend Engineer" \
  --date 2026-10-20 --channel email --note "Applied via referral"
```

- `--channel` must be one of: `email`, `kariyer.net`, `linkedin`.
- `--date` must be a valid date in `YYYY-MM-DD` format.
- New applications default to status `sent`.

### List all applications

```bash
python tracker.py list
```

Prints every application, sorted by applied date (oldest first), showing id,
company, position, applied date, channel, status, and note.

### Update the status of an application

```bash
python tracker.py status 3 interview
```

Updates the application with id `3`. The new status must be one of: `sent`,
`followed-up`, `interview`, `rejected`, `offer`.

### Show applications that need a follow-up

```bash
python tracker.py followup
```

Lists applications that are still in status `sent` and were applied more
than 7 days ago (relative to today).

## Design notes

- Core logic (database connection/schema setup, add/list/status/followup
  operations) lives in plain, importable functions in `tracker.py`, separate
  from the argparse command-line wiring. This makes it straightforward to
  write unit tests that call the functions directly (e.g. against an
  in-memory SQLite database) without going through the CLI.
- `get_connection(db_path)` accepts an explicit database path (including
  `:memory:`), so tests never need to touch the real `applications.db`.
- All SQL queries use parameterized placeholders (`?`) rather than string
  formatting, to avoid SQL injection.
