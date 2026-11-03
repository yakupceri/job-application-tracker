"""Flask web dashboard for the job application tracker.

Multi-user, SQLAlchemy-backed rewrite. Each registered user sees and edits
only their own applications and candidate jobs; every query that touches
``applications``/``candidate_jobs`` is scoped by ``user_id`` (see
``tests/test_isolation.py`` for the cross-account isolation tests).

Built as an app factory (``create_app``) so tests can spin up independent
instances against their own temporary database, while ``app = create_app()``
at the bottom still gives ``gunicorn app:app`` / ``python app.py`` the same
module-level object they expect (see ``Procfile``).

Environment variables (read via ``os.environ``, never hardcoded, except for
the one-time ``OWNER_EMAIL`` default below):

- ``SECRET_KEY`` -- required. Signs the session cookie.
- ``PANEL_USERNAME`` / ``PANEL_PASSWORD`` -- required. HTTP Basic Auth for
  ``/admin/*``, and the username used to seed the pre-existing owner account
  on first startup (see ``_bootstrap_owner_account``).
- ``DATABASE_URL`` -- optional. A full SQLAlchemy URL, e.g.
  ``postgresql://user:pass@host/db`` for production. Falls back to a local
  SQLite file (``DB_PATH``, or ``tracker.DEFAULT_DB_PATH``) in development.
- ``DB_PATH`` -- optional, only used when ``DATABASE_URL`` is unset.
- ``OWNER_EMAIL`` / ``OWNER_FULL_NAME`` / ``OWNER_TARGET_POSITION`` /
  ``OWNER_CITY`` -- optional, used only to backfill the pre-existing owner
  account's new profile fields the first time it's created under this
  schema. ``OWNER_EMAIL`` defaults to the project owner's own address so
  existing data lands on a real, usable account without extra setup.
- ``MAIL_*`` -- optional SMTP settings (see ``email_utils.py``). Email
  verification and "forgot password" are both just disabled in the UI when
  ``MAIL_SERVER`` isn't set; nothing else about the app depends on them.

Run locally with ``python app.py``, or in production with
``gunicorn app:app`` (see ``Procfile``). Database schema changes go through
Alembic (``flask db migrate`` / ``flask db upgrade``), not ad-hoc code here.
"""

from __future__ import annotations

import csv
import functools
import hmac
import io
import os
from datetime import datetime, timedelta

