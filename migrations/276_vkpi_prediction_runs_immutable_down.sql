-- 276 down — 仅移除 append-only 触发器，不改任何预测或评估数据。
DROP TRIGGER IF EXISTS trg_vkpi_prediction_runs_immutable ON vkpi_prediction_runs;
DROP FUNCTION IF EXISTS vkpi_prediction_runs_reject_mutation();
DROP TRIGGER IF EXISTS trg_vkpi_prediction_verified_evals_immutable ON vkpi_prediction_evals;
DROP FUNCTION IF EXISTS vkpi_prediction_verified_evals_reject_mutation();
DROP TRIGGER IF EXISTS trg_vkpi_finalized_outcome_evidence_immutable ON vkpi_gtm_outcomes;
DROP FUNCTION IF EXISTS vkpi_finalized_outcome_evidence_reject_mutation();
DROP TRIGGER IF EXISTS trg_vkpi_human_verification_events_immutable ON vkpi_event_ledger;
DROP FUNCTION IF EXISTS vkpi_human_verification_events_reject_mutation();
DROP INDEX IF EXISTS uq_vkpi_human_verification_event_entity;
DROP TRIGGER IF EXISTS trg_vkpi_reviewed_skill_run_truth_immutable ON vkpi_skill_runs;
DROP FUNCTION IF EXISTS vkpi_reviewed_skill_run_reject_truth_mutation();
DROP TRIGGER IF EXISTS trg_vkpi_verified_action_result_immutable ON vkpi_action_inbox;
DROP FUNCTION IF EXISTS vkpi_verified_action_result_reject_mutation();
DELETE FROM schema_migrations
WHERE version_key = '276_vkpi_prediction_runs_immutable.sql';
