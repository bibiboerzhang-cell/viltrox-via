from __future__ import annotations

import importlib.util
import sys
from contextlib import nullcontext
from pathlib import Path
from typing import Any


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ops" / "clean_final_v1_source_project_metadata.py"
SPEC = importlib.util.spec_from_file_location("clean_final_v1_source_project_metadata", SCRIPT)
assert SPEC and SPEC.loader
cleanup = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = cleanup
SPEC.loader.exec_module(cleanup)


def _cache_result() -> dict[str, Any]:
    return {
        "source": {
            "url": "https://example.test/video/1",
            "project_id": 7,
            "project_name": "Secret launch",
            "product_name": "Secret lens",
            "creator_handle": "creator",
        },
        "provenance": {"prompt_contract": "final_v1_pure_video_evidence_v2"},
        "raw_gemini_video": {"project_id": "must_remain_outside_source"},
        "layer1_visual_content": {"content_summary": "Observed video evidence."},
    }


class _Cursor:
    def __init__(self, conn: "_Connection") -> None:
        self.conn = conn
        self.rows: list[dict[str, Any]] = []
        self.rowcount = 0

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        compact = " ".join(str(sql).split())
        self.conn.log.append((compact, params))
        self.rowcount = 0
        if compact.startswith("SELECT id, target_id"):
            ids = params[1]
            source = self.conn.row["result"].get("source")
            has_key = isinstance(source, dict) and any(key in source for key in cleanup.SOURCE_KEYS)
            selected = ids is None or self.conn.row["id"] in ids
            self.rows = [dict(self.conn.row)] if has_key and selected else []
        elif compact.startswith("SELECT DISTINCT e.kol_pool_id"):
            self.rows = [{"kol_pool_id": 9, "viltrox_fit_score": 95}]
        elif compact.startswith("UPDATE vkpi_analysis_cache"):
            cleaned, cache_id, derive_method, original = params
            if (
                int(cache_id) == int(self.conn.row["id"])
                and derive_method == cleanup.DERIVE_METHOD
                and original.obj == self.conn.row["result"]
            ):
                self.conn.row["result"] = cleaned.obj
                self.rowcount = 1
            self.rows = []
        else:  # pragma: no cover - catches unexpected SQL in this safety test
            raise AssertionError(compact)

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self.rows)


class _Connection:
    def __init__(self) -> None:
        self.row = {
            "id": 11,
            "target_id": "101",
            "status": "ready",
            "result": _cache_result(),
        }
        self.log: list[tuple[str, tuple[Any, ...]]] = []

    def cursor(self, **_kwargs: Any) -> _Cursor:
        return _Cursor(self)

    def transaction(self):
        return nullcontext()


def test_clean_result_removes_only_three_source_keys_without_mutating_input() -> None:
    original = _cache_result()

    cleaned, removed = cleanup.clean_result(original)

    assert removed == ["project_id", "project_name", "product_name"]
    assert cleaned["source"] == {
        "url": "https://example.test/video/1",
        "creator_handle": "creator",
    }
    assert cleaned["raw_gemini_video"]["project_id"] == "must_remain_outside_source"
    assert original["source"]["project_id"] == 7


def test_default_run_is_dry_and_never_updates() -> None:
    conn = _Connection()

    summary = cleanup.run(conn)

    assert summary == {
        "mode": "dry_run",
        "candidates": 1,
        "matching_rows": 1,
        "batch_limit": cleanup.BATCH_LIMIT,
        "has_more": False,
        "would_clean": 1,
        "candidate_cache_ids_sample": [11],
        "candidate_cache_ids_truncated": False,
        "removed_keys": ["project_id", "project_name", "product_name"],
        "cleaned": 0,
        "concurrent_skipped": 0,
        "viltrox_fit_score_changed_ids": [],
        "provider_calls_performed": False,
    }
    assert conn.row["result"] == _cache_result()
    assert all(not sql.startswith("UPDATE") for sql, _params in conn.log)


def test_commit_is_bounded_idempotent_and_never_updates_fit_or_freshness() -> None:
    conn = _Connection()

    first = cleanup.run(conn, cache_ids=[11], commit=True)
    second = cleanup.run(conn, cache_ids=[11], commit=True)

    assert first["mode"] == "commit"
    assert first["cleaned"] == 1
    assert first["has_more"] is False
    assert first["concurrent_skipped"] == 0
    assert first["viltrox_fit_score_changed_ids"] == []
    assert second["candidates"] == 0
    assert second["cleaned"] == 0
    assert conn.row["result"]["source"] == {
        "url": "https://example.test/video/1",
        "creator_handle": "creator",
    }
    updates = [sql for sql, _params in conn.log if sql.startswith("UPDATE")]
    assert len(updates) == 1
    assert all("UPDATE vkpi_analysis_cache" in sql for sql in updates)
    assert all("vkpi_kol_pool" not in sql for sql in updates)
    assert all("updated_at" not in sql for sql in updates)


def test_cache_id_parser_is_positive_and_deduplicated() -> None:
    assert cleanup._parse_cache_ids(["3, 1", "3", "0"]) == [1, 3]
