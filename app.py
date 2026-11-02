"""Flask web dashboard for the job application tracker.

Multi-user rewrite. Reuses ``tracker.py``'s ``applications`` table and
``jobs.py``'s ``candidate_jobs`` table, both now scoped by a ``user_id``
column, plus a new ``users`` table for per-account login. Each registered
user sees and edits only their own applications and candidate jobs.

Environment variables (read via ``os.environ``, never hardcoded):

- ``SECRET_KEY`` -- required. Used to sign Flask's session cookie. The app
  refuses to start if it is missing, same pattern as the existing
  PANEL_USERNAME/PANEL_PASSWORD check below.
- ``PANEL_USERNAME`` / ``PANEL_PASSWORD`` -- required. Two jobs:
    1. HTTP Basic Auth credentials for the ``/admin/*`` endpoints used by
       the external Gmail-refresh script.
    2. On first startup after this migration, a regular user account named
       ``PANEL_USERNAME`` is created (password hashed from
       ``PANEL_PASSWORD``) and all pre-existing rows (created before user
       accounts existed) are assigned to it, so the owner's existing data
       is never lost and becomes reachable via normal login.
- ``DB_PATH`` -- optional. Falls back to ``tracker.DEFAULT_DB_PATH``. In
  production this should point at a mounted Railway volume, e.g.
  ``/data/applications.db``.
- ``PORT`` -- optional, used only by the ``flask run``-style dev server at
  the bottom of this file. Railway/gunicorn ignore this and bind on their
  own.

Run locally with ``python app.py``, or in production with
``gunicorn app:app`` (see ``Procfile``).
"""

from __future__ import annotations

import functools
import hmac
import os
import sqlite3
from datetime import datetime

from flask import (
    Flask,
    Response,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

import jobs
import tracker

DB_PATH = os.environ.get("DB_PATH", tracker.DEFAULT_DB_PATH)

PANEL_USERNAME = os.environ.get("PANEL_USERNAME")
PANEL_PASSWORD = os.environ.get("PANEL_PASSWORD")
SECRET_KEY = os.environ.get("SECRET_KEY")

if not PANEL_USERNAME or not PANEL_PASSWORD:
    raise RuntimeError(
        "PANEL_USERNAME and PANEL_PASSWORD environment variables must both "
        "be set before starting the dashboard. They protect the /admin "
        "endpoints and seed the owner's own login account."
    )

if not SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY environment variable must be set before starting the "
        "dashboard. Refusing to start without it, since Flask sessions "
        "(user login) cannot be signed safely otherwise."
    )

app = Flask(__name__)
app.secret_key = SECRET_KEY

MIN_PASSWORD_LENGTH = 6


# ---------------------------------------------------------------------------
# Database access + startup migration
# ---------------------------------------------------------------------------

USERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""


def get_db() -> sqlite3.Connection:
    """Open a connection to the shared applications database.

    Creates/initializes the ``applications`` table (via
    ``tracker.get_connection``) and the ``candidate_jobs`` table. The
    ``users`` table and column/data migrations are handled once at startup
    by ``migrate_db``, not on every request.
    """
    conn = tracker.get_connection(DB_PATH)
    jobs.init_candidate_jobs(conn)
    return conn


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, coltype: str) -> None:
    """Defensively add ``column`` to ``table`` if it isn't already there.

    SQLite before 3.35 has no ``ADD COLUMN IF NOT EXISTS``, so this checks
    via ``PRAGMA table_info`` first and also swallows the "duplicate
    column" ``OperationalError`` as a belt-and-suspenders guard. Safe to
    call on every startup.
    """
    existing_columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column in existing_columns:
        return
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
        conn.commit()
    except sqlite3.OperationalError as exc:
        if "duplicate column" not in str(exc).lower():
            raise


