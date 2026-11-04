# the admin dashboard (/admin) and the data it shows.

import os
from datetime import date, datetime, timedelta, timezone

from flask import Blueprint, g, jsonify, redirect, request, send_from_directory, session

from services import accounts, mailer
from routes.admin_sessions import admin_required, records, session_is_open, start_admin_session
from routes.auth_routes import current_user

admin_bp = Blueprint("admin", __name__)
# the page is NOT in static/: it is only served through /admin, after the admin check
PAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")

# a question typed by a student (uploads are stored as "📎 Uploaded ..." messages)
QUESTION_SQL = "m.role = 'user' AND m.content NOT LIKE '📎%%'"


# the page
@admin_bp.route("/admin")
def admin_page():
    user = current_user()
    if not user:
        return redirect("/login?next=/admin")
    if user.get("role") != "admin":
        return redirect("/")
    # a new admin session when there is none yet (or the last one timed out)
    if not session_is_open(session.get("admin_session_id")):
        start_admin_session(user["id"])
    response = send_from_directory(PAGE_DIR, "admin.html")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


# periods
def period(range_key):
    # (start, end, previous_start, label, bucket) for the range picked at the top.
    now = datetime.now(timezone.utc)
    today = now.date()
    if range_key == "30":
        start = today - timedelta(days=29)
        label = "last 30 days"
    elif range_key == "semester":
        start = date(today.year, 1, 1) if today.month <= 6 else date(today.year, 7, 1)
        label = "this semester"
    elif range_key == "year":
        start = date(today.year, 1, 1)
        label = "this year"
    else:
        start = today - timedelta(days=6)
        label = "last 7 days"
    days = (today - start).days + 1
    prev_start = start - timedelta(days=days)
    bucket = "day" if days <= 31 else "week"
    return start, today + timedelta(days=1), prev_start, label, bucket, days


def _one(cur, sql, params=()):
    cur.execute(sql, params)
    row = cur.fetchone()
    return row[0] if row and row[0] is not None else 0


def _counts(cur, start, end):
    # The six numbers at the top for one period [start, end).
    questions = _one(cur, f"""SELECT count(*) FROM chat_messages m
                              WHERE {QUESTION_SQL} AND m.created_at >= %s AND m.created_at < %s""", (start, end)) \
        + _one(cur, "SELECT count(*) FROM guest_questions WHERE created_at >= %s AND created_at < %s", (start, end))
    active = _one(cur, f"""SELECT count(DISTINCT uid) FROM (
                              SELECT s.user_id AS uid FROM chat_messages m JOIN chat_sessions s ON s.id = m.session_id
                              WHERE {QUESTION_SQL} AND m.created_at >= %s AND m.created_at < %s
                              UNION SELECT user_id FROM usage_events
                              WHERE user_id IS NOT NULL AND created_at >= %s AND created_at < %s) x""",
                  (start, end, start, end))
    cur.execute("""SELECT count(*) FILTER (WHERE rating = 'good'), count(*) FILTER (WHERE rating = 'bad')
                   FROM response_feedback WHERE created_at >= %s AND created_at < %s""", (start, end))
    good, bad = cur.fetchone()
    reg = _one(cur, "SELECT count(*) FROM usage_events WHERE kind = 'registration_check' AND created_at >= %s AND created_at < %s",
               (start, end))
    routes = _one(cur, "SELECT count(*) FROM usage_events WHERE kind = 'route' AND created_at >= %s AND created_at < %s",
                  (start, end))
    new_users = _one(cur, "SELECT count(*) FROM users WHERE created_at >= %s AND created_at < %s", (start, end))
    helpful = round(100 * good / (good + bad)) if good + bad else None
    return {"questions": questions, "active": active, "helpful": helpful, "good": good, "bad": bad,
            "registration": reg, "routes": routes, "new_users": new_users}


def _questions_series(cur, start, end, bucket):
    # Questions per day (or per week) in [start, end).
    trunc = "day" if bucket == "day" else "week"
    cur.execute(f"""SELECT date_trunc('{trunc}', t)::date AS b, count(*) FROM (
                       SELECT m.created_at AS t FROM chat_messages m
                       WHERE {QUESTION_SQL} AND m.created_at >= %s AND m.created_at < %s
                       UNION ALL SELECT created_at FROM guest_questions WHERE created_at >= %s AND created_at < %s) q
                    GROUP BY b""", (start, end, start, end))
    found = dict(cur.fetchall())
    out, d = [], start
    step = timedelta(days=1 if bucket == "day" else 7)
    if bucket == "week":
        d = start - timedelta(days=start.weekday())
    while d < end:
        out.append({"label": d.isoformat(), "count": found.get(d, 0)})
        d += step
    return out


