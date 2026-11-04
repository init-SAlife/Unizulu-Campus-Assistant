import hmac
import os
import threading
import time
from functools import wraps

from flask import Blueprint, current_app, g, jsonify, request, session

from services import accounts, guest, mailer

auth_bp = Blueprint("auth", __name__)
# cookie for "Remember this device" (only a hash of it is stored)
DEVICE_COOKIE = "campus_device"


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    # "sv" (session version) changes when the password changes, which signs
    # out every other device that was signed in with the old password
    return accounts.get_session_user(user_id, session.get("sv", 0))


def login_required(view):
    # Only lets signed-in users through; puts the user in g.user.
    @wraps(view)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user:
            # forget only the (expired) sign-in -- a guest's question count stays
            session.pop("user_id", None)
            session.pop("sv", None)
            return jsonify({"error": "Please sign in to use this.", "login_required": True}), 401
        g.user = user
        return view(*args, **kwargs)
    return wrapper


def sign_in(user):
    session.clear()                  # new cookie on every sign-in
    session.permanent = True
    session["user_id"] = user["id"]
    session["sv"] = accounts.get_session_version(user["id"])


# admin sign-up (request + approval)
# "Create account -> Administrator" does NOT make an admin straight away
ADMIN_CODE_TRIES = 5
ADMIN_CODE_WINDOW = 15 * 60          # seconds
_code_fails = {}
_code_lock = threading.Lock()


def admin_signup_code():
    return os.environ.get("ADMIN_SIGNUP_CODE", "").strip()


def _client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    return (forwarded.split(",")[0].strip() if forwarded else request.remote_addr) or "?"


def _recent_fails(ip, now):
    fails = [t for t in _code_fails.get(ip, []) if now - t < ADMIN_CODE_WINDOW]
    if fails:
        _code_fails[ip] = fails
    else:
        _code_fails.pop(ip, None)
    return fails


def check_admin_code(code):
    # None if no code is needed or the code is right, otherwise the error to show.
    expected = admin_signup_code()
    if not expected:
        return None
    ip, now = _client_ip(), time.time()
    with _code_lock:
        if len(_recent_fails(ip, now)) >= ADMIN_CODE_TRIES:
            return "Too many wrong admin access codes. Please wait 15 minutes and try again."
    if hmac.compare_digest(str(code or "").strip().encode(), expected.encode()):
        return None
    with _code_lock:
        _code_fails.setdefault(ip, []).append(now)
    return "That admin access code is not right."


def _tell_admins(user, reason):
    # Emails every admin that a request is waiting (best effort: it is on the dashboard anyway).
    try:
        subject, text, html = mailer.new_admin_request_email(user["full_name"], user["email"], reason)
        for email in accounts.admin_emails():
            try:
                mailer.send_email(email, subject, text, html)
            except mailer.EmailNotSent as exc:
                print(f"[email] new admin request note to an admin failed: {exc}")
    except Exception as exc:
        print(f"[auth] could not tell the admins about a request: {exc}")


#email codes
def _ip_hash():
    return accounts.hash_ip(guest.client_ip())


def _send_code(email, purpose, full_name):
    # Makes and emails a code. Raises CodeLimit, or EmailNotSent (then it is not counted).
    code, send_id, status = accounts.issue_code(email, purpose, _ip_hash())
    subject, text, html = mailer.account_code_email(full_name, code, accounts.CODE_MINUTES, purpose)
    try:
        mailer.send_email(email, subject, text, html)
    except mailer.EmailNotSent:
        accounts.cancel_send(send_id)
        raise
    return status


def _code_step(purpose, email, status, remember_days):
    # Tells the page to show the code boxes.
    return jsonify({"step": "code", "purpose": purpose, "email": email, "minutes": accounts.CODE_MINUTES,
                    "left": status["left"], "per_day": status["per_day"], "wait": status["wait"],
                    "remember_days": remember_days})


def _limit_reply(exc):
    return jsonify({"error": str(exc), "left": exc.left, "wait": exc.wait,
                    "per_day": accounts.CODE_EMAILS_PER_DAY}), 429


EMAIL_FAILED = "We couldn't send the email right now. Please try again in a minute."


def _finish_sign_in(user, remember, remember_role, extra=None):
    # Signs in after the code (or on a remembered device); admins start an admin session.
    sign_in(user)
    accounts.log_event("login", user["id"])
    body = {"user": user, **(extra or {})}
    if user.get("role") == "admin":
        from routes.admin_sessions import start_admin_session      # imported here: admin_sessions imports this file
        try:
            start_admin_session(user["id"])
        except Exception as exc:
            print(f"[admin] could not start the admin session: {exc}")
        body["redirect"] = "/admin"
    response = jsonify(body)
    if remember:
        token, days = accounts.trust_device(user["id"], remember_role, request.headers.get("User-Agent", ""))
        if token:
            response.set_cookie(DEVICE_COOKIE, token, max_age=days * 86400, httponly=True, samesite="Lax",
                                secure=bool(current_app.config.get("SESSION_COOKIE_SECURE")), path="/")
    return response