def migrate_db() -> None:
    """Idempotent startup migration. Safe to run on every app start.

    1. Create ``applications``/``candidate_jobs`` (via get_db) and ``users``.
    2. Add the ``user_id`` column to ``applications``/``candidate_jobs`` if
       an older database doesn't have it yet.
    3. Ensure an owner account named ``PANEL_USERNAME`` exists, created
       from ``PANEL_PASSWORD`` if missing.
    4. Assign any pre-existing ownerless rows (``user_id IS NULL``) to that
       owner account, so no existing data is lost or orphaned.
    """
    conn = get_db()
    try:
        conn.execute(USERS_SCHEMA)
        conn.commit()

        _add_column_if_missing(conn, "applications", "user_id", "INTEGER")
        _add_column_if_missing(conn, "candidate_jobs", "user_id", "INTEGER")

        owner = conn.execute(
            "SELECT id FROM users WHERE username = ?", (PANEL_USERNAME,)
        ).fetchone()
        if owner is None:
            conn.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (
                    PANEL_USERNAME,
                    generate_password_hash(PANEL_PASSWORD),
                    datetime.utcnow().isoformat(),
                ),
            )
            conn.commit()
            owner = conn.execute(
                "SELECT id FROM users WHERE username = ?", (PANEL_USERNAME,)
            ).fetchone()

        owner_id = owner["id"]
        conn.execute(
            "UPDATE applications SET user_id = ? WHERE user_id IS NULL", (owner_id,)
        )
        conn.execute(
            "UPDATE candidate_jobs SET user_id = ? WHERE user_id IS NULL", (owner_id,)
        )
        conn.commit()
    finally:
        conn.close()


def get_owner_user_id() -> int:
    """Look up the owner account's id (the user named ``PANEL_USERNAME``).

    Used by the admin endpoints so Gmail-refresh data lands on the owner's
    own account rather than as ownerless rows.
    """
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id FROM users WHERE username = ?", (PANEL_USERNAME,)
        ).fetchone()
        return row["id"]
    finally:
        conn.close()


migrate_db()


# ---------------------------------------------------------------------------
# Admin (HTTP Basic Auth) -- used by the external Gmail-refresh script
# ---------------------------------------------------------------------------


def _check_admin_credentials(username: str | None, password: str | None) -> bool:
    if username is None or password is None:
        return False
    return hmac.compare_digest(username, PANEL_USERNAME) and hmac.compare_digest(
        password, PANEL_PASSWORD
    )


def require_admin_auth(view):
    """Decorator enforcing HTTP Basic Auth against PANEL_USERNAME/PASSWORD."""

    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        auth = request.authorization
        if not auth or not _check_admin_credentials(auth.username, auth.password):
            return Response(
                "Authentication required.",
                401,
                {"WWW-Authenticate": 'Basic realm="Job Tracker Admin"'},
            )
        return view(*args, **kwargs)

    return wrapped


# ---------------------------------------------------------------------------
# Per-user session login
# ---------------------------------------------------------------------------


def login_required(view):
    """Decorator redirecting to /login unless a valid session exists."""

    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        user_id = session.get("user_id")
        if not user_id:
            return redirect(url_for("login"))
        g.user_id = user_id
        return view(*args, **kwargs)

    return wrapped


# ---------------------------------------------------------------------------
# Bilingual UI text
# ---------------------------------------------------------------------------