def _history(cur):
    # Month by month, every year since the first record, for each metric.
    metrics = {
        "questions": f"""SELECT date_trunc('month', t) AS mth, count(*) FROM (
                            SELECT m.created_at AS t FROM chat_messages m WHERE {QUESTION_SQL}
                            UNION ALL SELECT created_at FROM guest_questions) q GROUP BY mth""",
        "users": f"""SELECT date_trunc('month', t) AS mth, count(DISTINCT uid) FROM (
                        SELECT m.created_at AS t, s.user_id AS uid FROM chat_messages m
                        JOIN chat_sessions s ON s.id = m.session_id WHERE {QUESTION_SQL}
                        UNION ALL SELECT created_at, user_id FROM usage_events WHERE user_id IS NOT NULL) x GROUP BY mth""",
        "helpful": """SELECT date_trunc('month', created_at) AS mth,
                             round(100.0 * count(*) FILTER (WHERE rating = 'good') / count(*))
                      FROM response_feedback GROUP BY mth""",
        "registration": """SELECT date_trunc('month', created_at) AS mth, count(*) FROM usage_events
                           WHERE kind = 'registration_check' GROUP BY mth""",
        "routes": """SELECT date_trunc('month', created_at) AS mth, count(*) FROM usage_events
                     WHERE kind = 'route' GROUP BY mth""",
    }
    today = date.today()
    out = {}
    for name, sql in metrics.items():
        cur.execute(sql)
        years = {}
        for mth, value in cur.fetchall():
            years.setdefault(mth.year, [0] * 12)[mth.month - 1] = float(value) if name == "helpful" else int(value)
        for y, months in years.items():
            if name == "helpful":                      # months without ratings: no value, not 0 %
                cur.execute("""SELECT DISTINCT extract(month FROM created_at)::int FROM response_feedback
                               WHERE extract(year FROM created_at) = %s""", (y,))
                rated = {r[0] for r in cur.fetchall()}
                years[y] = [v if (i + 1) in rated else None for i, v in enumerate(months)]
            if y == today.year:                        # months still to come: no value yet
                years[y] = [v if i < today.month else None for i, v in enumerate(years[y])]
        out[name] = {str(y): v for y, v in sorted(years.items())}
    return out


def _feedback_rows(cur, where="", params=(), limit=200):
    cur.execute(f"""SELECT f.id, f.rating, f.question, f.answer, f.status, f.created_at,
                           u.full_name, u.email, f.resolved_at, r.email
                    FROM response_feedback f
                    LEFT JOIN users u ON u.id = f.user_id
                    LEFT JOIN users r ON r.id = f.resolved_by
                    {where} ORDER BY f.created_at DESC LIMIT %s""", (*params, limit))
    return [{"id": i, "type": "up" if rating == "good" else "down", "question": q or "",
             "answer": (a or "")[:600], "status": status, "at": at.isoformat(),
             "user": name or "Guest", "email": email or "",
             "resolved_at": res_at.isoformat() if res_at else None, "resolved_by": res_by or ""}
            for i, rating, q, a, status, at, name, email, res_at, res_by in cur.fetchall()]


EVENT_LABELS = {"login": "Signed in", "upload": "Uploaded a document",
                "registration_check": "Registration check", "route": "Navigator route",
                "admin_request": "Asked for an admin account"}


def _audit_rows(cur, limit=300, include_routes=False):
    # Admin actions and app events together, newest first.
    route_filter = "" if include_routes else "AND e.kind <> 'route'"
    cur.execute(f"""SELECT * FROM (
                       SELECT a.created_at, u.email, a.action, a.details, s.ip, 'admin' AS src
                       FROM admin_actions a JOIN users u ON u.id = a.admin_id
                       JOIN admin_sessions s ON s.id = a.session_id
                       UNION ALL
                       SELECT e.created_at, coalesce(u.email, 'Guest'), e.kind, e.details, '', 'app'
                       FROM usage_events e LEFT JOIN users u ON u.id = e.user_id
                       WHERE true {route_filter}) x
                    ORDER BY created_at DESC LIMIT %s""", (limit,))
    return [{"at": at.isoformat(), "user": who, "action": EVENT_LABELS.get(act, act) if src == "app" else act,
             "details": det or "", "ip": ip or "-", "source": src}
            for at, who, act, det, ip, src in cur.fetchall()]


# data for the page
@admin_bp.get("/admin/api/overview")
@admin_required
def overview():
    start, end, prev_start, label, bucket, days = period(request.args.get("range", "7"))
    with accounts._cursor() as cur:
        now = _counts(cur, start, end)
        before = _counts(cur, prev_start, start)
        total_users = _one(cur, "SELECT count(*) FROM users")
        series = _questions_series(cur, start, end, bucket)
        prev_series = _questions_series(cur, prev_start, start, bucket)
        cur.execute("""SELECT count(*) FILTER (WHERE rating = 'good'), count(*) FILTER (WHERE rating = 'bad'),
                              count(*) FILTER (WHERE status = 'open') FROM response_feedback""")
        up, down, open_reports = cur.fetchone()
        recent_feedback = _feedback_rows(cur, "WHERE f.rating = 'bad'", limit=3)
        recent_audit = _audit_rows(cur, 5)
        history = _history(cur)
    return jsonify({
        "me": {"name": g.user["full_name"], "email": g.user["email"]},
        "range": {"label": label, "days": days, "bucket": bucket},
        "stats": {"total_users": total_users, "now": now, "before": before},
        "series": [s["count"] for s in series], "series_labels": [s["label"] for s in series],
        "prev_series": [s["count"] for s in prev_series],
        "feedback": {"up": up, "down": down, "open": open_reports},
        "pending_admin_requests": accounts.count_pending_admin_requests(),
        "recent_feedback": recent_feedback,
        "recent_audit": recent_audit,
        "history": history,
    })


