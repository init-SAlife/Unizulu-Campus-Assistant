import hashlib
import os
import re
import secrets
import uuid
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import Json
from psycopg2.pool import ThreadedConnectionPool
from werkzeug.security import check_password_hash, generate_password_hash

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 8
MAX_TITLE_LENGTH = 80
SCHEMA_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "database", "accounts_schema.sql")

_pool = None
_pool_pid = None       # the process that made the pool (see _get_pool)
_pool_settings = None


class AccountError(ValueError):
    # A problem with what the user typed -- the message is safe to show them.
    pass


def init_pool(db_config, minconn=1, maxconn=10):
    global _pool_settings
    _pool_settings = (minconn, maxconn, dict(db_config))
    return _get_pool()


def _get_pool():
    # The connection pool for THIS process.
    global _pool, _pool_pid
    if _pool is None or _pool_pid != os.getpid():
        if _pool_settings is None:
            raise RuntimeError("accounts.init_pool() must be called first")
        minconn, maxconn, db_config = _pool_settings
        # keepalives: notice quickly when a hosted database drops an idle connection
        _pool = ThreadedConnectionPool(
            minconn, maxconn, **db_config,
            keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3,
        )
        _pool_pid = os.getpid()
    return _pool


@contextmanager
def _cursor():
    # A cursor on a pooled connection: commits on success, rolls back on any error, and always
    # returns the connection to the pool.
    pool = _get_pool()
    conn = pool.getconn()
    if conn.closed:                      # the database dropped it: throw it away, take a fresh one
        pool.putconn(conn, close=True)
        conn = pool.getconn()
    broken = False
    try:
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            broken = True                # connection died mid-request: don't give it back to the pool
            raise
        except Exception:
            conn.rollback()
            raise
        finally:
            if not cur.closed and not conn.closed:
                cur.close()
    finally:
        pool.putconn(conn, close=broken or bool(conn.closed))

#SCHEMA
EXPECTED_COLUMNS = {
    "users": {"id", "email", "full_name", "password_hash", "created_at", "last_login_at", "session_version",
              "role", "disabled"},
    "chat_sessions": {"id", "user_id", "title", "context", "created_at", "updated_at"},
    "chat_messages": {"id", "session_id", "role", "content", "created_at"},
    "password_resets": {"id", "user_id", "code_hash", "attempts", "reset_token_hash",
                        "created_at", "expires_at", "used_at"},
    "guest_chats": {"id", "context", "created_at", "updated_at"},
    "guest_questions": {"id", "ip_hash", "created_at"},
    "parking_status": {"parking_id", "status", "reported_at", "reports"},
    "explorer_views": {"id", "programme_code", "viewed_at"},
    "response_feedback": {"id", "user_id", "chat_id", "question", "answer", "rating", "created_at",
                          "status", "resolved_at", "resolved_by"},
    "admin_sessions": {"id", "admin_id", "login_at", "logout_at", "last_seen", "ended_by", "ip", "user_agent"},
    "admin_actions": {"id", "session_id", "admin_id", "action", "details", "created_at"},
    "usage_events": {"id", "kind", "user_id", "details", "created_at"},
    "admin_requests": {"id", "user_id", "status", "reason", "code_hash", "code_sent_at", "code_expires_at",
                       "attempts", "requested_at", "verified_at", "decided_at", "decided_by"},
    "pending_signups": {"id", "email", "full_name", "password_hash", "account_type", "admin_reason", "created_at"},
    "email_codes": {"id", "email", "purpose", "code_hash", "attempts", "created_at", "expires_at", "used_at"},
    "code_sends": {"id", "email", "purpose", "ip_hash", "sent_at"},
    "trusted_devices": {"id", "user_id", "token_hash", "created_at", "expires_at", "last_used_at", "user_agent"},
}


def ensure_schema():
    # Creates the tables if they don't exist, then checks they have the expected columns.
    with open(SCHEMA_FILE, encoding="utf-8") as f:
        ddl = f.read()
    with _cursor() as cur:
        cur.execute(ddl)
        for table, expected in EXPECTED_COLUMNS.items():
            cur.execute("""SELECT column_name FROM information_schema.columns
                           WHERE table_schema = 'public' AND table_name = %s""", (table,))
            missing = expected - {r[0] for r in cur.fetchall()}
            if missing:
                raise RuntimeError(
                    f"Table '{table}' already exists but is missing column(s): "
                    f"{', '.join(sorted(missing))}. It is probably an older table with the same "
                    f"name -- rename or drop it, then restart the server."
                )
    sync_admin_emails()


