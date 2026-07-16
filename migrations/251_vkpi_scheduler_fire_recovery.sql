-- 251: fail-closed recovery for stale APScheduler fire claims.
--
-- A scheduled callback holds a PostgreSQL session advisory lock for the whole
-- execution and renews this row-level lease from an independent heartbeat.
-- Recovery is deliberately terminal: once BOTH heartbeat and lease have
-- expired and another session can acquire the same fire advisory lock, the
-- running claim is changed to failed/outcome-unknown.  The same fire is never
-- replayed automatically because provider/business side effects may already
-- have happened before a process crash.
--
-- Existing 249 rows keep NULL lease fields.  They require manual review and
-- are intentionally ineligible for automatic recovery; migration-time code
-- cannot prove whether a legacy callback is still executing.
--
-- The migration runner owns the surrounding transaction and advisory lock.
-- Do not add BEGIN/COMMIT here.

ALTER TABLE vkpi_scheduler_fire_claims
  ADD COLUMN IF NOT EXISTS fire_lock_key TEXT,
  ADD COLUMN IF NOT EXISTS lease_token TEXT,
  ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS attempt_no INTEGER NOT NULL DEFAULT 1;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'chk_vkpi_scheduler_fire_attempt_no'
      AND conrelid = 'vkpi_scheduler_fire_claims'::regclass
  ) THEN
    ALTER TABLE vkpi_scheduler_fire_claims
      ADD CONSTRAINT chk_vkpi_scheduler_fire_attempt_no CHECK (attempt_no > 0);
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_vkpi_scheduler_fire_claims_stale_lease
  ON vkpi_scheduler_fire_claims (lease_expires_at, heartbeat_at, id)
  WHERE status = 'running'
    AND fire_lock_key IS NOT NULL
    AND lease_token IS NOT NULL
    AND heartbeat_at IS NOT NULL
    AND lease_expires_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS vkpi_scheduler_fire_recoveries (
    id                         BIGSERIAL PRIMARY KEY,
    fire_claim_id              BIGINT NOT NULL
                               REFERENCES vkpi_scheduler_fire_claims(id) ON DELETE CASCADE,
    task_key                   TEXT NOT NULL,
    scheduled_fire_at          TIMESTAMPTZ NOT NULL,
    attempt_no                 INTEGER NOT NULL,
    previous_leader_id         TEXT NOT NULL,
    previous_lease_token       TEXT NOT NULL,
    previous_heartbeat_at      TIMESTAMPTZ NOT NULL,
    previous_lease_expires_at  TIMESTAMPTZ NOT NULL,
    recovered_by               TEXT NOT NULL,
    recovered_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    recovery_action            TEXT NOT NULL DEFAULT 'marked_failed_outcome_unknown',
    reason                     TEXT NOT NULL,
    details_json               JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT chk_vkpi_scheduler_fire_recovery_attempt CHECK (attempt_no > 0),
    CONSTRAINT chk_vkpi_scheduler_fire_recovery_action CHECK (
      recovery_action = 'marked_failed_outcome_unknown'
    ),
    UNIQUE (fire_claim_id, previous_lease_token)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_scheduler_fire_recoveries_recent
  ON vkpi_scheduler_fire_recoveries (recovered_at DESC, fire_claim_id);

COMMENT ON COLUMN vkpi_scheduler_fire_claims.lease_token IS
  'Fencing token for one execution attempt; heartbeat/finalize updates use token CAS.';
COMMENT ON COLUMN vkpi_scheduler_fire_claims.fire_lock_key IS
  'Exact persisted key for the per-fire PostgreSQL advisory lock; NULL legacy rows are never auto-recovered.';
COMMENT ON COLUMN vkpi_scheduler_fire_claims.heartbeat_at IS
  'Independent execution heartbeat; NULL legacy claims are never auto-recovered.';
COMMENT ON COLUMN vkpi_scheduler_fire_claims.lease_expires_at IS
  'Recovery may consider a running claim only after this timestamp and heartbeat expiry.';
COMMENT ON TABLE vkpi_scheduler_fire_recoveries IS
  'Audit of conservative stale-fire terminalization; no row claims the original callback succeeded or was replayed.';
