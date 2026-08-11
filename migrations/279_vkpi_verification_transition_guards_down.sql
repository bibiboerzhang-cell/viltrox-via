-- Restore the migration-276 OLD-side-only functions when rolling back 279.

CREATE OR REPLACE FUNCTION vkpi_prediction_verified_evals_reject_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.outcome_id IS NOT NULL THEN
        RAISE EXCEPTION 'verified outcome-bound prediction evals are immutable';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION vkpi_human_verification_events_reject_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.event_type IN (
        'skill_run_accepted', 'skill_run_rejected',
        'action_result_accepted', 'action_result_rejected',
        'agent_tool_run_accepted', 'agent_tool_run_rejected',
        'prediction_actual_verified', 'gtm_window_observed'
    ) THEN
        RAISE EXCEPTION 'human verification events are append-only';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

DELETE FROM schema_migrations
WHERE version_key = '279_vkpi_verification_transition_guards.sql';
