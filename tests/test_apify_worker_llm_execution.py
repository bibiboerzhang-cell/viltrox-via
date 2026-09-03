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


def test_worker_execution_preflight_runs_fallback_only_ready_chain(monkeypatch) -> None:
    from app.workers import apify_jobs_worker as worker
    from app.workers import apify_jobs_worker_prep as prep
    from app.workers import apify_jobs_worker_runtime as runtime

    primary = worker.WORKER_GEMINI_MODEL
    fallback = "gemini-3.5-flash-lite"
    captured: dict[str, Any] = {}

    def fake_preflight(prompt: str, **kwargs: Any) -> dict[str, Any]:
        captured["preflight_kwargs"] = {"prompt": prompt, **kwargs}
        return {
            "provider_gate_reason": "provider_calls_allowed",
            "execution_class": "production",
            "providers": [
                {
                    "provider": "google",
                    "model": fallback,
                    "binding": f"google/{fallback}",
                    "provider_calls_allowed": True,
                    "production_authorized": True,
                    "execution_class": "production",
                    "authorization_scope": "production",
                    "signed_model_production_ready": True,
                    "signed_model_readiness_status": "production_ready",
                    "signed_model_readiness_evidence_source": "fallback-proof",
                    "estimated_cost_usd": 0.01,
                }
            ],
        }

    monkeypatch.setattr(prep.llm_gateway, "budget_preflight", fake_preflight)
    preflight = prep._llm_budget_preflight(
        {"id": 9101, "job_type": "video"},
        {
            "target_type": "video",
            "target_id": "3951",
            "derive_method": "video_analysis_final_v1",
            "gemini_final_v1_models": [fallback],
        },
    )
    plan = preflight["worker_model_execution"]
    assert captured["preflight_kwargs"]["model_override"] == fallback
    assert captured["preflight_kwargs"]["model_fallbacks"] == []
    assert plan["requested_models"] == [fallback]
    assert plan["ready_models"] == [fallback]
    assert plan["authorizations_by_model"][fallback][
        "signed_readiness_at_run"
    ]["evidence_source"] == "fallback-proof"

    namespace = dict(vars(worker))
    namespace.update(
        {
            "_acquire_llm_slot": lambda _conn: "0",
            "_advisory_lock": lambda *_args: True,
            "_advisory_unlock": lambda *_args: None,
            "_analysis_cache_exists": lambda *_args: False,
            "_analysis_cache_reuse_decision": lambda *_args: {
                "exists": False,
                "reusable": False,
                "reasons": [],
            },
            "_llm_budget_preflight": lambda *_args, **_kwargs: preflight,
            "_google_allowed": lambda *_args: (_ for _ in ()).throw(
                AssertionError("chain-aware plan must own worker authorization")
            ),
            "_google_execution_authorization": lambda *_args, **_kwargs: (
                _ for _ in ()
            ).throw(AssertionError("exact fallback authorization already exists")),
            "_log_budget_preflight_record_only": lambda **_kwargs: None,
            "_respect_gemini_qps": lambda _conn: None,
            "_process_gemini_video": lambda _conn, _job, payload, cost: captured.update(
                {"worker_payload": payload, "estimated_cost": cost}
            ),
            "verify_job_local_evaluation_capability": lambda *_args, **_kwargs: {
                "requested": False
            },
        }
    )
    runtime.process_job_impl(
        object(),
        {
            "id": 9101,
            "job_type": "video",
            "payload": {
                "target_type": "video",
                "target_id": "3951",
                "derive_method": "video_analysis_final_v1",
                "gemini_final_v1_models": [fallback],
            },
        },
        namespace,
    )

    assert captured["worker_payload"]["gemini_final_v1_models"] == [fallback]
    execution = captured["worker_payload"]["_llm_execution"]
    assert execution["binding"] == f"google/{fallback}"
    assert execution["ready_model_chain"] == [fallback]
    assert execution["execution_authorizations_by_model"][fallback][
        "signed_readiness_at_run"
    ]["production_ready"] is True
    assert captured["estimated_cost"] == 0.01
    assert primary not in execution["ready_model_chain"]