TRANSLATIONS: dict[str, dict[str, str]] = {
    "app_title": {"tr": "İş Başvuru Paneli", "en": "Job Application Tracker"},
    "app_subtitle": {
        "tr": "Başvurularını takip et, yeni ilanları değerlendir.",
        "en": "Track your applications, review new job leads.",
    },
    "nav_logout": {"tr": "Çıkış yap", "en": "Log out"},
    "nav_hello": {"tr": "Merhaba", "en": "Hello"},

    "stat_applications": {"tr": "Başvuru", "en": "Applications"},
    "stat_great_jobs": {"tr": "Çok uygun ilan", "en": "Great-fit leads"},
    "stat_candidate_jobs": {"tr": "Aday ilan", "en": "Candidate leads"},

    "section_my_applications": {"tr": "Başvurularım", "en": "My Applications"},
    "section_candidate_jobs": {"tr": "Başvurabileceğim ilanlar", "en": "Jobs I could apply to"},
    "section_add_application": {"tr": "Başvuru ekle", "en": "Add an application"},

    "count_records": {"tr": "kayıt", "en": "records"},
    "count_jobs": {"tr": "ilan", "en": "leads"},

    "th_company": {"tr": "Şirket", "en": "Company"},
    "th_position": {"tr": "Pozisyon", "en": "Position"},
    "th_date": {"tr": "Tarih", "en": "Date"},
    "th_channel": {"tr": "Kanal", "en": "Channel"},
    "th_status": {"tr": "Durum", "en": "Status"},
    "th_note": {"tr": "Not", "en": "Note"},
    "th_actions": {"tr": "İşlem", "en": "Actions"},

    "status_sent": {"tr": "Gönderildi", "en": "Sent"},
    "status_followed_up": {"tr": "Takip edildi", "en": "Followed up"},
    "status_interview": {"tr": "Mülakat", "en": "Interview"},
    "status_offer": {"tr": "Teklif", "en": "Offer"},
    "status_rejected": {"tr": "Olumsuz", "en": "Rejected"},

    "empty_applications": {"tr": "Henüz başvuru yok.", "en": "No applications yet."},
    "empty_candidate_jobs": {
        "tr": "Şu an aday ilan yok. “İlanları güncelle” diyerek yeni ilan tarat.",
        "en": "No candidate leads right now. Run a refresh to find new ones.",
    },

    "field_company": {"tr": "Şirket", "en": "Company"},
    "field_position": {"tr": "Pozisyon", "en": "Position"},
    "field_date": {"tr": "Başvuru tarihi", "en": "Applied date"},
    "field_channel": {"tr": "Kanal", "en": "Channel"},
    "field_note": {"tr": "Not (opsiyonel)", "en": "Note (optional)"},

    "channel_email": {"tr": "E-posta", "en": "Email"},
    "channel_kariyer.net": {"tr": "kariyer.net", "en": "kariyer.net"},
    "channel_linkedin": {"tr": "LinkedIn", "en": "LinkedIn"},

    "btn_add": {"tr": "Ekle", "en": "Add"},
    "btn_save": {"tr": "Kaydet", "en": "Save"},
    "btn_delete": {"tr": "Sil", "en": "Delete"},
    "btn_apply": {"tr": "Başvurdum ✓", "en": "Applied ✓"},
    "btn_view_listing": {"tr": "İlanı gör ↗", "en": "View listing ↗"},

    "tag_great": {"tr": "Çok uygun", "en": "Great fit"},
    "tag_good": {"tr": "Uygun", "en": "Good fit"},
    "tag_stretch": {"tr": "Zorlama", "en": "Stretch"},

    "city_unspecified": {"tr": "Belirtilmemiş", "en": "Unspecified"},

    "footer_text": {"tr": "İş Başvuru Paneli · çok kullanıcılı", "en": "Job Application Tracker · multi-user"},

    "confirm_delete": {
        "tr": "Bu başvuruyu silmek istediğine emin misin?",
        "en": "Are you sure you want to delete this application?",
    },

    "theme_toggle": {"tr": "Tema", "en": "Theme"},

    "login_heading": {"tr": "Giriş yap", "en": "Log in"},
    "login_username": {"tr": "Kullanıcı adı", "en": "Username"},
    "login_password": {"tr": "Şifre", "en": "Password"},
    "login_submit": {"tr": "Giriş yap", "en": "Log in"},
    "login_no_account": {"tr": "Hesabın yok mu?", "en": "No account yet?"},
    "login_register_link": {"tr": "Kayıt ol", "en": "Register"},
    "login_error": {"tr": "Kullanıcı adı veya şifre hatalı.", "en": "Invalid username or password."},

    "register_heading": {"tr": "Kayıt ol", "en": "Register"},
    "register_username": {"tr": "Kullanıcı adı", "en": "Username"},
    "register_password": {"tr": "Şifre", "en": "Password"},
    "register_password_hint": {
        "tr": "En az 6 karakter.",
        "en": "At least 6 characters.",
    },
    "register_submit": {"tr": "Hesap oluştur", "en": "Create account"},
    "register_have_account": {"tr": "Zaten hesabın var mı?", "en": "Already have an account?"},
    "register_login_link": {"tr": "Giriş yap", "en": "Log in"},
    "register_error_exists": {
        "tr": "Bu kullanıcı adı zaten alınmış.",
        "en": "That username is already taken.",
    },
    "register_error_short_password": {
        "tr": "Şifre en az 6 karakter olmalı.",
        "en": "Password must be at least 6 characters.",
    },
    "register_error_required": {
        "tr": "Kullanıcı adı ve şifre gerekli.",
        "en": "Username and password are required.",
    },
    "register_success": {
        "tr": "Hesap oluşturuldu, giriş yapabilirsin.",
        "en": "Account created, you can log in now.",
    },
}