from flask import (
    Blueprint,
    Flask,
    Response,
    current_app,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from flask_migrate import upgrade as migrate_upgrade

import email_utils
import jobs
import tracker
from extensions import db, login_manager, migrate
from models import User, Application, CandidateJob

bp = Blueprint("main", __name__)


# ---------------------------------------------------------------------------
# Login manager wiring (module-level: shared by every app instance)
# ---------------------------------------------------------------------------


@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    return db.session.get(User, int(user_id))


# ---------------------------------------------------------------------------
# Admin (HTTP Basic Auth) -- used by the external Gmail-refresh script
# ---------------------------------------------------------------------------


def _check_admin_credentials(username: str | None, password: str | None) -> bool:
    if username is None or password is None:
        return False
    expected_user = current_app.config["PANEL_USERNAME"]
    expected_pass = current_app.config["PANEL_PASSWORD"]
    return hmac.compare_digest(username, expected_user) and hmac.compare_digest(
        password, expected_pass
    )


def require_admin_auth(view):
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


def _get_owner_user() -> User:
    return User.query.filter_by(username=current_app.config["PANEL_USERNAME"]).one()


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
    "nav_profile": {"tr": "Profil", "en": "Profile"},

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
    "login_identifier": {"tr": "Kullanıcı adı veya e-posta", "en": "Username or email"},
    "login_password": {"tr": "Şifre", "en": "Password"},
    "login_remember": {"tr": "Beni hatırla", "en": "Remember me"},
    "login_submit": {"tr": "Giriş yap", "en": "Log in"},
    "login_no_account": {"tr": "Hesabın yok mu?", "en": "No account yet?"},
    "login_register_link": {"tr": "Kayıt ol", "en": "Register"},
    "login_forgot_link": {"tr": "Şifremi unuttum", "en": "Forgot password?"},
    "login_error": {"tr": "Kullanıcı adı/e-posta veya şifre hatalı.", "en": "Invalid username/email or password."},
    "login_locked": {
        "tr": "Çok fazla başarısız giriş denemesi. Lütfen biraz sonra tekrar deneyin.",
        "en": "Too many failed login attempts. Please try again later.",
    },

    "register_heading": {"tr": "Kayıt ol", "en": "Register"},
    "register_full_name": {"tr": "Ad Soyad", "en": "Full name"},
    "register_email": {"tr": "E-posta", "en": "Email"},
    "register_username": {"tr": "Kullanıcı adı", "en": "Username"},
    "register_password": {"tr": "Şifre", "en": "Password"},
    "register_password2": {"tr": "Şifre (tekrar)", "en": "Confirm password"},
    "register_target_position": {"tr": "Hedef pozisyon", "en": "Target position"},
    "register_target_position_placeholder": {"tr": "ör. backend, AI, web", "en": "e.g. backend, AI, web"},
    "register_city": {"tr": "Şehir tercihi", "en": "City preference"},
    "register_password_hint": {
        "tr": "En az 8 karakter, harf ve rakam içermeli.",
        "en": "At least 8 characters, with letters and digits.",
    },
    "register_strength_weak": {"tr": "Zayıf", "en": "Weak"},
    "register_strength_medium": {"tr": "Orta", "en": "Medium"},
    "register_strength_strong": {"tr": "Güçlü", "en": "Strong"},
    "register_kvkk_label": {
        "tr": "KVKK Aydınlatma Metni'ni okudum, kabul ediyorum.",
        "en": "I have read and accept the Privacy Notice (KVKK).",
    },
    "register_kvkk_link": {"tr": "Aydınlatma metnini oku", "en": "Read the privacy notice"},
    "register_submit": {"tr": "Hesap oluştur", "en": "Create account"},
    "register_have_account": {"tr": "Zaten hesabın var mı?", "en": "Already have an account?"},
    "register_login_link": {"tr": "Giriş yap", "en": "Log in"},
    "register_error_exists_username": {
        "tr": "Bu kullanıcı adı zaten alınmış.",
        "en": "That username is already taken.",
    },
    "register_error_exists_email": {
        "tr": "Bu e-posta adresi zaten kayıtlı.",
        "en": "That email address is already registered.",
    },
    "register_error_password_too_short": {
        "tr": "Şifre en az 8 karakter olmalı.",
        "en": "Password must be at least 8 characters.",
    },
    "register_error_password_weak": {
        "tr": "Şifre en az bir harf ve bir rakam içermeli.",
        "en": "Password must contain at least one letter and one digit.",
    },
    "register_error_password_mismatch": {
        "tr": "Şifreler eşleşmiyor.",
        "en": "Passwords do not match.",
    },
    "register_error_required": {
        "tr": "Ad soyad, e-posta, kullanıcı adı ve şifre gerekli.",
        "en": "Full name, email, username and password are required.",
    },
    "register_error_invalid_email": {
        "tr": "Geçerli bir e-posta adresi gir.",
        "en": "Enter a valid email address.",
    },
    "register_error_kvkk": {
        "tr": "Devam etmek için KVKK Aydınlatma Metni'ni onaylaman gerekiyor.",
        "en": "You must accept the Privacy Notice (KVKK) to continue.",
    },
    "register_success": {
        "tr": "Hesap oluşturuldu, giriş yapabilirsin.",
        "en": "Account created, you can log in now.",
    },

    "kvkk_notice_heading": {"tr": "KVKK Aydınlatma Metni", "en": "Privacy Notice"},
    "kvkk_notice_body": {
        "tr": (
            "Kayıt formunda verdiğin ad soyad, e-posta, kullanıcı adı, hedef "
            "pozisyon ve şehir tercihi bilgileri, yalnızca bu uygulamada sana "
            "ait başvuru ve ilan verilerini göstermek amacıyla saklanır. "
            "Şifren asla düz metin olarak tutulmaz; hash'lenerek saklanır. "
            "Verilerin profil sayfasından istediğin zaman güncellenebilir, "
            "CSV olarak indirilebilir veya tamamen silinebilir."
        ),
        "en": (
            "The full name, email, username, target position and city "
            "preference you provide at registration are stored only to show "
            "you your own application and job data inside this app. Your "
            "password is never stored in plain text; it is hashed. You can "
            "update, export as CSV, or permanently delete your data at any "
            "time from the profile page."
        ),
    },

    "forgot_heading": {"tr": "Şifremi unuttum", "en": "Forgot password"},
    "forgot_email": {"tr": "E-posta", "en": "Email"},
    "forgot_submit": {"tr": "Sıfırlama bağlantısı gönder", "en": "Send reset link"},
    "forgot_sent": {
        "tr": "Bu e-posta kayıtlıysa, şifre sıfırlama bağlantısı gönderildi.",
        "en": "If that email is registered, a reset link has been sent.",
    },
    "forgot_disabled": {
        "tr": "Bu özellik şu anda kapalı: sunucuda e-posta gönderimi (SMTP) ayarlanmamış. Lütfen uygulama yöneticisiyle iletişime geç.",
        "en": "This feature is currently disabled: the server has no SMTP email sending configured. Please contact the app administrator.",
    },
    "forgot_back_to_login": {"tr": "Girişe dön", "en": "Back to login"},

    "reset_heading": {"tr": "Yeni şifre belirle", "en": "Set a new password"},
    "reset_invalid": {
        "tr": "Bu bağlantı geçersiz veya süresi dolmuş. Yeniden şifre sıfırlama isteyebilirsin.",
        "en": "This link is invalid or has expired. You can request a new one.",
    },
    "reset_submit": {"tr": "Şifreyi güncelle", "en": "Update password"},
    "reset_success": {"tr": "Şifren güncellendi, giriş yapabilirsin.", "en": "Your password has been updated, you can log in now."},

    "profile_heading": {"tr": "Profil", "en": "Profile"},
    "profile_section_info": {"tr": "Bilgilerim", "en": "My info"},
    "profile_section_password": {"tr": "Şifre değiştir", "en": "Change password"},
    "profile_section_email": {"tr": "E-posta doğrulama", "en": "Email verification"},
    "profile_section_export": {"tr": "Verilerimi dışa aktar", "en": "Export my data"},
    "profile_section_danger": {"tr": "Hesabı sil", "en": "Delete account"},
    "profile_current_password": {"tr": "Mevcut şifre", "en": "Current password"},
    "profile_new_password": {"tr": "Yeni şifre", "en": "New password"},
    "profile_new_password2": {"tr": "Yeni şifre (tekrar)", "en": "Confirm new password"},
    "profile_save": {"tr": "Kaydet", "en": "Save"},
    "profile_update_success": {"tr": "Bilgilerin güncellendi.", "en": "Your info has been updated."},
    "profile_password_success": {"tr": "Şifren güncellendi.", "en": "Your password has been updated."},
    "profile_password_wrong_current": {"tr": "Mevcut şifre hatalı.", "en": "Current password is incorrect."},
    "profile_email_verified": {"tr": "E-posta doğrulandı ✓", "en": "Email verified ✓"},
    "profile_email_not_verified": {"tr": "E-posta doğrulanmadı", "en": "Email not verified"},
    "profile_email_disabled": {
        "tr": "E-posta doğrulama şu anda kapalı (SMTP ayarlanmamış). Hesabın normal şekilde çalışır.",
        "en": "Email verification is currently disabled (SMTP not configured). Your account works normally.",
    },
    "profile_resend_verification": {"tr": "Doğrulama e-postası gönder", "en": "Send verification email"},
    "profile_verification_sent": {"tr": "Doğrulama e-postası gönderildi.", "en": "Verification email sent."},
    "profile_export_applications": {"tr": "Başvurularımı indir (CSV)", "en": "Download my applications (CSV)"},
    "profile_export_jobs": {"tr": "İlanlarımı indir (CSV)", "en": "Download my candidate jobs (CSV)"},
    "profile_delete_warning": {
        "tr": "Hesabını ve tüm başvuru/ilan verilerini kalıcı olarak siler. Bu işlem geri alınamaz.",
        "en": "Permanently deletes your account and all your application/job data. This cannot be undone.",
    },
    "profile_delete_password_label": {"tr": "Onaylamak için şifreni gir", "en": "Enter your password to confirm"},
    "profile_delete_submit": {"tr": "Hesabımı kalıcı olarak sil", "en": "Permanently delete my account"},
    "profile_delete_wrong_password": {"tr": "Şifre hatalı, hesap silinmedi.", "en": "Incorrect password, account not deleted."},

    "verify_success": {"tr": "E-posta adresin doğrulandı.", "en": "Your email has been verified."},
    "verify_invalid": {
        "tr": "Bu doğrulama bağlantısı geçersiz veya süresi dolmuş.",
        "en": "This verification link is invalid or has expired.",
    },
}