def test_full_worker_chain_runs_only_partial_ready_fallback(monkeypatch) -> None:
    from app.core.video_model_chain import final_v1_model_chain
    from app.workers import apify_jobs_worker as worker
    from app.workers import apify_jobs_worker_prep as prep
    from app.workers import apify_jobs_worker_runtime as runtime

    primary, fallback = final_v1_model_chain()
    captured: dict[str, Any] = {}

    def fake_preflight(prompt: str, **kwargs: Any) -> dict[str, Any]:
        captured["gateway"] = {"prompt": prompt, **kwargs}
        return {
            "provider_gate_reason": "provider_calls_allowed",
            "execution_class": "production",
            "providers": [
                {
                    "provider": "google",
                    "model": primary,
                    "binding": f"google/{primary}",
                    "provider_calls_allowed": False,
                    "binding_gate_reason": "readiness_not_production_ready",
                },
                {
                    "provider": "google",
                    "model": fallback,
                    "binding": f"google/{fallback}",
                    "provider_calls_allowed": True,
                    "production_authorized": True,
                    "execution_class": "production",
                    "authorization_scope": "production",
                    "signed_model_production_ready": True,
                    "signed_model_readiness_status": "production_ready",
                    "signed_model_readiness_evidence_source": "fallback-proof",
                    "operational_authorization_source": "signed_evidence",
                    "estimated_cost_usd": 0.01,
                },
            ],
        }

    monkeypatch.setattr(prep.llm_gateway, "budget_preflight", fake_preflight)
    payload = {
        "target_type": "video",
        "target_id": "3951",
        "derive_method": "video_analysis_final_v1",
        "gemini_final_v1_models": [primary, fallback],
    }
    preflight = prep._llm_budget_preflight(
        {"id": 9201, "job_type": "video"}, payload
    )
    plan = preflight["worker_model_execution"]
    assert captured["gateway"]["model_override"] == primary
    assert captured["gateway"]["model_fallbacks"] == [("google", fallback)]
    assert plan["requested_models"] == [primary, fallback]
    assert plan["ready_models"] == [fallback]
    assert plan["blocked_models"] == {
        primary: "readiness_not_production_ready"
    }

    namespace = dict(vars(worker))
    namespace.update(
        {
            "_advisory_lock": lambda *_args: True,
            "_advisory_unlock": lambda *_args: None,
            "_acquire_llm_slot": lambda _conn: "0",
            "_analysis_cache_reuse_decision": lambda *_args: {
                "exists": False,
                "reusable": False,
                "reasons": [],
            },
            "_llm_budget_preflight": lambda *_args, **_kwargs: preflight,
            "_log_budget_preflight_record_only": lambda **_kwargs: None,
            "_respect_gemini_qps": lambda _conn: None,
            "_process_gemini_video": lambda _conn, _job, worker_payload, cost: captured.update(
                {"worker_payload": worker_payload, "cost": cost}
            ),
            "verify_job_local_evaluation_capability": lambda *_args, **_kwargs: {
                "requested": False
            },
        }
    )
    runtime.process_job_impl(
        object(),
        {"id": 9201, "job_type": "video", "payload": payload},
        namespace,
    )
    execution = captured["worker_payload"]["_llm_execution"]
    assert captured["worker_payload"]["gemini_final_v1_models"] == [fallback]
    assert execution["requested_model_chain"] == [primary, fallback]
    assert execution["ready_model_chain"] == [fallback]
    assert execution["binding"] == f"google/{fallback}"


