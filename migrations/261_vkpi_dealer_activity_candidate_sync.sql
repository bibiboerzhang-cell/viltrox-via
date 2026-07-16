-- 261: Register the fail-closed Dealer activity -> Event candidate sync.
--
-- This migration enables no source, performs no fetch and writes no Dealer,
-- Event, Event Radar opportunity or candidate row.  Operators must separately
-- approve an exact feed, create a current source_registry passport and enable
-- both the watch target and this task before the scheduler can access a source.
-- Candidate promotion always remains manual.

-- Source ownership is shared, but one candidate-sync execution must be fenced
-- to one explicit workspace and one lease token.  The task remains OFF and no
-- existing source is claimed by this migration.
ALTER TABLE vkpi_event_watch_targets
  ADD COLUMN IF NOT EXISTS activity_sync_claim_token TEXT NOT NULL DEFAULT '';
ALTER TABLE vkpi_event_watch_targets
  ADD COLUMN IF NOT EXISTS activity_sync_claim_organization_id BIGINT;
ALTER TABLE vkpi_event_watch_targets
  ADD COLUMN IF NOT EXISTS activity_sync_claimed_at TIMESTAMPTZ;
ALTER TABLE vkpi_event_watch_targets
  ADD COLUMN IF NOT EXISTS activity_sync_claim_expires_at TIMESTAMPTZ;

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
      activity_sync_claim_token ~ '^[0-9a-f]{32}$'
      AND activity_sync_claim_organization_id > 0
      AND activity_sync_claimed_at IS NOT NULL
      AND activity_sync_claim_expires_at > activity_sync_claimed_at
    )
  );

CREATE INDEX IF NOT EXISTS idx_event_watch_activity_sync_claim
  ON vkpi_event_watch_targets
  (activity_sync_claim_expires_at, next_check_at, priority_tier)
  WHERE source_kind = 'dealer_event' AND country_code = 'US'
    AND status = 'active' AND enabled = TRUE;

INSERT INTO scheduler_tasks
  (task_key,label,enabled,max_daily_runs,max_daily_cost_cents,
   allowed_hours,owner,risk_level)
VALUES
  ('vkpi_dealer_activity_candidate_sync',
   'Approved Dealer activity feeds to Event candidate review queue',
   FALSE,48,0,'00:00-23:59 UTC','marketing_ops','medium')
ON CONFLICT (task_key) DO NOTHING;