def t(key: str) -> str:
    """Translate ``key`` into the current request's language (tr/en)."""
    entry = TRANSLATIONS.get(key, {})
    return entry.get(g.get("lang", "tr"), key)


@app.before_request
def _set_language() -> None:
    lang = request.cookies.get("lang", "tr")
    g.lang = lang if lang in ("tr", "en") else "tr"


@app.context_processor
def _inject_i18n():
    return {"t": t, "lang": g.get("lang", "tr")}


@app.route("/lang/<lang_code>")
def set_lang(lang_code: str):
    if lang_code not in ("tr", "en"):
        lang_code = "tr"
    target = request.referrer or url_for("index")
    resp = redirect(target)
    resp.set_cookie("lang", lang_code, max_age=60 * 60 * 24 * 365)
    return resp


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------


@app.route("/register", methods=["GET", "POST"])
def register():
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        if not username or not password:
            error = t("register_error_required")
        elif len(password) < MIN_PASSWORD_LENGTH:
            error = t("register_error_short_password")
        else:
            conn = get_db()
            try:
                existing = conn.execute(
                    "SELECT id FROM users WHERE username = ?", (username,)
                ).fetchone()
                if existing is not None:
                    error = t("register_error_exists")
                else:
                    try:
                        conn.execute(
                            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                            (
                                username,
                                generate_password_hash(password),
                                datetime.utcnow().isoformat(),
                            ),
                        )
                        conn.commit()
                    except sqlite3.IntegrityError:
                        error = t("register_error_exists")
                    else:
                        return redirect(url_for("login", registered=1))
            finally:
                conn.close()

    return render_template("register.html", error=error)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        conn = get_db()
        try:
            user = conn.execute(
                "SELECT id, password_hash FROM users WHERE username = ?", (username,)
            ).fetchone()
        finally:
            conn.close()

        if user is not None and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["username"] = username
            return redirect(url_for("index"))
        error = t("login_error")

    just_registered = request.args.get("registered") == "1"
    return render_template("login.html", error=error, just_registered=just_registered)


@app.route("/logout", methods=["POST", "GET"])
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Main dashboard (per-user)
# ---------------------------------------------------------------------------


@app.route("/")
@login_required
def index():
    conn = get_db()
    try:
        applications = tracker.list_applications(conn, user_id=g.user_id)
        candidate_jobs = jobs.list_candidate_jobs(conn, user_id=g.user_id)
    finally:
        conn.close()
    return render_template(
        "index.html",
        applications=applications,
        candidate_jobs=candidate_jobs,
        username=session.get("username"),
        valid_channels=tracker.VALID_CHANNELS,
        valid_statuses=tracker.VALID_STATUSES,
    )


@app.route("/applications", methods=["POST"])
@login_required
def create_application():
    conn = get_db()
    try:
        tracker.add_application(
            conn,
            company=request.form.get("company", ""),
            position=request.form.get("position", ""),
            applied_date=request.form.get("applied_date", ""),
            channel=request.form.get("channel", ""),
            note=request.form.get("note") or None,
            user_id=g.user_id,
        )
    except ValueError:
        # Keep this simple: a bad manual entry just redirects back without
        # saving, rather than crashing the page.
        pass
    finally:
        conn.close()
    return redirect(url_for("index"))


