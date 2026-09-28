"""
Sign-in: creating accounts, signing in and out, and the login_required
check that every chatbot and navigator route uses.

Web routes (all JSON):
    POST /auth/register   {full_name, email, password}
    POST /auth/login      {email, password}
    POST /auth/logout
    GET  /auth/me         the signed-in user (or, for a guest, user null + guest allowance)

    POST /auth/password/change   {current_password, new_password}   (signed in)
    POST /auth/password/forgot   {email}                  emails a 6-digit code
    POST /auth/password/verify   {email, code}            -> {reset_token}
    POST /auth/password/reset    {email, reset_token, new_password}
"""

from functools import wraps

from flask import Blueprint, g, jsonify, request, session

import accounts
import guest
import mailer

auth_bp = Blueprint("auth", __name__)


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    # "sv" (session version) changes when the password changes, which signs
    # out every other device that was signed in with the old password
    return accounts.get_session_user(user_id, session.get("sv", 0))


def login_required(view):
    """Only lets signed-in users through; puts the user in g.user."""
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


@auth_bp.route("/auth/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    try:
        user = accounts.register_user(data.get("full_name"), data.get("email"), data.get("password"))
    except accounts.AccountError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        print(f"[auth] error registering: {exc}")
        return jsonify({"error": "Your account could not be created. Please try again."}), 500
    sign_in(user)
    return jsonify({"user": user}), 201


@auth_bp.route("/auth/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    try:
        user = accounts.authenticate(data.get("email"), data.get("password"))
    except Exception as exc:
        print(f"[auth] error signing in: {exc}")
        return jsonify({"error": "Could not sign in right now. Please try again."}), 500
    if not user:
        return jsonify({"error": "That email and password don't match an account."}), 401
    sign_in(user)
    return jsonify({"user": user})


@auth_bp.route("/auth/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@auth_bp.route("/auth/me", methods=["GET"])
def me():
    """The signed-in user, or for a guest: user null and their question allowance."""
    user = current_user()
    if user:
        return jsonify({"user": user})
    return jsonify({"user": None, "guest": guest.status()})


# ---------------------------------------------------------------- passwords
@auth_bp.route("/auth/password/change", methods=["POST"])
@login_required
def change_password():
    """Settings -> Change password (needs the current password)."""
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
    """Step 1: email a 6-digit code. Answers the same whether or not the
    email has an account, so nobody can use this page to find out who is registered."""
    data = request.get_json(silent=True) or {}
    try:
        user, code = accounts.start_password_reset(data.get("email"))
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
            print(f"[email] password reset email to user {user['id']} failed: {exc}")
            return jsonify({"error": "We couldn't send the email right now. Please try again later."}), 502
    return jsonify({
        "ok": True,
        "minutes": accounts.RESET_CODE_MINUTES,
        "resend_after": accounts.RESET_RESEND_SECONDS,
    })


@auth_bp.route("/auth/password/verify", methods=["POST"])
def verify_reset_code():
    """Step 2: check the code. The right code gives a one-time reset token."""
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
    """Step 3: set the new password (signs out every device)."""
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
