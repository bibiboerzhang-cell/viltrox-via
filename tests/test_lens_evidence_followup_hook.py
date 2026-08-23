"""波 D·D2「深析完成即提列」:单条提列入口 + worker 钩子合同。

- extract_for_cache_id:只认 final_v1/ready 行;落表 + 账本;重跑幂等(行数不翻倍、账本一条);
- 「X 系列」归 family(series:xxx),不再计 unresolved;
- worker 钩子:_write_gemini_cache 成功路径恰好调一次;钩子内部炸掉只 warning,cache 写照常;
- 日任务兜底 run_lens_evidence_backfill:账本新鲜 → empty,零写。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "backend", ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app.domains.kol import lens_evidence as le  # noqa: E402
from app.domains.kol import lens_evidence_followup as followup  # noqa: E402
from app.domains.kol import lens_evidence_store as store  # noqa: E402
from tests.test_kol_lens_evidence import _conn, _result  # noqa: E402


def test_extract_for_cache_id_writes_once_and_is_idempotent() -> None:
    conn = _conn()
    first = followup.extract_for_cache_id(conn, 500)
    assert first["status"] == "scanned" and first["evidence_id"] == 41 and first["kol_pool_id"] == 9
    assert first["mention_rows"] > 0 and first["by_resolution"]["unresolved"] == 0
    rows_before = conn.execute("SELECT COUNT(*) FROM vkpi_kol_lens_evidence WHERE cache_id=500").fetchone()[0]
    assert rows_before == first["mention_rows"]
    ledger = conn.execute("SELECT extractor_version, scan_status, mention_rows FROM vkpi_kol_lens_evidence_scan WHERE cache_id=500").fetchall()
    assert len(ledger) == 1 and ledger[0]["extractor_version"] == le.EXTRACTOR_VERSION and ledger[0]["scan_status"] == "scanned"

    again = followup.extract_for_cache_id(conn, 500)
    assert again["mention_rows"] == first["mention_rows"]
    assert conn.execute("SELECT COUNT(*) FROM vkpi_kol_lens_evidence WHERE cache_id=500").fetchone()[0] == rows_before
    assert conn.execute("SELECT COUNT(*) FROM vkpi_kol_lens_evidence_scan").fetchone()[0] == 1

    # 零提及行也记账本(empty_result),无 evidence 归属的缓存记 no_evidence
    assert followup.extract_for_cache_id(conn, 501)["status"] == "empty_result"
    assert followup.extract_for_cache_id(conn, 600)["status"] == "no_evidence"
    # 不存在 / 非 final_v1 行诚实返回,不写
    assert followup.extract_for_cache_id(conn, 999999)["status"] == "not_final_v1_ready"
    assert followup.extract_for_cache_id(conn, 0)["status"] == "invalid_cache_id"

    # 钩子提列过的行,回填脚本再扫 = 账本新鲜 → 跳过(两个写口共用同一账本)
    backfill = store.backfill_lens_evidence(conn, apply=True)
    assert backfill["cache_rows_considered"] == 1  # 只剩 502(钩子没碰过的那条)
    daily = followup.run_lens_evidence_backfill(conn=conn)
    assert daily["status"] == "empty" and daily["written_rows"] == 0 and daily["provider_calls_performed"] is False


def test_series_mentions_resolve_to_family_not_unresolved() -> None:
    conn = _conn()
    conn.execute(
        "INSERT INTO vkpi_analysis_cache (id, target_type, target_id, derive_method, result, status, updated_at) VALUES (700, 'video', '42', 'video_analysis_final_v1', ?, 'ready', '2026-08-21T00:00:00Z')",
        (json.dumps(_result(product_presence="画面里多次出现 Viltrox LAB 系列标识;Viltrox Air 系列 也上镜。", content_summary="", scene_timeline=[]) | {"layer4_attribution": {}, "raw_gemini_video": {}}, ensure_ascii=False),),
    )
    out = followup.extract_for_cache_id(conn, 700)
    assert out["status"] == "scanned" and out["by_resolution"]["unresolved"] == 0 and out["by_resolution"]["family"] == 2
    rows = conn.execute("SELECT lens_key, resolution, display_name, candidate_skus FROM vkpi_kol_lens_evidence WHERE cache_id=700 ORDER BY lens_key").fetchall()
    assert [(r["lens_key"], r["resolution"], r["display_name"]) for r in rows] == [
        ("series:air", "family", "Air 系列"),
        ("series:lab", "family", "LAB 系列"),
    ]
    assert "AF-50MM-F20-AIR-FE" in json.loads(rows[0]["candidate_skus"])
    # 投影仍是 likely(仅系列不算确认出镜);统计口径:系列行不再计 unresolved
    stats = store.backfill_lens_evidence(conn, apply=False, cache_ids=[700])
    assert stats["by_resolution"] == {"sku": 0, "family": 2, "unresolved": 0}
    assert stats["series_only_rows"] == 2 and stats["unresolved_pct"] == 0.0 and stats["by_v_relevance"]["likely"] == 2
    # 目录里没有该系列任何 SKU(本夹具无 Pro 行)仍诚实 unresolved(零候选不杜撰 family)
    index = le.load_catalog_index(conn)
    assert index.series_outcome("pro")["resolution"] == "unresolved" and index.series_outcome("pro")["candidate_skus"] == []


def test_worker_hook_runs_once_and_never_breaks_cache_write(monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    import app.workers.apify_jobs_worker  # noqa: F401 — 先经正门导入,避开 gemini 簇的循环导入
    from app.workers import apify_jobs_worker_gemini as worker_gemini
    from app.workers import apify_jobs_worker_gemini_followups as hooks

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

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(worker_gemini, "_sync_search_session_job", lambda *a, **k: None)
    monkeypatch.setattr(worker_gemini, "_sync_deep_analysis_result_from_cache", lambda *a, **k: None)
    monkeypatch.setattr(worker_gemini, "_enqueue_content_fit_after_final_v1", lambda *a, **k: None)
    monkeypatch.setattr(worker_gemini, "_enqueue_account_dossier_extract_after_final_v1", lambda *a, **k: None)
    monkeypatch.setattr(worker_gemini, "_search_session_analysis_summary_from_result", lambda *a, **k: {})
    monkeypatch.setattr(worker_gemini, "_shape_gemini_result", lambda **k: {"shaped": True})
    monkeypatch.setattr(worker_gemini, "ensure_final_v1_result_cacheable", lambda raw: None)
    monkeypatch.setattr(worker_gemini, "extract_lens_evidence_after_final_v1", lambda **kw: calls.append(kw) or {"status": "scanned"})

    def _write() -> None:
        worker_gemini._write_gemini_cache(
            conn=_Conn(),
            job={"id": 9, "payload": {}},
            payload={"target_type": "video", "target_id": "701", "derive_method": "video_analysis_final_v1"},
            evidence={"id": 701},
            raw={"analyzed": True, "model": "gemini-3.6-flash", "llm_execution": {"production_authorized": True}},
            cost=0.02, cost_basis="x", preflight_cost=0.02, latency_ms=10,
            derive_method="video_analysis_final_v1",
        )

    _write()
    assert calls == [{"cache_id": 42, "derive_method": "video_analysis_final_v1", "job_id": 9}]
    assert sum(1 for sql, _p in statements if "vkpi_analysis_cache" in sql) == 1
    assert any("status='done'" in sql for sql, _p in statements)

    # 真钩子:内部 DB 作用域炸掉 → 只 warning,cache 写 + job done 照常
    monkeypatch.setattr(worker_gemini, "extract_lens_evidence_after_final_v1", hooks.extract_lens_evidence_after_final_v1)

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(hooks, "db_connection_sync_scope", _boom)
    statements.clear()
    with caplog.at_level("WARNING"):
        _write()
    assert sum(1 for sql, _p in statements if "vkpi_analysis_cache" in sql) == 1
    assert any("lens evidence extract failed (non-fatal)" in rec.getMessage() for rec in caplog.records)

    # 非 final_v1 derive(keyframe_qa / v2)不提列,也不开 DB 作用域
    assert hooks.extract_lens_evidence_after_final_v1(cache_id=1, derive_method="video_analysis_final_v1_keyframe_qa") is None
    assert hooks.extract_lens_evidence_after_final_v1(cache_id=None, derive_method="video_analysis_final_v1") is None


def test_worker_hook_extracts_through_scoped_conn(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.workers.apify_jobs_worker  # noqa: F401
    from contextlib import contextmanager

    from app.workers import apify_jobs_worker_gemini_followups as hooks

    conn = _conn()

    @contextmanager
    def _scope():
        yield None

    monkeypatch.setattr(hooks, "db_connection_sync_scope", _scope)
    monkeypatch.setattr(hooks, "get_conn", lambda: conn)
    out = hooks.extract_lens_evidence_after_final_v1(cache_id=500, derive_method="video_analysis_final_v1", job_id=1)
    assert out["status"] == "scanned" and out["mention_rows"] > 0
    assert conn.execute("SELECT COUNT(*) FROM vkpi_kol_lens_evidence_scan WHERE cache_id=500").fetchone()[0] == 1
