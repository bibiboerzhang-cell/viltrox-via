-- 262: Make migration 261's source-claim invariant NULL-safe.
-- The task and every source remain disabled; this migration imports no data.

ALTER TABLE vkpi_event_watch_targets
  DROP CONSTRAINT IF EXISTS chk_event_watch_activity_sync_claim;

ALTER TABLE vkpi_event_watch_targets
  ADD CONSTRAINT chk_event_watch_activity_sync_claim CHECK (
    (
      activity_sync_claim_token = ''
      AND activity_sync_claim_organization_id IS NULL
      AND activity_sync_claimed_at IS NULL
      AND activity_sync_claim_expires_at IS NULL
    ) OR (
      activity_sync_claim_token <> ''
      AND activity_sync_claim_token ~ '^[0-9a-f]{32}$'
      AND activity_sync_claim_organization_id IS NOT NULL
      AND activity_sync_claim_organization_id > 0
      AND activity_sync_claimed_at IS NOT NULL
      AND activity_sync_claim_expires_at IS NOT NULL
      AND activity_sync_claim_expires_at > activity_sync_claimed_at
    )
  );