def sync_admin_emails():
    # ADMIN_EMAILS (comma separated, in .env or Render's Environment) get the admin role when
    # the server starts.
    emails = listed_admin_emails()
    if not emails:
        return
    with _cursor() as cur:
        cur.execute("UPDATE users SET role = 'admin' WHERE lower(email) = ANY(%s) AND role <> 'admin'", (emails,))
        cur.execute("""UPDATE admin_requests SET status = 'approved', decided_at = now()
                       WHERE status = 'pending' AND user_id IN (SELECT id FROM users WHERE lower(email) = ANY(%s))""",
                    (emails,))


#USERS
def _user_dict(row, role=None):
    if not row:
        return None
    user = {"id": row[0], "email": row[1], "full_name": row[2]}
    if role is not None:
        user["role"] = role
    return user


class AccountDisabled(Exception):
    # The email and password are right, but an admin has disabled the account.
    pass


def _clean_email(email):
    return str(email or "").strip().lower()


def _check_new_account(full_name, email, password):
    # Cleans and checks what was typed on Create account.
    full_name = re.sub(r"\s+", " ", str(full_name or "")).strip()
    email = _clean_email(email)
    password = str(password or "")
    if not full_name:
        raise AccountError("Please enter your full name.")
    if len(full_name) > 120:
        raise AccountError("That name is too long.")
    if not EMAIL_PATTERN.match(email) or len(email) > 254:
        raise AccountError("Please enter a valid email address.")
    _check_new_password(password)
    return full_name, email, password


def authenticate(email, password):
    # The user if the email and password match, otherwise None.
    email = _clean_email(email)
    with _cursor() as cur:
        cur.execute("""SELECT id, email, full_name, password_hash, role, disabled
                       FROM users WHERE lower(email) = %s""", (email,))
        row = cur.fetchone()
        if not row or not check_password_hash(row[3], str(password or "")):
            return None
        if row[5]:
            raise AccountDisabled()
        cur.execute("UPDATE users SET last_login_at = now() WHERE id = %s", (row[0],))
        return _user_dict(row[:3], row[4])


def get_user(user_id):
    with _cursor() as cur:
        cur.execute("SELECT id, email, full_name FROM users WHERE id = %s", (user_id,))
        return _user_dict(cur.fetchone())


def get_session_user(user_id, session_version):
    # The user for a sign-in cookie, or None if the cookie is out of date (the password was
    # changed after it was made, so it no longer counts).
    with _cursor() as cur:
        cur.execute("SELECT id, email, full_name, session_version, role, disabled FROM users WHERE id = %s",
                    (user_id,))
        row = cur.fetchone()
    if not row or row[3] != (session_version or 0) or row[5]:
        return None                              # out of date, or disabled by an admin
    return _user_dict(row[:3], row[4])


