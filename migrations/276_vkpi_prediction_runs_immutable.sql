-- 276_vkpi_prediction_runs_immutable.sql — 冻结首次预测，防止结果已知后改写历史分位。
--
-- 应用层 record_prediction_run 只允许完全相同的幂等重放；本触发器补数据库
-- 最后一层保护，禁止任何 UPDATE/DELETE。需要产生修订预测时必须使用新的 run_id，
-- 从而保留旧预测、模型版本、输入指纹与评估的可复算历史。
CREATE OR REPLACE FUNCTION vkpi_prediction_runs_reject_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'vkpi_prediction_runs is append-only; create a new run_id';
END;
$$;

DROP TRIGGER IF EXISTS trg_vkpi_prediction_runs_immutable ON vkpi_prediction_runs;
CREATE TRIGGER trg_vkpi_prediction_runs_immutable
BEFORE UPDATE OR DELETE ON vkpi_prediction_runs
FOR EACH ROW EXECUTE FUNCTION vkpi_prediction_runs_reject_mutation();

COMMENT ON FUNCTION vkpi_prediction_runs_reject_mutation() IS
  '预测真值保护：首次预测不可 UPDATE 或 DELETE；修订必须产生新的 run_id。';

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

DROP TRIGGER IF EXISTS trg_vkpi_prediction_verified_evals_immutable ON vkpi_prediction_evals;
CREATE TRIGGER trg_vkpi_prediction_verified_evals_immutable
BEFORE UPDATE OR DELETE ON vkpi_prediction_evals
FOR EACH ROW EXECUTE FUNCTION vkpi_prediction_verified_evals_reject_mutation();

CREATE OR REPLACE FUNCTION vkpi_finalized_outcome_evidence_reject_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.decision <> 'open' AND TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'finalized GTM outcome evidence is immutable';
    END IF;
    IF OLD.decision <> 'open' AND (
        NEW.decision IS DISTINCT FROM OLD.decision
        OR NEW.decided_at IS DISTINCT FROM OLD.decided_at
        OR NEW.decided_by IS DISTINCT FROM OLD.decided_by
        OR NEW.lesson IS DISTINCT FROM OLD.lesson
        OR NEW.next_weight_change IS DISTINCT FROM OLD.next_weight_change
        OR NEW.actual_result IS DISTINCT FROM OLD.actual_result
        OR NEW.window_7d IS DISTINCT FROM OLD.window_7d
        OR NEW.window_14d IS DISTINCT FROM OLD.window_14d
        OR NEW.window_28d IS DISTINCT FROM OLD.window_28d
        OR NEW.product_sku IS DISTINCT FROM OLD.product_sku
        OR NEW.market IS DISTINCT FROM OLD.market
        OR NEW.channel IS DISTINCT FROM OLD.channel
        OR NEW.action_type IS DISTINCT FROM OLD.action_type
        OR NEW.action_inbox_id IS DISTINCT FROM OLD.action_inbox_id
    ) THEN
        RAISE EXCEPTION 'finalized GTM outcome evidence is immutable';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_vkpi_finalized_outcome_evidence_immutable ON vkpi_gtm_outcomes;
CREATE TRIGGER trg_vkpi_finalized_outcome_evidence_immutable
BEFORE UPDATE OR DELETE ON vkpi_gtm_outcomes
FOR EACH ROW EXECUTE FUNCTION vkpi_finalized_outcome_evidence_reject_mutation();

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

DROP TRIGGER IF EXISTS trg_vkpi_human_verification_events_immutable ON vkpi_event_ledger;
CREATE TRIGGER trg_vkpi_human_verification_events_immutable
BEFORE UPDATE OR DELETE ON vkpi_event_ledger
FOR EACH ROW EXECUTE FUNCTION vkpi_human_verification_events_reject_mutation();

CREATE UNIQUE INDEX IF NOT EXISTS uq_vkpi_human_verification_event_entity
ON vkpi_event_ledger(organization_id, entity_type, entity_id, source)
WHERE event_type IN (
    'skill_run_accepted', 'skill_run_rejected',
    'action_result_accepted', 'action_result_rejected',
    'agent_tool_run_accepted', 'agent_tool_run_rejected',
    'prediction_actual_verified'
);

CREATE OR REPLACE FUNCTION vkpi_reviewed_skill_run_reject_truth_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF (
        OLD.accepted IS NOT NULL
        OR OLD.human_score IS NOT NULL
        OR OLD.business_result IS NOT NULL
    ) AND (
        NEW.skill_name IS DISTINCT FROM OLD.skill_name
        OR NEW.skill_version IS DISTINCT FROM OLD.skill_version
        OR NEW.input_schema IS DISTINCT FROM OLD.input_schema
        OR NEW.model_used IS DISTINCT FROM OLD.model_used
        OR NEW.prompt_version IS DISTINCT FROM OLD.prompt_version
        OR NEW.output IS DISTINCT FROM OLD.output
        OR NEW.accepted IS DISTINCT FROM OLD.accepted
        OR NEW.human_score IS DISTINCT FROM OLD.human_score
        OR NEW.business_result IS DISTINCT FROM OLD.business_result
    ) THEN
        RAISE EXCEPTION 'reviewed skill run truth is immutable';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_vkpi_reviewed_skill_run_truth_immutable ON vkpi_skill_runs;
CREATE TRIGGER trg_vkpi_reviewed_skill_run_truth_immutable
BEFORE UPDATE ON vkpi_skill_runs
FOR EACH ROW EXECUTE FUNCTION vkpi_reviewed_skill_run_reject_truth_mutation();

CREATE OR REPLACE FUNCTION vkpi_verified_action_result_reject_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM vkpi_event_ledger ev
        WHERE ev.organization_id = 1
          AND ev.entity_type = 'action'
          AND ev.entity_id = CAST(OLD.id AS TEXT)
          AND ev.event_type IN ('action_result_accepted', 'action_result_rejected')
          AND ev.source = 'action_inbox.human_verification'
    ) THEN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'human-verified action result is immutable';
        END IF;
        IF NEW.status IS DISTINCT FROM OLD.status
           OR NEW.category IS DISTINCT FROM OLD.category
           OR NEW.dedupe_key IS DISTINCT FROM OLD.dedupe_key
           OR NEW.suggested_endpoint IS DISTINCT FROM OLD.suggested_endpoint
           OR NEW.entity_type IS DISTINCT FROM OLD.entity_type
           OR NEW.entity_id IS DISTINCT FROM OLD.entity_id
           OR NEW.payload_json IS DISTINCT FROM OLD.payload_json
           OR NEW.result_checklist_json IS DISTINCT FROM OLD.result_checklist_json
        THEN
            RAISE EXCEPTION 'human-verified action result is immutable';
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_vkpi_verified_action_result_immutable ON vkpi_action_inbox;
CREATE TRIGGER trg_vkpi_verified_action_result_immutable
BEFORE UPDATE OR DELETE ON vkpi_action_inbox
FOR EACH ROW EXECUTE FUNCTION vkpi_verified_action_result_reject_mutation();
