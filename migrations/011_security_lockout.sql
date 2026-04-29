CREATE TABLE IF NOT EXISTS failed_logins (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    attempted_at TEXT NOT NULL,
    ip_truncated TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_failed_logins_user_time
    ON failed_logins(user_id, attempted_at DESC);
