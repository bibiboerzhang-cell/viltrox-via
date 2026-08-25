from __future__ import annotations

from copy import deepcopy

import pytest

from app.core.video_analysis_contract import (
    FINAL_V1_DERIVE_METHOD,
    FINAL_V1_PROMPT_CONTRACT,
    FINAL_V1_SCHEMA_VERSION,
)
from app.core.video_model_chain import final_v1_model_chain
from app.domains.analysis.cache_reuse import canonical_final_v1_cache_reuse


TARGET_ID = "701"


def _quality_layers() -> dict[str, object]:
    scores = {
        key: {"score": None, "evidence": "not enough evidence"}
        for key in (
            "content_quality_score",
            "viewer_heart_score",
            "channel_value_score",
            "asset_reuse_score",
            "product_proof_score",
            "marketing_value_score",
        )
    }
    return {
        "layer1_visual_content": {
            "content_summary": "A creator demonstrates a camera setup.",
            "scene_timeline": [{"timestamp": "00:03", "what": "Camera body shown"}],
            "brand_product_evidence": {
                "viltrox_status": "unknown",
                "inspection_complete": True,
                "checked_modalities": ["visual", "audio"],
                "viltrox_evidence": [],
                "viltrox_products": [],
                "competitors": [],
            },
        },
        "layer2_viewer_emotion": {},
        "layer3_three_values": {},
        "layer4_attribution": {},
        "layer5_recommendations": {},
        "layer6_flags_and_scores": {
            "risk_flags": [],
            "scores": scores,
            "final_verdict": "Descriptive evidence only.",
            "key_hook": "Practical setup walkthrough.",
        },
    }


def _snapshots(*, operator_ack: bool = False) -> tuple[dict[str, object], dict[str, object]]:
    if operator_ack:
        return (
            {
                "scope": "execution_time_snapshot",
                "authorized": True,
                "production_authorized": True,
                "evaluation_only": False,
                "status": "operationally_authorized",
                "source": "operator_ack",
                "temporary": True,
            },
            {
                "scope": "execution_time_snapshot",
                "production_ready": False,
                "status": "not_production_ready",
                "claim_status": "descriptive_only",
                "evidence_source": "not_configured",
            },
        )
    return (
        {
            "scope": "execution_time_snapshot",
            "authorized": True,
            "production_authorized": True,
            "evaluation_only": False,
            "status": "operationally_authorized",
            "source": "signed_evidence",
            "temporary": False,
        },
        {
            "scope": "execution_time_snapshot",
            "production_ready": True,
            "status": "production_ready",
            "claim_status": "descriptive_only",
            "evidence_source": "signed_registry",
        },
    )


def _canonical_row(
    *,
    selected_index: int = 0,
    ready_from_index: int = 0,
    operator_ack: bool = False,
) -> dict[str, object]:
    requested = final_v1_model_chain()
    selected = requested[selected_index]
    ready = requested[ready_from_index:]
    execution_snapshot, signed_snapshot = _snapshots(operator_ack=operator_ack)

    by_model: dict[str, object] = {}
    by_binding: dict[str, object] = {}
    for model in ready:
        entry = {
            "binding": f"google/{model}",
            "model": model,
            "execution_authorization_at_run": deepcopy(execution_snapshot),
            "signed_readiness_at_run": deepcopy(signed_snapshot),
        }
        by_model[model] = deepcopy(entry)
        by_binding[f"google/{model}"] = deepcopy(entry)

    execution = {
        "binding": f"google/{selected}",
        "model": selected,
        "selected_model": selected,
        "provider_reported_model": selected,
        "provider_model_match": True,
        "model_match": True,
        "requested_model_chain": requested,
        "ready_model_chain": ready,
        "model_chain": ready,
        "fallback_used": selected != requested[0],
        "authorization_snapshot_match": True,
        "execution_authorizations_by_model": by_model,
        "execution_authorizations_by_binding": by_binding,
        "execution_class": "production",
        "authorization_scope": "production",
        "evaluation_only": False,
        "production_authorized": True,
        "execution_authorization_at_run": deepcopy(execution_snapshot),
        "signed_readiness_at_run": deepcopy(signed_snapshot),
    }
    provenance = {
        "prompt_contract": FINAL_V1_PROMPT_CONTRACT,
        "binding": f"google/{selected}",
        "selected_model": selected,
        "provider_reported_model": selected,
        "requested_model_chain": requested,
        "ready_model_chain": ready,
        "model_chain": ready,
        "fallback_used": selected != requested[0],
        "authorization_snapshot_match": True,
        "execution_class": "production",
        "authorization_scope": "production",
        "evaluation_only": False,
        "production_authorized": True,
        "execution_authorization_at_run": deepcopy(execution_snapshot),
        "signed_readiness_at_run": deepcopy(signed_snapshot),
    }
    result = {
        "schema_version": FINAL_V1_SCHEMA_VERSION,
        "status": "completed",
        "quality_status": "quality_complete",
        "quality_issues": [],
        "mock": False,
        "analysis_method": FINAL_V1_DERIVE_METHOD,
        "model": selected,
        "target_type": "video",
        "target_id": TARGET_ID,
        "evaluation_only": False,
        "production_authorized": True,
        "claim_status": "descriptive_only",
        "provenance": provenance,
        "llm_execution": execution,
        "execution_authorization_at_run": deepcopy(execution_snapshot),
        "signed_readiness_at_run": deepcopy(signed_snapshot),
        **_quality_layers(),
    }
    return {
        "id": 99,
        "target_type": "video",
        "target_id": TARGET_ID,
        "derive_method": FINAL_V1_DERIVE_METHOD,
        "model": selected,
        "prompt_version": FINAL_V1_PROMPT_CONTRACT,
        "status": "ready",
        "result": result,
    }


