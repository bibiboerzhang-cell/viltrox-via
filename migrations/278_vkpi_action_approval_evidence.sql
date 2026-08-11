-- 278: Action approval evidence and terminal Agent receipt immutability.
-- The migration runner owns the surrounding transaction; no BEGIN/COMMIT here.

ALTER TABLE vkpi_action_inbox
  ADD COLUMN IF NOT EXISTS approved_by_staff_id BIGINT REFERENCES staff(id) ON DELETE RESTRICT;
ALTER TABLE vkpi_action_inbox
  ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ;
ALTER TABLE vkpi_action_inbox
  ADD COLUMN IF NOT EXISTS approval_snapshot_sha256 TEXT;

ALTER TABLE vkpi_project_content_observation_windows
  ADD COLUMN IF NOT EXISTS source_shipment_id BIGINT REFERENCES vkpi_shipments(id) ON DELETE RESTRICT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_vkpi_observation_window_source_shipment
ON vkpi_project_content_observation_windows(source_shipment_id)
WHERE source_shipment_id IS NOT NULL;

CREATE OR REPLACE FUNCTION vkpi_sourced_observation_window_reject_identity_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'UPDATE'
       AND NEW.source_shipment_id IS DISTINCT FROM OLD.source_shipment_id THEN
        RAISE EXCEPTION 'observation window shipment binding is immutable';
    END IF;
    IF OLD.source_shipment_id IS NOT NULL THEN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'shipment-bound observation window identity is immutable';
        END IF;
        IF NEW.source_shipment_id IS DISTINCT FROM OLD.source_shipment_id
           OR NEW.project_id IS DISTINCT FROM OLD.project_id
           OR NEW.assignment_id IS DISTINCT FROM OLD.assignment_id
           OR NEW.kol_pool_id IS DISTINCT FROM OLD.kol_pool_id
           OR NEW.starts_at IS DISTINCT FROM OLD.starts_at
           OR NEW.ends_at IS DISTINCT FROM OLD.ends_at
        THEN
            RAISE EXCEPTION 'shipment-bound observation window identity is immutable';
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_vkpi_sourced_observation_window_identity_immutable
ON vkpi_project_content_observation_windows;
CREATE TRIGGER trg_vkpi_sourced_observation_window_identity_immutable
BEFORE UPDATE OR DELETE ON vkpi_project_content_observation_windows
FOR EACH ROW EXECUTE FUNCTION vkpi_sourced_observation_window_reject_identity_mutation();

CREATE UNIQUE INDEX IF NOT EXISTS uq_vkpi_action_required_approval_event
ON vkpi_event_ledger(organization_id, entity_type, entity_id, source)
WHERE event_type = 'action_approved'
  AND source = 'action_inbox.required_approval';

CREATE OR REPLACE FUNCTION vkpi_required_action_approval_event_reject_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF (OLD.event_type = 'action_approved'
        AND OLD.source = 'action_inbox.required_approval')
       OR (
         TG_OP = 'UPDATE'
         AND NEW.event_type = 'action_approved'
         AND NEW.source = 'action_inbox.required_approval'
       ) THEN
        RAISE EXCEPTION 'required Action approval event is immutable';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_vkpi_required_action_approval_event_immutable ON vkpi_event_ledger;
CREATE TRIGGER trg_vkpi_required_action_approval_event_immutable
BEFORE UPDATE OR DELETE ON vkpi_event_ledger
FOR EACH ROW EXECUTE FUNCTION vkpi_required_action_approval_event_reject_mutation();