def test_full_worker_chain_all_blocked_never_calls_analyzer(monkeypatch) -> None:
    from app.core.video_model_chain import final_v1_model_chain
    from app.workers import apify_jobs_worker as worker
    from app.workers import apify_jobs_worker_prep as prep
    from app.workers import apify_jobs_worker_runtime as runtime

    primary, fallback = final_v1_model_chain()
    captured: dict[str, Any] = {"blocks": []}

    def fake_preflight(prompt: str, **kwargs: Any) -> dict[str, Any]:
        captured["gateway"] = {"prompt": prompt, **kwargs}
        return {
            "provider_gate_reason": "model_binding_blocked",
            "execution_class": "production",
            "providers": [
                {
                    "provider": "google",
                    "model": model,
                    "binding": f"google/{model}",
                    "provider_calls_allowed": False,
                    "binding_gate_reason": "readiness_not_production_ready",
                }
                for model in (primary, fallback)
            ],
        }

    monkeypatch.setattr(prep.llm_gateway, "budget_preflight", fake_preflight)
    payload = {
        "target_type": "video",
        "target_id": "3952",
        "derive_method": "video_analysis_final_v1",
        "gemini_final_v1_models": [primary, fallback],
    }
    preflight = prep._llm_budget_preflight(
        {"id": 9202, "job_type": "video"}, payload
    )
    assert preflight["worker_model_execution"]["ready_models"] == []
    assert set(preflight["worker_model_execution"]["blocked_models"]) == {
        primary,
        fallback,
    }
    assert captured["gateway"]["model_fallbacks"] == [("google", fallback)]

    namespace = dict(vars(worker))
    namespace.update(
        {
            "_advisory_lock": lambda *_args: True,
            "_advisory_unlock": lambda *_args: None,
            "_acquire_llm_slot": lambda _conn: "0",
            "_analysis_cache_reuse_decision": lambda *_args: {
                "exists": False,
                "reusable": False,
                "reasons": [],
            },
            "_llm_budget_preflight": lambda *_args, **_kwargs: preflight,
            "_log_budget_preflight_record_only": lambda **_kwargs: None,
            "_block_job": lambda *args, **_kwargs: captured["blocks"].append(args),
            "_process_gemini_video": lambda *_args: (_ for _ in ()).throw(
                AssertionError("all-blocked chain must not call analyzer")
            ),
            "verify_job_local_evaluation_capability": lambda *_args, **_kwargs: {
                "requested": False
            },
        }
    )
    runtime.process_job_impl(
        object(),
        {"id": 9202, "job_type": "video", "payload": payload},
        namespace,
    )
    assert captured["blocks"]
    # 2026-09-03 预算误杀修复:模型未就绪就写成「预算已达上限」是 GATE1 M3 的病灶本身。
    # 这条断言从固化谎言改成固化真话——budget_guard_blocked 现在只留给真花超。
    assert captured["blocks"][0][2] == "model_binding_blocked"
    assert captured["blocks"][0][3]["reason_detail"] == "model_binding_blocked"
    assert "budget" not in captured["blocks"][0][2]