def _classify(row: dict[str, object]) -> dict[str, object]:
    return canonical_final_v1_cache_reuse(
        row,
        target_type="video",
        target_id=TARGET_ID,
        derive_method=FINAL_V1_DERIVE_METHOD,
    )


@pytest.mark.parametrize("operator_ack", [False, True])
def test_canonical_cache_accepts_truthful_signed_or_operator_ack_snapshot(operator_ack: bool) -> None:
    decision = _classify(_canonical_row(operator_ack=operator_ack))
    assert decision == {
        "exists": True,
        "reusable": True,
        "cache_id": 99,
        "cache_reuse_status": "canonical",
        "revalidation_required": False,
        "claim_status": "descriptive_only",
        "reasons": [],
    }


def test_canonical_cache_accepts_primary_blocked_fallback_selected() -> None:
    assert _classify(_canonical_row(selected_index=1, ready_from_index=1))["reusable"] is True


def test_current_worker_shaper_emits_a_canonical_reusable_result() -> None:
    from app.workers import apify_jobs_worker  # noqa: F401 - initialize worker exports
    from app.workers import apify_jobs_worker_gemini as worker

    fixture = _canonical_row()
    execution = fixture["result"]["llm_execution"]
    shaped = worker._shape_gemini_result(
        job={"id": 9001},
        evidence={"id": int(TARGET_ID), "platform": "youtube"},
        raw={
            "analyzed": True,
            "status": "completed",
            "model": fixture["model"],
            "method": f"gemini_direct_{fixture['model']}",
            "provider_reported_model": fixture["model"],
            "quality_status": "quality_complete",
            "quality_issues": [],
            "video_analysis_final_v1": _quality_layers(),
            "llm_execution": execution,
        },
        cost=0.01,
        cost_basis="unit_test",
        preflight_cost=0.01,
        latency_ms=10,
        derive_method=FINAL_V1_DERIVE_METHOD,
    )
    decision = _classify(
        {
            "id": 100,
            "target_type": "video",
            "target_id": TARGET_ID,
            "derive_method": FINAL_V1_DERIVE_METHOD,
            "model": fixture["model"],
            "prompt_version": FINAL_V1_PROMPT_CONTRACT,
            "status": "ready",
            "result": shaped,
        }
    )
    assert decision["reusable"] is True, decision["reasons"]


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (
            lambda row: row["result"]["provenance"].pop("prompt_contract"),
            "result_prompt_contract_mismatch",
        ),
        (
            lambda row: row["result"]["llm_execution"].pop("signed_readiness_at_run"),
            "signed_readiness_snapshot_missing",
        ),
        (
            lambda row: row["result"].__setitem__("mock", True),
            "mock_result_forbidden",
        ),
        (
            lambda row: row["result"]["llm_execution"].__setitem__("execution_class", "local_evaluation"),
            "execution_class_invalid",
        ),
        (
            lambda row: row["result"]["llm_execution"].__setitem__("provider_reported_model", "unrelated-model"),
            "provider_reported_model_identity_mismatch",
        ),
        (
            lambda row: row["result"]["llm_execution"]["execution_authorizations_by_model"][row["model"]].pop("binding"),
            "by_model_binding_mismatch",
        ),
        (
            lambda row: row["result"]["provenance"].__setitem__("ready_model_chain", []),
            "provenance_ready_model_chain_mismatch",
        ),
    ],
)
def test_noncanonical_ready_cache_is_descriptive_only(mutate, reason: str) -> None:
    row = _canonical_row()
    mutate(row)
    decision = _classify(row)
    assert decision["reusable"] is False
    assert decision["cache_reuse_status"] == "legacy_unverified"
    assert decision["revalidation_required"] is True
    assert decision["claim_status"] == "descriptive_only"
    assert reason in decision["reasons"]


