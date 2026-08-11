-- 281: durable, unique and immutable workflow completion evidence.
--
-- A workflow run may be counted as completed only when its fenced terminal
-- transition and workflow_completed event commit together.  Existing exact
-- legacy receipts are upgraded from their completed run, then both sides of
-- the evidence join become immutable.  The migration runner owns transaction.

CREATE OR REPLACE FUNCTION vkpi_completed_workflow_run_reject_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.status = 'completed' THEN
        RAISE EXCEPTION 'completed workflow run is immutable';
    END IF;
    IF TG_OP = 'UPDATE'
       AND NEW.status = 'completed'
       AND (
         NEW.organization_id IS DISTINCT FROM OLD.organization_id
         OR NEW.workflow_name IS DISTINCT FROM OLD.workflow_name
         OR NEW.input_json IS DISTINCT FROM OLD.input_json
         OR NEW.current_step IS DISTINCT FROM OLD.current_step
         OR NEW.entity_type IS DISTINCT FROM OLD.entity_type
         OR NEW.entity_id IS DISTINCT FROM OLD.entity_id
         OR NEW.trace_id IS DISTINCT FROM OLD.trace_id
         OR NEW.fence_token IS DISTINCT FROM OLD.fence_token
       ) THEN
        RAISE EXCEPTION 'workflow completion identity changed during transition';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_vkpi_completed_workflow_run_immutable ON vkpi_workflow_runs;
CREATE TRIGGER trg_vkpi_completed_workflow_run_immutable
BEFORE UPDATE OR DELETE ON vkpi_workflow_runs
FOR EACH ROW EXECUTE FUNCTION vkpi_completed_workflow_run_reject_mutation();

UPDATE vkpi_event_ledger AS event
SET payload_json = jsonb_build_object(
      'workflow', run.workflow_name,
      'steps', run.current_step,
      'current_step', run.current_step,
      'fence_token', run.fence_token,
      'entity_type', run.entity_type,
      'entity_id', run.entity_id
    ),
    provenance_json = jsonb_build_object(
      'evidence_verification', 'server_bound_fenced_workflow_completion',
      'server_bound_run_id', run.id,
      'server_bound_entity_type', run.entity_type,
      'server_bound_entity_id', run.entity_id,
      'server_bound_current_step', run.current_step,
      'fence_token', run.fence_token
    )
FROM vkpi_workflow_runs AS run
WHERE event.organization_id = run.organization_id
  AND event.event_type = 'workflow_completed'
  AND event.entity_type = 'workflow'
  AND event.entity_id = run.id::text
  AND event.actor_type = 'system'
  AND event.actor_id = ''
  AND event.source = 'workflow_engine'
  AND event.trace_id = run.trace_id
  AND event.confidence IS NULL
  AND run.status = 'completed'
  AND event.payload_json = jsonb_build_object(
        'workflow', run.workflow_name,
        'steps', run.current_step,
        'fence_token', run.fence_token
      )
  AND event.provenance_json = '{}'::jsonb;

CREATE UNIQUE INDEX IF NOT EXISTS uq_vkpi_workflow_completed_event
ON vkpi_event_ledger(organization_id, entity_type, entity_id, source)
WHERE event_type = 'workflow_completed'
  AND entity_type = 'workflow'
  AND source = 'workflow_engine';

CREATE OR REPLACE FUNCTION vkpi_workflow_completed_event_reject_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF (OLD.event_type = 'workflow_completed'
        AND OLD.entity_type = 'workflow'
        AND OLD.source = 'workflow_engine')
       OR (
         TG_OP = 'UPDATE'
         AND NEW.event_type = 'workflow_completed'
         AND NEW.entity_type = 'workflow'
         AND NEW.source = 'workflow_engine'
       ) THEN
        RAISE EXCEPTION 'workflow completion evidence is append-only';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_vkpi_workflow_completed_event_immutable ON vkpi_event_ledger;
CREATE TRIGGER trg_vkpi_workflow_completed_event_immutable
BEFORE UPDATE OR DELETE ON vkpi_event_ledger
FOR EACH ROW EXECUTE FUNCTION vkpi_workflow_completed_event_reject_mutation();

COMMENT ON FUNCTION vkpi_workflow_completed_event_reject_mutation() IS
  'Freezes required workflow_completed events and rejects UPDATE transition-in attacks.';

COMMENT ON FUNCTION vkpi_completed_workflow_run_reject_mutation() IS
  'Freezes completed workflow identity and rejects identity changes during completion.';
