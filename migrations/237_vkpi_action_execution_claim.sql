-- Action Inbox execution claim: persist the in-flight state used by the
-- approved -> executing compare-and-set before any external side effect.
ALTER TABLE vkpi_action_inbox
  DROP CONSTRAINT IF EXISTS chk_action_inbox_status;

ALTER TABLE vkpi_action_inbox
  ADD CONSTRAINT chk_action_inbox_status
  CHECK (status IN ('suggested', 'approved', 'executing', 'dismissed', 'snoozed', 'executed', 'failed'));

COMMENT ON COLUMN vkpi_action_inbox.status IS
  'Action state machine; executing is an exclusive durable claim and must be reconciled before replay.';