@app.route("/applications/<int:app_id>/status", methods=["POST"])
@login_required
def set_application_status(app_id: int):
    new_status = request.form.get("status", "")
    conn = get_db()
    try:
        tracker.update_status(conn, app_id, new_status, user_id=g.user_id)
    except ValueError:
        return Response("No such application.", 404)
    finally:
        conn.close()
    return redirect(url_for("index"))


@app.route("/applications/<int:app_id>/delete", methods=["POST"])
@login_required
def delete_application(app_id: int):
    conn = get_db()
    try:
        tracker.delete_application(conn, app_id, user_id=g.user_id)
    except ValueError:
        return Response("No such application.", 404)
    finally:
        conn.close()
    return redirect(url_for("index"))


@app.route("/apply/<int:candidate_id>", methods=["POST"])
@login_required
def apply(candidate_id: int):
    conn = get_db()
    try:
        jobs.mark_candidate_applied(conn, candidate_id, user_id=g.user_id)
    except ValueError:
        return Response("No such candidate job.", 404)
    finally:
        conn.close()
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# Admin endpoints (HTTP Basic Auth) -- used by the external Gmail script
# ---------------------------------------------------------------------------


@app.route("/admin/refresh-jobs", methods=["POST"])
@require_admin_auth
def refresh_jobs():
    """Replace the owner's candidate jobs with a fresh batch.

    Expects a JSON body: {"jobs": [{"company", "position", "city", "link",
    "reason", "tag"}, ...]}. Clears the owner's existing candidate_jobs rows
    and inserts the new batch, scoped to the owner account. Any row that
    fails validation (e.g. bad tag or link) aborts the whole request before
    anything is cleared, so a bad batch never leaves the table half-updated.
    """
    payload = request.get_json(silent=True) or {}
    new_jobs = payload.get("jobs")
    if not isinstance(new_jobs, list):
        return jsonify({"error": "Expected JSON body with a 'jobs' list."}), 400

    for job in new_jobs:
        if job.get("tag") not in jobs.VALID_TAGS:
            return jsonify({"error": f"Invalid tag in job {job}"}), 400
        link = job.get("link")
        if link and not link.lower().startswith(("http://", "https://")):
            return jsonify({"error": f"Invalid link in job {job}"}), 400

    owner_id = get_owner_user_id()
    conn = get_db()
    try:
        jobs.clear_candidate_jobs(conn, user_id=owner_id)
        for job in new_jobs:
            jobs.add_candidate_job(
                conn,
                company=job["company"],
                position=job["position"],
                city=job.get("city"),
                link=job.get("link"),
                reason=job.get("reason"),
                tag=job["tag"],
                user_id=owner_id,
            )
    finally:
        conn.close()
    return jsonify({"inserted": len(new_jobs)})


@app.route("/admin/add-applications", methods=["POST"])
@require_admin_auth
def add_applications():
    """Bulk-add applications already made elsewhere (e.g. found in email history).

    Expects a JSON body: {"applications": [{"company", "position",
    "applied_date", "channel", "note"}, ...]}. Each one goes through
    tracker.add_application scoped to the owner account, so the same
    validation (date format, channel, default status "sent") applies. Does
    not clear existing rows.
    """
    payload = request.get_json(silent=True) or {}
    new_apps = payload.get("applications")
    if not isinstance(new_apps, list):
        return jsonify({"error": "Expected JSON body with an 'applications' list."}), 400

    owner_id = get_owner_user_id()
    conn = get_db()
    try:
        inserted_ids = []
        for a in new_apps:
            inserted_ids.append(
                tracker.add_application(
                    conn,
                    company=a["company"],
                    position=a["position"],
                    applied_date=a["applied_date"],
                    channel=a["channel"],
                    note=a.get("note"),
                    user_id=owner_id,
                )
            )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        conn.close()
    return jsonify({"inserted": len(inserted_ids)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
