from __future__ import annotations

from app.platform.models import evaluation_artifact, readiness
from scripts.ops import vkpi_model_evidence_plan


def test_evidence_plan_signer_roles_match_runtime_verifiers() -> None:
    plan = vkpi_model_evidence_plan.build_plan()
    manifest = plan["execution_manifest"]

    assert manifest["exact_probe_signer_role"] == readiness._PROBE_ATTESTATION_ROLE
    assert (
        manifest["evaluation_signer_role"]
        == evaluation_artifact._EVALUATION_ATTESTATION_ROLE
    )
    assert manifest["signers_must_be_distinct"] is True


def test_empty_code_reviewed_trust_roots_fail_closed() -> None:
    assert dict(readiness.TRUSTED_EXACT_PROBE_ED25519_PUBLIC_KEYS) == {}
    assert dict(evaluation_artifact.TRUSTED_EVALUATION_ED25519_PUBLIC_KEYS) == {}

    plan = vkpi_model_evidence_plan.build_plan()
    assert plan["release_gate"]["passed"] is False
    assert plan["claim_status"] == "descriptive_only"
    trust_roots = plan["current_readiness"]["attestation_trust_roots"]
    assert trust_roots["ready_to_verify_signed_evidence"] is False
    assert trust_roots["failure_reasons"] == [
        "probe_trust_root_missing",
        "evaluation_trust_root_missing",
    ]


def test_release_reason_matches_measured_readiness_and_pricing_counts() -> None:
    plan = vkpi_model_evidence_plan.build_plan()
    readiness_summary = plan["current_readiness"]
    manifest = plan["execution_manifest"]
    reason = plan["release_gate"]["reason"]

    missing = (
        int(readiness_summary["candidate_count"])
        - int(readiness_summary["production_ready_count"])
    )
    assert f"{missing}/{readiness_summary['candidate_count']} exact bindings" in reason
    assert "unsigned planning manifest" in reason
    if manifest["unknown_cost_binding_count"] == 0:
        assert "lack a registered pricing contract" not in reason


def test_execution_plan_requires_thirty_actuals_per_task_not_per_shared_binding() -> None:
    plan = vkpi_model_evidence_plan.build_plan()
    manifest = plan["execution_manifest"]
    rows = {row["binding"]: row for row in manifest["bindings"]}

    assert manifest["task_binding_count"] == 18
    assert manifest["unique_binding_count"] == 8
    assert manifest["minimum_actual_evaluation_cases_per_task"] == 30
    assert manifest["minimum_possible_generation_calls"] == 540
    assert manifest["provider_generation_calls_ceiling"] == 548
    assert manifest["known_text_only_cost_subtotal_usd"] == 6.3513
    assert rows["openai/gpt-5.4-mini"]["required_calls"] == {
        "actual_evaluation_cases": 120,
        "actual_evaluation_cases_per_task": {
            "audit_pre_filter": 30,
            "kol_content_fit_analysis": 30,
            "kol_product_fit_reason": 30,
            "via_chat": 30,
        },
        "minimum_actual_evaluation_cases_per_task": 30,
        "exact_response_probe": 1,
        "provider_generation_calls_ceiling": 121,
        "note": (
            "Every bound task requires its own 30 actual evaluation cases. "
            "The probe response must also be included in the signed evaluation "
            "artifact; the per-binding ceiling may be reduced by one by "
            "designating one evaluation case as the exact probe."
        ),
    }
    assert rows["anthropic/claude-sonnet-4-6"]["required_calls"][
        "actual_evaluation_cases"
    ] == 90
    assert rows["anthropic/claude-opus-4-7"]["tasks"] == [
        "ai_today_evidence_strategy",
        "contract_pdf_extract",
        "deepsight_strategy",
        "invoice_extract",
    ]
    assert rows["google/gemini-2.5-pro"]["tasks"] == [
        "ai_today_grounded_discovery",
        "deepsight_opportunity",
    ]
