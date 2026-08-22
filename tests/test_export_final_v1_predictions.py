"""scripts/ops/export_final_v1_predictions.py — 导出契约 + compat SQL 口径 + 一致率汇总。"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from scripts.ops import export_final_v1_predictions as exporter  # noqa: E402
from app.domains.kol.final_v1_quality_eval import (  # noqa: E402
    PREDICTION_SCHEMA_VERSION,
    FinalV1QualityInputError,
    validate_predictions,
)

SYNTHETIC_PREDICTIONS = ROOT / "evals" / "fixtures" / "gemini_final_v1_synthetic_predictions.json"
CHECKSUM = "f" * 64


class _Row(dict):
    pass


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[_Row]:
        return [_Row(row) for row in self._rows]

    def fetchone(self) -> _Row | None:
        return _Row(self._rows[0]) if self._rows else None


class FakeCompatConn:
    """Records every SQL statement; answers by table name."""

    def __init__(self, *, cache_rows: list[dict[str, Any]], evidence_rows: list[dict[str, Any]], asset_rows: list[dict[str, Any]]) -> None:
        self.statements: list[tuple[str, tuple[Any, ...]]] = []
        self._cache_rows = cache_rows
        self._evidence_rows = evidence_rows
        self._asset_rows = asset_rows

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Cursor:
        self.statements.append((sql, tuple(params)))
        if "FROM vkpi_analysis_cache" in sql:
            return _Cursor(self._cache_rows)
        if "FROM vkpi_kol_video_evidence" in sql:
            return _Cursor(self._evidence_rows)
        if "FROM vkpi_media_cache_assets" in sql:
            return _Cursor(self._asset_rows)
        raise AssertionError(f"unexpected table in SQL: {sql}")


def _final_v1_output(status: str = "present") -> dict[str, Any]:
    fixture = json.loads(SYNTHETIC_PREDICTIONS.read_text(encoding="utf-8"))
    output = copy.deepcopy(fixture["predictions"][0]["output"])
    output["layer1_visual_content"]["brand_product_evidence"]["viltrox_status"] = status
    output["cost"] = {"recorded_cost_usd": 0.0123, "latency_ms": 41000, "usage_metadata": {"prompt_token_count": 5000, "candidates_token_count": 3000}}
    output["llm_execution"] = {"model": "gemini-3.6-flash"}
    output["raw_gemini_video"] = {"method": "gemini_local_fileapi_gemini-3.6-flash", "media_resolution": {"cache_asset_id": 77}}
    output["source"] = {"url": "https://www.youtube.com/watch?v=abc"}
    output["job_id"] = 901
    return output


def _cache_row(target_id: str, *, model: str, status: str = "ready", output: dict[str, Any] | None = None, dims: bool = True) -> dict[str, Any]:
    return {
        "cache_id": int(target_id) * 10,
        "target_id": target_id,
        "model": model,
        "status": status,
        "cost": 0.0123,
        "updated_at": "2026-08-22T00:00:00Z",
        "result": output if output is not None else _final_v1_output(),
        "dimensions_11_present": dims,
    }


def test_read_evidence_ids_dedupes_and_ignores_comments(tmp_path: Path) -> None:
    path = tmp_path / "ids.txt"
    path.write_text("# header\n295\n343 # dup below\n343\n\n290\n", encoding="utf-8")
    assert exporter.read_evidence_ids(path) == [295, 343, 290]
    bad = tmp_path / "bad.txt"
    bad.write_text("295\nabc\n", encoding="utf-8")
    with pytest.raises(ValueError):
        exporter.read_evidence_ids(bad)


def test_compat_sql_uses_question_placeholders_without_literal_percent_or_like() -> None:
    conn = FakeCompatConn(cache_rows=[], evidence_rows=[], asset_rows=[])
    exporter.fetch_cache_rows(conn, [295, 343], derive_method=exporter.FINAL_V1_DERIVE_METHOD)
    exporter.fetch_evidence_urls(conn, [295, 343])
    exporter.fetch_media_checksums(conn, [77, 0, 77])
    assert len(conn.statements) == 3
    for sql, params in conn.statements:
        assert "%" not in sql
        assert not re.search(r"\bLIKE\b", sql, flags=re.IGNORECASE)
        assert sql.count("?") == len(params)
        assert re.match(r"^\s*SELECT\b", sql)
    cache_sql, cache_params = conn.statements[0]
    assert cache_params == (exporter.DEEP_ANALYSIS_KIND, exporter.FINAL_V1_DERIVE_METHOD, "295", "343")
    assert "target_type = 'video'" in cache_sql
    assert conn.statements[2][1] == (77,)


def test_export_manifest_validates_and_reports_missing_rows() -> None:
    rows = {
        "295": _cache_row("295", model="gemini-3.6-flash"),
        "343": _cache_row("343", model="gemini-2.5-flash"),  # stale-marking missed -> model mismatch
        "290": _cache_row("290", model="gemini-3.6-flash", status="stale"),
    }
    rows["295"]["result"]["raw_gemini_video"]["media_resolution"]["cache_asset_id"] = 77
    manifest = exporter.build_predictions(
        rows=rows,
        evidence_ids=[295, 343, 290, 286],
        evidence_urls={295: "https://www.youtube.com/watch?v=abc", 286: "https://www.youtube.com/watch?v=zzz"},
        checksums={77: CHECKSUM},
        model="gemini-3.6-flash",
        dataset_id="model-upgrade-2026-08",
        prompt_version="video_analysis_final_v1@deadbeef0000",
        database_label="postgresql://***@127.0.0.1:54333/vkpi_eval_gemini_3_6_flash",
    )
    assert validate_predictions(manifest)["schema_version"] == PREDICTION_SCHEMA_VERSION
    assert manifest["claim_status"] == "descriptive_only"
    assert manifest["execution"]["provider_calls"] is False and manifest["execution"]["database_writes"] is False
    assert [item["case_id"] for item in manifest["predictions"]] == ["evidence-295"]
    record = manifest["predictions"][0]
    assert record["media_sha256"] == CHECKSUM
    assert record["meta"]["media_sha256_source"] == "media_cache_checksum"
    assert record["meta"]["cost_usd"] == pytest.approx(0.0123)
    assert record["meta"]["latency_ms"] == 41000
    assert record["meta"]["llm_dimensions_11_present"] is True
    assert "raw_gemini_video" not in record["output"]
    assert record["output"]["schema_version"] == "video_analysis_final_v1"
    missing = {item["evidence_id"]: item["reason"] for item in manifest["source"]["missing"]}
    assert missing == {343: "model_mismatch", 290: "status_stale", 286: "no_cache_row"}
    assert manifest["source"] == {**manifest["source"], "requested": 4, "exported": 1}
    assert "***@" in manifest["source"]["database"]


def test_media_sha_falls_back_to_source_url_hash_when_no_cache_checksum() -> None:
    output = _final_v1_output()
    output["raw_gemini_video"]["media_resolution"] = {}
    rows = {"295": _cache_row("295", model="gemini-3.6-flash", output=output)}
    manifest = exporter.build_predictions(
        rows=rows,
        evidence_ids=[295],
        evidence_urls={295: "ignored-because-result-source-url-wins"},
        checksums={},
        model="gemini-3.6-flash",
        dataset_id="ds",
        prompt_version="pv",
        database_label="db",
    )
    record = manifest["predictions"][0]
    expected = hashlib.sha256(b"https://www.youtube.com/watch?v=abc").hexdigest()
    assert record["media_sha256"] == expected
    assert record["meta"]["media_sha256_source"] == "source_url_sha256"
    validate_predictions(manifest)


def test_execution_model_mismatch_is_excluded_even_when_column_matches() -> None:
    output = _final_v1_output()
    output["llm_execution"]["model"] = "gemini-2.5-flash"
    rows = {"295": _cache_row("295", model="gemini-3.6-flash", output=output)}
    manifest = exporter.build_predictions(
        rows=rows, evidence_ids=[295], evidence_urls={}, checksums={}, model="gemini-3.6-flash",
        dataset_id="ds", prompt_version="pv", database_label="db",
    )
    assert manifest["predictions"] == []
    assert manifest["source"]["missing"][0]["reason"] == "model_mismatch"
    assert manifest["source"]["missing"][0]["execution_model"] == "gemini-2.5-flash"


def _manifest_from_fixture(model: str, *, mutate=None) -> dict[str, Any]:
    manifest = json.loads(SYNTHETIC_PREDICTIONS.read_text(encoding="utf-8"))
    manifest["model"] = model
    for record in manifest["predictions"]:
        record["model"] = model
        record["meta"] = {"cost_usd": 0.01 if model.endswith("lite") else 0.02, "latency_ms": 30000, "llm_dimensions_11_present": True}
    if mutate:
        mutate(manifest)
    return manifest


def test_compare_summary_reports_contract_validity_and_agreement(tmp_path: Path) -> None:
    baseline = _manifest_from_fixture("gemini-2.5-flash")

    def flip_one(manifest: dict[str, Any]) -> None:
        block = manifest["predictions"][2]["output"]["layer1_visual_content"]["brand_product_evidence"]
        block["viltrox_status"] = "absent"  # unknown -> absent without full inspection = unsupported absent
        block["inspection_complete"] = False
        manifest["predictions"][1]["output"]["layer1_visual_content"].pop("production_observations")

    candidate = _manifest_from_fixture("gemini-3.5-flash-lite", mutate=flip_one)
    base_path = tmp_path / "base.json"
    cand_path = tmp_path / "cand.json"
    out_path = tmp_path / "summary.json"
    base_path.write_text(json.dumps(baseline), encoding="utf-8")
    cand_path.write_text(json.dumps(candidate), encoding="utf-8")

    rc = exporter.main(["compare", "--baseline", str(base_path), "--candidate", str(cand_path), "--output", str(out_path)])
    assert rc == 0
    report = json.loads(out_path.read_text(encoding="utf-8"))
    assert report["claim_status"] == "descriptive_only"
    assert report["gold"] == "none_agreement_vs_baseline_mode"
    assert report["baseline"]["contract"]["validity_rate"] == 1.0
    assert report["baseline"]["brand_status_distribution"] == {"present": 1, "absent": 1, "unknown": 1}
    assert "agreement_vs_baseline" not in report["baseline"]
    cand = report["candidates"][0]
    assert cand["model"] == "gemini-3.5-flash-lite"
    assert cand["contract"]["validity_rate"] == pytest.approx(2 / 3)
    assert cand["contract"]["missing_paths"] == ["layer1_visual_content.production_observations"]
    agreement = cand["agreement_vs_baseline"]
    assert agreement["paired_cases"] == 3
    assert agreement["brand_status_agreement_rate"] == pytest.approx(2 / 3)
    assert agreement["brand_status_disagreements"] == [
        {"case_id": "synthetic-unknown-001", "baseline": "unknown", "candidate": "absent"}
    ]
    assert agreement["products_jaccard_mean"] == 1.0
    assert cand["unsupported_absent_count"] == 1
    assert cand["cost_usd"]["p50"] == pytest.approx(0.01)
    assert cand["llm_dimensions_11_rate"] == 1.0


def test_compare_rejects_invalid_predictions(tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"schema_version": "nope", "dataset_id": "x", "predictions": []}), encoding="utf-8")
    rc = exporter.main(["compare", "--baseline", str(broken), "--candidate", str(broken)])
    assert rc == 2


def test_exported_manifest_is_rejected_when_contract_broken() -> None:
    manifest = exporter.build_predictions(
        rows={}, evidence_ids=[1], evidence_urls={}, checksums={}, model="m", dataset_id="", prompt_version="pv", database_label="db",
    )
    with pytest.raises(FinalV1QualityInputError):
        validate_predictions(manifest)