def test_operator_ack_cannot_promote_signed_readiness() -> None:
    row = _canonical_row(operator_ack=True)
    signed = row["result"]["llm_execution"]["signed_readiness_at_run"]
    signed["production_ready"] = True
    signed["status"] = "production_ready"
    row["result"]["provenance"]["signed_readiness_at_run"] = deepcopy(signed)
    row["result"]["signed_readiness_at_run"] = deepcopy(signed)
    model = row["model"]
    binding = f"google/{model}"
    row["result"]["llm_execution"]["execution_authorizations_by_model"][model][
        "signed_readiness_at_run"
    ] = deepcopy(signed)
    row["result"]["llm_execution"]["execution_authorizations_by_binding"][binding][
        "signed_readiness_at_run"
    ] = deepcopy(signed)
    decision = _classify(row)
    assert decision["reusable"] is False
    assert "operator_ack_must_not_promote_signed_readiness" in decision["reasons"]


def test_cache_id_alias_is_preserved_for_derived_consumers() -> None:
    row = _canonical_row()
    row["cache_id"] = row.pop("id")
    assert _classify(row)["cache_id"] == 99


def test_all_legacy_batch_is_terminal_partial_and_never_claims_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domains.kol import video_analysis_enqueue as enqueue

    monkeypatch.setattr(enqueue, "get_conn", lambda: object())
    monkeypatch.setattr(
        enqueue,
        "_enqueue_final_v1_video_analysis",
        lambda *_args, **kwargs: {
            "status": "partial",
            "state": "partial",
            "effective_status": "legacy_unverified",
            "terminal": True,
            "kol_pool_id": kwargs["kol_pool_id"],
            "evidence_id": kwargs["evidence_id"],
            "cache_reuse_status": "legacy_unverified",
            "revalidation_required": True,
            "claim_status": "descriptive_only",
            "provider_calls": False,
            "write_db": False,
        },
    )

    result = enqueue.enqueue_final_v1_video_analysis_batch(
        items=[{"kol_pool_id": 9, "evidence_id": 701}],
        staff={"id": 1},
    )

    assert result["status"] == result["state"] == "partial"
    assert result["effective_status"] == "legacy_unverified"
    assert result["terminal"] is True
    assert result["legacy_unverified_count"] == 1
    assert result["queued"] == 0
    assert result["provider_calls"] is False
    assert result["write_db"] is False
    assert result["writes"] == []


def test_mixed_batch_preserves_completed_receipt_with_queued_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domains.kol import video_analysis_enqueue as enqueue

    monkeypatch.setattr(enqueue, "get_conn", lambda: object())

    def enqueue_one(*_args, **kwargs):
        if kwargs["evidence_id"] == 701:
            return {"status": "queued", "provider_calls": False, "write_db": True}
        return {
            "status": "partial",
            "effective_status": "legacy_unverified",
            "terminal": True,
            "provider_calls": False,
            "write_db": False,
        }

    monkeypatch.setattr(enqueue, "_enqueue_final_v1_video_analysis", enqueue_one)
    result = enqueue.enqueue_final_v1_video_analysis_batch(
        items=[
            {"kol_pool_id": 9, "evidence_id": 701},
            {"kol_pool_id": 9, "evidence_id": 702},
        ]
    )
    assert result["status"] == "completed"
    assert result["state"] == "queued"
    assert result["terminal"] is False
    assert result["queued"] == 1
    assert result["legacy_unverified_count"] == 1
