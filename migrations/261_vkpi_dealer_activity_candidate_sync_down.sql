DELETE FROM scheduler_tasks
WHERE task_key = 'vkpi_dealer_activity_candidate_sync';

DROP INDEX IF EXISTS idx_event_watch_activity_sync_claim;
ALTER TABLE vkpi_event_watch_targets
  DROP CONSTRAINT IF EXISTS chk_event_watch_activity_sync_claim;
ALTER TABLE vkpi_event_watch_targets
  DROP COLUMN IF EXISTS activity_sync_claim_expires_at;
ALTER TABLE vkpi_event_watch_targets
  DROP COLUMN IF EXISTS activity_sync_claimed_at;
ALTER TABLE vkpi_event_watch_targets
  DROP COLUMN IF EXISTS activity_sync_claim_organization_id;
ALTER TABLE vkpi_event_watch_targets
  DROP COLUMN IF EXISTS activity_sync_claim_token;

DELETE FROM schema_migrations
WHERE version_key = '261_vkpi_dealer_activity_candidate_sync.sql';
