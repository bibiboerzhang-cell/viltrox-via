DROP INDEX IF EXISTS uq_vkpi_action_outreach_truth_event;
DROP INDEX IF EXISTS uq_vkpi_action_outreach_reply_truth_event;
DROP TRIGGER IF EXISTS trg_vkpi_action_outreach_reply_truth_immutable
  ON vkpi_action_outreach_reply_truth_receipts;
DROP FUNCTION IF EXISTS vkpi_action_outreach_reply_truth_reject_mutation();
DROP TABLE IF EXISTS vkpi_action_outreach_reply_truth_receipts;

DROP TRIGGER IF EXISTS trg_vkpi_action_outreach_truth_event_immutable ON vkpi_event_ledger;
DROP FUNCTION IF EXISTS vkpi_action_outreach_truth_event_reject_mutation();

DROP TRIGGER IF EXISTS trg_vkpi_action_outreach_truth_bridge_immutable
  ON vkpi_action_outreach_truth_bridges;
DROP FUNCTION IF EXISTS vkpi_action_outreach_truth_bridge_reject_mutation();
DROP TABLE IF EXISTS vkpi_action_outreach_truth_bridges;

DELETE FROM schema_migrations
WHERE version_key = '277_vkpi_action_outreach_truth_bridge.sql';
