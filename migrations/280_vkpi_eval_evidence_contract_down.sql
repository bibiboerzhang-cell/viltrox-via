DROP INDEX IF EXISTS uq_vkpi_eval_suite_completed_event;

DROP TRIGGER IF EXISTS trg_vkpi_eval_event_immutable ON vkpi_event_ledger;
DROP FUNCTION IF EXISTS vkpi_eval_event_reject_mutation();

DROP TRIGGER IF EXISTS trg_vkpi_eval_result_terminal_evidence ON vkpi_eval_results;
DROP FUNCTION IF EXISTS vkpi_eval_result_guard_terminal_evidence();

DROP TRIGGER IF EXISTS trg_vkpi_eval_run_terminal_evidence ON vkpi_eval_runs;
DROP FUNCTION IF EXISTS vkpi_eval_run_guard_terminal_evidence();

DELETE FROM schema_migrations
WHERE version_key = '280_vkpi_eval_evidence_contract.sql';
