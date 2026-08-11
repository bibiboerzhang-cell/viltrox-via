-- 279: block transition-in attacks on verified prediction and human-review truth.
--
-- Migration 276 made already-verified rows append-only, but an UPDATE could
-- still turn an ordinary row into a verified row because its trigger inspected
-- only OLD.  Existing databases have already recorded 276, so this additive
-- migration replaces the two trigger functions in place.  The migration runner
-- owns the surrounding transaction; no BEGIN/COMMIT appears here.

CREATE OR REPLACE FUNCTION vkpi_prediction_verified_evals_reject_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.outcome_id IS NOT NULL
       OR (TG_OP = 'UPDATE' AND NEW.outcome_id IS NOT NULL) THEN
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
    ) OR (
        TG_OP = 'UPDATE'
        AND NEW.event_type IN (
            'skill_run_accepted', 'skill_run_rejected',
            'action_result_accepted', 'action_result_rejected',
            'agent_tool_run_accepted', 'agent_tool_run_rejected',
            'prediction_actual_verified', 'gtm_window_observed'
        )
    ) THEN
        RAISE EXCEPTION 'human verification events are append-only';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION vkpi_prediction_verified_evals_reject_mutation() IS
  'Rejects mutation of verified evals and any UPDATE that attempts to become outcome-bound.';
COMMENT ON FUNCTION vkpi_human_verification_events_reject_mutation() IS
  'Rejects mutation of human-verification events and any UPDATE into a protected event type.';