CREATE OR REPLACE FUNCTION vkpi_approved_action_contract_reject_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.approved_at IS NOT NULL
       OR OLD.status IN ('approved', 'executing', 'executed', 'failed') THEN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'approved Action contract is immutable';
        END IF;
        IF NEW.dedupe_key IS DISTINCT FROM OLD.dedupe_key
           OR NEW.category IS DISTINCT FROM OLD.category
           OR NEW.title IS DISTINCT FROM OLD.title
           OR NEW.detail IS DISTINCT FROM OLD.detail
           OR NEW.priority IS DISTINCT FROM OLD.priority
           OR NEW.entity_type IS DISTINCT FROM OLD.entity_type
           OR NEW.entity_id IS DISTINCT FROM OLD.entity_id
           OR NEW.suggested_endpoint IS DISTINCT FROM OLD.suggested_endpoint
           OR NEW.estimated_cost_cents IS DISTINCT FROM OLD.estimated_cost_cents
           OR NEW.writes_business_data IS DISTINCT FROM OLD.writes_business_data
           OR NEW.uses_llm IS DISTINCT FROM OLD.uses_llm
           OR NEW.requires_approval IS DISTINCT FROM OLD.requires_approval
           OR NEW.owner_staff_id IS DISTINCT FROM OLD.owner_staff_id
           OR NEW.reason IS DISTINCT FROM OLD.reason
           OR NEW.payload_json IS DISTINCT FROM OLD.payload_json
           OR NEW.touches_v6_fit IS DISTINCT FROM OLD.touches_v6_fit
           OR NEW.expected_gain IS DISTINCT FROM OLD.expected_gain
           OR NEW.risk_level IS DISTINCT FROM OLD.risk_level
           OR NEW.evidence_refs_json IS DISTINCT FROM OLD.evidence_refs_json
           OR NEW.verification_plan_json IS DISTINCT FROM OLD.verification_plan_json
           OR NEW.affected_tables_json IS DISTINCT FROM OLD.affected_tables_json
        THEN
            RAISE EXCEPTION 'approved Action contract is immutable';
        END IF;
        IF OLD.approved_at IS NOT NULL
           AND (
             NEW.approval_reason IS DISTINCT FROM OLD.approval_reason
             OR NEW.approved_by_staff_id IS DISTINCT FROM OLD.approved_by_staff_id
             OR NEW.approved_at IS DISTINCT FROM OLD.approved_at
             OR NEW.approval_snapshot_sha256 IS DISTINCT FROM OLD.approval_snapshot_sha256
           ) THEN
            RAISE EXCEPTION 'Action approval evidence is immutable';
        END IF;
        IF OLD.approved_at IS NULL AND NEW.status IS DISTINCT FROM OLD.status THEN
            RAISE EXCEPTION 'legacy approved Action must be evidence-sealed before transition';
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_vkpi_approved_action_contract_immutable ON vkpi_action_inbox;
CREATE TRIGGER trg_vkpi_approved_action_contract_immutable
BEFORE UPDATE OR DELETE ON vkpi_action_inbox
FOR EACH ROW EXECUTE FUNCTION vkpi_approved_action_contract_reject_mutation();

CREATE OR REPLACE FUNCTION vkpi_terminal_agent_tool_run_reject_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'UPDATE'
       AND (
         NEW.plan_id IS DISTINCT FROM OLD.plan_id
         OR NEW.tool_id IS DISTINCT FROM OLD.tool_id
         OR NEW.step_index IS DISTINCT FROM OLD.step_index
         OR NEW.inputs_json IS DISTINCT FROM OLD.inputs_json
       ) THEN
        RAISE EXCEPTION 'Agent tool receipt identity is immutable';
    END IF;
    IF OLD.status IN ('executed', 'failed', 'skipped') THEN
        RAISE EXCEPTION 'terminal Agent tool receipt is immutable; append a new receipt';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_vkpi_terminal_agent_tool_run_immutable ON vkpi_agent_tool_run;
CREATE TRIGGER trg_vkpi_terminal_agent_tool_run_immutable
BEFORE UPDATE OR DELETE ON vkpi_agent_tool_run
FOR EACH ROW EXECUTE FUNCTION vkpi_terminal_agent_tool_run_reject_mutation();

COMMENT ON COLUMN vkpi_action_inbox.approval_snapshot_sha256 IS
  'SHA-256 of the canonical server-side Action execution contract approved by a manager.';
COMMENT ON FUNCTION vkpi_terminal_agent_tool_run_reject_mutation() IS
  'Terminal Agent receipts are append-only because learning evidence binds their plan/tool/input identity.';