def t(key: str) -> str:
    entry = TRANSLATIONS.get(key, {})
    return entry.get(g.get("lang", "tr"), key)


@bp.before_app_request
def _set_language() -> None:
    lang = request.cookies.get("lang", "tr")
    g.lang = lang if lang in ("tr", "en") else "tr"


@bp.app_context_processor
def _inject_i18n():
    return {
        "t": t,
        "lang": g.get("lang", "tr"),
        "mail_configured": email_utils.mail_is_configured(),
    }


@bp.route("/lang/<lang_code>")
def set_lang(lang_code: str):
    if lang_code not in ("tr", "en"):
        lang_code = "tr"
    target = request.referrer or url_for("main.index")
    resp = redirect(target)
    resp.set_cookie("lang", lang_code, max_age=60 * 60 * 24 * 365)
    return resp


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------


def _password_strength_errors(password: str, password2: str) -> str | None:
    if password != password2:
        return "register_error_password_mismatch"
    err = email_utils.password_error(password)
    if err == "too_short":
        return "register_error_password_too_short"
    if err == "needs_letter_and_digit":
        return "register_error_password_weak"
    return None


@bp.route("/register", methods=["GET", "POST"])
def register():
    error = None
    form = {
        "full_name": "",
        "email": "",
        "username": "",
        "target_position": "",
        "city_preference": "",
    }

    if request.method == "POST":
        full_name = (request.form.get("full_name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        password2 = request.form.get("password2") or ""
        target_position = (request.form.get("target_position") or "").strip()
        city_preference = (request.form.get("city_preference") or "").strip()
        kvkk_accepted = request.form.get("kvkk_accept") == "on"

        form.update(
            full_name=full_name,
            email=email,
            username=username,
            target_position=target_position,
            city_preference=city_preference,
        )

        if not full_name or not email or not username or not password:
            error = "register_error_required"
        elif "@" not in email or "." not in email.split("@")[-1]:
            error = "register_error_invalid_email"
        elif not kvkk_accepted:
            error = "register_error_kvkk"
        else:
            error = _password_strength_errors(password, password2)

        if error is None:
            if User.query.filter_by(username=username).first() is not None:
                error = "register_error_exists_username"
            elif User.query.filter_by(email=email).first() is not None:
                error = "register_error_exists_email"

        if error is None:
            user = User(
                full_name=full_name,
                email=email,
                username=username,
                target_position=target_position or None,
                city_preference=city_preference or None,
                kvkk_accepted_at=datetime.utcnow(),
                email_verified=not email_utils.mail_is_configured(),
            )
            user.set_password(password)
            db.session.add(user)
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
                error = "register_error_exists_username"
            else:
                if email_utils.mail_is_configured():
                    _send_verification_email(user)
                return redirect(url_for("main.login", registered=1))

    return render_template("register.html", error=error, form=form)


def _send_verification_email(user: User) -> None:
    token = email_utils.make_token(
        current_app.secret_key, user.id, "verify-email", user.email
    )
    link = request.url_root.rstrip("/") + url_for("main.verify_email", token=token)
    email_utils.send_email(
        user.email,
        "E-postanı doğrula / Verify your email",
        f"Hesabını doğrulamak için bu bağlantıya tıkla / Click to verify your account:\n{link}\n\n"
        "Bu bağlantı 24 saat geçerlidir. / This link is valid for 24 hours.",
    )


@bp.route("/verify-email/<token>")
def verify_email(token: str):
    user_id = email_utils.read_token(
        current_app.secret_key, token, "verify-email", email_utils.EMAIL_VERIFY_MAX_AGE, ""
    )
    # Verification tokens are bound to the email address, not a fixed
    # string, so re-check below once we know which user they claim to be.
    user = None
    if user_id is not None:
        user = db.session.get(User, user_id)
    if user is not None:
        user_id = email_utils.read_token(
            current_app.secret_key, token, "verify-email", email_utils.EMAIL_VERIFY_MAX_AGE, user.email
        )
    ok = user is not None and user_id == user.id
    if ok:
        user.email_verified = True
        db.session.commit()
    return render_template("message.html", ok=ok, message_key="verify_success" if ok else "verify_invalid")


@bp.route("/profile/resend-verification", methods=["POST"])
@login_required
def resend_verification():
    if email_utils.mail_is_configured() and not current_user.email_verified:
        _send_verification_email(current_user)
        session["verification_sent"] = True
    return redirect(url_for("main.profile"))


@bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        identifier = (request.form.get("identifier") or request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        remember = request.form.get("remember") == "on"

        user = User.query.filter(
            (User.username == identifier) | (User.email == identifier.lower())
        ).first()

        if user is not None and user.is_locked():
            error = "login_locked"
        elif user is not None and user.check_password(password):
            user.register_successful_login()
            db.session.commit()
            login_user(user, remember=remember, duration=timedelta(days=30))
            return redirect(url_for("main.index"))
        else:
            if user is not None:
                user.register_failed_login()
                db.session.commit()
            error = "login_error"

    just_registered = request.args.get("registered") == "1"
    return render_template("login.html", error=error, just_registered=just_registered)


@bp.route("/logout", methods=["POST", "GET"])
@login_required
def logout():
    logout_user()
    return redirect(url_for("main.login"))


@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    sent = False
    if request.method == "POST" and email_utils.mail_is_configured():
        email = (request.form.get("email") or "").strip().lower()
        user = User.query.filter_by(email=email).first()
        if user is not None:
            token = email_utils.make_token(
                current_app.secret_key, user.id, "reset-password", user.password_hash
            )
            link = request.url_root.rstrip("/") + url_for("main.reset_password", token=token)
            email_utils.send_email(
                user.email,
                "Şifre sıfırlama / Password reset",
                f"Şifreni sıfırlamak için bu bağlantıya tıkla / Click to reset your password:\n{link}\n\n"
                "Bu bağlantı 1 saat geçerlidir. / This link is valid for 1 hour.",
            )
        sent = True  # Always report success, regardless of whether the email existed.
    return render_template("forgot_password.html", sent=sent)


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token: str):
    # The token is bound to the user's *current* password hash, so it is
    # automatically invalidated the moment the password actually changes.
    error = None
    user = None
    for candidate in User.query.all():
        uid = email_utils.read_token(
            current_app.secret_key,
            token,
            "reset-password",
            email_utils.PASSWORD_RESET_MAX_AGE,
            candidate.password_hash,
        )
        if uid == candidate.id:
            user = candidate
            break

    if user is None:
        return render_template("reset_password.html", invalid=True)

    if request.method == "POST":
        password = request.form.get("password") or ""
        password2 = request.form.get("password2") or ""
        error = _password_strength_errors(password, password2)
        if error is None:
            user.set_password(password)
            user.register_successful_login()
            db.session.commit()
            return redirect(url_for("main.login"))

    return render_template("reset_password.html", invalid=False, error=error, token=token)


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


@bp.route("/profile", methods=["GET"])
@login_required
def profile():
    update_success = session.pop("profile_update_success", False)
    password_success = session.pop("profile_password_success", False)
    verification_sent = session.pop("verification_sent", False)
    return render_template(
        "profile.html",
        update_success=update_success,
        password_success=password_success,
        verification_sent=verification_sent,
        update_error=session.pop("profile_update_error", None),
        password_error=session.pop("profile_password_error", None),
        delete_error=session.pop("profile_delete_error", None),
    )


@bp.route("/profile/update", methods=["POST"])
@login_required
def profile_update():
    full_name = (request.form.get("full_name") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    target_position = (request.form.get("target_position") or "").strip()
    city_preference = (request.form.get("city_preference") or "").strip()

    if not full_name or not email:
        session["profile_update_error"] = "register_error_required"
    elif "@" not in email or "." not in email.split("@")[-1]:
        session["profile_update_error"] = "register_error_invalid_email"
    elif (
        email != current_user.email
        and User.query.filter(User.email == email, User.id != current_user.id).first() is not None
    ):
        session["profile_update_error"] = "register_error_exists_email"
    else:
        email_changed = email != current_user.email
        current_user.full_name = full_name
        current_user.email = email
        current_user.target_position = target_position or None
        current_user.city_preference = city_preference or None
        if email_changed:
            current_user.email_verified = not email_utils.mail_is_configured()
        db.session.commit()
        session["profile_update_success"] = True

    return redirect(url_for("main.profile"))


@bp.route("/profile/password", methods=["POST"])
@login_required
def profile_password():
    current_password = request.form.get("current_password") or ""
    new_password = request.form.get("new_password") or ""
    new_password2 = request.form.get("new_password2") or ""

    if not current_user.check_password(current_password):
        session["profile_password_error"] = "profile_password_wrong_current"
    else:
        error = _password_strength_errors(new_password, new_password2)
        if error is not None:
            session["profile_password_error"] = error
        else:
            current_user.set_password(new_password)
            db.session.commit()
            session["profile_password_success"] = True

    return redirect(url_for("main.profile"))


def _csv_response(filename: str, header: list[str], rows: list[list]) -> Response:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@bp.route("/profile/export/applications.csv")
@login_required
def profile_export_applications():
    rows = (
        Application.query.filter_by(user_id=current_user.id)
        .order_by(Application.applied_date.asc())
        .all()
    )
    return _csv_response(
        "basvurularim.csv",
        ["id", "company", "position", "applied_date", "channel", "status", "note"],
        [
            [r.id, r.company, r.position, r.applied_date, r.channel, r.status, r.note or ""]
            for r in rows
        ],
    )


@bp.route("/profile/export/jobs.csv")
@login_required
def profile_export_jobs():
    rows = (
        CandidateJob.query.filter_by(user_id=current_user.id)
        .order_by(CandidateJob.found_date.asc())
        .all()
    )
    return _csv_response(
        "ilanlarim.csv",
        ["id", "company", "position", "city", "link", "reason", "tag", "found_date"],
        [
            [r.id, r.company, r.position, r.city or "", r.link or "", r.reason or "", r.tag, r.found_date]
            for r in rows
        ],
    )


@bp.route("/profile/delete", methods=["POST"])
@login_required
def profile_delete():
    password = request.form.get("password") or ""
    if not current_user.check_password(password):
        session["profile_delete_error"] = "profile_delete_wrong_password"
        return redirect(url_for("main.profile"))

    user = db.session.get(User, current_user.id)
    logout_user()
    db.session.delete(user)
    db.session.commit()
    return redirect(url_for("main.login"))


# ---------------------------------------------------------------------------
# Main dashboard (per-user)
# ---------------------------------------------------------------------------


@bp.route("/")
@login_required
def index():
    applications = (
        Application.query.filter_by(user_id=current_user.id)
        .order_by(Application.applied_date.asc())
        .all()
    )
    tag_order = {tag: i for i, tag in enumerate(jobs.VALID_TAGS)}
    candidate_jobs = sorted(
        CandidateJob.query.filter_by(user_id=current_user.id).all(),
        key=lambda c: (tag_order.get(c.tag, 99), c.found_date),
    )
    return render_template(
        "index.html",
        applications=applications,
        candidate_jobs=candidate_jobs,
        username=current_user.username,
        valid_channels=tracker.VALID_CHANNELS,
        valid_statuses=tracker.VALID_STATUSES,
    )


@bp.route("/applications", methods=["POST"])
@login_required
def create_application():
    company = request.form.get("company", "")
    position = request.form.get("position", "")
    applied_date = request.form.get("applied_date", "")
    channel = request.form.get("channel", "")
    note = request.form.get("note") or None

    if (
        company
        and position
        and tracker.is_valid_date(applied_date)
        and channel in tracker.VALID_CHANNELS
    ):
        db.session.add(
            Application(
                company=company,
                position=position,
                applied_date=applied_date,
                channel=channel,
                note=note,
                status=tracker.DEFAULT_STATUS,
                user_id=current_user.id,
            )
        )
        db.session.commit()
    return redirect(url_for("main.index"))


@bp.route("/applications/<int:app_id>/status", methods=["POST"])
@login_required
def set_application_status(app_id: int):
    new_status = request.form.get("status", "")
    application = Application.query.filter_by(id=app_id, user_id=current_user.id).first()
    if application is None or new_status not in tracker.VALID_STATUSES:
        return Response("No such application.", 404)
    application.status = new_status
    db.session.commit()
    return redirect(url_for("main.index"))


@bp.route("/applications/<int:app_id>/delete", methods=["POST"])
@login_required
def delete_application(app_id: int):
    application = Application.query.filter_by(id=app_id, user_id=current_user.id).first()
    if application is None:
        return Response("No such application.", 404)
    db.session.delete(application)
    db.session.commit()
    return redirect(url_for("main.index"))


@bp.route("/apply/<int:candidate_id>", methods=["POST"])
@login_required
def apply(candidate_id: int):
    candidate = CandidateJob.query.filter_by(id=candidate_id, user_id=current_user.id).first()
    if candidate is None:
        return Response("No such candidate job.", 404)

    channel = jobs._infer_channel(candidate.link)
    note_parts = []
    if candidate.link:
        note_parts.append(f"Source: {candidate.link}")
    if candidate.reason:
        note_parts.append(candidate.reason)

    db.session.add(
        Application(
            company=candidate.company,
            position=candidate.position,
            applied_date=datetime.utcnow().date().isoformat(),
            channel=channel,
            note=" | ".join(note_parts) or None,
            status=tracker.DEFAULT_STATUS,
            user_id=current_user.id,
        )
    )
    db.session.delete(candidate)
    db.session.commit()
    return redirect(url_for("main.index"))


# ---------------------------------------------------------------------------
# Admin endpoints (HTTP Basic Auth) -- used by the external Gmail script
# ---------------------------------------------------------------------------


@bp.route("/admin/refresh-jobs", methods=["POST"])
@require_admin_auth
def refresh_jobs():
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

    owner = _get_owner_user()
    CandidateJob.query.filter_by(user_id=owner.id).delete()
    for job in new_jobs:
        db.session.add(
            CandidateJob(
                company=job["company"],
                position=job["position"],
                city=job.get("city"),
                link=job.get("link"),
                reason=job.get("reason"),
                tag=job["tag"],
                found_date=datetime.utcnow().date().isoformat(),
                user_id=owner.id,
            )
        )
    db.session.commit()
    return jsonify({"inserted": len(new_jobs)})


@bp.route("/admin/add-applications", methods=["POST"])
@require_admin_auth
def add_applications():
    payload = request.get_json(silent=True) or {}
    new_apps = payload.get("applications")
    if not isinstance(new_apps, list):
        return jsonify({"error": "Expected JSON body with an 'applications' list."}), 400

    owner = _get_owner_user()
    inserted = []
    try:
        for a in new_apps:
            if not tracker.is_valid_date(a["applied_date"]):
                raise ValueError(f"Invalid date '{a['applied_date']}'")
            if a["channel"] not in tracker.VALID_CHANNELS:
                raise ValueError(f"Invalid channel '{a['channel']}'")
            application = Application(
                company=a["company"],
                position=a["position"],
                applied_date=a["applied_date"],
                channel=a["channel"],
                note=a.get("note"),
                status=tracker.DEFAULT_STATUS,
                user_id=owner.id,
            )
            db.session.add(application)
            inserted.append(application)
    except (ValueError, KeyError) as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400
    db.session.commit()
    return jsonify({"inserted": len(inserted)})


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def _bootstrap_owner_account(app: Flask) -> None:
    """Idempotent startup seed: ensure the owner account exists.

    Creates a ``User`` row for ``PANEL_USERNAME`` if one doesn't already
    exist (backfilling the new profile fields from ``OWNER_*`` config/env),
    then assigns any pre-existing ownerless rows (``user_id IS NULL`` --
    left over from before user accounts existed) to that account, so no
    existing data is lost or orphaned. Safe to call on every startup.
    """
    panel_username = app.config["PANEL_USERNAME"]
    owner = User.query.filter_by(username=panel_username).first()
    if owner is None:
        owner = User(
            full_name=app.config.get("OWNER_FULL_NAME") or panel_username,
            email=app.config["OWNER_EMAIL"],
            username=panel_username,
            target_position=app.config.get("OWNER_TARGET_POSITION") or None,
            city_preference=app.config.get("OWNER_CITY") or None,
            kvkk_accepted_at=datetime.utcnow(),
            email_verified=True,
        )
        owner.set_password(app.config["PANEL_PASSWORD"])
        db.session.add(owner)
        db.session.commit()

    Application.query.filter_by(user_id=None).update({"user_id": owner.id})
    CandidateJob.query.filter_by(user_id=None).update({"user_id": owner.id})
    db.session.commit()


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    cfg = test_config or {}

    panel_username = cfg.get("PANEL_USERNAME", os.environ.get("PANEL_USERNAME"))
    panel_password = cfg.get("PANEL_PASSWORD", os.environ.get("PANEL_PASSWORD"))
    secret_key = cfg.get("SECRET_KEY", os.environ.get("SECRET_KEY"))

    if not panel_username or not panel_password:
        raise RuntimeError(
            "PANEL_USERNAME and PANEL_PASSWORD environment variables must both "
            "be set before starting the dashboard. They protect the /admin "
            "endpoints and seed the owner's own login account."
        )
    if not secret_key:
        raise RuntimeError(
            "SECRET_KEY environment variable must be set before starting the "
            "dashboard. Refusing to start without it, since Flask sessions "
            "(user login) cannot be signed safely otherwise."
        )

    app.secret_key = secret_key
    app.config["PANEL_USERNAME"] = panel_username
    app.config["PANEL_PASSWORD"] = panel_password

    database_url = cfg.get("SQLALCHEMY_DATABASE_URI") or os.environ.get("DATABASE_URL")
    if database_url and database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    if not database_url:
        db_path = cfg.get("DB_PATH", os.environ.get("DB_PATH", tracker.DEFAULT_DB_PATH))
        database_url = f"sqlite:///{db_path}"
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    app.config["OWNER_EMAIL"] = cfg.get(
        "OWNER_EMAIL", os.environ.get("OWNER_EMAIL", "yakupceri24@gmail.com")
    )
    app.config["OWNER_FULL_NAME"] = cfg.get("OWNER_FULL_NAME", os.environ.get("OWNER_FULL_NAME", ""))
    app.config["OWNER_TARGET_POSITION"] = cfg.get(
        "OWNER_TARGET_POSITION", os.environ.get("OWNER_TARGET_POSITION", "")
    )
    app.config["OWNER_CITY"] = cfg.get("OWNER_CITY", os.environ.get("OWNER_CITY", ""))

    secure_cookies = cfg.get(
        "SESSION_COOKIE_SECURE",
        os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true",
    )
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = secure_cookies
    app.config["REMEMBER_COOKIE_HTTPONLY"] = True
    app.config["REMEMBER_COOKIE_SECURE"] = secure_cookies
    app.config["REMEMBER_COOKIE_DURATION"] = timedelta(days=30)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)

    app.register_blueprint(bp)

    with app.app_context():
        # Schema is owned by Alembic (migrations/), not db.create_all():
        # that's what lets a pre-existing production database (created by
        # earlier, pre-Alembic versions of this app) pick up new columns
        # instead of only ever getting brand-new tables.
        migrate_upgrade()
        _bootstrap_owner_account(app)

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
