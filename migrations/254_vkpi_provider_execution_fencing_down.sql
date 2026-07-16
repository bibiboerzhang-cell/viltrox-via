DROP INDEX IF EXISTS idx_vkpi_apify_reservation_actor_history;
DROP INDEX IF EXISTS idx_vkpi_apify_reservation_open;
DROP INDEX IF EXISTS uq_vkpi_apify_reservation_run;
DROP TABLE IF EXISTS vkpi_apify_budget_reservations;

DROP INDEX IF EXISTS idx_vkpi_provider_execution_claim_lease;
DROP TABLE IF EXISTS vkpi_provider_execution_claims;

ALTER TABLE vkpi_worker_heartbeat
    DROP COLUMN IF EXISTS redis_readiness_error_code,
    DROP COLUMN IF EXISTS redis_heartbeat_interval_seconds,
    DROP COLUMN IF EXISTS redis_ready_sequence,
    DROP COLUMN IF EXISTS redis_consumer_count,
    DROP COLUMN IF EXISTS redis_group_name,
    DROP COLUMN IF EXISTS redis_stream_key,
    DROP COLUMN IF EXISTS redis_readiness_at,
    DROP COLUMN IF EXISTS redis_ready;
