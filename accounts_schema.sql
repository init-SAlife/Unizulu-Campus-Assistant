-- =====================================================================
-- Accounts and chats for the Campus AI Assistant.
--
-- api.py creates these tables automatically when the server starts
-- (accounts.ensure_schema()), so running this file by hand is optional.
-- It is safe to run more than once.
--
--   psql -U postgres -d Campus_AI_Assistant_database -f accounts_schema.sql
-- =====================================================================

-- One row per registered student.
CREATE TABLE IF NOT EXISTS users (
    id             serial PRIMARY KEY,
    email          text NOT NULL,
    full_name      text NOT NULL,
    password_hash  text NOT NULL,              -- never the password itself
    created_at     timestamptz NOT NULL DEFAULT now(),
    last_login_at  timestamptz
);
-- one account per email address, ignoring upper/lower case
CREATE UNIQUE INDEX IF NOT EXISTS users_email_key ON users (lower(email));

-- One row per chat. Each chat has its own memory (context): chosen
-- handbook, last programme/module discussed, uploaded documents, the
-- pending follow-up suggestion, and the recent history the chatbot uses.
CREATE TABLE IF NOT EXISTS chat_sessions (
    id          uuid PRIMARY KEY,
    user_id     integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       text,                          -- written by Claude; NULL until the chat has a real question
    context     jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chat_sessions_user_recent ON chat_sessions (user_id, updated_at DESC);

-- Every message of every chat, in order, so an old chat can be reopened
-- and shown exactly as it was.
CREATE TABLE IF NOT EXISTS chat_messages (
    id          bigserial PRIMARY KEY,
    session_id  uuid NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role        text NOT NULL CHECK (role IN ('user', 'assistant')),
    content     text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chat_messages_session_order ON chat_messages (session_id, id);
