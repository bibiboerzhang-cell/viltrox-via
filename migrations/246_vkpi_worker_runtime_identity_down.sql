BEGIN;

ALTER TABLE vkpi_worker_heartbeat
    DROP COLUMN IF EXISTS started_at,
    DROP COLUMN IF EXISTS boot_nonce_sha256,
    DROP COLUMN IF EXISTS worker_git_sha;

COMMIT;
