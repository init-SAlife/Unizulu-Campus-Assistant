-- One-off update for the Render database (run once in pgAdmin's Query Tool).
-- Take a backup first. Everything runs in one transaction: any error changes nothing.
BEGIN;

-- 1. elective labels: 'E' and empty both mean Elective (2026 handbook, 7 rows)
UPDATE programme_modules SET effective_type = 'Elective'
WHERE effective_type IS NULL OR effective_type IN ('E', '');

-- 2. prerequisite typos, checked against the 2026 handbook (pages 394 and 398)
UPDATE modules SET prerequisite_subjects = array_replace(prerequisite_subjects, 'MTH222', '4MTH222')
WHERE module_code = '4AMT331' AND handbook_edition_id = 1;
UPDATE modules SET corequisite_subjects = array_replace(corequisite_subjects, '4MTH211', '4MTH222')
WHERE module_code = '4STT212' AND handbook_edition_id = 1;

-- 3. only the three real labels are allowed from now on
ALTER TABLE programme_modules ALTER COLUMN effective_type SET NOT NULL;
ALTER TABLE programme_modules DROP CONSTRAINT IF EXISTS programme_modules_effective_type_check;
ALTER TABLE programme_modules ADD CONSTRAINT programme_modules_effective_type_check
    CHECK (effective_type IN ('Compulsory', 'Major', 'Elective'));

-- 4. not used by the app any more
DROP TABLE IF EXISTS progression_rules;
DROP EXTENSION IF EXISTS vector;

-- 5. tables for the new sign-in (the app also makes these itself when it starts)
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

CREATE TABLE IF NOT EXISTS code_sends (
    id       bigserial PRIMARY KEY,
    email    text NOT NULL,
    purpose  text NOT NULL,
    ip_hash  text NOT NULL,
    sent_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS code_sends_email_time ON code_sends (lower(email), sent_at);
CREATE INDEX IF NOT EXISTS code_sends_ip_time ON code_sends (ip_hash, sent_at);

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

COMMIT;

-- check: should show only Compulsory, Major and Elective, and 0 missing prerequisites
SELECT handbook_edition_id, effective_type, count(*) FROM programme_modules GROUP BY 1, 2 ORDER BY 1, 2;
SELECT count(*) AS missing_prerequisites
FROM modules m, unnest(coalesce(m.prerequisite_subjects, '{}') || coalesce(m.corequisite_subjects, '{}')) x(code)
WHERE NOT EXISTS (SELECT 1 FROM modules m2 WHERE m2.module_code = x.code AND m2.handbook_edition_id = m.handbook_edition_id);
