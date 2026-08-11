DROP TRIGGER IF EXISTS trg_vkpi_workflow_completed_event_immutable ON vkpi_event_ledger;
DROP FUNCTION IF EXISTS vkpi_workflow_completed_event_reject_mutation();
DROP INDEX IF EXISTS uq_vkpi_workflow_completed_event;
DROP TRIGGER IF EXISTS trg_vkpi_completed_workflow_run_immutable ON vkpi_workflow_runs;
DROP FUNCTION IF EXISTS vkpi_completed_workflow_run_reject_mutation();

DELETE FROM schema_migrations
WHERE version_key = '281_vkpi_workflow_completion_evidence.sql';
