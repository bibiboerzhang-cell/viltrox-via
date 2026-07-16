-- A rollback cannot safely replay in-flight side effects. Mark them failed so
-- operators must reconcile them before restoring the old status constraint.
UPDATE vkpi_action_inbox
SET status = 'failed', updated_at = NOW()
WHERE status = 'executing';

ALTER TABLE vkpi_action_inbox
  DROP CONSTRAINT IF EXISTS chk_action_inbox_status;

ALTER TABLE vkpi_action_inbox
  ADD CONSTRAINT chk_action_inbox_status
  CHECK (status IN ('suggested', 'approved', 'dismissed', 'snoozed', 'executed', 'failed'));