@admin_bp.get("/admin/api/users")
@admin_required
def users():
    with accounts._cursor() as cur:
        cur.execute("""SELECT u.id, u.full_name, u.email, u.role, u.disabled, u.created_at,
                              count(DISTINCT s.id) AS chats,
                              greatest(u.last_login_at, max(m.created_at)) AS last_active
                       FROM users u
                       LEFT JOIN chat_sessions s ON s.user_id = u.id
                       LEFT JOIN chat_messages m ON m.session_id = s.id
                       GROUP BY u.id ORDER BY last_active DESC NULLS LAST, u.id""")
        rows = cur.fetchall()
    return jsonify([{"id": i, "name": n, "email": e, "role": r, "active": not d,
                     "joined": c.isoformat() if c else None, "chats": chats,
                     "last": last.isoformat() if last else None, "me": i == g.user["id"]}
                    for i, n, e, r, d, c, chats, last in rows])


@admin_bp.post("/admin/api/users/<int:user_id>/status")
@admin_required
@records("Changed user status")
def set_user_status(user_id):
    body = request.get_json(silent=True) or {}
    disable = bool(body.get("disabled"))
    if user_id == g.user["id"] and disable:
        return jsonify({"error": "You cannot disable your own account."}), 400
    with accounts._cursor() as cur:
        # disabling also signs the account out on every device (session_version changes)
        cur.execute("""UPDATE users SET disabled = %s,
                              session_version = session_version + CASE WHEN %s THEN 1 ELSE 0 END
                       WHERE id = %s RETURNING email""", (disable, disable, user_id))
        row = cur.fetchone()
        if row and disable:
            # a disabled account also loses every remembered device
            cur.execute("DELETE FROM trusted_devices WHERE user_id = %s", (user_id,))
    if not row:
        return jsonify({"error": "That user was not found."}), 404
    g.audit_action = "Disabled user" if disable else "Enabled user"
    g.audit_details = row[0]
    return jsonify({"ok": True, "id": user_id, "active": not disable})


@admin_bp.get("/admin/api/feedback")
@admin_required
def feedback():
    status = request.args.get("status")
    where, params = "", ()
    if status in ("open", "done"):
        where, params = "WHERE f.status = %s", (status,)
    with accounts._cursor() as cur:
        rows = _feedback_rows(cur, where, params, limit=500)
    return jsonify(rows)


@admin_bp.post("/admin/api/feedback/<int:fid>/resolve")
@admin_required
@records("Resolved feedback")
def resolve_feedback(fid):
    with accounts._cursor() as cur:
        cur.execute("""UPDATE response_feedback SET status = 'done', resolved_at = now(), resolved_by = %s
                       WHERE id = %s RETURNING left(coalesce(question, answer), 60)""", (g.user["id"], fid))
        row = cur.fetchone()
    if not row:
        return jsonify({"error": "That feedback was not found."}), 404
    g.audit_details = row[0]
    return jsonify({"ok": True})


@admin_bp.get("/admin/api/audit")
@admin_required
def audit():
    with accounts._cursor() as cur:
        rows = _audit_rows(cur, 500, include_routes=request.args.get("routes") == "1")
    return jsonify(rows)


# admin requests
# People who chose "Administrator" on Create account and confirmed their staff
# email. Approving gives the account the admin role; both are in the audit log.
@admin_bp.get("/admin/api/admin-requests")
@admin_required
def admin_requests():
    return jsonify(accounts.list_admin_requests())


def _decide(request_id, approve):
    try:
        person = accounts.decide_admin_request(request_id, g.user["id"], approve)
    except accounts.AccountError as exc:
        return jsonify({"error": str(exc)}), 409
    g.audit_action = "Approved admin request" if approve else "Rejected admin request"
    g.audit_details = person["email"]
    subject, text, html = mailer.admin_request_decision_email(person["full_name"], approve)
    try:
        mailer.send_email(person["email"], subject, text, html)
    except mailer.EmailNotSent as exc:
        print(f"[email] admin request decision to user {person['id']} failed: {exc}")
    return jsonify({"ok": True, "status": "approved" if approve else "rejected",
                    "pending": accounts.count_pending_admin_requests()})


@admin_bp.post("/admin/api/admin-requests/<int:request_id>/approve")
@admin_required
@records("Approved admin request")
def approve_admin_request(request_id):
    return _decide(request_id, True)


@admin_bp.post("/admin/api/admin-requests/<int:request_id>/reject")
@admin_required
@records("Rejected admin request")
def reject_admin_request(request_id):
    return _decide(request_id, False)
