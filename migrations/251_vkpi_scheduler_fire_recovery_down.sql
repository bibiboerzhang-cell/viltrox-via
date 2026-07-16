BEGIN;
DROP TABLE IF EXISTS vkpi_scheduler_fire_recoveries;
DROP INDEX IF EXISTS idx_vkpi_scheduler_fire_claims_stale_lease;
ALTER TABLE vkpi_scheduler_fire_claims
  DROP CONSTRAINT IF EXISTS chk_vkpi_scheduler_fire_attempt_no,
  DROP COLUMN IF EXISTS attempt_no,
  DROP COLUMN IF EXISTS lease_expires_at,
  DROP COLUMN IF EXISTS heartbeat_at,
  DROP COLUMN IF EXISTS lease_token,
  DROP COLUMN IF EXISTS fire_lock_key;
DELETE FROM schema_migrations
WHERE version_key = '251_vkpi_scheduler_fire_recovery.sql';
COMMIT;
