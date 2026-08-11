"""Fail-closed SQL contracts for Marketing Brain observed activity.

The scorecard owns scoring; this module only defines stable evidence units and
the server-side proof each unit must carry.  It has no database or provider
access, which keeps the contracts directly testable.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ActivityEvidenceContract:
    """Pure SQL contract for one scorecard activity evidence unit."""

    table: str
    unit_sql: str
    timestamp_column: str
    where_sql: str


def text_nonproduction_guard(expression: str, *, prefix_only: bool = False) -> str:
    """Exclude synthetic labels without raw percent signs in executable SQL."""

    value = f"LOWER(BTRIM(COALESCE({expression}, '')))"
    test_guard = (
        f"LEFT({value}, 4) <> 'test'"
        if prefix_only
        else f"POSITION('test' IN {value}) = 0"
    )
    return (
        f"{test_guard} "
        f"AND POSITION('demo' IN {value}) = 0 "
        f"AND POSITION('smoke' IN {value}) = 0 "
        f"AND POSITION('dry_run' IN {value}) = 0 "
        f"AND POSITION('dry-run' IN {value}) = 0 "
        f"AND POSITION('dry run' IN {value}) = 0"
    )


def json_nonproduction_guard(expression: str) -> str:
    """Reject boolean/mode markers used by test, demo and dry-run callers."""

    false_values = "('1', 'true', 'yes', 'on')"
    return " AND ".join(
        [
            f"LOWER(COALESCE({expression}->>'is_test', 'false')) NOT IN {false_values}",
            f"LOWER(COALESCE({expression}->>'test', 'false')) NOT IN {false_values}",
            f"LOWER(COALESCE({expression}->>'is_demo', 'false')) NOT IN {false_values}",
            f"LOWER(COALESCE({expression}->>'demo', 'false')) NOT IN {false_values}",
            f"LOWER(COALESCE({expression}->>'dry_run', 'false')) NOT IN {false_values}",
            f"LOWER(COALESCE({expression}->>'mode', '')) "
            "NOT IN ('test', 'demo', 'dry_run', 'dry-run', 'dry run')",
        ]
    )


def server_bound_event_sql() -> str:
    """Accept only event kinds whose producer persists verifiable bindings."""

    return """(
        (
            event_type IN ('skill_run_accepted', 'skill_run_rejected')
            AND entity_type = 'skill_run'
            AND actor_type = 'staff' AND actor_id <> ''
            AND source = 'skill_studio.human_review'
            AND COALESCE(provenance_json->>'evidence_verification', '')
                = 'staff_attestation_bound_to_skill_run'
            AND COALESCE(provenance_json->>'review_eligibility', '')
                = 'usable_production_output'
            AND COALESCE(provenance_json->>'server_bound_run_id', '') = entity_id
            AND COALESCE(provenance_json->>'server_bound_input_sha256', '')
                ~ '^[0-9a-f]{64}$'
            AND COALESCE(provenance_json->>'server_bound_output_sha256', '')
                ~ '^[0-9a-f]{64}$'
        ) OR (
            event_type IN (
                'action_result_accepted', 'action_result_rejected',
                'agent_tool_run_accepted', 'agent_tool_run_rejected'
            )
            AND actor_type = 'staff' AND actor_id <> ''
            AND source = 'action_inbox.human_verification'
            AND COALESCE(provenance_json->>'evidence_verification', '')
                = 'staff_attestation_bound_to_execution_ledger'
            AND COALESCE(provenance_json->>'execution_ledger_id', '')
                ~ '^[1-9][0-9]*$'
            AND COALESCE(provenance_json->>'execution_effect', '')
                IN ('state_changed', 'external_confirmed')
        ) OR (
            event_type = 'prediction_actual_verified'
            AND entity_type = 'prediction_eval'
            AND actor_type = 'staff' AND actor_id <> ''
            AND source = 'prediction_ledger.human_actual_review'
            AND COALESCE(provenance_json->>'evidence_verification', '')
                = 'server_resolved_outcome_contract'
            AND COALESCE(provenance_json->>'prediction_run_immutable', '') = 'true'
            AND COALESCE(provenance_json->>'payload_sha256', '')
                ~ '^[0-9a-f]{64}$'
            AND COALESCE(payload_json->>'actual_binding_sha256', '')
                ~ '^[0-9a-f]{64}$'
            AND COALESCE(payload_json->>'run_snapshot_sha256', '')
                ~ '^[0-9a-f]{64}$'
            AND COALESCE(payload_json->>'outcome_evidence_sha256', '')
                ~ '^[0-9a-f]{64}$'
        ) OR (
            event_type = 'gtm_window_observed'
            AND entity_type = 'gtm_outcome'
            AND actor_type = 'system' AND actor_id = 'gtm_windows'
            AND source = 'gtm_windows.refresh'
            AND COALESCE(provenance_json->>'evidence_verification', '')
                = 'server_produced_observation_window'
            AND COALESCE(payload_json->>'evidence_field', '')
                IN ('window_7d', 'window_14d', 'window_28d')
            AND COALESCE(payload_json->>'evidence_sha256', '')
                ~ '^[0-9a-f]{64}$'
        )
    )"""


def activity_evidence_contracts() -> dict[str, ActivityEvidenceContract]:
    """Build fail-closed contracts for event, workflow and eval activity."""

    event_base = " AND ".join(
        [
            "organization_id = 1",
            "entity_type <> ''",
            "entity_id <> ''",
            text_nonproduction_guard("event_type"),
            text_nonproduction_guard("source"),
            text_nonproduction_guard("entity_id", prefix_only=True),
            json_nonproduction_guard("payload_json"),
            json_nonproduction_guard("provenance_json"),
        ]
    )
    event_verified = " AND ".join(
        [
            event_base,
            "trace_id IS NOT NULL AND trace_id <> ''",
            "provenance_json IS NOT NULL AND provenance_json <> '{}'::jsonb",
            server_bound_event_sql(),
        ]
    )
    workflow_nonproduction = " AND ".join(
        [
            text_nonproduction_guard("workflow_name"),
            text_nonproduction_guard("entity_id", prefix_only=True),
            json_nonproduction_guard("input_json"),
        ]
    )
    # Migration 281 makes this receipt atomic, unique and immutable.  Count a
    # run only when the terminal fenced rows and the exact server receipt agree.
    workflow_verified = f"""
        organization_id = 1
        AND status = 'completed'
        AND entity_type IS NOT NULL AND entity_type <> ''
        AND entity_id IS NOT NULL AND entity_id <> ''
        AND trace_id IS NOT NULL AND trace_id <> ''
        AND fence_token > 0
        AND {workflow_nonproduction}
        AND EXISTS (
            SELECT 1 FROM vkpi_workflow_steps ws
            WHERE ws.run_id = vkpi_workflow_runs.id
              AND ws.status = 'done'
              AND ws.finished_at IS NOT NULL
              AND ws.fence_token = vkpi_workflow_runs.fence_token
              AND ws.output_json IS NOT NULL
              AND ws.output_json <> '{{}}'::jsonb
        )
        AND EXISTS (
            SELECT 1 FROM vkpi_workflow_checkpoints checkpoint
            WHERE checkpoint.run_id = vkpi_workflow_runs.id
              AND checkpoint.fence_token = vkpi_workflow_runs.fence_token
              AND checkpoint.step_index = vkpi_workflow_runs.current_step - 1
              AND checkpoint.state_json IS NOT NULL
              AND checkpoint.state_json <> '{{}}'::jsonb
        )
        AND (
            SELECT COUNT(*) FROM vkpi_event_ledger workflow_ev
            WHERE workflow_ev.organization_id = vkpi_workflow_runs.organization_id
              AND workflow_ev.event_type = 'workflow_completed'
              AND workflow_ev.entity_type = 'workflow'
              AND workflow_ev.entity_id = CAST(vkpi_workflow_runs.id AS TEXT)
              AND workflow_ev.source = 'workflow_engine'
        ) = 1
        AND EXISTS (
            SELECT 1 FROM vkpi_event_ledger workflow_ev
            WHERE workflow_ev.organization_id = vkpi_workflow_runs.organization_id
              AND workflow_ev.event_type = 'workflow_completed'
              AND workflow_ev.entity_type = 'workflow'
              AND workflow_ev.entity_id = CAST(vkpi_workflow_runs.id AS TEXT)
              AND workflow_ev.actor_type = 'system'
              AND workflow_ev.actor_id = ''
              AND workflow_ev.source = 'workflow_engine'
              AND workflow_ev.trace_id = vkpi_workflow_runs.trace_id
              AND workflow_ev.confidence IS NULL
              AND workflow_ev.payload_json = jsonb_build_object(
                    'workflow', vkpi_workflow_runs.workflow_name,
                    'steps', vkpi_workflow_runs.current_step,
                    'current_step', vkpi_workflow_runs.current_step,
                    'fence_token', vkpi_workflow_runs.fence_token,
                    'entity_type', vkpi_workflow_runs.entity_type,
                    'entity_id', vkpi_workflow_runs.entity_id
                  )
              AND workflow_ev.provenance_json = jsonb_build_object(
                    'evidence_verification',
                      'server_bound_fenced_workflow_completion',
                    'server_bound_run_id', vkpi_workflow_runs.id,
                    'server_bound_entity_type', vkpi_workflow_runs.entity_type,
                    'server_bound_entity_id', vkpi_workflow_runs.entity_id,
                    'server_bound_current_step', vkpi_workflow_runs.current_step,
                    'fence_token', vkpi_workflow_runs.fence_token
                  )
        )
    """.strip()
    eval_verified = f"""
        status = 'done'
        AND total > 0 AND total = passed
        AND finished_at IS NOT NULL
        AND summary_json IS NOT NULL AND summary_json <> '{{}}'::jsonb
        AND COALESCE(summary_json->>'organization_id', '') = '1'
        AND COALESCE(summary_json->>'evidence_verification', '')
            = 'server_bound_eval_suite'
        AND COALESCE(summary_json->>'server_bound_run_id', '')
            = CAST(vkpi_eval_runs.id AS TEXT)
        AND COALESCE(summary_json->>'result_set_sha256', '')
            ~ '^[0-9a-f]{{64}}$'
        AND {text_nonproduction_guard('suite')}
        AND {json_nonproduction_guard('summary_json')}
        AND (SELECT COUNT(*) FROM vkpi_eval_results eval_result
             WHERE eval_result.run_id = vkpi_eval_runs.id) = vkpi_eval_runs.total
        AND (SELECT COUNT(DISTINCT eval_result.case_name)
             FROM vkpi_eval_results eval_result
             WHERE eval_result.run_id = vkpi_eval_runs.id) = vkpi_eval_runs.total
        AND NOT EXISTS (
            SELECT 1 FROM vkpi_eval_results eval_result
            WHERE eval_result.run_id = vkpi_eval_runs.id
              AND eval_result.passed IS NOT TRUE
        )
        AND EXISTS (
            SELECT 1 FROM vkpi_event_ledger eval_ev
            WHERE eval_ev.organization_id = 1
              AND eval_ev.event_type = 'eval_suite_completed'
              AND eval_ev.entity_type = 'eval_run'
              AND eval_ev.entity_id = CAST(vkpi_eval_runs.id AS TEXT)
              AND eval_ev.actor_type = 'system'
              AND eval_ev.actor_id = 'run_builtin_suite'
              AND eval_ev.source = 'platform.evals'
              AND eval_ev.trace_id <> ''
              AND COALESCE(eval_ev.payload_json->>'suite', '') = vkpi_eval_runs.suite
              AND COALESCE(eval_ev.payload_json->>'total', '')
                  = CAST(vkpi_eval_runs.total AS TEXT)
              AND COALESCE(eval_ev.payload_json->>'passed', '')
                  = CAST(vkpi_eval_runs.passed AS TEXT)
              AND COALESCE(eval_ev.provenance_json->>'evidence_verification', '')
                  = 'server_bound_eval_suite'
              AND COALESCE(eval_ev.provenance_json->>'server_bound_run_id', '')
                  = CAST(vkpi_eval_runs.id AS TEXT)
              AND COALESCE(eval_ev.provenance_json->>'result_set_sha256', '')
                  = COALESCE(vkpi_eval_runs.summary_json->>'result_set_sha256', '')
              AND COALESCE(eval_ev.payload_json->>'result_set_sha256', '')
                  = COALESCE(vkpi_eval_runs.summary_json->>'result_set_sha256', '')
              AND {json_nonproduction_guard('eval_ev.payload_json')}
              AND {json_nonproduction_guard('eval_ev.provenance_json')}
        )
    """.strip()
    event_unit = (
        "ROW(LOWER(BTRIM(event_type)), LOWER(BTRIM(entity_type)), "
        "BTRIM(entity_id), COALESCE(payload_json->>'evidence_field', ''))"
    )
    return {
        "event": ActivityEvidenceContract(
            "vkpi_event_ledger", event_unit, "occurred_at", event_verified,
        ),
        "event_base": ActivityEvidenceContract(
            "vkpi_event_ledger", event_unit, "occurred_at", event_base,
        ),
        "workflow": ActivityEvidenceContract(
            "vkpi_workflow_runs",
            "ROW(LOWER(BTRIM(workflow_name)), LOWER(BTRIM(entity_type)), BTRIM(entity_id))",
            "created_at",
            workflow_verified,
        ),
        "eval": ActivityEvidenceContract(
            "vkpi_eval_runs", "LOWER(BTRIM(suite))", "finished_at", eval_verified,
        ),
    }


__all__ = ["ActivityEvidenceContract", "activity_evidence_contracts", "server_bound_event_sql"]
