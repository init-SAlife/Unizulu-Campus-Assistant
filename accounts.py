"""
Accounts and chats: registered users, their chat sessions, and every
message in those chats, all stored in PostgreSQL.

Each chat keeps its own memory (the chatbot "context": chosen handbook,
last programme/module, uploaded documents, pending suggestion, recent
history) in chat_sessions.context, so chats never share state -- neither
between users nor between two chats of the same user.

This module uses its OWN small connection pool, separate from the single
read-only connection in Chatbot.py. Account and chat writes commit
immediately and several users can be served at the same time without
sharing one connection's transaction.
"""

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
SCHEMA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "accounts_schema.sql")

_pool = None
_pool_pid = None       # the process that made the pool (see _get_pool)
_pool_settings = None


class AccountError(ValueError):
    """A problem with what the user typed -- the message is safe to show them."""


def init_pool(db_config, minconn=1, maxconn=10):
    global _pool_settings
    _pool_settings = (minconn, maxconn, dict(db_config))
    return _get_pool()


def _get_pool():
    """The connection pool for THIS process.

    gunicorn starts the app once and then copies it into each worker
    process. A database connection must never be shared by two processes:
    over SSL (Render) that corrupts it ("SSL error: decryption failed or bad
    record mac", "SSL SYSCALL error: EOF detected"). So each worker makes its
    own pool the first time it needs one. The parent's connections are left
    alone (closing them here would break them for the parent too)."""
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
    """A cursor on a pooled connection: commits on success, rolls back on
    any error, and always returns the connection to the pool."""
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


# ------------------------------------------------------------------ schema
EXPECTED_COLUMNS = {
    "users": {"id", "email", "full_name", "password_hash", "created_at", "last_login_at", "session_version"},
    "chat_sessions": {"id", "user_id", "title", "context", "created_at", "updated_at"},
    "chat_messages": {"id", "session_id", "role", "content", "created_at"},
    "password_resets": {"id", "user_id", "code_hash", "attempts", "reset_token_hash",
                        "created_at", "expires_at", "used_at"},
    "guest_chats": {"id", "context", "created_at", "updated_at"},
    "guest_questions": {"id", "ip_hash", "created_at"},
}


def ensure_schema():
    """Creates the tables if they don't exist, then checks they have
    the expected columns. An older table with the same name but different
    columns (e.g. an old chat_sessions) is reported clearly instead of
    causing confusing errors later."""
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


# ------------------------------------------------------------------ users
def _user_dict(row):
    return {"id": row[0], "email": row[1], "full_name": row[2]} if row else None


def _clean_email(email):
    return str(email or "").strip().lower()


def register_user(full_name, email, password):
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

    try:
        with _cursor() as cur:
            cur.execute("""INSERT INTO users (email, full_name, password_hash, last_login_at)
                           VALUES (%s, %s, %s, now())
                           RETURNING id, email, full_name""",
                        (email, full_name, generate_password_hash(password)))
            return _user_dict(cur.fetchone())
    except Exception as exc:
        if "users_email_key" in str(exc):
            raise AccountError("An account with that email already exists. Please sign in instead.")
        raise


def authenticate(email, password):
    """The user if the email and password match, otherwise None. The same
    None is returned for "no such email" and "wrong password", so the login
    page never reveals which emails are registered."""
    email = _clean_email(email)
    with _cursor() as cur:
        cur.execute("SELECT id, email, full_name, password_hash FROM users WHERE lower(email) = %s",
                    (email,))
        row = cur.fetchone()
        if not row or not check_password_hash(row[3], str(password or "")):
            return None
        cur.execute("UPDATE users SET last_login_at = now() WHERE id = %s", (row[0],))
        return _user_dict(row[:3])


def get_user(user_id):
    with _cursor() as cur:
        cur.execute("SELECT id, email, full_name FROM users WHERE id = %s", (user_id,))
        return _user_dict(cur.fetchone())