@auth_bp.route("/auth/register-options", methods=["GET"])
def register_options():
    # What the sign-in page needs: the allowed email (Gmail) and the code rules.
    return jsonify({
        "email_domain": accounts.ALLOWED_EMAIL_DOMAIN,
        "needs_code": bool(admin_signup_code()),
        "code_minutes": accounts.CODE_MINUTES,
        "per_day": accounts.CODE_EMAILS_PER_DAY,
        "resend_seconds": accounts.CODE_RESEND_SECONDS,
        "remember_days": {"student": accounts.trusted_days("student"), "admin": accounts.trusted_days("admin")},
    })


@auth_bp.route("/auth/register", methods=["POST"])
def register():
    # Step 1 of creating an account: check everything, then email a code. The account is
    # only made once the code is right (/auth/code/verify).
    data = request.get_json(silent=True) or {}
    account_type = data.get("account_type")
    if account_type == "admin":
        problem = check_admin_code(data.get("admin_code"))
        if problem:
            return jsonify({"error": problem, "field": "admin_code"}), 403
    try:
        pending = accounts.start_signup(data.get("full_name"), data.get("email"), data.get("password"),
                                        account_type, data.get("admin_reason"))
        status = _send_code(pending["email"], "signup", pending["full_name"])
    except accounts.CodeLimit as exc:
        return _limit_reply(exc)
    except accounts.AccountError as exc:
        return jsonify({"error": str(exc)}), 400
    except mailer.EmailNotSent as exc:
        print(f"[email] sign-up code to {data.get('email')} failed: {exc}")
        return jsonify({"error": EMAIL_FAILED}), 502
    except Exception as exc:
        print(f"[auth] error starting sign-up: {exc}")
        return jsonify({"error": "Your account could not be created. Please try again."}), 500
    session["pending_auth"] = {"purpose": "signup", "email": pending["email"], "role": account_type}
    return _code_step("signup", pending["email"], status, accounts.trusted_days(account_type))


@auth_bp.route("/auth/login", methods=["POST"])
def login():
    # Step 1 of signing in: the password, then a code by email (unless this device is remembered).
    data = request.get_json(silent=True) or {}
    if not accounts.email_allowed(data.get("email")):
        return jsonify({"error": accounts.WRONG_EMAIL_MESSAGE}), 400
    try:
        user = accounts.apply_admin_list(accounts.authenticate(data.get("email"), data.get("password")))
    except accounts.AccountDisabled:
        return jsonify({"error": "This account has been disabled. Please contact the system administrator."}), 403
    except Exception as exc:
        print(f"[auth] error signing in: {exc}")
        return jsonify({"error": "Could not sign in right now. Please try again."}), 500
    if not user:
        return jsonify({"error": "That email and password don't match an account."}), 401

    if accounts.device_is_trusted(user["id"], request.cookies.get(DEVICE_COOKIE)):
        return _finish_sign_in(user, False, user["role"])
    try:
        status = _send_code(user["email"], "login", user["full_name"])
    except accounts.CodeLimit as exc:
        return _limit_reply(exc)
    except mailer.EmailNotSent as exc:
        print(f"[email] sign-in code to user {user['id']} failed: {exc}")
        return jsonify({"error": EMAIL_FAILED}), 502
    session["pending_auth"] = {"purpose": "login", "email": user["email"], "user_id": user["id"], "role": user["role"]}
    return _code_step("login", user["email"], status, accounts.trusted_days(user["role"]))


@auth_bp.route("/auth/code/verify", methods=["POST"])
def verify_code():
    # Step 2: the 6-digit code. Creates the account (sign-up) or finishes signing in.
    pending = session.get("pending_auth")
    if not pending:
        return jsonify({"error": "Please start again.", "restart": True}), 400
    data = request.get_json(silent=True) or {}
    remember = bool(data.get("remember"))
    try:
        accounts.check_code(pending["email"], pending["purpose"], data.get("code"))
        if pending["purpose"] == "signup":
            user, wants_admin, reason = accounts.finish_signup(pending["email"])
            extra = {"admin_request": None}
            if wants_admin:
                accounts.log_event("admin_request", user["id"], reason[:200])
                _tell_admins(user, reason)
                extra["admin_request"] = {"status": "pending"}
            return _finish_sign_in(user, remember, pending.get("role", "student"), extra)
        user = accounts.apply_admin_list(accounts.get_login_user(pending["user_id"]))
    except accounts.AccountDisabled:
        session.pop("pending_auth", None)
        return jsonify({"error": "This account has been disabled. Please contact the system administrator."}), 403
    except accounts.AccountError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        print(f"[auth] error checking a code: {exc}")
        return jsonify({"error": "Something went wrong. Please try again."}), 500
    return _finish_sign_in(user, remember, user["role"])


