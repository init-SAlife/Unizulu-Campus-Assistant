"""
Sign-in: creating accounts, signing in and out, and the login_required
check that every chatbot and navigator route uses.

Web routes (all JSON):
    POST /auth/register   {full_name, email, password}
    POST /auth/login      {email, password}
    POST /auth/logout
    GET  /auth/me         the signed-in user
"""

from functools import wraps

from flask import Blueprint, g, jsonify, request, session

import accounts

auth_bp = Blueprint("auth", __name__)


def current_user():
    user_id = session.get("user_id")
    return accounts.get_user(user_id) if user_id else None


def login_required(view):
    """Only lets signed-in users through; puts the user in g.user."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user:
            session.clear()
            return jsonify({"error": "Please sign in to continue.", "login_required": True}), 401
        g.user = user
        return view(*args, **kwargs)
    return wrapper


def sign_in(user):
    session.clear()                  # new cookie on every sign-in
    session.permanent = True
    session["user_id"] = user["id"]


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
@login_required
def me():
    return jsonify({"user": g.user})
