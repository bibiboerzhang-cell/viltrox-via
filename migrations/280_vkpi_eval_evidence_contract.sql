-- 280: make completed eval evidence atomic, unique and terminally immutable.
--
-- The migration runner owns the transaction.  Do not add BEGIN/COMMIT here.

CREATE OR REPLACE FUNCTION vkpi_eval_run_guard_terminal_evidence()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    result_count BIGINT;
    distinct_case_count BIGINT;
    passed_count BIGINT;
    failed_count BIGINT;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status = 'done' THEN
            RAISE EXCEPTION 'eval runs must transition to done after bound evidence is inserted';
        END IF;
        RETURN NEW;
    END IF;

    IF TG_OP = 'DELETE' THEN
        IF OLD.status = 'done' THEN
            RAISE EXCEPTION 'completed eval run evidence is immutable';
        END IF;
        RETURN OLD;
    END IF;

    IF OLD.status = 'done' THEN
        RAISE EXCEPTION 'completed eval run evidence is immutable';
    END IF;
    IF NEW.status <> 'done' THEN
        RETURN NEW;
    END IF;
    IF OLD.status <> 'running' THEN
        RAISE EXCEPTION 'eval run terminal transition must start from running';
    END IF;
    IF NEW.total <= 0
       OR NEW.passed < 0
       OR NEW.passed > NEW.total
       OR NEW.finished_at IS NULL
    THEN
        RAISE EXCEPTION 'completed eval run must be nonempty with bounded counts';
    END IF;
    IF COALESCE(NEW.summary_json->>'organization_id', '') <> '1'
       OR COALESCE(NEW.summary_json->>'evidence_verification', '')
            <> 'server_bound_eval_suite'
       OR COALESCE(NEW.summary_json->>'server_bound_run_id', '')
            <> CAST(NEW.id AS TEXT)
       OR COALESCE(NEW.summary_json->>'result_set_sha256', '')
            !~ '^[0-9a-f]{64}$'
    THEN
        RAISE EXCEPTION 'completed eval run summary is not server-bound';
    END IF;

    SELECT COUNT(*), COUNT(DISTINCT case_name),
           COUNT(*) FILTER (WHERE passed IS TRUE),
           COUNT(*) FILTER (WHERE passed IS FALSE)
      INTO result_count, distinct_case_count, passed_count, failed_count
      FROM vkpi_eval_results
     WHERE run_id = NEW.id;
    IF result_count <> NEW.total
       OR distinct_case_count <> NEW.total
       OR passed_count <> NEW.passed
       OR failed_count <> NEW.total - NEW.passed
    THEN
        RAISE EXCEPTION 'completed eval run results do not match the terminal summary';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM vkpi_event_ledger ev
         WHERE ev.organization_id = 1
           AND ev.event_type = 'eval_suite_completed'
           AND ev.entity_type = 'eval_run'
           AND ev.entity_id = CAST(NEW.id AS TEXT)
           AND ev.actor_type = 'system'
           AND ev.actor_id = 'run_builtin_suite'
           AND ev.source = 'platform.evals'
           AND COALESCE(ev.trace_id, '') <> ''
           AND COALESCE(ev.payload_json->>'suite', '') = NEW.suite
           AND COALESCE(ev.payload_json->>'total', '') = CAST(NEW.total AS TEXT)
           AND COALESCE(ev.payload_json->>'passed', '') = CAST(NEW.passed AS TEXT)
           AND COALESCE(ev.payload_json->>'result_set_sha256', '')
                = COALESCE(NEW.summary_json->>'result_set_sha256', '')
           AND COALESCE(ev.provenance_json->>'evidence_verification', '')
                = 'server_bound_eval_suite'
           AND COALESCE(ev.provenance_json->>'server_bound_run_id', '')
                = CAST(NEW.id AS TEXT)
           AND COALESCE(ev.provenance_json->>'result_set_sha256', '')
                = COALESCE(NEW.summary_json->>'result_set_sha256', '')
    ) THEN
        RAISE EXCEPTION 'completed eval run event binding is missing or inconsistent';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_vkpi_eval_run_terminal_evidence ON vkpi_eval_runs;
CREATE TRIGGER trg_vkpi_eval_run_terminal_evidence
BEFORE INSERT OR UPDATE OR DELETE ON vkpi_eval_runs
FOR EACH ROW EXECUTE FUNCTION vkpi_eval_run_guard_terminal_evidence();

CREATE OR REPLACE FUNCTION vkpi_eval_result_guard_terminal_evidence()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    old_terminal BOOLEAN := FALSE;
    new_terminal BOOLEAN := FALSE;
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        SELECT COALESCE(status = 'done', FALSE)
          INTO old_terminal
          FROM vkpi_eval_runs
         WHERE id = OLD.run_id;
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        SELECT COALESCE(status = 'done', FALSE)
          INTO new_terminal
          FROM vkpi_eval_runs
         WHERE id = NEW.run_id;
    END IF;
    IF old_terminal OR new_terminal THEN
        RAISE EXCEPTION 'completed eval result evidence is immutable';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_vkpi_eval_result_terminal_evidence ON vkpi_eval_results;
CREATE TRIGGER trg_vkpi_eval_result_terminal_evidence
BEFORE INSERT OR UPDATE OR DELETE ON vkpi_eval_results
FOR EACH ROW EXECUTE FUNCTION vkpi_eval_result_guard_terminal_evidence();

CREATE OR REPLACE FUNCTION vkpi_eval_event_reject_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.event_type = 'eval_suite_completed'
       OR (TG_OP = 'UPDATE' AND NEW.event_type = 'eval_suite_completed') THEN
        RAISE EXCEPTION 'completed eval events are append-only';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_vkpi_eval_event_immutable ON vkpi_event_ledger;
CREATE TRIGGER trg_vkpi_eval_event_immutable
BEFORE UPDATE OR DELETE ON vkpi_event_ledger
FOR EACH ROW EXECUTE FUNCTION vkpi_eval_event_reject_mutation();

CREATE UNIQUE INDEX IF NOT EXISTS uq_vkpi_eval_suite_completed_event
ON vkpi_event_ledger(organization_id, entity_type, entity_id, event_type)
WHERE event_type = 'eval_suite_completed';

COMMENT ON FUNCTION vkpi_eval_run_guard_terminal_evidence() IS
  'Allows running to done only after complete org1 server-bound results and event evidence; freezes done rows.';
COMMENT ON FUNCTION vkpi_eval_result_guard_terminal_evidence() IS
  'Rejects inserts, updates and deletes against a completed eval run.';
COMMENT ON FUNCTION vkpi_eval_event_reject_mutation() IS
  'Rejects mutation of eval completion events and transition-in updates.';