@auth_bp.route("/auth/code/resend", methods=["POST"])
def resend_code():
    pending = session.get("pending_auth")
    if not pending:
        return jsonify({"error": "Please start again.", "restart": True}), 400
    try:
        if pending["purpose"] == "signup":
            row = accounts.pending_signup(pending["email"])
            if not row:
                raise accounts.AccountError("Your sign-up has expired. Please create your account again.")
            name = row[0]
        else:
            name = accounts.get_login_user(pending["user_id"])["full_name"]
        status = _send_code(pending["email"], pending["purpose"], name)
    except accounts.CodeLimit as exc:
        return _limit_reply(exc)
    except accounts.AccountDisabled:
        return jsonify({"error": "This account has been disabled. Please contact the system administrator."}), 403
    except accounts.AccountError as exc:
        return jsonify({"error": str(exc), "restart": True}), 400
    except mailer.EmailNotSent as exc:
        print(f"[email] new code to {pending['email']} failed: {exc}")
        return jsonify({"error": EMAIL_FAILED}), 502
    return jsonify({"ok": True, "left": status["left"], "per_day": status["per_day"], "wait": status["wait"]})


@auth_bp.route("/auth/admin-request", methods=["GET"])
@login_required
def admin_request_status():
    return jsonify({"admin_request": accounts.get_admin_request(g.user["id"]),
                    "email": g.user["email"]})


@auth_bp.route("/auth/logout", methods=["POST"])
def logout():
    # signing out of the app also ends an open admin session
    if session.get("admin_session_id"):
        from routes.admin_sessions import end_admin_session
        try:
            end_admin_session("logout")
        except Exception as exc:
            print(f"[admin] could not end the admin session: {exc}")
    session.clear()
    return jsonify({"ok": True})


@auth_bp.route("/auth/me", methods=["GET"])
def me():
    # The signed-in user, or for a guest: user null and their question allowance.
    user = current_user()
    if user:
        try:
            req = accounts.get_admin_request(user["id"])
        except Exception:
            req = None
        return jsonify({"user": user, "admin_request": req})
    return jsonify({"user": None, "guest": guest.status()})


#passwords
@auth_bp.route("/auth/password/change", methods=["POST"])
@login_required
def change_password():
    # Settings -> Change password (needs the current password).
    data = request.get_json(silent=True) or {}
    try:
        new_version = accounts.change_password(g.user["id"], data.get("current_password"), data.get("new_password"))
    except accounts.AccountError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        print(f"[auth] error changing password: {exc}")
        return jsonify({"error": "Your password could not be changed. Please try again."}), 500
    session["sv"] = new_version          # this device stays signed in, all others are signed out
    return jsonify({"ok": True})


@auth_bp.route("/auth/password/forgot", methods=["POST"])
def forgot_password():
    # Step 1: email a 6-digit code (same limits as every other code email).
    data = request.get_json(silent=True) or {}
    if not accounts.email_allowed(data.get("email")):
        return jsonify({"error": accounts.WRONG_EMAIL_MESSAGE}), 400
    try:
        user, code, send_id, status = accounts.start_password_reset(data.get("email"), _ip_hash())
    except accounts.CodeLimit as exc:
        return _limit_reply(exc)
    except accounts.AccountError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        print(f"[auth] error starting password reset: {exc}")
        return jsonify({"error": "Something went wrong. Please try again."}), 500

    if user:
        subject, text, html = mailer.reset_code_email(user["full_name"], code, accounts.RESET_CODE_MINUTES)
        try:
            mailer.send_email(user["email"], subject, text, html)
        except mailer.EmailNotSent as exc:
            accounts.cancel_send(send_id)
            print(f"[email] password reset email to user {user['id']} failed: {exc}")
            return jsonify({"error": EMAIL_FAILED}), 502
    return jsonify({
        "ok": True,
        "minutes": accounts.RESET_CODE_MINUTES,
        "resend_after": status["wait"],
        "left": status["left"],
        "per_day": status["per_day"],
    })


@auth_bp.route("/auth/password/verify", methods=["POST"])
def verify_reset_code():
    # Step 2: check the code.
    data = request.get_json(silent=True) or {}
    try:
        token = accounts.verify_reset_code(data.get("email"), data.get("code"))
    except accounts.AccountError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        print(f"[auth] error verifying reset code: {exc}")
        return jsonify({"error": "Something went wrong. Please try again."}), 500
    return jsonify({"reset_token": token})


@auth_bp.route("/auth/password/reset", methods=["POST"])
def reset_password():
    # Step 3: set the new password (signs out every device).
    data = request.get_json(silent=True) or {}
    try:
        accounts.finish_password_reset(data.get("email"), data.get("reset_token"), data.get("new_password"))
    except accounts.AccountError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        print(f"[auth] error resetting password: {exc}")
        return jsonify({"error": "Your password could not be changed. Please try again."}), 500
    session.clear()
    return jsonify({"ok": True})
