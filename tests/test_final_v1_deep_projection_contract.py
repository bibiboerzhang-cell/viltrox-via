"""final_v1 ready-cache and deep-result score-availability contract."""
from __future__ import annotations

import importlib.util
import sys
from contextlib import nullcontext
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.domains.kol import final_v1_extract, quality_compliance, risk_index, video_similarity
from app.services.ai.analyzers.gemini_video_results import (
    ensure_final_v1_result_cacheable,
    validate_final_v1_result,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "backfill_kol_llm_deep_analysis_results.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("backfill_kol_llm_deep_analysis_results_contract", SCRIPT)
assert SPEC and SPEC.loader
backfill = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = backfill
SPEC.loader.exec_module(backfill)


def _payload(*, score: Any = None) -> dict[str, Any]:
    scores: dict[str, Any] = {
        "content_quality_score": {"score": 81, "confidence": 0.8},
        "product_proof_score": {"score": 64, "confidence": 0.7},
    }
    if score is not None:
        scores["marketing_value_score"] = score
    return {
        "layer1_visual_content": {
            "content_summary": "A creator demonstrates autofocus performance.",
            "scene_timeline": [
                {"timestamp": "00:08", "what": "Autofocus tracking demonstration."},
            ],
            "evidence": {"timestamps": ["00:08 autofocus demonstration"]},
        },
        "layer5_recommendations": {"why": "Useful product-demo evidence."},
        "layer6_flags_and_scores": {
            "scores": scores,
            "final_verdict": "Analysis complete; scalar availability is reported separately.",
        },
    }


def _ready_cache(*, score: Any = None, qa_result: Any = None) -> dict[str, Any]:
    result = {
        "analyzed": True,
        "status": "completed",
        "model": "gemini-test",
        "method": "gemini_fileapi_gemini-test",
        "video_analysis_final_v1": _payload(score=score),
    }
    return {
        "final_cache_id": 501,
        "final_result": result,
        "final_model": "gemini-test",
        "final_cost": Decimal("0.01"),
        "evidence_id": 701,
        "kol_pool_id": 41,
        "content_url": "https://example.test/video/701",
        "title": "Autofocus demo",
        "video_title": "Autofocus demo",
        "handle": "creator",
        "display_name": "Creator",
        "platform": "youtube",
        "qa_cache_id": 601 if qa_result else None,
        "qa_result": qa_result,
        "qa_model": "gemini-qa" if qa_result else None,
        "qa_cost": Decimal("0.001") if qa_result else None,
    }


def test_ready_validator_and_projector_agree_when_marketing_score_is_missing() -> None:
    row = _ready_cache()
    raw = row["final_result"]

    assert validate_final_v1_result(raw, allow_legacy_status=False) == []
    ensure_final_v1_result_cacheable(raw)
    projected = final_v1_extract.prepare_deep_analysis_projection(row)

    assert projected["status"] == "ready"
    assert projected["llm_v6_fit"] is None
    assert projected["confidence"] is None
    assert projected["score_status"] == "unknown"
    assert projected["score_missing_reason"] == final_v1_extract.MISSING_SCORE_REASON
    dimensions = projected["llm_dimensions_11"]
    assert dimensions["schema_version"] == final_v1_extract.DIMENSIONS_SCHEMA_VERSION
    assert dimensions["llm_v6_fit"] == {
        "status": "unknown",
        "score": None,
        "base_marketing_value_score": None,
        "score_path": None,
        "qa_adjusted": False,
        "confidence": None,
        "reason": final_v1_extract.MISSING_SCORE_REASON,
        "note": "Independent LLM/video deep-fit signal; not viltrox_fit_score.",
    }
    assert "marketing_value_score" not in dimensions["scores"]
    assert dimensions["scores"]["content_quality_score"]["score"] == 81.0


def test_qa_correction_without_a_base_score_cannot_invent_one() -> None:
    row = _ready_cache(
        qa_result={
            "qa_pass": True,
            "score_correction": {
                "apply": True,
                "corrected_marketing_value_score": 77,
            },
        }
    )

    projected = final_v1_extract.prepare_deep_analysis_projection(row)

    assert projected["llm_v6_fit"] is None
    assert projected["score_status"] == "unknown"
    assert projected["llm_dimensions_11"]["llm_v6_fit"]["qa_adjusted"] is False
    assert projected["llm_dimensions_11"]["qa"]["score_correction"]["apply"] is True


def test_real_score_and_qa_correction_remain_numeric_and_attributed() -> None:
    row = _ready_cache(
        score={"score": 72, "confidence": 0.91, "rationale": "Timestamp-backed."},
        qa_result={
            "qa_pass": True,
            "score_correction": {
                "apply": True,
                "corrected_marketing_value_score": 68,
            },
        },
    )

    projected = final_v1_extract.prepare_deep_analysis_projection(row)

    assert projected["llm_v6_fit"] == Decimal("68.000")
    assert projected["confidence"] == Decimal("0.910")
    assert projected["score_status"] == "available"
    fit = projected["llm_dimensions_11"]["llm_v6_fit"]
    assert fit["status"] == "available"
    assert fit["base_marketing_value_score"] == 72.0
    assert fit["score"] == 68.0
    assert fit["qa_adjusted"] is True
    assert fit["reason"] is None


def test_legacy_top_level_marketing_score_is_preserved_in_normalized_scores() -> None:
    row = _ready_cache()
    layer6 = row["final_result"]["video_analysis_final_v1"]["layer6_flags_and_scores"]
    layer6["marketing_value_score"] = {"score": 55, "confidence": 0.5}

    projected = final_v1_extract.prepare_deep_analysis_projection(row)

    assert projected["llm_v6_fit"] == Decimal("55.000")
    assert projected["llm_dimensions_11"]["scores"]["marketing_value_score"] == {
        "score": 55.0,
        "confidence": 0.5,
    }


def test_valid_legacy_score_replaces_invalid_nested_placeholder_consistently() -> None:
    row = _ready_cache(score={"score": None, "reason": "legacy placeholder"})
    layer6 = row["final_result"]["video_analysis_final_v1"]["layer6_flags_and_scores"]
    layer6["marketing_value_score"] = {"score": 55, "confidence": 0.5}

    projected = final_v1_extract.prepare_deep_analysis_projection(row)

    assert projected["llm_v6_fit"] == Decimal("55.000")
    assert projected["score_status"] == "available"
    assert projected["llm_dimensions_11"]["scores"]["marketing_value_score"] == {
        "score": 55.0,
        "confidence": 0.5,
    }


def test_score_consumers_keep_missing_marketing_dimension_missing_not_zero() -> None:
    dimensions = final_v1_extract.prepare_deep_analysis_projection(_ready_cache())["llm_dimensions_11"]

    assert risk_index._depth_component(dimensions) == 19.0
    assert risk_index._asset_reuse_component(dimensions) is None
    quality_scores = quality_compliance._video_scores(dimensions)
    assert quality_scores["content_quality_score"] == 81.0
    assert "marketing_value_score" not in quality_scores
    vector = video_similarity._numeric_vector(dimensions)
    assert vector["content_quality_score"] == 81.0
    assert "marketing_value_score" not in vector
    assert video_similarity.FIT_KEY not in vector


def test_backfill_uses_the_same_projection_and_counts_unknown_separately(monkeypatch: Any) -> None:
    prepared, skipped = backfill.build_plan([_ready_cache()], {})

    assert skipped == []
    assert len(prepared) == 1
    assert prepared[0].llm_v6_fit is None
    assert prepared[0].llm_dimensions_11["llm_v6_fit"]["status"] == "unknown"
    assert backfill._bucket(prepared[0].llm_v6_fit) == "unknown"
    output: list[str] = []
    monkeypatch.setattr(backfill, "out", lambda value: output.append(str(value)))
    backfill.print_report([_ready_cache()], prepared, skipped)
    assert output[0] == "mode: dry-run (no writes)"
    assert "score_unknown: 1" in output
    assert "  unknown: 1" in output

    output.clear()
    backfill.print_report([_ready_cache()], prepared, skipped, commit=True)
    assert output[0] == "mode: commit (writes enabled)"


class _Cursor:
    def __init__(self) -> None:
        self.params: dict[str, Any] = {}

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def execute(self, _sql: str, params: dict[str, Any]) -> None:
        self.params = params

    def fetchone(self) -> dict[str, int]:
        return {"id": 901}


class _Connection:
    def __init__(self) -> None:
        self.last_cursor = _Cursor()

    def transaction(self) -> Any:
        return nullcontext()

    def cursor(self, **_kwargs: Any) -> _Cursor:
        self.last_cursor = _Cursor()
        return self.last_cursor


def test_upsert_writes_sql_null_and_returns_unknown_without_touching_fit(monkeypatch: Any) -> None:
    row = _ready_cache()
    conn = _Connection()
    monkeypatch.setattr(final_v1_extract, "_fetch_cache_row", lambda *_args: row)
    monkeypatch.setattr(final_v1_extract, "_existing_result_ids", lambda *_args: [])
    monkeypatch.setattr(final_v1_extract, "_fit_snapshot", lambda *_args: {"viltrox_fit_score": 95})

    result = final_v1_extract.upsert_deep_analysis_from_final_v1_cache(conn, 501)

    assert conn.last_cursor.params["llm_v6_fit"] is None
    assert conn.last_cursor.params["confidence"] is None
    assert result["status"] == "ready"
    assert result["llm_v6_fit"] is None
    assert result["score_status"] == "unknown"
    assert result["viltrox_fit_score_changed_ids"] == []
