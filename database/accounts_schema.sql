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

-- Bumped whenever the password changes: every sign-in cookie made before
-- the change stops working, so other devices are signed out.
ALTER TABLE users ADD COLUMN IF NOT EXISTS session_version integer NOT NULL DEFAULT 0;

-- "Forgotten password?": one row per emailed code. Only hashes are stored,
-- never the code itself. After the right code is entered, a one-time
-- reset token (also stored as a hash) lets the student choose a new password.
CREATE TABLE IF NOT EXISTS password_resets (
    id                bigserial PRIMARY KEY,
    user_id           integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code_hash         text NOT NULL,
    attempts          integer NOT NULL DEFAULT 0,   -- wrong guesses so far
    reset_token_hash  text,                         -- set once the right code is entered
    created_at        timestamptz NOT NULL DEFAULT now(),
    expires_at        timestamptz NOT NULL,
    used_at           timestamptz                   -- set when used or replaced by a newer code
);
CREATE INDEX IF NOT EXISTS password_resets_user_recent ON password_resets (user_id, created_at DESC);

-- Guest mode: visitors who are not signed in can ask the AI Assistant a
-- few questions. Their chat memory is kept here for a day and then
-- deleted; nothing is linked to a person.
CREATE TABLE IF NOT EXISTS guest_chats (
    id          uuid PRIMARY KEY,
    context     jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS guest_chats_updated ON guest_chats (updated_at);

-- One row per guest question, used only for the guest limits (per network
-- per hour, and all guests together per hour). The network address is
-- stored as a salted hash, never the address itself. Rows older than two
-- days are deleted automatically.
CREATE TABLE IF NOT EXISTS guest_questions (
    id          bigserial PRIMARY KEY,
    ip_hash     text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS guest_questions_ip_recent ON guest_questions (ip_hash, created_at);
CREATE INDEX IF NOT EXISTS guest_questions_recent ON guest_questions (created_at);

-- Campus Navigator: which parking areas drivers have reported FULL
-- (see parking_status.py). One row per parking area; a "full" report
-- counts for PARKING_FULL_MINUTES (default 45) and then expires by itself.
CREATE TABLE IF NOT EXISTS parking_status (
    parking_id   text PRIMARY KEY,               -- OSM id of the parking, e.g. w1559223000
    status       text NOT NULL,                  -- 'full' or 'available'
    reported_at  double precision NOT NULL,      -- unix time of the latest report
    reports      integer NOT NULL DEFAULT 1      -- reports in a row with this status
);

-- Programme & Module Explorer: one row each time a programme page is opened,
-- for the "Popular programmes -- most viewed this week" list. Nothing is
-- linked to a person. Rows older than 30 days are deleted automatically.
CREATE TABLE IF NOT EXISTS explorer_views (
    id              bigserial PRIMARY KEY,
    programme_code  text NOT NULL,
    viewed_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS explorer_views_recent ON explorer_views (viewed_at);

-- AI Assistant: "Good response" / "Bad response" ratings under each answer.
-- user_id is NULL for guests. Used to see which answers students found
-- useful; the question and answer are kept so a bad answer can be looked at.
CREATE TABLE IF NOT EXISTS response_feedback (
    id          bigserial PRIMARY KEY,
    user_id     integer REFERENCES users(id) ON DELETE SET NULL,
    chat_id     text,
    question    text,
    answer      text NOT NULL,
    rating      text NOT NULL CHECK (rating IN ('good', 'bad')),
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS response_feedback_recent ON response_feedback (created_at);

-- ===================================================================
-- Admin dashboard (admin_routes.py, admin_sessions.py)
-- ===================================================================
-- who is an admin, and accounts an admin has disabled (a disabled
-- account cannot sign in, and is signed out everywhere)
ALTER TABLE users ADD COLUMN IF NOT EXISTS role text NOT NULL DEFAULT 'student';
ALTER TABLE users ADD COLUMN IF NOT EXISTS disabled boolean NOT NULL DEFAULT false;

-- every admin login -> logout, and what was done in it
CREATE TABLE IF NOT EXISTS admin_sessions (
    id          serial PRIMARY KEY,
    admin_id    integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    login_at    timestamptz NOT NULL DEFAULT now(),
    logout_at   timestamptz,
    last_seen   timestamptz NOT NULL DEFAULT now(),
    ended_by    varchar(10),                       -- 'logout' | 'timeout' (NULL while active)
    ip          varchar(45),
    user_agent  text
);
CREATE TABLE IF NOT EXISTS admin_actions (
    id          serial PRIMARY KEY,
    session_id  integer NOT NULL REFERENCES admin_sessions(id) ON DELETE CASCADE,
    admin_id    integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    action      varchar(80) NOT NULL,
    details     text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_admin_actions_session ON admin_actions(session_id);
CREATE INDEX IF NOT EXISTS idx_admin_actions_time ON admin_actions(created_at);

-- counts for the dashboard: sign-ins, uploads, registration checks,
-- navigator routes. No network address is stored for students.
CREATE TABLE IF NOT EXISTS usage_events (
    id          bigserial PRIMARY KEY,
    kind        text NOT NULL,                     -- 'login' | 'upload' | 'registration_check' | 'route'
    user_id     integer REFERENCES users(id) ON DELETE SET NULL,
    details     text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS usage_events_kind_time ON usage_events (kind, created_at);

-- the Feedback Inbox: a "Bad response" rating stays open until an admin resolves it
ALTER TABLE response_feedback ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'open';
ALTER TABLE response_feedback ADD COLUMN IF NOT EXISTS resolved_at timestamptz;
ALTER TABLE response_feedback ADD COLUMN IF NOT EXISTS resolved_by integer REFERENCES users(id) ON DELETE SET NULL;
UPDATE response_feedback SET status = 'done' WHERE rating = 'good' AND status = 'open';

-- ===================================================================
-- Admin requests: "Create account -> Administrator" makes a student
-- account plus a request. The person confirms their staff email with an
-- emailed code (unverified -> pending), then an existing admin approves
-- (the account becomes an admin) or rejects it, on the dashboard.
-- ===================================================================
CREATE TABLE IF NOT EXISTS admin_requests (
    id              serial PRIMARY KEY,
    user_id         integer NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    status          text NOT NULL DEFAULT 'unverified'
                    CHECK (status IN ('unverified', 'pending', 'approved', 'rejected')),
    reason          text,                               -- their role at UNIZULU / why they need it
    code_hash       text,                               -- emailed 6-digit code (hashed)
    code_sent_at    timestamptz,
    code_expires_at timestamptz,
    attempts        integer NOT NULL DEFAULT 0,
    requested_at    timestamptz NOT NULL DEFAULT now(),
    verified_at     timestamptz,
    decided_at      timestamptz,
    decided_by      integer REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS admin_requests_status ON admin_requests (status, requested_at);

-- sign-ups waiting for their email code; the account is only made once the code is right
CREATE TABLE IF NOT EXISTS pending_signups (
    id             serial PRIMARY KEY,
    email          text NOT NULL,
    full_name      text NOT NULL,
    password_hash  text NOT NULL,
    account_type   text NOT NULL CHECK (account_type IN ('student', 'admin')),
    admin_reason   text,
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS pending_signups_email ON pending_signups (lower(email));

-- 6-digit codes for creating an account and signing in (only the hash is kept)
CREATE TABLE IF NOT EXISTS email_codes (
    id          bigserial PRIMARY KEY,
    email       text NOT NULL,
    purpose     text NOT NULL CHECK (purpose IN ('signup', 'login')),
    code_hash   text NOT NULL,
    attempts    integer NOT NULL DEFAULT 0,
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL,
    used_at     timestamptz
);
CREATE INDEX IF NOT EXISTS email_codes_recent ON email_codes (lower(email), purpose, created_at DESC);

-- every code email sent (sign-up, sign-in, password reset): for the daily limit and the wait
CREATE TABLE IF NOT EXISTS code_sends (
    id       bigserial PRIMARY KEY,
    email    text NOT NULL,
    purpose  text NOT NULL,
    ip_hash  text NOT NULL,
    sent_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS code_sends_email_time ON code_sends (lower(email), sent_at);
CREATE INDEX IF NOT EXISTS code_sends_ip_time ON code_sends (ip_hash, sent_at);

-- "Remember this device": the browser keeps a random token, only its hash is stored here
CREATE TABLE IF NOT EXISTS trusted_devices (
    id            bigserial PRIMARY KEY,
    user_id       integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash    text NOT NULL UNIQUE,
    created_at    timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL,
    last_used_at  timestamptz,
    user_agent    text
);
CREATE INDEX IF NOT EXISTS trusted_devices_user ON trusted_devices (user_id);
