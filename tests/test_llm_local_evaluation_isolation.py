from __future__ import annotations

import copy
import sqlite3
from contextlib import contextmanager
from typing import Any

import pytest


def _configure_local(monkeypatch):
    from app.platform import llm_local_evaluation as local_eval

    monkeypatch.setattr(local_eval, "IS_PRODUCTION", False)
    monkeypatch.setenv("VKPI_LLM_LOCAL_EVALUATION_ENABLED", "1")
    monkeypatch.setenv("VKPI_LLM_LOCAL_EVALUATION_SIGNING_SECRET", "test-only-secret")
    return local_eval


def _issued_payload(monkeypatch, *, job_id: int = 41, now: int = 1_000):
    local_eval = _configure_local(monkeypatch)
    payload: dict[str, Any] = {
        "target_type": "video",
        "target_id": "3951",
        "derive_method": "video_analysis_final_v1",
        "local_evaluation": True,
        "execution_class": "local_evaluation",
        "model_binding": "google/gemini-2.5-flash",
    }
    payload[local_eval.LOCAL_EVALUATION_CAPABILITY_FIELD] = (
        local_eval.issue_local_evaluation_capability(
            job_id=job_id,
            target_type="video",
            target_id="3951",
            derive_method="video_analysis_final_v1",
            now=now,
        )
    )
    return local_eval, payload


def test_old_jobs_are_not_reinterpreted_by_local_operator_flag(monkeypatch) -> None:
    local_eval = _configure_local(monkeypatch)

    result = local_eval.verify_job_local_evaluation_capability(
        {
            "target_type": "video",
            "target_id": "3951",
            "derive_method": "video_analysis_final_v1",
        },
        job_id=41,
        now=1_001,
    )

    assert result["valid"] is True
    assert result["requested"] is False
    assert result["execution_class"] == "production"


def test_capability_binds_job_target_derive_model_expiry_and_nonce(monkeypatch) -> None:
    local_eval, payload = _issued_payload(monkeypatch)

    valid = local_eval.verify_job_local_evaluation_capability(
        payload, job_id=41, now=1_001
    )
    assert valid["valid"] is True
    assert valid["cache_derive_method"] == "video_analysis_final_v1__local_eval"
    assert valid["claim_status"] == "descriptive_only"
    assert valid["model_readiness_status"] == "evaluation_only_not_production_ready"
    assert len(valid["capability_nonce_sha256"]) == 64

    # Same target, same token, different durable row: replay is rejected.
    replay = local_eval.verify_job_local_evaluation_capability(
        payload, job_id=42, now=1_001
    )
    assert replay["valid"] is False
    assert replay["reason"] == "local_evaluation_capability_job_id_mismatch"

    for field, value, reason in (
        ("target_id", "3952", "local_evaluation_capability_target_id_mismatch"),
        ("derive_method", "video_analysis_final_v1_keyframe_qa", "local_evaluation_capability_derive_method_mismatch"),
        ("model_binding", "google/gemini-3.5-flash", "local_evaluation_capability_model_binding_mismatch"),
    ):
        tampered = {**payload, field: value}
        result = local_eval.verify_job_local_evaluation_capability(
            tampered, job_id=41, now=1_001
        )
        assert result["valid"] is False
        assert result["reason"] == reason

    expired = local_eval.verify_job_local_evaluation_capability(
        payload, job_id=41, now=2_801
    )
    assert expired["valid"] is False
    assert expired["reason"] == "local_evaluation_capability_expired"

    claims_tampered = copy.deepcopy(payload)
    claims_tampered[local_eval.LOCAL_EVALUATION_CAPABILITY_FIELD]["claims"]["nonce"] = "changed"
    bad_signature = local_eval.verify_job_local_evaluation_capability(
        claims_tampered, job_id=41, now=1_001
    )
    assert bad_signature["valid"] is False
    assert bad_signature["reason"] == "local_evaluation_capability_signature_invalid"


def test_local_evaluation_is_final_v1_only_and_production_forbidden(monkeypatch) -> None:
    local_eval = _configure_local(monkeypatch)

    with pytest.raises(local_eval.LocalEvaluationCapabilityError, match="derive_not_allowed"):
        local_eval.issue_local_evaluation_capability(
            job_id=41,
            target_type="video",
            target_id="3951",
            derive_method="video_analysis_final_v1_keyframe_qa",
            now=1_000,
        )
    with pytest.raises(local_eval.LocalEvaluationCapabilityError, match="model_not_allowed"):
        local_eval.issue_local_evaluation_capability(
            job_id=41,
            target_type="video",
            target_id="3951",
            derive_method="video_analysis_final_v1",
            model_binding="google/gemini-3.5-flash",
            now=1_000,
        )

    monkeypatch.setattr(local_eval, "IS_PRODUCTION", True)
    with pytest.raises(local_eval.LocalEvaluationCapabilityError, match="forbidden_in_production"):
        local_eval.issue_local_evaluation_capability(
            job_id=41,
            target_type="video",
            target_id="3951",
            derive_method="video_analysis_final_v1",
            now=1_000,
        )


def test_capability_is_recursively_removed_from_api_values(monkeypatch) -> None:
    local_eval, payload = _issued_payload(monkeypatch)
    value = {"job": {"payload": payload}, "items": [{"payload": payload}]}

    redacted = local_eval.redact_local_evaluation_capability(value)

    assert local_eval.LOCAL_EVALUATION_CAPABILITY_FIELD not in redacted["job"]["payload"]
    assert local_eval.LOCAL_EVALUATION_CAPABILITY_FIELD not in redacted["items"][0]["payload"]
    assert "test-only-secret" not in repr(redacted)


