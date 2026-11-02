"""Flask web dashboard for the job application tracker.

Reuses ``tracker.py``'s ``get_connection``/``applications`` table and
``jobs.py``'s ``candidate_jobs`` table. Meant to be deployed to Railway and
reached only by the owner through a password-protected link (HTTP Basic
Auth), never left open to the public.

Environment variables (read via ``os.environ``, never hardcoded):

- ``PANEL_USERNAME`` / ``PANEL_PASSWORD`` -- required. The app refuses to
  start if either is missing, rather than silently serving without auth.
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

from flask import Flask, Response, jsonify, redirect, render_template_string, request

import jobs
import tracker

DB_PATH = os.environ.get("DB_PATH", tracker.DEFAULT_DB_PATH)

PANEL_USERNAME = os.environ.get("PANEL_USERNAME")
PANEL_PASSWORD = os.environ.get("PANEL_PASSWORD")

if not PANEL_USERNAME or not PANEL_PASSWORD:
    raise RuntimeError(
        "PANEL_USERNAME and PANEL_PASSWORD environment variables must both "
        "be set before starting the dashboard. Refusing to start without "
        "them, since that would leave the panel unauthenticated."
    )

app = Flask(__name__)


def get_db() -> sqlite3.Connection:
    """Open a connection to the shared applications database.

    Creates/initializes both the ``applications`` table (via
    ``tracker.get_connection``) and the ``candidate_jobs`` table.
    """
    conn = tracker.get_connection(DB_PATH)
    jobs.init_candidate_jobs(conn)
    return conn


def _check_credentials(username: str | None, password: str | None) -> bool:
    if username is None or password is None:
        return False
    return hmac.compare_digest(username, PANEL_USERNAME) and hmac.compare_digest(
        password, PANEL_PASSWORD
    )


def require_auth(view):
    """Decorator enforcing HTTP Basic Auth on every decorated route."""

    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        auth = request.authorization
        if not auth or not _check_credentials(auth.username, auth.password):
            return Response(
                "Authentication required.",
                401,
                {"WWW-Authenticate": 'Basic realm="Job Tracker"'},
            )
        return view(*args, **kwargs)

    return wrapped


PAGE_TEMPLATE = """
<!doctype html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>İş Başvuru Paneli</title>
<style>
  :root {
    color-scheme: light dark;
    --bg: #f5f6f8;
    --card-bg: #ffffff;
    --text: #1b1f24;
    --muted: #666;
    --border: #e2e4e8;
    --accent: #2563eb;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    padding: 1rem;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
  }
  h1 { font-size: 1.4rem; margin: 0 0 1rem; }
  h2 { font-size: 1.1rem; margin: 1.5rem 0 0.75rem; }
  .card {
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 1rem;
    margin-bottom: 1rem;
  }
  .table-wrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; min-width: 560px; }
  th, td {
    text-align: left;
    padding: 0.5rem 0.6rem;
    border-bottom: 1px solid var(--border);
    font-size: 0.9rem;
    white-space: nowrap;
  }
  th { color: var(--muted); font-weight: 600; }
  .job-list { display: flex; flex-direction: column; gap: 0.75rem; }
  .job-card {
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 1rem;
  }
  .job-card h3 { margin: 0 0 0.25rem; font-size: 1rem; }
  .job-meta { color: var(--muted); font-size: 0.85rem; margin-bottom: 0.5rem; }
  .job-reason { font-size: 0.9rem; margin-bottom: 0.75rem; }
  .job-actions { display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; flex-wrap: wrap; }
  .badge {
    display: inline-block;
    padding: 0.2rem 0.6rem;
    border-radius: 999px;
    font-size: 0.75rem;
    font-weight: 600;
    color: #fff;
  }
  .badge-cok-uygun { background: #15803d; }
  .badge-uygun { background: #ca8a04; }
  .badge-zorlama { background: #b91c1c; }
  a { color: var(--accent); word-break: break-all; }
  button {
    background: var(--accent);
    color: #fff;
    border: none;
    border-radius: 6px;
    padding: 0.5rem 0.9rem;
    font-size: 0.9rem;
    cursor: pointer;
  }
  button:hover { opacity: 0.9; }
  .empty { color: var(--muted); font-style: italic; }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #15171a;
      --card-bg: #1f2227;
      --text: #e8e9ea;
      --muted: #9aa0a6;
      --border: #2d3036;
    }
  }
</style>
</head>
<body>
  <h1>İş Başvuru Paneli</h1>

  <h2>Başvurularım</h2>
  <div class="card table-wrap">
    {% if applications %}
    <table>
      <thead>
        <tr>
          <th>Şirket</th>
          <th>Pozisyon</th>
          <th>Başvuru Tarihi</th>
          <th>Kanal</th>
          <th>Durum</th>
          <th>Not</th>
        </tr>
      </thead>
      <tbody>
        {% for a in applications %}
        <tr>
          <td>{{ a['company'] }}</td>
          <td>{{ a['position'] }}</td>
          <td>{{ a['applied_date'] }}</td>
          <td>{{ a['channel'] }}</td>
          <td>{{ a['status'] }}</td>
          <td>{{ a['note'] or '' }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
    {% else %}
    <p class="empty">Henüz başvuru yok.</p>
    {% endif %}
  </div>

  <h2>Başvurabileceğim ilanlar</h2>
  <div class="job-list">
    {% for job in candidate_jobs %}
    <div class="job-card">
      <h3>{{ job['company'] }} &mdash; {{ job['position'] }}</h3>
      <div class="job-meta">
        {{ job['city'] or '-' }}
        {% if job['link'] %} &middot; <a href="{{ job['link'] }}" target="_blank" rel="noopener">İlana git</a>{% endif %}
      </div>
      <div class="job-reason">{{ job['reason'] or '' }}</div>
      <div class="job-actions">
        {% if job['tag'] == 'Çok uygun' %}
          <span class="badge badge-cok-uygun">Çok uygun</span>
        {% elif job['tag'] == 'Uygun' %}
          <span class="badge badge-uygun">Uygun</span>
        {% else %}
          <span class="badge badge-zorlama">Zorlama</span>
        {% endif %}
        <form method="post" action="{{ url_for('apply', candidate_id=job['id']) }}">
          <button type="submit">Başvurdum</button>
        </form>
      </div>
    </div>
    {% else %}
    <p class="empty">Şu an aday ilan yok.</p>
    {% endfor %}
  </div>
</body>
</html>
"""


@app.route("/")
@require_auth
def index():
    conn = get_db()
    try:
        applications = tracker.list_applications(conn)
        candidate_jobs = jobs.list_candidate_jobs(conn)
    finally:
        conn.close()
    return render_template_string(
        PAGE_TEMPLATE, applications=applications, candidate_jobs=candidate_jobs
    )


@app.route("/apply/<int:candidate_id>", methods=["POST"])
@require_auth
def apply(candidate_id: int):
    conn = get_db()
    try:
        jobs.mark_candidate_applied(conn, candidate_id)
    except ValueError:
        return Response("No such candidate job.", 404)
    finally:
        conn.close()
    return redirect("/")


@app.route("/admin/refresh-jobs", methods=["POST"])
@require_auth
def refresh_jobs():
    """Replace all candidate jobs with a fresh batch (used by 'ilanları güncelle').

    Expects a JSON body: {"jobs": [{"company", "position", "city", "link",
    "reason", "tag"}, ...]}. Clears the existing candidate_jobs table and
    inserts the new batch. Any row that fails validation (e.g. bad tag or
    link) aborts the whole request before anything is cleared, so a bad
    batch never leaves the table half-updated.
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

    conn = get_db()
    try:
        jobs.clear_candidate_jobs(conn)
        for job in new_jobs:
            jobs.add_candidate_job(
                conn,
                company=job["company"],
                position=job["position"],
                city=job.get("city"),
                link=job.get("link"),
                reason=job.get("reason"),
                tag=job["tag"],
            )
    finally:
        conn.close()
    return jsonify({"inserted": len(new_jobs)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
