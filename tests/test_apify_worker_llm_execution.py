"""Offline coverage for exact worker-model authorization and result labels."""

from __future__ import annotations

from typing import Any


def test_worker_preflight_and_analyzer_use_the_same_exact_model(monkeypatch) -> None:
    from app.workers import apify_jobs_worker as worker
    from app.workers import apify_jobs_worker_gemini as gemini_worker
    from app.workers import apify_jobs_worker_prep as prep

    captured: dict[str, Any] = {}

    def fake_preflight(prompt: str, **kwargs: Any) -> dict[str, Any]:
        captured.update({"prompt": prompt, **kwargs})
        return {"providers": []}

    monkeypatch.setattr(prep.llm_gateway, "budget_preflight", fake_preflight)
    monkeypatch.setattr(prep, "WORKER_GEMINI_MODEL", "gemini-3.6-flash")
    # Process-wide worker configuration cannot reinterpret an old job.
    monkeypatch.setattr(prep, "WORKER_LLM_EXECUTION_CLASS", "local_evaluation")
    prep._llm_budget_preflight(
        {"job_type": "video"},
        {"target_type": "video", "target_id": "3951"},
    )
    analyzer = gemini_worker._gemini_analyzer_payload(
        {
            "gemini_model": "gemini-3.5-flash",
            "gemini_final_v1_models": ["gemini-3.5-flash"],
        },
        "video_analysis_final_v1",
    )

    assert captured["preferred_provider"] == "google"
    assert captured["model_override"] == "gemini-3.6-flash"
    assert captured["model_fallbacks"] == []
    assert captured["execution_class"] == "production"
    prep._llm_budget_preflight(
        {"job_type": "video"},
        {"target_type": "video", "target_id": "3951"},
        execution_class="local_evaluation",
    )
    assert captured["execution_class"] == "local_evaluation"
    # C1(2026-08-23):子进程不再强制单模型(gemini_model 置空);final_v1 生产 job 发
    # 认可链(主力 → lite 回退),payload 里链外模型(gemini-3.5-flash)不能放宽链。
    from app.core.video_model_chain import final_v1_model_chain

    chain = final_v1_model_chain()
    assert chain[0] == worker.WORKER_GEMINI_MODEL and len(chain) == 2
    assert analyzer["gemini_model"] == ""
    assert analyzer["gemini_models"] == chain
    assert analyzer["gemini_final_v1_models"] == chain
    assert worker.FINAL_V1_GEMINI_MODELS == [worker.WORKER_GEMINI_MODEL]


def test_local_evaluation_result_and_cost_ledger_are_descriptive_only(
    monkeypatch,
) -> None:
    from app.workers import apify_jobs_worker_gemini as gemini_worker

    raw = {
        "analyzed": True,
        "model": "gemini-3.6-flash",
        "method": "gemini_local_fileapi_gemini-3.6-flash",
        "video_analysis_final_v1": {},
        "llm_execution": {
            "binding": "google/gemini-3.6-flash",
            "model": "gemini-3.6-flash",
            "reported_model": "gemini-3.6-flash",
            "execution_class": "local_evaluation",
            "authorization_scope": "evaluation_only",
            "evaluation_only": True,
            "production_authorized": False,
            "claim_status": "descriptive_only",
        },
    }
    shaped = gemini_worker._shape_gemini_result(
        job={"id": 18432},
        evidence={"id": 3951, "platform": "instagram"},
        raw=raw,
        cost=0.01,
        cost_basis="test",
        preflight_cost=0.02,
        latency_ms=100,
        derive_method="video_analysis_final_v1",
    )

    captured: dict[str, Any] = {}

    def fake_record_cost(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(gemini_worker.budget_guard, "record_cost", fake_record_cost)
    gemini_worker._record_gemini_cost(
        job={"id": 18432},
        payload={"target_type": "video", "target_id": "3951"},
        raw=raw,
        cost=0.01,
        cost_basis="test",
        tokens_in=10,
        tokens_out=20,
        latency_ms=100,
        preflight_cost=0.02,
    )

    assert shaped["evaluation_only"] is True
    assert shaped["production_authorized"] is False
    assert shaped["claim_status"] == "descriptive_only"
    assert shaped["provenance"]["binding"] == "google/gemini-3.6-flash"
    assert shaped["llm_execution"]["model_match"] is True
    assert captured["metadata"]["execution_class"] == "local_evaluation"
    assert captured["metadata"]["evaluation_only"] is True
    assert captured["metadata"]["production_authorized"] is False
    assert captured["metadata"]["claim_status"] == "descriptive_only"


def test_google_authorization_preserves_evaluation_scope() -> None:
    from app.workers.apify_jobs_worker_prep import _google_execution_authorization

    result = _google_execution_authorization(
        {
            "execution_class": "local_evaluation",
            "claim_status": "descriptive_only",
            "providers": [
                {
                    "provider": "google",
                    "binding": "google/gemini-3.6-flash",
                    "model": "gemini-3.6-flash",
                    "execution_class": "local_evaluation",
                    "authorization_scope": "evaluation_only",
                    "evaluation_only": True,
                    "production_authorized": False,
                    "model_claim_status": "descriptive_only",
                    "model_readiness_status": "evaluation_only_not_production_ready",
                }
            ],
        }
    )

    assert result == {
        "binding": "google/gemini-3.6-flash",
        "model": "gemini-3.6-flash",
        "execution_class": "local_evaluation",
        "authorization_scope": "evaluation_only",
        "evaluation_only": True,
        "production_authorized": False,
        "claim_status": "descriptive_only",
        "model_readiness_status": "evaluation_only_not_production_ready",
    }