def get_session_user(user_id, session_version):
    """The user for a sign-in cookie, or None if the cookie is out of date
    (the password was changed after it was made, so it no longer counts)."""
    with _cursor() as cur:
        cur.execute("SELECT id, email, full_name, session_version FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
    if not row or row[3] != (session_version or 0):
        return None
    return _user_dict(row[:3])


def get_session_version(user_id):
    with _cursor() as cur:
        cur.execute("SELECT session_version FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
    return row[0] if row else 0


# ------------------------------------------------------------------ passwords
RESET_CODE_MINUTES = 10        # an emailed code works for this long
RESET_TOKEN_MINUTES = 15       # after the right code: time left to choose the new password
RESET_MAX_ATTEMPTS = 5         # wrong guesses allowed per code
RESET_RESEND_SECONDS = 60      # wait at least this long between two codes
RESET_MAX_PER_HOUR = 5         # codes per account per hour


def _check_new_password(password):
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AccountError(f"Your password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > 200:
        raise AccountError("That password is too long.")


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _set_password(cur, user_id, new_password):
    """Stores the new password and signs out every existing sign-in cookie.
    Returns the new session version (to keep the current device signed in)."""
    cur.execute("""UPDATE users SET password_hash = %s, session_version = session_version + 1
                   WHERE id = %s RETURNING session_version""",
                (generate_password_hash(new_password), user_id))
    return cur.fetchone()[0]


def change_password(user_id, current_password, new_password):
    """Settings -> Change password. Returns the new session version."""
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


def start_password_reset(email):
    """Makes a new 6-digit code for this email. Returns (user, code), or
    (None, None) when no account has this email -- the caller answers the
    same way in both cases, so the page never reveals which emails exist."""
    email = _clean_email(email)
    if not EMAIL_PATTERN.match(email):
        raise AccountError("Please enter a valid email address.")
    with _cursor() as cur:
        cur.execute("SELECT id, email, full_name FROM users WHERE lower(email) = %s", (email,))
        row = cur.fetchone()
        if not row:
            return None, None
        user = _user_dict(row)

        cur.execute("""SELECT count(*),
                              coalesce(extract(epoch FROM now() - max(created_at)), 1e9)
                       FROM password_resets
                       WHERE user_id = %s AND created_at > now() - interval '1 hour'""", (user["id"],))
        count, seconds_since_last = cur.fetchone()
        if seconds_since_last < RESET_RESEND_SECONDS:
            wait = int(RESET_RESEND_SECONDS - seconds_since_last) + 1
            raise AccountError(f"Please wait {wait} seconds before asking for another code.")
        if count >= RESET_MAX_PER_HOUR:
            raise AccountError("Too many codes were requested. Please try again in an hour.")

        code = f"{secrets.randbelow(1_000_000):06d}"
        # only the newest code works
        cur.execute("UPDATE password_resets SET used_at = now() WHERE user_id = %s AND used_at IS NULL",
                    (user["id"],))
        cur.execute("""INSERT INTO password_resets (user_id, code_hash, expires_at)
                       VALUES (%s, %s, now() + %s * interval '1 minute')""",
                    (user["id"], generate_password_hash(code), RESET_CODE_MINUTES))
        return user, code


def verify_reset_code(email, code):
    """Checks the emailed code. Returns a one-time reset token when it is right."""
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
        if not check_password_hash(code_hash, code):
            cur.execute("UPDATE password_resets SET attempts = attempts + 1 WHERE id = %s", (reset_id,))
            left = RESET_MAX_ATTEMPTS - attempts - 1
            if left <= 0:
                raise AccountError("That code is not correct, and there are no tries left. Please ask for a new code.")
            raise AccountError(f"That code is not correct. {left} {'try' if left == 1 else 'tries'} left.")
        token = secrets.token_urlsafe(32)
        cur.execute("""UPDATE password_resets
                       SET reset_token_hash = %s, expires_at = now() + %s * interval '1 minute'
                       WHERE id = %s""", (_sha256(token), RESET_TOKEN_MINUTES, reset_id))
        return token


def finish_password_reset(email, token, new_password):
    """Sets the new password using the token from verify_reset_code."""
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


# ------------------------------------------------------------------ chats
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
    """The chat with its memory, or None if it doesn't exist OR belongs to
    someone else -- a user can never open another user's chat."""
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
    """The user's chats, most recently used first."""
    with _cursor() as cur:
        cur.execute("""SELECT id, title, created_at, updated_at FROM chat_sessions
                       WHERE user_id = %s ORDER BY updated_at DESC""", (user_id,))
        return [{"id": str(r[0]), "title": r[1], "created_at": r[2].isoformat(),
                 "updated_at": r[3].isoformat()} for r in cur.fetchall()]


def save_exchange(user_id, chat_id, context, messages):
    """Stores new messages [(role, content), ...] and the chat's updated
    memory in one transaction, and marks the chat as just used."""
    with _cursor() as cur:
        cur.execute("""UPDATE chat_sessions SET context = %s, updated_at = now()
                       WHERE id = %s AND user_id = %s""", (Json(context), chat_id, user_id))
        if cur.rowcount != 1:
            raise LookupError("chat not found")
        for role, content in messages:
            cur.execute("""INSERT INTO chat_messages (session_id, role, content)
                           VALUES (%s, %s, %s)""", (chat_id, role, content))


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
    """Sets (or renames) a chat's title. Returns the stored title, or None
    if the chat doesn't belong to this user."""
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


# ------------------------------------------------------------------ guests
# Visitors who are not signed in can use the Campus Navigator freely and ask
# the AI Assistant a few questions (limits are in chatbot_routes.py). A
# guest chat's memory lives in guest_chats for a day; guest_questions only
# counts questions so the limits work across all server workers.
GUEST_KEEP_HOURS = 24


def hash_ip(ip):
    """A salted hash of a network address, so the address itself is never stored."""
    salt = os.environ.get("FLASK_SECRET_KEY", "campus-ai-guest")
    return _sha256(f"{salt}|{ip or 'unknown'}")


def get_guest_chat(chat_id):
    """The guest chat's memory, or None if it doesn't exist or has expired."""
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
    """Creates or updates a guest chat. Returns its id."""
    chat_id = _valid_chat_id(chat_id) or str(uuid.uuid4())
    with _cursor() as cur:
        cur.execute("""INSERT INTO guest_chats (id, context) VALUES (%s, %s)
                       ON CONFLICT (id) DO UPDATE SET context = EXCLUDED.context, updated_at = now()""",
                    (chat_id, Json(context)))
    return chat_id


def record_guest_question(ip_hash, per_ip_per_hour, all_guests_per_hour):
    """Counts one guest question. Returns None when it is allowed, or
    "network" / "busy" when the per-network or all-guests hourly limit is
    already used up (then nothing is counted). Old guest rows are cleaned
    up here too."""
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
