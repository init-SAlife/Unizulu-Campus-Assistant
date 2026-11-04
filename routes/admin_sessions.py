# admin session tracking for the Campus AI Assistant (from the campus_ai_assistant admin
# package, joined to this app).

from datetime import timedelta
from functools import wraps

from flask import Blueprint, g, jsonify, request, session

from services import accounts
from routes.auth_routes import current_user

admin_sessions_bp = Blueprint("admin_sessions", __name__)

IDLE_LIMIT = timedelta(minutes=30)


def client_ip():
    # The admin's network address (behind Render's proxy: the forwarded one).
    forwarded = request.headers.get("X-Forwarded-For", "")
    return (forwarded.split(",")[0].strip() if forwarded else request.remote_addr or "")[:45]


def start_admin_session(admin_id):
    # Opens a new admin session (call right after an admin signs in).
    close_idle_sessions()
    with accounts._cursor() as cur:
        cur.execute(
            "INSERT INTO admin_sessions (admin_id, ip, user_agent) VALUES (%s, %s, %s) RETURNING id",
            (admin_id, client_ip(), request.headers.get("User-Agent", "")[:300]),
        )
        session_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO admin_actions (session_id, admin_id, action, details) VALUES (%s, %s, %s, %s)",
            (session_id, admin_id, "Logged in", "Admin dashboard"),
        )
    session["admin_session_id"] = session_id
    return session_id


def close_idle_sessions():
    # Sessions left open when the browser was closed are marked as timed out.
    with accounts._cursor() as cur:
        cur.execute(
            """UPDATE admin_sessions SET logout_at = last_seen, ended_by = 'timeout'
               WHERE logout_at IS NULL AND last_seen < now() - %s""",
            (IDLE_LIMIT,),
        )


def session_is_open(sid):
    if not sid:
        return False
    with accounts._cursor() as cur:
        cur.execute("SELECT logout_at IS NULL AND last_seen >= now() - %s FROM admin_sessions WHERE id = %s",
                    (IDLE_LIMIT, sid))
        row = cur.fetchone()
    return bool(row and row[0])


def log_admin_action(action, details=""):
    # Records one action against the current admin session.
    sid = session.get("admin_session_id")
    uid = session.get("user_id")
    if not sid or not uid:
        return
    with accounts._cursor() as cur:
        cur.execute(
            "INSERT INTO admin_actions (session_id, admin_id, action, details) VALUES (%s, %s, %s, %s)",
            (sid, uid, str(action)[:80], str(details or "")[:500]),
        )
        cur.execute("UPDATE admin_sessions SET last_seen = now() WHERE id = %s", (sid,))


def end_admin_session(how="logout"):
    # Closes the current admin session (if any) and returns its summary.
    sid = session.get("admin_session_id")
    if not sid:
        return None
    if how == "logout":
        log_admin_action("Logged out")
    with accounts._cursor() as cur:
        cur.execute(
            """UPDATE admin_sessions SET logout_at = now(), last_seen = now(), ended_by = %s
               WHERE id = %s AND logout_at IS NULL""",
            (how, sid),
        )
    session.pop("admin_session_id", None)
    return load_session(sid)


def admin_required(view):
    # Only signed-in admins with an open admin session; keeps last_seen fresh.
    @wraps(view)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user or user.get("role") != "admin":
            return jsonify({"error": "Admin sign-in required.", "code": "not_admin"}), 401
        sid = session.get("admin_session_id")
        if not session_is_open(sid):
            close_idle_sessions()
            session.pop("admin_session_id", None)
            return jsonify({"error": "Your admin session has ended. Open the dashboard again.",
                            "code": "session_ended"}), 401
        with accounts._cursor() as cur:
            cur.execute("UPDATE admin_sessions SET last_seen = now() WHERE id = %s", (sid,))
        g.user = user
        return view(*args, **kwargs)
    return wrapper


def records(action):
    # Put on an admin route so it is recorded automatically after it succeeds.
    def deco(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            result = view(*args, **kwargs)
            status = result[1] if isinstance(result, tuple) else 200
            if status < 400:
                body = request.get_json(silent=True) or {}
                # a route can name the action more exactly (e.g. "Disabled user") in g.audit_action
                log_admin_action(g.get("audit_action") or action,
                                 str(body.get("details") or g.get("audit_details") or ""))
            return result
        return wrapper
    return deco


# routes
@admin_sessions_bp.post("/admin/api/actions")
@admin_required
def add_action():
    # Actions that only happen in the page (opening a tab, refreshing, exporting).
    body = request.get_json(silent=True) or {}
    action = (body.get("action") or "").strip()[:80]
    if not action:
        return jsonify({"error": "action is required"}), 400
    log_admin_action(action, body.get("details") or "")
    return jsonify({"ok": True})


@admin_sessions_bp.get("/admin/api/current-session")
@admin_required
def current_session():
    return jsonify(load_session(session["admin_session_id"]))


@admin_sessions_bp.post("/admin/api/logout")
@admin_required
def admin_logout():
    # Log out of the dashboard: closes the admin session and signs out.
    summary = end_admin_session("logout")
    session.clear()
    return jsonify(summary)


@admin_sessions_bp.get("/admin/api/sessions")
@admin_required
def list_sessions():
    close_idle_sessions()
    admin = request.args.get("admin")
    try:
        limit = min(int(request.args.get("limit", 100)), 500)
    except ValueError:
        limit = 100
    sql = "SELECT s.id FROM admin_sessions s JOIN users u ON u.id = s.admin_id"
    params = []
    if admin:
        sql += " WHERE lower(u.email) = lower(%s)"
        params.append(admin)
    sql += " ORDER BY s.login_at DESC LIMIT %s"
    params.append(limit)
    with accounts._cursor() as cur:
        cur.execute(sql, params)
        ids = [r[0] for r in cur.fetchall()]
    return jsonify([load_session(i) for i in ids])


def load_session(sid):
    # One session with its actions, in the shape the dashboard expects.
    with accounts._cursor() as cur:
        cur.execute(
            """SELECT u.email, u.full_name, s.login_at, s.logout_at, s.ended_by, s.ip
               FROM admin_sessions s JOIN users u ON u.id = s.admin_id WHERE s.id = %s""",
            (sid,),
        )
        row = cur.fetchone()
        if not row:
            return None
        email, name, login_at, logout_at, ended_by, ip = row
        cur.execute(
            "SELECT created_at, action, details FROM admin_actions WHERE session_id = %s ORDER BY created_at, id",
            (sid,),
        )
        actions = [{"at": t.isoformat(), "action": a, "details": d or ""} for t, a, d in cur.fetchall()]
    return {
        "id": sid,
        "admin": email,
        "name": name,
        "login": login_at.isoformat(),
        "logout": logout_at.isoformat() if logout_at else None,
        "ended": ended_by or "active",
        "ip": ip or "",
        "actions": actions,
    }
