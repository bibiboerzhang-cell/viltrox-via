-- Bind each worker heartbeat to one concrete process boot and source build.
-- The migration runner owns the transaction and fleet advisory lock.  Do not
-- add BEGIN/COMMIT here.
ALTER TABLE vkpi_worker_heartbeat
    ADD COLUMN IF NOT EXISTS worker_git_sha TEXT,
    ADD COLUMN IF NOT EXISTS boot_nonce_sha256 TEXT,
    ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ;
