"""优化波 B·C5:vkpi_analysis_cache 模型键(迁移 289)——prompt_version / model_family 写读两侧。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.domains.analysis import cache_repo


ROOT = Path(__file__).resolve().parents[1]
FORWARD = ROOT / "migrations" / "289_vkpi_analysis_cache_model_family.sql"
DOWN = ROOT / "migrations" / "289_vkpi_analysis_cache_model_family_down.sql"


@pytest.mark.parametrize(
    "model, family",
    [
        ("gemini-3.6-flash", "gemini-3.6"),
        ("gemini-3.5-flash-lite", "gemini-3.5"),
        ("gemini-2.5-flash", "gemini-2.5"),
        ("gemini-2.5-pro", "gemini-2.5"),
        ("gemini-3-flash-preview", "gemini-3"),
        ("gemini-3-flash-preview+claude-opus-4-8", "gemini-3"),
        ("gpt-5.4-mini-2026-03-17", "gpt-5.4"),
        ("gpt-5.5", "gpt-5.5"),
        ("claude-opus-4-8", "claude-opus-4"),
        ("claude-sonnet-4-6", "claude-sonnet-4"),
        ("gemini-flash-latest", "gemini"),
        ("mock", "mock"),
        ("llm_gateway", "llm_gateway"),
        ("  Gemini-3.6-Flash ", "gemini-3.6"),
        ("", None),
        (None, None),
    ],
)
def test_model_family_prefix_rule(model: Any, family: str | None) -> None:
    assert cache_repo.model_family(model) == family


def test_upsert_params_fill_prompt_version_and_family() -> None:
    params = cache_repo.video_analysis_cache_upsert_params(
        target_type="video",
        target_id="701",
        model="gemini-3.6-flash",
        derive_method="video_analysis_final_v1",
        result_json="{}",
        cost=0.01,
        triggered_by_user_id=5,
        prompt_version="final_v1_pure_video_evidence_v2",
    )
    assert params == (
        "video", "701", "gemini-3.6-flash", "video_analysis_final_v1", "{}", 0.01,
        5, "final_v1_pure_video_evidence_v2", "gemini-3.6", "ready",
    )
    sql = cache_repo.VIDEO_ANALYSIS_CACHE_UPSERT_SQL
    assert sql.count("%s") == 10
    assert "prompt_version = EXCLUDED.prompt_version" in sql
    assert "model_family = EXCLUDED.model_family" in sql
    assert "ON CONFLICT (target_type, target_id, derive_method)" in sql  # 唯一键不动
    blank = cache_repo.video_analysis_cache_upsert_params(
        target_type="video", target_id="1", model="mock", derive_method="mock", result_json="{}",
        cost=0, triggered_by_user_id=None, prompt_version="  ",
    )
    assert blank[7] is None and blank[8] == "mock" and blank[9] == "ready"

    incomplete = cache_repo.video_analysis_cache_upsert_params(
        target_type=cache_repo.VIDEO_QUALITY_TRIAGE_TARGET_TYPE,
        target_id="2", model="mock", derive_method="video_analysis_final_v1",
        result_json="{}", cost=0, triggered_by_user_id=None, prompt_version=None,
        status="quality_incomplete",
    )
    assert incomplete[0] == "video_quality_triage"
    assert incomplete[9] == "quality_incomplete"
    with pytest.raises(ValueError, match="cache status"):
        cache_repo.video_analysis_cache_upsert_params(
            target_type="video", target_id="3", model="mock", derive_method="video_analysis_final_v1",
            result_json="{}", cost=0, triggered_by_user_id=None, prompt_version=None,
            status="forged_ready",
        )


@pytest.mark.parametrize(
    ("target_type", "status", "message"),
    [
        ("video", "quality_incomplete", "must use target_type=video_quality_triage"),
        ("VIDEO_QUALITY_TRIAGE", "quality_incomplete", "must use target_type=video_quality_triage"),
        ("video_quality_triage", "ready", "must not use target_type=video_quality_triage"),
        ("video", "", "cache status"),
        ("video", None, "cache status"),
    ],
)
def test_upsert_params_fail_closed_status_namespace_matrix(
    target_type: str,
    status: str | None,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        cache_repo.video_analysis_cache_upsert_params(
            target_type=target_type,
            target_id="guarded",
            model="gemini-3.6-flash",
            derive_method="video_analysis_final_v1",
            result_json="{}",
            cost=0.01,
            triggered_by_user_id=None,
            prompt_version="final_v1_pure_video_evidence_v2",
            status=status,  # type: ignore[arg-type] - invalid runtime input is the contract under test.
        )


def test_row_to_entry_reads_new_columns_and_tolerates_old_rows() -> None:
    new_row = {
        "target_type": "video", "target_id": "1", "derive_method": "video_analysis_final_v1", "model": "gemini-3.6-flash",
        "cost": 0.1, "status": "ready", "triggered_by_user_id": None, "result": "{}", "created_at": None, "updated_at": None,
        "prompt_version": "final_v1_pure_video_evidence_v2", "model_family": "gemini-3.6",
    }
    entry = cache_repo._row_to_entry(new_row)
    assert entry["prompt_version"] == "final_v1_pure_video_evidence_v2"
    assert entry["model_family"] == "gemini-3.6"
    old_row = {k: v for k, v in new_row.items() if k not in {"prompt_version", "model_family"}}
    old_row["model"] = "gemini-2.5-flash"
    legacy = cache_repo._row_to_entry(old_row)
    assert legacy["prompt_version"] is None
    assert legacy["model_family"] == "gemini-2.5"  # 缺列时从 model 派生,读侧永远有家族


def test_migration_289_files_follow_compat_rules() -> None:
    forward = FORWARD.read_text(encoding="utf-8")
    down = DOWN.read_text(encoding="utf-8")
    for text in (forward, down):
        assert "?" not in text, "ASCII question mark is a compat placeholder"
        assert "%" not in text
        assert "BEGIN" not in text.upper().split("COMMENT")[0] or "BEGIN" not in text
    assert "ADD COLUMN IF NOT EXISTS prompt_version TEXT NULL" in forward
    assert "ADD COLUMN IF NOT EXISTS model_family TEXT NULL" in forward
    assert "UPDATE vkpi_analysis_cache" in forward  # 回填历史行
    assert "uq_vkpi_analysis_cache_target_method" not in forward  # 唯一键不动
    assert "DROP COLUMN IF EXISTS model_family" in down and "DROP COLUMN IF EXISTS prompt_version" in down
    assert "289_vkpi_analysis_cache_model_family.sql" in down


def test_worker_write_paths_use_shared_upsert(monkeypatch: pytest.MonkeyPatch) -> None:
    """两条 worker 写路径都走 cache_repo 的单一 SQL,prompt_version/model_family 一起落。"""

    import app.workers.apify_jobs_worker  # noqa: F401  入口先加载(gemini 子模块底部回灌 import)
    from app.workers import apify_jobs_worker_gemini as worker_gemini

    source = Path(worker_gemini.__file__).read_text(encoding="utf-8")
    assert "INSERT INTO vkpi_analysis_cache" not in source
    assert source.count("cache_id = upsert_video_analysis_cache(") == 2  # 两条写路径同一入口
    assert worker_gemini._cache_prompt_version("video_analysis_final_v1") == "final_v1_pure_video_evidence_v2"
    assert worker_gemini._cache_prompt_version("video_analysis_final_v1_keyframe_qa") == "final_v1_pure_video_evidence_v2"
    assert worker_gemini._cache_prompt_version("gemini_video_v2") is None


def test_write_gemini_cache_params_carry_model_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.workers.apify_jobs_worker  # noqa: F401
    from app.workers import apify_jobs_worker_gemini as worker_gemini

    statements: list[tuple[str, tuple[Any, ...]]] = []

    class _Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
            statements.append((sql, params))
        def fetchone(self): return (42,)

    class _Tx:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class _Conn:
        def transaction(self): return _Tx()
        def cursor(self, **_k): return _Cur()

    monkeypatch.setattr(worker_gemini, "_sync_search_session_job", lambda *a, **k: None)
    monkeypatch.setattr(worker_gemini, "_sync_deep_analysis_result_from_cache", lambda *a, **k: None)
    monkeypatch.setattr(worker_gemini, "_enqueue_content_fit_after_final_v1", lambda *a, **k: None)
    monkeypatch.setattr(worker_gemini, "_enqueue_account_dossier_extract_after_final_v1", lambda *a, **k: None)
    monkeypatch.setattr(worker_gemini, "_search_session_analysis_summary_from_result", lambda *a, **k: {})
    monkeypatch.setattr(worker_gemini, "_shape_gemini_result", lambda **k: {"shaped": True})
    monkeypatch.setattr(worker_gemini, "ensure_final_v1_result_cacheable", lambda raw: None)
    raw = {"analyzed": True, "model": "gemini-3.6-flash", "method": "gemini_direct_gemini-3.6-flash", "llm_execution": {"production_authorized": True}}
    try:
        worker_gemini._write_gemini_cache(
            conn=_Conn(),
            job={"id": 9, "payload": {}},
            payload={"target_type": "video", "target_id": "701", "derive_method": "video_analysis_final_v1"},
            evidence={"id": 701},
            raw=raw,
            cost=0.02,
            cost_basis="x",
            preflight_cost=0.02,
            latency_ms=10,
            derive_method="video_analysis_final_v1",
        )
    except Exception as exc:  # 后续派生入队不在本测试范围,只要 INSERT 已发出
        if not statements:
            raise exc
    insert = next((sql, p) for sql, p in statements if "vkpi_analysis_cache" in sql)
    assert insert[0] is cache_repo.VIDEO_ANALYSIS_CACHE_UPSERT_SQL
    params = insert[1]
    assert params[2] == "gemini-3.6-flash"
    assert params[7] == "final_v1_pure_video_evidence_v2"
    assert params[8] == "gemini-3.6"
    assert json.loads(params[4]) == {"shaped": True}