def get_session_version(user_id):
    with _cursor() as cur:
        cur.execute("SELECT session_version FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
    return row[0] if row else 0


#passwords
RESET_CODE_MINUTES = 10        # an emailed code works for this long
RESET_TOKEN_MINUTES = 15       # after the right code: time left to choose the new password
RESET_MAX_ATTEMPTS = 5         # wrong guesses allowed per code


def _check_new_password(password):
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AccountError(f"Your password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > 200:
        raise AccountError("That password is too long.")


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _set_password(cur, user_id, new_password):
    # Stores the new password and signs out every existing sign-in cookie.
    cur.execute("""UPDATE users SET password_hash = %s, session_version = session_version + 1
                   WHERE id = %s RETURNING session_version""",
                (generate_password_hash(new_password), user_id))
    version = cur.fetchone()[0]
    cur.execute("DELETE FROM trusted_devices WHERE user_id = %s", (user_id,))
    return version


def change_password(user_id, current_password, new_password):
    # Settings -> Change password.
    current_password = str(current_password or "")
    new_password = str(new_password or "")
    with _cursor() as cur:
        cur.execute("SELECT password_hash FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        if not row or not check_password_hash(row[0], current_password):
            raise AccountError("Your current password is not correct.")
        _check_new_password(new_password)
        if check_password_hash(row[0], new_password):
            raise AccountError("Your new password must be different from your current one.")
        return _set_password(cur, user_id, new_password)


def start_password_reset(email, ip_hash):
    # Makes a new 6-digit code for this email. Returns (user, code, send_id, status) or None values.
    email = _clean_email(email)
    if not EMAIL_PATTERN.match(email):
        raise AccountError("Please enter a valid email address.")
    with _cursor() as cur:
        # the limits count for every email typed, so the numbers shown never tell whether an
        # account exists
        status = _check_can_send(cur, email, ip_hash)
        cur.execute("SELECT id, email, full_name FROM users WHERE lower(email) = %s", (email,))
        row = cur.fetchone()
        if not row:
            _record_send(cur, email, "reset", ip_hash)
            return None, None, None, _after_send(status)
        user = _user_dict(row)
        code = f"{secrets.randbelow(1_000_000):06d}"
        # only the newest code works
        cur.execute("UPDATE password_resets SET used_at = now() WHERE user_id = %s AND used_at IS NULL",
                    (user["id"],))
        cur.execute("""INSERT INTO password_resets (user_id, code_hash, expires_at)
                       VALUES (%s, %s, now() + %s * interval '1 minute')""",
                    (user["id"], generate_password_hash(code), RESET_CODE_MINUTES))
        send_id = _record_send(cur, email, "reset", ip_hash)
        return user, code, send_id, _after_send(status)


def verify_reset_code(email, code):
    # Checks the emailed code.
    email = _clean_email(email)
    code = re.sub(r"\D", "", str(code or ""))
    wrong = AccountError("That code is not correct. Please check the email and try again.")
    if len(code) != 6:
        raise AccountError("Please enter the 6-digit code from the email.")
    with _cursor() as cur:
        cur.execute("""SELECT r.id, r.code_hash, r.attempts, r.expires_at < now()
                       FROM password_resets r JOIN users u ON u.id = r.user_id
                       WHERE lower(u.email) = %s AND r.used_at IS NULL AND r.reset_token_hash IS NULL
                       ORDER BY r.created_at DESC LIMIT 1""", (email,))
        row = cur.fetchone()
        if not row:
            raise wrong
        reset_id, code_hash, attempts, expired = row
        if expired:
            raise AccountError("This code has expired. Please ask for a new one.")
        if attempts >= RESET_MAX_ATTEMPTS:
            raise AccountError("Too many wrong tries. Please ask for a new code.")
        wrong_try = not check_password_hash(code_hash, code)
        if wrong_try:
            cur.execute("UPDATE password_resets SET attempts = attempts + 1 WHERE id = %s", (reset_id,))
        else:
            token = secrets.token_urlsafe(32)
            cur.execute("""UPDATE password_resets
                           SET reset_token_hash = %s, expires_at = now() + %s * interval '1 minute'
                           WHERE id = %s""", (_sha256(token), RESET_TOKEN_MINUTES, reset_id))
    # raised outside the block, so the wrong try is saved
    if wrong_try:
        raise _wrong_code_error(RESET_MAX_ATTEMPTS - attempts - 1)
    return token


def finish_password_reset(email, token, new_password):
    # Sets the new password using the token from verify_reset_code.
    email = _clean_email(email)
    new_password = str(new_password or "")
    with _cursor() as cur:
        cur.execute("""SELECT r.id, u.id, u.email, u.full_name
                       FROM password_resets r JOIN users u ON u.id = r.user_id
                       WHERE lower(u.email) = %s AND r.reset_token_hash = %s
                         AND r.used_at IS NULL AND r.expires_at > now()""",
                    (email, _sha256(str(token or ""))))
        row = cur.fetchone()
        if not row:
            raise AccountError("This reset link has expired. Please start again and ask for a new code.")
        _check_new_password(new_password)
        _set_password(cur, row[1], new_password)
        cur.execute("UPDATE password_resets SET used_at = now() WHERE id = %s", (row[0],))
        return _user_dict(row[1:])


#which emails can be used: Gmail only, for every account type
ALLOWED_EMAIL_DOMAIN = "gmail.com"
WRONG_EMAIL_MESSAGE = "Please use a Gmail address (ending in @gmail.com)."


def email_allowed(email):
    return _clean_email(email).rsplit("@", 1)[-1] == ALLOWED_EMAIL_DOMAIN


def listed_admin_emails():
    # ADMIN_EMAILS in .env / Render: these accounts are always admins
    return [e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()]


def apply_admin_list(user):
    # Makes a listed account an admin straight away (no restart needed) and returns the user.
    if user and user.get("role") != "admin" and user["email"].lower() in listed_admin_emails():
        with _cursor() as cur:
            cur.execute("UPDATE users SET role = 'admin' WHERE id = %s", (user["id"],))
            cur.execute("UPDATE admin_requests SET status = 'approved', decided_at = now() "
                        "WHERE user_id = %s AND status = 'pending'", (user["id"],))
        user = {**user, "role": "admin"}
    return user


#email codes and their limits
def _env_int(setting, default):
    try:
        return max(0, int(os.environ.get(setting, default)))
    except ValueError:
        return default


CODE_MINUTES = 10                                           # a code works for this long
CODE_MAX_ATTEMPTS = 5                                       # wrong tries per code
CODE_EMAILS_PER_DAY = _env_int("CODE_EMAILS_PER_DAY", 5)    # code emails per address per day
CODE_RESEND_SECONDS = _env_int("CODE_RESEND_SECONDS", 120)  # wait between two code emails
CODE_EMAILS_PER_IP_DAY = _env_int("CODE_EMAILS_PER_IP_DAY", 30)
PENDING_SIGNUP_HOURS = 24
TRUSTED_DAYS = {"student": _env_int("TRUSTED_DAYS_STUDENT", 5), "admin": _env_int("TRUSTED_DAYS_ADMIN", 1)}
# the daily count starts again at midnight South African time
SA_DAY_START = "(date_trunc('day', now() AT TIME ZONE 'Africa/Johannesburg') AT TIME ZONE 'Africa/Johannesburg')"


class CodeLimit(AccountError):
    # No code email can be sent right now; says how many are left today and how long to wait.
    def __init__(self, message, left, wait):
        super().__init__(message)
        self.left = left
        self.wait = wait


def wait_text(seconds):
    minutes, secs = divmod(int(seconds), 60)
    if minutes and secs:
        return f"{minutes} min {secs} s"
    return f"{minutes} min" if minutes else f"{secs} s"


def _wrong_code_error(left):
    if left <= 0:
        return AccountError("That code is not correct, and there are no tries left. Please ask for a new code.")
    return AccountError(f"That code is not correct. {left} {'try' if left == 1 else 'tries'} left.")


def _send_status(cur, email, ip_hash=None):
    cur.execute(f"""SELECT count(*) FILTER (WHERE sent_at >= {SA_DAY_START}),
                           extract(epoch FROM now() - max(sent_at))
                    FROM code_sends WHERE lower(email) = %s AND sent_at > now() - interval '2 days'""",
                (email,))
    today, since_last = cur.fetchone()
    wait = 0
    if since_last is not None and since_last < CODE_RESEND_SECONDS:
        wait = int(CODE_RESEND_SECONDS - since_last) + 1
    ip_today = 0
    if ip_hash:
        cur.execute(f"SELECT count(*) FROM code_sends WHERE ip_hash = %s AND sent_at >= {SA_DAY_START}",
                    (ip_hash,))
        ip_today = cur.fetchone()[0]
    return {"left": max(0, CODE_EMAILS_PER_DAY - today), "wait": wait, "ip_today": ip_today}


def _status_dict(status):
    return {"left": status["left"], "wait": status["wait"], "per_day": CODE_EMAILS_PER_DAY}


def _after_send(status):
    # the numbers to show right after a code email went out
    return {"left": max(0, status["left"] - 1), "wait": CODE_RESEND_SECONDS, "per_day": CODE_EMAILS_PER_DAY}


def _check_can_send(cur, email, ip_hash):
    status = _send_status(cur, email, ip_hash)
    if status["left"] <= 0:
        raise CodeLimit(f"You have used all {CODE_EMAILS_PER_DAY} code emails for today. "
                        "Please try again after midnight.", 0, 0)
    if status["wait"] > 0:
        raise CodeLimit(f"Please wait {wait_text(status['wait'])} before asking for another code.",
                        status["left"], status["wait"])
    if status["ip_today"] >= CODE_EMAILS_PER_IP_DAY:
        raise CodeLimit("Too many codes were asked for from this network today. Please try again tomorrow.",
                        status["left"], 0)
    return status


def _record_send(cur, email, purpose, ip_hash):
    cur.execute("INSERT INTO code_sends (email, purpose, ip_hash) VALUES (%s, %s, %s) RETURNING id",
                (email, purpose, ip_hash))
    return cur.fetchone()[0]


def cancel_send(send_id):
    # The email could not be sent: it does not count towards the limit.
    if send_id:
        with _cursor() as cur:
            cur.execute("DELETE FROM code_sends WHERE id = %s", (send_id,))


def code_send_status(email):
    with _cursor() as cur:
        return _status_dict(_send_status(cur, _clean_email(email)))


def issue_code(email, purpose, ip_hash):
    # Checks the limits, makes a new code (older ones stop working) and counts the email.
    email = _clean_email(email)
    with _cursor() as cur:
        status = _check_can_send(cur, email, ip_hash)
        code = f"{secrets.randbelow(1_000_000):06d}"
        cur.execute("UPDATE email_codes SET used_at = now() WHERE lower(email) = %s AND purpose = %s AND used_at IS NULL",
                    (email, purpose))
        cur.execute("""INSERT INTO email_codes (email, purpose, code_hash, expires_at)
                       VALUES (%s, %s, %s, now() + %s * interval '1 minute')""",
                    (email, purpose, generate_password_hash(code), CODE_MINUTES))
        send_id = _record_send(cur, email, purpose, ip_hash)
    return code, send_id, _after_send(status)


def check_code(email, purpose, code):
    # Raises AccountError unless the code is the newest one for this email and still valid.
    email = _clean_email(email)
    code = re.sub(r"\D", "", str(code or ""))
    if len(code) != 6:
        raise AccountError("Please enter the 6-digit code from the email.")
    with _cursor() as cur:
        cur.execute("""SELECT id, code_hash, attempts, expires_at < now() FROM email_codes
                       WHERE lower(email) = %s AND purpose = %s AND used_at IS NULL
                       ORDER BY created_at DESC LIMIT 1""", (email, purpose))
        row = cur.fetchone()
        if not row:
            raise AccountError("This code is no longer valid. Please ask for a new one.")
        code_id, code_hash, attempts, expired = row
        if expired:
            raise AccountError("This code has expired. Please ask for a new one.")
        if attempts >= CODE_MAX_ATTEMPTS:
            raise AccountError("Too many wrong tries. Please ask for a new code.")
        wrong_try = not check_password_hash(code_hash, code)
        if wrong_try:
            cur.execute("UPDATE email_codes SET attempts = attempts + 1 WHERE id = %s", (code_id,))
        else:
            cur.execute("UPDATE email_codes SET used_at = now() WHERE id = %s", (code_id,))
    # raised outside the block, so the wrong try is saved
    if wrong_try:
        raise _wrong_code_error(CODE_MAX_ATTEMPTS - attempts - 1)


#creating an account (finished only after the email code)
def start_signup(full_name, email, password, account_type, admin_reason=""):
    if account_type not in ("student", "admin"):
        raise AccountError("Please choose your account type: Student or Administrator.")
    full_name, email, password = _check_new_account(full_name, email, password)
    if not email_allowed(email):
        raise AccountError(WRONG_EMAIL_MESSAGE)
    reason = re.sub(r"\s+", " ", str(admin_reason or "")).strip()[:300]
    if account_type == "admin" and len(reason) < 3:
        raise AccountError("Please say what your role at UNIZULU is.")
    with _cursor() as cur:
        cur.execute("SELECT 1 FROM users WHERE lower(email) = %s", (email,))
        if cur.fetchone():
            raise AccountError("An account with that email already exists. Please sign in instead.")
        cur.execute("DELETE FROM pending_signups WHERE lower(email) = %s OR created_at < now() - %s * interval '1 hour'",
                    (email, PENDING_SIGNUP_HOURS))
        cur.execute("""INSERT INTO pending_signups (email, full_name, password_hash, account_type, admin_reason)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (email, full_name, generate_password_hash(password), account_type, reason or None))
    return {"email": email, "full_name": full_name, "account_type": account_type}


def pending_signup(email):
    # (full_name, account_type) of an unfinished sign-up, or None.
    with _cursor() as cur:
        cur.execute("""SELECT full_name, account_type FROM pending_signups
                       WHERE lower(email) = %s AND created_at > now() - %s * interval '1 hour'""",
                    (_clean_email(email), PENDING_SIGNUP_HOURS))
        return cur.fetchone()


def finish_signup(email):
    # Makes the account once the code was right. An admin sign-up also becomes a pending request.
    email = _clean_email(email)
    with _cursor() as cur:
        cur.execute("""SELECT full_name, password_hash, account_type, admin_reason FROM pending_signups
                       WHERE lower(email) = %s AND created_at > now() - %s * interval '1 hour'""",
                    (email, PENDING_SIGNUP_HOURS))
        row = cur.fetchone()
        if not row:
            raise AccountError("Your sign-up has expired. Please create your account again.")
        full_name, password_hash, account_type, reason = row
        cur.execute("SELECT 1 FROM users WHERE lower(email) = %s", (email,))
        if cur.fetchone():
            raise AccountError("An account with that email already exists. Please sign in instead.")
        # an email on ADMIN_EMAILS is an admin at once; anyone else who asked gets a pending request
        role = "admin" if email in listed_admin_emails() else "student"
        cur.execute("""INSERT INTO users (email, full_name, password_hash, role, last_login_at)
                       VALUES (%s, %s, %s, %s, now()) RETURNING id, email, full_name""",
                    (email, full_name, password_hash, role))
        user = _user_dict(cur.fetchone(), role)
        wants_admin = account_type == "admin" and role != "admin"
        if wants_admin:
            cur.execute("""INSERT INTO admin_requests (user_id, reason, status, verified_at)
                           VALUES (%s, %s, 'pending', now())""", (user["id"], reason))
        cur.execute("DELETE FROM pending_signups WHERE lower(email) = %s", (email,))
    return user, wants_admin, reason or ""


def get_login_user(user_id):
    # The user (with role) to sign in after the code, or AccountDisabled.
    with _cursor() as cur:
        cur.execute("SELECT id, email, full_name, role, disabled FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
    if not row:
        raise AccountError("This account no longer exists.")
    if row[4]:
        raise AccountDisabled()
    return _user_dict(row[:3], row[3])


#remembered devices ("Remember this device" on the code screen)
def trusted_days(role):
    return TRUSTED_DAYS["admin" if role == "admin" else "student"]


def trust_device(user_id, role, user_agent=""):
    # Returns (token for the cookie, days), or (None, 0) when remembering is switched off.
    days = trusted_days(role)
    if days <= 0:
        return None, 0
    token = secrets.token_urlsafe(32)
    with _cursor() as cur:
        cur.execute("DELETE FROM trusted_devices WHERE expires_at < now()")
        cur.execute("""INSERT INTO trusted_devices (user_id, token_hash, expires_at, user_agent)
                       VALUES (%s, %s, now() + %s * interval '1 day', %s)""",
                    (user_id, _sha256(token), days, (user_agent or "")[:300]))
    return token, days


def device_is_trusted(user_id, token):
    if not token:
        return False
    with _cursor() as cur:
        cur.execute("""UPDATE trusted_devices SET last_used_at = now()
                       WHERE user_id = %s AND token_hash = %s AND expires_at > now() RETURNING id""",
                    (user_id, _sha256(str(token))))
        return cur.fetchone() is not None


def forget_devices(user_id):
    with _cursor() as cur:
        cur.execute("DELETE FROM trusted_devices WHERE user_id = %s", (user_id,))


#admin requests
def get_admin_request(user_id):
    # {status, requested_at, decided_at} of this user's request, or None.
    with _cursor() as cur:
        cur.execute("SELECT status, requested_at, decided_at FROM admin_requests WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
    if not row:
        return None
    return {"status": row[0], "requested_at": row[1].isoformat(),
            "decided_at": row[2].isoformat() if row[2] else None}


def list_admin_requests():
    # Every request that has confirmed its email (pending first), for the dashboard.
    with _cursor() as cur:
        cur.execute("""SELECT r.id, u.full_name, u.email, r.reason, r.status, r.requested_at, r.verified_at,
                              r.decided_at, d.email, u.disabled
                       FROM admin_requests r
                       JOIN users u ON u.id = r.user_id
                       LEFT JOIN users d ON d.id = r.decided_by
                       WHERE r.status <> 'unverified'
                       ORDER BY (r.status = 'pending') DESC, coalesce(r.decided_at, r.verified_at) DESC""")
        rows = cur.fetchall()
    iso = lambda t: t.isoformat() if t else None
    return [{"id": r[0], "name": r[1], "email": r[2], "reason": r[3] or "", "status": r[4],
             "requested_at": iso(r[5]), "verified_at": iso(r[6]), "decided_at": iso(r[7]),
             "decided_by": r[8] or "", "disabled": r[9]} for r in rows]


def admin_emails():
    # Emails of every enabled admin (told when a new request is waiting).
    with _cursor() as cur:
        cur.execute("SELECT email FROM users WHERE role = 'admin' AND NOT disabled")
        return [r[0] for r in cur.fetchall()]


def count_pending_admin_requests():
    with _cursor() as cur:
        cur.execute("SELECT count(*) FROM admin_requests WHERE status = 'pending'")
        return cur.fetchone()[0]


def decide_admin_request(request_id, admin_id, approve):
    # Approve (the account becomes an admin) or reject a pending request.
    with _cursor() as cur:
        cur.execute("""UPDATE admin_requests SET status = %s, decided_at = now(), decided_by = %s
                       WHERE id = %s AND status = 'pending'
                       RETURNING user_id""",
                    ("approved" if approve else "rejected", admin_id, request_id))
        row = cur.fetchone()
        if not row:
            raise AccountError("This request has already been dealt with, or no longer exists.")
        if approve:
            cur.execute("UPDATE users SET role = 'admin' WHERE id = %s", (row[0],))
        cur.execute("SELECT id, email, full_name FROM users WHERE id = %s", (row[0],))
        return _user_dict(cur.fetchone())


#chats
def _valid_chat_id(chat_id):
    try:
        return str(uuid.UUID(str(chat_id)))
    except (ValueError, TypeError, AttributeError):
        return None


def create_chat(user_id, context):
    chat_id = str(uuid.uuid4())
    with _cursor() as cur:
        cur.execute("""INSERT INTO chat_sessions (id, user_id, context)
                       VALUES (%s, %s, %s)""", (chat_id, user_id, Json(context)))
    return {"id": chat_id, "title": None, "context": context}


def get_chat(user_id, chat_id):
    # The chat with its memory, or None if it doesn't exist OR belongs to someone else -- a user
    # can never open another user's chat.
    chat_id = _valid_chat_id(chat_id)
    if not chat_id:
        return None
    with _cursor() as cur:
        cur.execute("""SELECT id, title, context, created_at, updated_at
                       FROM chat_sessions WHERE id = %s AND user_id = %s""", (chat_id, user_id))
        row = cur.fetchone()
    if not row:
        return None
    return {"id": str(row[0]), "title": row[1], "context": row[2] or {},
            "created_at": row[3].isoformat(), "updated_at": row[4].isoformat()}


def list_chats(user_id):
    # The user's chats, most recently used first.
    with _cursor() as cur:
        cur.execute("""SELECT id, title, created_at, updated_at FROM chat_sessions
                       WHERE user_id = %s ORDER BY updated_at DESC""", (user_id,))
        return [{"id": str(r[0]), "title": r[1], "created_at": r[2].isoformat(),
                 "updated_at": r[3].isoformat()} for r in cur.fetchall()]


def save_exchange(user_id, chat_id, context, messages):
    # Stores new messages [(role, content), ...] and the chat's updated memory in one
    # transaction, and marks the chat as just used.
    with _cursor() as cur:
        cur.execute("""UPDATE chat_sessions SET context = %s, updated_at = now()
                       WHERE id = %s AND user_id = %s""", (Json(context), chat_id, user_id))
        if cur.rowcount != 1:
            raise LookupError("chat not found")
        for role, content in messages:
            cur.execute("""INSERT INTO chat_messages (session_id, role, content)
                           VALUES (%s, %s, %s)""", (chat_id, role, content))


def drop_last_exchange(user_id, chat_id, question):
    # "Regenerate": removes the chat's newest answer and the question before it (only when that
    # question is the one being asked again)
    chat_id = _valid_chat_id(chat_id)
    if not chat_id:
        return False
    with _cursor() as cur:
        cur.execute("""SELECT m.id, m.role, m.content
                       FROM chat_messages m JOIN chat_sessions s ON s.id = m.session_id
                       WHERE m.session_id = %s AND s.user_id = %s
                       ORDER BY m.id DESC LIMIT 2""", (chat_id, user_id))
        rows = cur.fetchall()
        if (len(rows) == 2 and rows[0][1] == "assistant" and rows[1][1] == "user"
                and rows[1][2].strip() == (question or "").strip()):
            cur.execute("DELETE FROM chat_messages WHERE id IN (%s, %s)", (rows[0][0], rows[1][0]))
            return True
    return False


def get_messages(user_id, chat_id):
    chat_id = _valid_chat_id(chat_id)
    if not chat_id:
        return []
    with _cursor() as cur:
        cur.execute("""SELECT m.role, m.content, m.created_at
                       FROM chat_messages m JOIN chat_sessions s ON s.id = m.session_id
                       WHERE m.session_id = %s AND s.user_id = %s
                       ORDER BY m.id""", (chat_id, user_id))
        return [{"role": r[0], "content": r[1], "created_at": r[2].isoformat()}
                for r in cur.fetchall()]


def count_user_messages(chat_id):
    with _cursor() as cur:
        cur.execute("SELECT count(*) FROM chat_messages WHERE session_id = %s AND role = 'user'",
                    (chat_id,))
        return cur.fetchone()[0]


def clean_title(title):
    title = re.sub(r"\s+", " ", str(title or "")).strip().strip("\"'`*#").strip()
    title = re.sub(r"^(title|chat title)\s*:\s*", "", title, flags=re.IGNORECASE).strip()
    title = title.rstrip(".!?:;,").strip()
    if len(title) > MAX_TITLE_LENGTH:
        title = title[:MAX_TITLE_LENGTH - 1].rsplit(" ", 1)[0].rstrip() + "…"
    return title


def set_title(user_id, chat_id, title):
    # Sets (or renames) a chat's title.
    title = clean_title(title)
    if not title:
        raise AccountError("The chat name can't be empty.")
    with _cursor() as cur:
        cur.execute("""UPDATE chat_sessions SET title = %s
                       WHERE id = %s AND user_id = %s RETURNING title""",
                    (title, _valid_chat_id(chat_id), user_id))
        row = cur.fetchone()
    return row[0] if row else None


def delete_chat(user_id, chat_id):
    chat_id = _valid_chat_id(chat_id)
    if not chat_id:
        return False
    with _cursor() as cur:
        cur.execute("DELETE FROM chat_sessions WHERE id = %s AND user_id = %s", (chat_id, user_id))
        return cur.rowcount == 1


# guests Visitors who are not signed in can use the Campus Navigator freely and ask the AI
# Assistant a few questions (limits are in chatbot_routes.py).
GUEST_KEEP_HOURS = 24


def hash_ip(ip):
    # A salted hash of a network address, so the address itself is never stored.
    salt = os.environ.get("FLASK_SECRET_KEY", "campus-ai-guest")
    return _sha256(f"{salt}|{ip or 'unknown'}")


def get_guest_chat(chat_id):
    # The guest chat's memory, or None if it doesn't exist or has expired.
    chat_id = _valid_chat_id(chat_id)
    if not chat_id:
        return None
    with _cursor() as cur:
        cur.execute("""SELECT context FROM guest_chats
                       WHERE id = %s AND updated_at > now() - %s * interval '1 hour'""",
                    (chat_id, GUEST_KEEP_HOURS))
        row = cur.fetchone()
    return {"id": chat_id, "context": row[0] or {}} if row else None


def save_guest_chat(chat_id, context):
    # Creates or updates a guest chat.
    chat_id = _valid_chat_id(chat_id) or str(uuid.uuid4())
    with _cursor() as cur:
        cur.execute("""INSERT INTO guest_chats (id, context) VALUES (%s, %s)
                       ON CONFLICT (id) DO UPDATE SET context = EXCLUDED.context, updated_at = now()""",
                    (chat_id, Json(context)))
    return chat_id


def record_guest_question(ip_hash, per_ip_per_hour, all_guests_per_hour):
    # Counts one guest question.
    with _cursor() as cur:
        cur.execute("DELETE FROM guest_questions WHERE created_at < now() - interval '2 days'")
        cur.execute("DELETE FROM guest_chats WHERE updated_at < now() - %s * interval '1 hour'",
                    (GUEST_KEEP_HOURS,))
        cur.execute("""SELECT count(*) FILTER (WHERE ip_hash = %s), count(*)
                       FROM guest_questions WHERE created_at > now() - interval '1 hour'""",
                    (ip_hash,))
        from_network, from_everyone = cur.fetchone()
        if from_everyone >= all_guests_per_hour:
            return "busy"
        if from_network >= per_ip_per_hour:
            return "network"
        cur.execute("INSERT INTO guest_questions (ip_hash) VALUES (%s)", (ip_hash,))
    return None


#ANSWER FEEDBACK
def save_feedback(user_id, chat_id, question, answer, rating):
    # Stores a "Good response" / "Bad response" rating for one AI answer.
    if rating not in ("good", "bad"):
        raise ValueError("rating must be 'good' or 'bad'")
    with _cursor() as cur:
        # a "bad" rating waits in the admin Feedback Inbox until it is resolved
        cur.execute("""INSERT INTO response_feedback (user_id, chat_id, question, answer, rating, status)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (user_id, chat_id, question, answer, rating, "open" if rating == "bad" else "done"))


#USAGE EVENTS (counted on the admin dashboard)
def log_event(kind, user_id=None, details=None):
    # Records one sign-in / upload / registration check / navigator route.
    try:
        with _cursor() as cur:
            cur.execute("INSERT INTO usage_events (kind, user_id, details) VALUES (%s, %s, %s)",
                        (kind, user_id, (details or "")[:300] or None))
    except Exception as exc:
        print(f"[usage] could not record {kind}: {exc}")
