#!/usr/bin/env python3
"""Build a secret-free exact-model probe/evaluation execution plan.

This command performs no provider, database or network I/O.  It inventories
the currently selected task bindings and turns the runtime readiness contract
into a reviewable call/cost/signing plan.  The resulting manifest hash is an
integrity identifier, not an authorization signature.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.core.model_registry import current_task_model_binding  # noqa: E402
from app.platform.models.readiness import (  # noqa: E402
    build_model_readiness_catalog,
    configured_providers_from_environment,
    model_attestation_trust_root_status,
    readiness_evidence_from_environment,
)
from app.platform.models.runtime import resolve_model_binding, split_binding  # noqa: E402


PLAN_VERSION = "vkpi_model_evidence_execution_plan_v1"
EVAL_CASES_PER_TASK = 30
EXACT_PROBES_PER_BINDING = 1
TEXT_INPUT_TOKENS_PER_CASE = 1_500
TEXT_OUTPUT_TOKENS_PER_CASE = 600


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _estimated_text_cost_usd(binding: str, provider_calls: int) -> float | None:
    provider, model = split_binding(binding)
    resolved = resolve_model_binding(provider, model, runtime_availability={})
    if not resolved.pricing_known:
        return None
    input_cents = (
        TEXT_INPUT_TOKENS_PER_CASE
        * float(resolved.input_cents_per_million or 0)
        / 1_000_000
    )
    output_cents = (
        TEXT_OUTPUT_TOKENS_PER_CASE
        * float(resolved.output_cents_per_million or 0)
        / 1_000_000
    )
    return round(provider_calls * (input_cents + output_cents) / 100, 6)


def build_plan(*, credential_directory_probes: int = 0) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )
    task_bindings = current_task_model_binding()
    tasks_by_binding: dict[str, list[str]] = defaultdict(list)
    for task, binding in sorted(task_bindings.items()):
        tasks_by_binding[str(binding)].append(task)
    bindings = list(tasks_by_binding)
    evidence, evidence_source = readiness_evidence_from_environment()
    configured = configured_providers_from_environment()
    readiness = build_model_readiness_catalog(
        bindings,
        evidence_by_binding=evidence,
        configured_providers=configured,
        expected_tasks_by_binding=tasks_by_binding,
        as_of=generated_at,
    )
    readiness_by_binding = {item["binding"]: item for item in readiness["items"]}

    rows: list[dict[str, Any]] = []
    known_cost_total = 0.0
    unknown_cost_bindings = 0
    minimum_generation_calls = 0
    generation_calls_ceiling = 0
    for binding, tasks in sorted(tasks_by_binding.items()):
        provider, model = split_binding(binding)
        resolved = resolve_model_binding(provider, model, runtime_availability={})
        item = readiness_by_binding[binding]
        actual_evaluation_cases = len(tasks) * EVAL_CASES_PER_TASK
        calls_per_binding = actual_evaluation_cases + EXACT_PROBES_PER_BINDING
        minimum_generation_calls += actual_evaluation_cases
        generation_calls_ceiling += calls_per_binding
        estimated_cost = _estimated_text_cost_usd(binding, calls_per_binding)
        if estimated_cost is None:
            unknown_cost_bindings += 1
        else:
            known_cost_total += estimated_cost
        rows.append(
            {
                "binding": binding,
                "tasks": tasks,
                "provider": provider,
                "model": model,
                "registered": resolved.registered,
                "transport_ready": resolved.transport_ready,
                "provider_configured": bool(configured.get(provider)),
                "pricing_known": resolved.pricing_known,
                "pricing_version": resolved.pricing_version or None,
                "current_state": item["state"],
                "production_ready": item["production_ready"],
                "failure_reasons": item["failure_reasons"],
                "required_calls": {
                    "actual_evaluation_cases": actual_evaluation_cases,
                    "actual_evaluation_cases_per_task": {
                        task: EVAL_CASES_PER_TASK for task in tasks
                    },
                    "minimum_actual_evaluation_cases_per_task": EVAL_CASES_PER_TASK,
                    "exact_response_probe": EXACT_PROBES_PER_BINDING,
                    "provider_generation_calls_ceiling": calls_per_binding,
                    "note": (
                        "Every bound task requires its own 30 actual evaluation cases. "
                        "The probe response must also be included in the signed evaluation "
                        "artifact; the per-binding ceiling may be reduced by one by "
                        "designating one evaluation case as the exact probe."
                    ),
                },
                "planning_text_token_envelope": {
                    "input_tokens_per_call": TEXT_INPUT_TOKENS_PER_CASE,
                    "output_tokens_per_call": TEXT_OUTPUT_TOKENS_PER_CASE,
                    "applicable_to_video_media_billing": False,
                },
                "estimated_text_only_cost_usd": estimated_cost,
                "cost_gate": (
                    "requires_operator_budget_approval"
                    if estimated_cost is not None
                    else "blocked_until_exact_pricing_contract_is_registered"
                ),
            }
        )

    execution_manifest = {
        "task_binding_count": len(task_bindings),
        "unique_binding_count": len(bindings),
        "bindings": rows,
        "provider_generation_calls_ceiling": generation_calls_ceiling,
        "minimum_possible_generation_calls": minimum_generation_calls,
        "minimum_actual_evaluation_cases_per_task": EVAL_CASES_PER_TASK,
        "known_text_only_cost_subtotal_usd": round(known_cost_total, 6),
        "unknown_cost_binding_count": unknown_cost_bindings,
        "exact_probe_signer_role": "exact_probe",
        # Must match the verifier's signed-domain role exactly.  This is an
        # attestation role, not the name of the model-evaluation subsystem.
        "evaluation_signer_role": "evaluation",
        "signers_must_be_distinct": True,
        "trusted_public_keys_must_be_code_reviewed": True,
        "raw_prompts_or_responses_in_claim_tables": False,
    }
    trust_roots = model_attestation_trust_root_status()
    production_ready_count = int(readiness["production_ready_count"])
    candidate_count = int(readiness["candidate_count"])
    release_reasons = [
        (
            f"{candidate_count - production_ready_count}/{candidate_count} exact bindings "
            "lack fresh independently signed probe and 30-case actual evaluation evidence "
            f"for every one of {len(task_bindings)} task bindings"
        )
    ]
    if unknown_cost_bindings:
        release_reasons.append(
            f"{unknown_cost_bindings} exact bindings lack a registered pricing contract"
        )
    release_reasons.append(
        "this unsigned planning manifest is not provider execution authority"
    )
    return {
        "version": PLAN_VERSION,
        "generated_at": generated_at,
        "claim_status": "descriptive_only",
        "provider_calls_performed_by_this_command": 0,
        "provider_tokens_consumed_by_this_command": 0,
        "provider_cost_usd_by_this_command": 0.0,
        "credential_directory_probes_observed": max(0, int(credential_directory_probes)),
        "credential_directory_probe_scope": (
            "credential_and_directory_reachability_only_not_exact_model_readiness"
        ),
        "current_readiness": {
            "status": readiness["status"],
            "production_ready_count": production_ready_count,
            "candidate_count": candidate_count,
            "evidence_source": evidence_source,
            "attestation_trust_roots": trust_roots,
        },
        "execution_manifest": execution_manifest,
        "execution_manifest_sha256": _sha256(execution_manifest),
        "authorization": {
            "operator_budget_approval_required_before_calls": True,
            "independent_probe_private_key_required": True,
            "independent_evaluation_private_key_required": True,
            "private_keys_must_not_be_generated_or_stored_by_runtime": True,
            "unsigned_manifest_is_not_execution_authority": True,
        },
        "release_gate": {
            "passed": False,
            "reason": "; ".join(release_reasons) + ".",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--credential-directory-probes", type=int, default=0)
    args = parser.parse_args()
    plan = build_plan(credential_directory_probes=args.credential_directory_probes)
    rendered = json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
