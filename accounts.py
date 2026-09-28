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

import os
import re
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


class AccountError(ValueError):
    """A problem with what the user typed -- the message is safe to show them."""


def init_pool(db_config, minconn=1, maxconn=10):
    global _pool
    if _pool is None:
        # keepalives: notice quickly when a hosted database drops an idle connection
        _pool = ThreadedConnectionPool(
            minconn, maxconn, **db_config,
            keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3,
        )
    return _pool


@contextmanager
def _cursor():
    """A cursor on a pooled connection: commits on success, rolls back on
    any error, and always returns the connection to the pool."""
    if _pool is None:
        raise RuntimeError("accounts.init_pool() must be called first")
    conn = _pool.getconn()
    if conn.closed:                      # the database dropped it: throw it away, take a fresh one
        _pool.putconn(conn, close=True)
        conn = _pool.getconn()
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
        _pool.putconn(conn, close=broken or bool(conn.closed))


# ------------------------------------------------------------------ schema
EXPECTED_COLUMNS = {
    "users": {"id", "email", "full_name", "password_hash", "created_at", "last_login_at"},
    "chat_sessions": {"id", "user_id", "title", "context", "created_at", "updated_at"},
    "chat_messages": {"id", "session_id", "role", "content", "created_at"},
}


def ensure_schema():
    """Creates the three tables if they don't exist, then checks they have
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
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AccountError(f"Your password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > 200:
        raise AccountError("That password is too long.")

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
