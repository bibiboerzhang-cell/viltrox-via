"""final_v1 prompt must stay pure video evidence: no project SKU / name / links.

Regression for the rollback of 8a5545ccc ("ground Gemini video analysis in SKU
context"), which leaked per-project commercial context into the global final_v1
cache row.  SKU association lives only in the independent tracking layer and in
the project-isolated content-fit layer.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest

from app.services.ai.analyzers.gemini_video_prompts import (
    _video_final_v1_dynamic_prompt,
    _video_final_v1_prompt,
)
from app.workers import apify_jobs_video_context as video_context
from app.workers.apify_jobs_worker_prep import _load_video_evidence


PROJECT_EVIDENCE: dict[str, Any] = {
    "id": 71,
    "kol_pool_id": 88,
    "creator_handle": "lensnerd",
    "creator_name": "Lens Nerd",
    "project_id": 9,
    "project_name": "Launch Q3 Secret",
    "product_sku": "AF-35-LAB",
    "product_name": "Viltrox AF 35mm LAB",
    "assignment_id": 12,
    "linked_products": [
        {"sku": "AF-27-PRO", "model_name": "AF 27mm F1.2 Pro", "relation_type": "manual", "confidence": 1},
    ],
    "view_count": 1200,
    "like_count": 80,
}

PROJECT_MARKERS = ("AF-35-LAB", "AF-27-PRO", "Launch Q3 Secret", "Viltrox AF 35mm LAB", "candidate_products", "linked_products")


def test_final_context_contains_no_project_scope() -> None:
    context = video_context._video_final_context(PROJECT_EVIDENCE)
    product_context = context["product_context"]

    assert video_context.final_v1_context_is_project_free(context)
    for key in ("project_id", "project_name", "product_sku", "product_name", "candidate_products", "linked_products"):
        assert key not in product_context
    assert product_context["project_scope"] == "none"
    assert product_context["brand_lexicon_is_evidence"] is False
    assert product_context["brand_lexicon"] == list(video_context.FINAL_V1_BRAND_LEXICON)
    assert product_context["creator_handle"] == "lensnerd"
    assert context["prompt_contract"] == video_context.FINAL_V1_PROMPT_CONTRACT
    serialized = json.dumps(context, ensure_ascii=False, default=str)
    for marker in PROJECT_MARKERS:
        assert marker not in serialized


def test_brand_lexicon_is_static_and_project_agnostic() -> None:
    # Same lexicon regardless of which project / SKU the evidence belongs to.
    a = video_context._video_final_context(PROJECT_EVIDENCE)["product_context"]["brand_lexicon"]
    b = video_context._video_final_context({**PROJECT_EVIDENCE, "product_sku": "AF-85-PRO", "project_id": 2})[
        "product_context"
    ]["brand_lexicon"]
    assert a == b
    joined = " ".join(a)
    assert "Viltrox" in joined
    for marker in ("AF-35-LAB", "AF-85-PRO", "AF-27-PRO"):
        assert marker not in joined


@pytest.mark.parametrize("builder", [_video_final_v1_prompt, _video_final_v1_dynamic_prompt])
def test_final_v1_prompt_never_carries_project_fields(builder) -> None:
    context = video_context._video_final_context(PROJECT_EVIDENCE)
    prompt = builder(
        title="Untitled lens test",
        profile_ctx="",
        subtitle_ctx="",
        subtitle_used=False,
        performance_context=context,
    )
    for marker in PROJECT_MARKERS:
        assert marker not in prompt
    assert "association_is_evidence" not in prompt
    assert "brand_lexicon" in prompt


def test_project_scoped_key_guard_catches_nested_leaks() -> None:
    leaked = video_context._project_scoped_keys(
        {"product_context": {"candidate_products": [{"product_sku": "X"}]}, "ok": 1}
    )
    assert leaked == {"product_context.candidate_products", "product_context.candidate_products[0].product_sku"}
    assert video_context.final_v1_context_is_project_free({"product_context": {"kol_pool_id": 1}})


class _Cursor:
    def __init__(self) -> None:
        self.sql = ""

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, _params=()) -> None:
        self.sql = " ".join(str(sql).split())

    def fetchone(self):
        return {"id": 71, "content_url": "https://www.youtube.com/watch?v=abcdefghijk"}


class _Conn:
    def __init__(self) -> None:
        self.value = _Cursor()

    def cursor(self, **_kwargs):
        return self.value


def test_worker_evidence_read_does_not_join_product_links_or_project_sku() -> None:
    conn = _Conn()
    _load_video_evidence(conn, "71")
    assert "vkpi_kol_video_product_links" not in conn.value.sql
    assert "linked_products" not in conn.value.sql
    assert "p.product_sku" not in conn.value.sql


def test_shaped_final_v1_result_records_pure_prompt_contract() -> None:
    from app.workers import apify_jobs_worker_gemini as gemini

    shaped = gemini._shape_gemini_result(
        job={"id": 1},
        evidence={
            **PROJECT_EVIDENCE,
            "content_url": "https://youtu.be/x",
            "title": "Untitled lens test",
        },
        raw={"analyzed": True, "model": gemini.WORKER_GEMINI_MODEL, "video_analysis_final_v1": {}},
        cost=0.0,
        cost_basis="test",
        preflight_cost=0.0,
        latency_ms=1,
        derive_method="video_analysis_final_v1",
    )
    assert shaped["provenance"]["prompt_contract"] == video_context.FINAL_V1_PROMPT_CONTRACT
    assert not ({"project_id", "project_name", "product_name"} & shaped["source"].keys())
    serialized = json.dumps(shaped, ensure_ascii=False, default=str)
    for marker in PROJECT_MARKERS:
        assert marker not in serialized


# ── cache clean-up script ──────────────────────────────────────────────


def _load_script():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "ops" / "mark_stale_final_v1_sku_context_cache.py"
    spec = importlib.util.spec_from_file_location("mark_stale_final_v1_sku_context_cache", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_cleanup_classifies_rows_by_prompt_contract_and_idempotent_marker() -> None:
    script = _load_script()
    pure = {"id": 1, "status": "ready", "result_text": json.dumps({"provenance": {"prompt_contract": script.FINAL_V1_PROMPT_CONTRACT}})}
    polluted = {"id": 2, "status": "ready", "result_text": json.dumps({"provenance": {"provider": "gemini"}})}
    legacy_no_provenance = {"id": 3, "status": "ready", "result_text": "{}"}
    already_marked = {"id": 4, "status": "ready", "result_text": json.dumps({"stale_reason": script.STALE_REASON})}
    stale = {"id": 5, "status": "stale", "result_text": "{}"}
    found, clean = script.classify([pure, polluted, legacy_no_provenance, already_marked, stale])
    assert [row["id"] for row in found] == [2, 3]
    assert clean == 3


class _ScriptCursor:
    description = [("id",), ("target_id",), ("status",), ("updated_at",), ("result_text",)]

    def __init__(self, rows: list[tuple[Any, ...]], log: list[tuple[str, tuple[Any, ...]]]) -> None:
        self.rows = rows
        self.log = log
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        compact = " ".join(str(sql).split())
        self.log.append((compact, params))
        if compact.startswith("UPDATE"):
            self.rowcount = 1

    def fetchall(self):
        return list(self.rows)


class _ScriptConn:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.log: list[tuple[str, tuple[Any, ...]]] = []
        self.rows = rows
        self.commits = 0

    def cursor(self):
        return _ScriptCursor(self.rows, self.log)

    def commit(self) -> None:
        self.commits += 1


def test_cleanup_dry_run_never_writes_and_apply_marks_only_polluted() -> None:
    script = _load_script()
    since = datetime(2026, 8, 21, 22, 38, 35, tzinfo=timezone.utc)
    until = datetime(2026, 8, 22, tzinfo=timezone.utc)
    rows = [
        (10, "701", "ready", since, json.dumps({"provenance": {"prompt_contract": script.FINAL_V1_PROMPT_CONTRACT}})),
        (11, "702", "ready", since, json.dumps({"provenance": {"provider": "gemini"}})),
    ]
    conn = _ScriptConn(rows)
    summary = script.run(conn, since=since, until=until, apply=False)
    assert summary["mode"] == "dry_run"
    assert summary["polluted"] == 1 and summary["polluted_ids"] == [11]
    assert summary["marked_stale"] == 0
    assert conn.commits == 0
    assert all(not sql.startswith("UPDATE") for sql, _ in conn.log)
    select_sql, select_params = conn.log[0]
    assert "status = 'ready'" in select_sql and "derive_method = %s" in select_sql
    assert select_params[0] == "video_analysis_final_v1"

    conn = _ScriptConn(rows)
    summary = script.run(conn, since=since, until=until, apply=True)
    assert summary["marked_stale"] == 1
    assert conn.commits == 1
    updates = [(sql, params) for sql, params in conn.log if sql.startswith("UPDATE")]
    assert len(updates) == 1
    assert updates[0][1][1] == 11
    assert json.loads(updates[0][1][0])["stale_reason"] == script.STALE_REASON
    assert "SET status = 'stale'" in updates[0][0]
