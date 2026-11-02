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
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  :root {
    color-scheme: light dark;
    --bg: #f1f2f8;
    --bg-grad: radial-gradient(1200px 500px at 10% -10%, #eef0ff 0%, transparent 60%),
               radial-gradient(900px 500px at 100% 0%, #fff0f6 0%, transparent 55%);
    --surface: #ffffff;
    --surface-2: #f8f8fc;
    --text: #15161d;
    --muted: #6b6f80;
    --border: #e7e8f1;
    --accent: #5b5bf5;
    --accent-2: #8a5bf5;
    --accent-ink: #ffffff;
    --shadow: 0 1px 2px rgba(20, 20, 40, 0.04), 0 8px 24px -8px rgba(20, 20, 40, 0.10);
    --shadow-hover: 0 4px 10px rgba(20, 20, 40, 0.06), 0 16px 32px -12px rgba(20, 20, 40, 0.16);
    --radius: 18px;
    --ok: #0f9d58;
    --warn: #d97706;
    --bad: #dc2626;
    --info: #5b5bf5;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #0e0f14;
      --bg-grad: radial-gradient(1200px 500px at 10% -10%, #1a1b33 0%, transparent 60%),
                 radial-gradient(900px 500px at 100% 0%, #2a1a2b 0%, transparent 55%);
      --surface: #171821;
      --surface-2: #1d1f2b;
      --text: #eef0f8;
      --muted: #9a9db1;
      --border: #2a2c3a;
      --accent: #7c7bff;
      --accent-2: #b07cff;
      --accent-ink: #0e0f14;
      --shadow: 0 1px 2px rgba(0,0,0,0.3), 0 8px 24px -8px rgba(0,0,0,0.5);
      --shadow-hover: 0 4px 10px rgba(0,0,0,0.35), 0 16px 32px -12px rgba(0,0,0,0.6);
    }
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; background: var(--bg); }
  body {
    background-image: var(--bg-grad);
    font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    color: var(--text);
    -webkit-font-smoothing: antialiased;
  }
  .wrap { max-width: 980px; margin: 0 auto; padding: 1.5rem 1.1rem 4rem; }
  header.top {
    display: flex;
    align-items: center;
    gap: 0.9rem;
    margin-bottom: 1.6rem;
  }
  .logo {
    width: 44px; height: 44px; border-radius: 13px;
    background: linear-gradient(135deg, var(--accent), var(--accent-2));
    display: flex; align-items: center; justify-content: center;
    font-size: 1.3rem; flex-shrink: 0;
    box-shadow: var(--shadow);
  }
  header.top h1 { font-size: 1.3rem; font-weight: 800; margin: 0; letter-spacing: -0.02em; }
  header.top p { margin: 0.15rem 0 0; color: var(--muted); font-size: 0.86rem; }

  .stats-row { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.7rem; margin-bottom: 2rem; }
  .stat {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 0.9rem 1rem;
    box-shadow: var(--shadow);
  }
  .stat .num { font-size: 1.5rem; font-weight: 800; letter-spacing: -0.02em; }
  .stat .label { font-size: 0.74rem; color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; margin-top: 0.1rem; }
  .stat.accent-ok .num { color: var(--ok); }
  .stat.accent-warn .num { color: var(--warn); }

  section { margin-bottom: 2.2rem; }
  .section-head { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 0.9rem; }
  .section-head h2 { font-size: 1.05rem; font-weight: 700; margin: 0; letter-spacing: -0.01em; }
  .section-head .count { color: var(--muted); font-size: 0.82rem; font-weight: 600; }

  .panel {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    overflow: hidden;
  }
  .table-wrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; min-width: 640px; }
  th, td {
    text-align: left;
    padding: 0.7rem 1rem;
    border-bottom: 1px solid var(--border);
    font-size: 0.86rem;
    white-space: nowrap;
  }
  tbody tr:last-child td { border-bottom: none; }
  tbody tr { transition: background 0.15s; }
  tbody tr:hover { background: var(--surface-2); }
  th { color: var(--muted); font-weight: 600; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em; background: var(--surface-2); }
  td.company { font-weight: 600; }
  td.note { white-space: normal; color: var(--muted); font-size: 0.82rem; max-width: 220px; }

  .pill {
    display: inline-flex; align-items: center; gap: 0.35rem;
    padding: 0.28rem 0.7rem;
    border-radius: 999px;
    font-size: 0.72rem;
    font-weight: 700;
  }
  .pill::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
  .pill-sent { background: #eef0ff; color: #4338ca; }
  .pill-followed-up { background: #fff7ed; color: #c2410c; }
  .pill-interview { background: #f3e8ff; color: #7e22ce; }
  .pill-offer { background: #ecfdf3; color: #0f7d44; }
  .pill-rejected { background: #fef2f2; color: #b91c1c; }
  @media (prefers-color-scheme: dark) {
    .pill-sent { background: #23244a; color: #b3b2ff; }
    .pill-followed-up { background: #3a2a14; color: #ffbd7a; }
    .pill-interview { background: #33223f; color: #dcb4ff; }
    .pill-offer { background: #113626; color: #7cf0ae; }
    .pill-rejected { background: #3a1818; color: #ff9b9b; }
  }

  .job-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 0.9rem; }
  @media (max-width: 680px) { .job-grid { grid-template-columns: 1fr; } }
  .job-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 1.1rem;
    box-shadow: var(--shadow);
    transition: box-shadow 0.18s, transform 0.18s;
    display: flex; flex-direction: column; gap: 0.6rem;
  }
  .job-card:hover { box-shadow: var(--shadow-hover); transform: translateY(-2px); }
  .job-card-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 0.6rem; }
  .job-card h3 { margin: 0; font-size: 0.98rem; font-weight: 700; line-height: 1.3; }
  .job-card h3 .pos { display: block; color: var(--muted); font-weight: 500; font-size: 0.82rem; margin-top: 0.15rem; }
  .job-meta { color: var(--muted); font-size: 0.8rem; display: flex; align-items: center; gap: 0.4rem; flex-wrap: wrap; }
  .job-meta .dot { opacity: 0.5; }
  .job-reason { font-size: 0.85rem; line-height: 1.45; color: var(--text); opacity: 0.85; flex-grow: 1; }
  .job-actions { display: flex; align-items: center; justify-content: space-between; gap: 0.6rem; margin-top: 0.2rem; }

  .tag {
    display: inline-flex; align-items: center;
    padding: 0.25rem 0.65rem;
    border-radius: 999px;
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.01em;
    white-space: nowrap;
  }
  .tag-cok-uygun { background: linear-gradient(135deg, #0f9d58, #35c17e); color: #fff; }
  .tag-uygun { background: #fff3da; color: #92600a; }
  .tag-zorlama { background: #fde8e8; color: #a32424; }
  @media (prefers-color-scheme: dark) {
    .tag-uygun { background: #3a2c10; color: #ffcf7a; }
    .tag-zorlama { background: #3a1a1a; color: #ff9b9b; }
  }

  a.link-btn {
    color: var(--accent); text-decoration: none; font-weight: 600; font-size: 0.82rem;
    display: inline-flex; align-items: center; gap: 0.25rem;
  }
  a.link-btn:hover { text-decoration: underline; }

  button.apply-btn {
    background: linear-gradient(135deg, var(--accent), var(--accent-2));
    color: #fff;
    border: none;
    border-radius: 10px;
    padding: 0.5rem 1rem;
    font-size: 0.82rem;
    font-weight: 700;
    cursor: pointer;
    box-shadow: 0 4px 12px -4px rgba(91, 91, 245, 0.5);
    transition: filter 0.15s, transform 0.1s;
  }
  button.apply-btn:hover { filter: brightness(1.08); }
  button.apply-btn:active { transform: scale(0.97); }

  .empty {
    color: var(--muted);
    font-size: 0.88rem;
    padding: 2rem 1rem;
    text-align: center;
  }
  footer.foot { text-align: center; color: var(--muted); font-size: 0.75rem; margin-top: 2.5rem; }
</style>
</head>
<body>
<div class="wrap">
  <header class="top">
    <div class="logo">💼</div>
    <div>
      <h1>İş Başvuru Paneli</h1>
      <p>Başvurularını takip et, yeni ilanları değerlendir.</p>
    </div>
  </header>

  <div class="stats-row">
    <div class="stat">
      <div class="num">{{ applications|length }}</div>
      <div class="label">Başvuru</div>
    </div>
    <div class="stat accent-ok">
      <div class="num">{{ candidate_jobs|selectattr('tag', 'equalto', 'Çok uygun')|list|length }}</div>
      <div class="label">Çok uygun ilan</div>
    </div>
    <div class="stat accent-warn">
      <div class="num">{{ candidate_jobs|length }}</div>
      <div class="label">Aday ilan</div>
    </div>
  </div>

  <section>
    <div class="section-head">
      <h2>Başvurularım</h2>
      <span class="count">{{ applications|length }} kayıt</span>
    </div>
    <div class="panel table-wrap">
      {% if applications %}
      <table>
        <thead>
          <tr>
            <th>Şirket</th>
            <th>Pozisyon</th>
            <th>Tarih</th>
            <th>Kanal</th>
            <th>Durum</th>
            <th>Not</th>
          </tr>
        </thead>
        <tbody>
          {% for a in applications %}
          <tr>
            <td class="company">{{ a['company'] }}</td>
            <td>{{ a['position'] }}</td>
            <td>{{ a['applied_date'] }}</td>
            <td>{{ a['channel'] }}</td>
            <td>
              {% set s = a['status'] %}
              <span class="pill pill-{{ s }}">
                {% if s == 'sent' %}Gönderildi
                {% elif s == 'followed-up' %}Takip edildi
                {% elif s == 'interview' %}Mülakat
                {% elif s == 'offer' %}Teklif
                {% elif s == 'rejected' %}Olumsuz
                {% else %}{{ s }}
                {% endif %}
              </span>
            </td>
            <td class="note">{{ a['note'] or '' }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
      {% else %}
      <p class="empty">Henüz başvuru yok.</p>
      {% endif %}
    </div>
  </section>

  <section>
    <div class="section-head">
      <h2>Başvurabileceğim ilanlar</h2>
      <span class="count">{{ candidate_jobs|length }} ilan</span>
    </div>
    {% if candidate_jobs %}
    <div class="job-grid">
      {% for job in candidate_jobs %}
      <div class="job-card">
        <div class="job-card-top">
          <h3>{{ job['company'] }}<span class="pos">{{ job['position'] }}</span></h3>
          {% if job['tag'] == 'Çok uygun' %}
            <span class="tag tag-cok-uygun">Çok uygun</span>
          {% elif job['tag'] == 'Uygun' %}
            <span class="tag tag-uygun">Uygun</span>
          {% else %}
            <span class="tag tag-zorlama">Zorlama</span>
          {% endif %}
        </div>
        <div class="job-meta">
          <span>📍 {{ job['city'] or 'Belirtilmemiş' }}</span>
          {% if job['link'] %}
          <span class="dot">&middot;</span>
          <a class="link-btn" href="{{ job['link'] }}" target="_blank" rel="noopener">İlanı gör ↗</a>
          {% endif %}
        </div>
        <div class="job-reason">{{ job['reason'] or '' }}</div>
        <div class="job-actions">
          <form method="post" action="{{ url_for('apply', candidate_id=job['id']) }}">
            <button type="submit" class="apply-btn">Başvurdum ✓</button>
          </form>
        </div>
      </div>
      {% endfor %}
    </div>
    {% else %}
    <div class="panel"><p class="empty">Şu an aday ilan yok. "İlanları güncelle" diyerek yeni ilan tarat.</p></div>
    {% endif %}
  </section>

  <footer class="foot">İş Başvuru Paneli &middot; sadece sana özel</footer>
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


@app.route("/admin/add-applications", methods=["POST"])
@require_auth
def add_applications():
    """Bulk-add applications already made elsewhere (e.g. found in email history).

    Expects a JSON body: {"applications": [{"company", "position",
    "applied_date", "channel", "note"}, ...]}. Each one goes through
    tracker.add_application, so the same validation (date format, channel,
    default status "sent") applies. Does not clear existing rows.
    """
    payload = request.get_json(silent=True) or {}
    new_apps = payload.get("applications")
    if not isinstance(new_apps, list):
        return jsonify({"error": "Expected JSON body with an 'applications' list."}), 400

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
                )
            )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        conn.close()
    return jsonify({"inserted": len(inserted_ids)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