def test_selected_fallback_uses_its_own_snapshot_and_missing_snapshot_fails_closed() -> None:
    from app.workers import apify_jobs_worker_gemini as gemini_worker
    from app.workers.apify_jobs_worker_gemini_result import (
        bind_execution_authorization_to_selected_model,
    )

    primary = gemini_worker.WORKER_GEMINI_MODEL
    fallback = "gemini-3.5-flash-lite"

    def authorization(model: str, source: str) -> dict[str, Any]:
        return {
            "binding": f"google/{model}",
            "model": model,
            "execution_class": "production",
            "authorization_scope": "production",
            "evaluation_only": False,
            "production_authorized": True,
            "claim_status": "descriptive_only",
            "model_readiness_status": "production_ready",
            "execution_authorization_at_run": {
                "scope": "execution_time_snapshot",
                "authorized": True,
                "production_authorized": True,
                "evaluation_only": False,
                "status": "operationally_authorized",
                "source": source,
                "temporary": False,
            },
            "signed_readiness_at_run": {
                "scope": "execution_time_snapshot",
                "production_ready": True,
                "status": "production_ready",
                "claim_status": "descriptive_only",
                "evidence_source": source,
            },
        }

    primary_auth = authorization(primary, "primary-proof")
    fallback_auth = authorization(fallback, "fallback-proof")
    selected = bind_execution_authorization_to_selected_model(
        {
            **primary_auth,
            "requested_model_chain": [primary, fallback],
            "ready_model_chain": [primary, fallback],
            "execution_authorizations_by_model": {
                primary: primary_auth,
                fallback: fallback_auth,
            },
            "execution_authorizations_by_binding": {
                f"google/{primary}": primary_auth,
                f"google/{fallback}": fallback_auth,
            },
        },
        selected_model=fallback,
        provider_reported_model=fallback,
        model_chain=[primary, fallback],
        worker_execution_class="production",
        worker_gemini_model=primary,
    )
    assert selected["binding"] == f"google/{fallback}"
    assert selected["selected_model"] == fallback
    assert selected["provider_reported_model"] == fallback
    assert selected["requested_model_chain"] == [primary, fallback]
    assert selected["ready_model_chain"] == [primary, fallback]
    assert selected["fallback_used"] is True
    assert selected["authorization_snapshot_match"] is True
    assert selected["execution_authorization_at_run"]["source"] == "fallback-proof"
    assert selected["signed_readiness_at_run"]["evidence_source"] == "fallback-proof"

    shaped = gemini_worker._shape_gemini_result(
        job={"id": 9102},
        evidence={"id": 3951, "platform": "youtube"},
        raw={
            "analyzed": True,
            "model": fallback,
            "method": f"gemini_direct_{fallback}",
            "quality_status": "ready",
            "quality_issues": [],
            "video_analysis_final_v1": {},
            "llm_execution": selected,
        },
        cost=0.01,
        cost_basis="test",
        preflight_cost=0.01,
        latency_ms=10,
        derive_method="video_analysis_final_v1",
    )
    assert shaped["provenance"]["binding"] == f"google/{fallback}"
    assert shaped["provenance"]["execution_authorization_at_run"][
        "source"
    ] == "fallback-proof"
    assert shaped["provenance"]["signed_readiness_at_run"][
        "evidence_source"
    ] == "fallback-proof"
    assert shaped["llm_execution"]["selected_model"] == fallback
    assert shaped["llm_execution"]["provider_reported_model"] == fallback
    assert shaped["llm_execution"]["authorization_snapshot_match"] is True
    assert shaped["llm_execution"]["execution_authorizations_by_model"][
        fallback
    ]["signed_readiness_at_run"]["evidence_source"] == "fallback-proof"

    missing = bind_execution_authorization_to_selected_model(
        primary_auth,
        selected_model=fallback,
        provider_reported_model=fallback,
        model_chain=[primary, fallback],
        worker_execution_class="production",
        worker_gemini_model=primary,
    )
    assert missing["authorization_snapshot_match"] is False
    assert missing["authorization_issue"] == "authorization_snapshot_missing"
    assert missing["production_authorized"] is False
    assert missing["execution_authorization_at_run"]["authorized"] is False
    assert missing["signed_readiness_at_run"] == {
        "scope": "execution_time_snapshot",
        "production_ready": False,
        "status": "not_production_ready",
        "claim_status": "descriptive_only",
        "evidence_source": "not_recorded",
    }

    identity_missing = bind_execution_authorization_to_selected_model(
        {
            **primary_auth,
            "execution_authorizations_by_model": {
                fallback: {
                    "production_authorized": True,
                    "execution_authorization_at_run": fallback_auth[
                        "execution_authorization_at_run"
                    ],
                    "signed_readiness_at_run": fallback_auth[
                        "signed_readiness_at_run"
                    ],
                }
            },
        },
        selected_model=fallback,
        provider_reported_model=fallback,
        model_chain=[primary, fallback],
        worker_execution_class="production",
        worker_gemini_model=primary,
    )
    assert identity_missing["authorization_snapshot_match"] is False
    assert identity_missing["authorization_issue"] == "authorization_snapshot_missing"


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
    assert shaped["execution_authorization_at_run"] == {
        "scope": "execution_time_snapshot",
        "authorized": True,
        "production_authorized": False,
        "evaluation_only": True,
        "status": "evaluation_only_authorized",
        "source": "local_evaluation",
        "temporary": False,
    }
    assert shaped["signed_readiness_at_run"] == {
        "scope": "execution_time_snapshot",
        "production_ready": False,
        "status": "not_production_ready",
        "claim_status": "descriptive_only",
        "evidence_source": "not_recorded",
    }
    assert shaped["provenance"]["execution_authorization_at_run"] == shaped[
        "execution_authorization_at_run"
    ]
    assert shaped["provenance"]["signed_readiness_at_run"] == shaped[
        "signed_readiness_at_run"
    ]
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
        "execution_authorization_at_run": {
            "scope": "execution_time_snapshot",
            "authorized": True,
            "production_authorized": False,
            "evaluation_only": True,
            "status": "evaluation_only_authorized",
            "source": "local_evaluation",
            "temporary": False,
        },
        "signed_readiness_at_run": {
            "scope": "execution_time_snapshot",
            "production_ready": False,
            "status": "not_production_ready",
            "claim_status": "descriptive_only",
            "evidence_source": "not_configured",
        },
    }
