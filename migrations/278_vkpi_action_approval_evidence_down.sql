DROP TRIGGER IF EXISTS trg_vkpi_terminal_agent_tool_run_immutable ON vkpi_agent_tool_run;
DROP FUNCTION IF EXISTS vkpi_terminal_agent_tool_run_reject_mutation();
DROP TRIGGER IF EXISTS trg_vkpi_approved_action_contract_immutable ON vkpi_action_inbox;
DROP FUNCTION IF EXISTS vkpi_approved_action_contract_reject_mutation();
DROP TRIGGER IF EXISTS trg_vkpi_required_action_approval_event_immutable ON vkpi_event_ledger;
DROP FUNCTION IF EXISTS vkpi_required_action_approval_event_reject_mutation();
DROP INDEX IF EXISTS uq_vkpi_action_required_approval_event;
DROP TRIGGER IF EXISTS trg_vkpi_sourced_observation_window_identity_immutable
ON vkpi_project_content_observation_windows;
DROP FUNCTION IF EXISTS vkpi_sourced_observation_window_reject_identity_mutation();
DROP INDEX IF EXISTS uq_vkpi_observation_window_source_shipment;
ALTER TABLE vkpi_project_content_observation_windows DROP COLUMN IF EXISTS source_shipment_id;
ALTER TABLE vkpi_action_inbox DROP COLUMN IF EXISTS approval_snapshot_sha256;
ALTER TABLE vkpi_action_inbox DROP COLUMN IF EXISTS approved_at;
ALTER TABLE vkpi_action_inbox DROP COLUMN IF EXISTS approved_by_staff_id;
DELETE FROM schema_migrations
WHERE version_key = '278_vkpi_action_approval_evidence.sql';