def _cache_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE vkpi_analysis_cache (
          id INTEGER PRIMARY KEY,
          target_type TEXT,
          target_id TEXT,
          derive_method TEXT,
          model TEXT,
          cost REAL,
          status TEXT,
          triggered_by_user_id INTEGER,
          result TEXT,
          created_at TEXT,
          updated_at TEXT
        )
        """
    )
    return conn


def test_cache_isolation_is_opt_in_and_production_always_wins() -> None:
    from app.domains.analysis.cache_repo import get_analysis_cache_entry

    conn = _cache_conn()
    conn.execute(
        "INSERT INTO vkpi_analysis_cache VALUES (1,'video','3951','video_analysis_final_v1__local_eval','gemini-2.5-flash',0,'ready',NULL,'{\"evaluation_only\":true}','2026-01-02','2026-01-02')"
    )

    assert get_analysis_cache_entry(
        "video", "3951", derive_method="video_analysis_final_v1", conn=conn
    ) is None
    evaluation = get_analysis_cache_entry(
        "video",
        "3951",
        derive_method="video_analysis_final_v1",
        allow_local_evaluation_fallback=True,
        conn=conn,
    )
    assert evaluation and evaluation["derive_method"] == "video_analysis_final_v1__local_eval"

    # An older production result is still authoritative over a newer eval row.
    conn.execute(
        "INSERT INTO vkpi_analysis_cache VALUES (2,'video','3951','video_analysis_final_v1','gemini-3.5-flash',0,'ready',NULL,'{\"evaluation_only\":false}','2026-01-01','2026-01-01')"
    )
    production = get_analysis_cache_entry(
        "video",
        "3951",
        derive_method="video_analysis_final_v1",
        allow_local_evaluation_fallback=True,
        conn=conn,
    )
    assert production and production["derive_method"] == "video_analysis_final_v1"


class _FakeCursor:
    def __init__(self, statements: list[tuple[str, Any]]) -> None:
        self.statements = statements
        self.last_sql = ""

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql: str, params: Any = None) -> None:
        self.last_sql = sql
        self.statements.append((sql, params))

    def fetchone(self):
        return (77,) if "RETURNING id" in self.last_sql else None


class _FakePsycopgConnection:
    def __init__(self) -> None:
        self.statements: list[tuple[str, Any]] = []

    @contextmanager
    def transaction(self):
        yield

    def cursor(self, **_kwargs):
        return _FakeCursor(self.statements)


def test_eval_cache_write_uses_separate_method_and_skips_production_followups(monkeypatch) -> None:
    # Import the owner module first; gemini intentionally participates in a
    # late-import cycle with the worker facade.
    from app.workers import apify_jobs_worker as _worker  # noqa: F401
    from app.workers import apify_jobs_worker_gemini as gemini_worker

    conn = _FakePsycopgConnection()
    summaries: list[dict[str, Any]] = []
    monkeypatch.setattr(gemini_worker, "ensure_final_v1_result_cacheable", lambda _raw: None)
    monkeypatch.setattr(
        gemini_worker,
        "_shape_gemini_result",
        lambda **_kwargs: {"evaluation_only": True, "claim_status": "descriptive_only"},
    )
    monkeypatch.setattr(
        gemini_worker,
        "_sync_search_session_job",
        lambda *_args, **kwargs: summaries.append(kwargs["analysis_summary"]),
    )
    monkeypatch.setattr(
        gemini_worker,
        "_sync_deep_analysis_result_from_cache",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("eval entered deep result")),
    )

    gemini_worker._write_gemini_cache(
        conn,
        job={"id": 41},
        payload={"target_type": "video", "target_id": "3951"},
        evidence={"id": 3951},
        raw={
            "model": "gemini-2.5-flash",
            "llm_execution": {
                "execution_class": "local_evaluation",
                "evaluation_only": True,
                "production_authorized": False,
                "claim_status": "descriptive_only",
                "model_readiness_status": "evaluation_only_not_production_ready",
                "cache_derive_method": "video_analysis_final_v1__local_eval",
                "base_derive_method": "video_analysis_final_v1",
            },
        },
        cost=0.01,
        cost_basis="test",
        preflight_cost=0.01,
        latency_ms=5,
        derive_method="video_analysis_final_v1",
    )

    insert_params = next(params for sql, params in conn.statements if "INSERT INTO vkpi_analysis_cache" in sql)
    assert insert_params[3] == "video_analysis_final_v1__local_eval"
    assert summaries[0]["evaluation_only"] is True
    assert summaries[0]["claim_status"] == "descriptive_only"


def test_single_video_api_rejects_user_local_eval(monkeypatch) -> None:
    from app.api.routers import vkpi_kol_pool
    from fastapi import HTTPException

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        vkpi_kol_pool.kol_video_analysis_enqueue,
        "enqueue_final_v1_video_analysis",
        lambda **kwargs: calls.append(kwargs) or {"status": "queued"},
    )

    vkpi_kol_pool.enqueue_pool_item_video_analysis(
        10, {"evidence_id": 20, "local_evaluation": "true"}, staff={"id": 1}
    )
    with pytest.raises(HTTPException) as raised:
        vkpi_kol_pool.enqueue_pool_item_video_analysis(
            10, {"evidence_id": 20, "local_evaluation": True}, staff={"id": 1}
        )

    assert raised.value.status_code == 403
    assert raised.value.detail == "local_evaluation_http_forbidden"
    assert len(calls) == 1
    assert calls[0]["local_evaluation"] is False
