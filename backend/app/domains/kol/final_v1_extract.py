"""Extract final_v1 cache rows into independent KOL deep-analysis results.

This module only reads vkpi_analysis_cache/vkpi_kol_video_evidence and writes
vkpi_kol_llm_deep_analysis_results. It must never update vkpi_kol_pool scoring
fields or call any LLM/provider.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


FINAL_DERIVE_METHOD = "video_analysis_final_v1"
QA_DERIVE_METHOD = "video_analysis_final_v1_keyframe_qa"
ANALYSIS_KIND = "video_final_v1"
METHOD = "video_final_v1_cache_extract_v1"
PROVIDER = "gemini"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "pass", "passed"}


def _decimal_or_none(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        parsed = Decimal(str(value))
    except Exception:
        return None
    if parsed.is_nan():
        return None
    if parsed < Decimal("0"):
        return Decimal("0")
    if parsed > Decimal("100"):
        return Decimal("100")
    return parsed.quantize(Decimal("0.001"))


def _score_from_value(value: Any) -> tuple[Decimal | None, Decimal | None]:
    if isinstance(value, dict):
        score = _decimal_or_none(value.get("score"))
        confidence = _decimal_or_none(value.get("confidence"))
        if confidence is not None:
            confidence = min(confidence, Decimal("1.000"))
        return score, confidence
    return _decimal_or_none(value), None


def _json_ready(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    return value


def _clean_text(value: Any, limit: int = 1200) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text[:limit]


def _final_payload(result: Any) -> dict[str, Any]:
    root = _as_dict(result)
    nested = _as_dict(root.get(FINAL_DERIVE_METHOD))
    if _as_dict(nested.get("layer1_visual_content")) or _as_dict(nested.get("layer6_flags_and_scores")):
        return nested
    return root


def _qa_payload(result: Any) -> dict[str, Any]:
    root = _as_dict(result)
    direct = _as_dict(root.get("keyframe_qa"))
    if direct:
        return direct
    nested = _as_dict(root.get("final_v1_keyframe_qa"))
    if nested:
        return nested
    wrapped = _as_dict(_as_dict(root.get(QA_DERIVE_METHOD)).get("final_v1_keyframe_qa"))
    if wrapped:
        return wrapped
    if any(key in root for key in ("qa_pass", "checks", "issues", "score_correction")):
        return root
    return {}


def _normalised_scores(layer6: dict[str, Any]) -> dict[str, Any]:
    scores = _as_dict(layer6.get("scores"))
    output: dict[str, Any] = {}
    for key, value in scores.items():
        score, confidence = _score_from_value(value)
        entry: dict[str, Any] = {}
        if score is not None:
            entry["score"] = score
        if confidence is not None:
            entry["confidence"] = confidence
        if isinstance(value, dict):
            for meta_key in ("rationale", "evidence", "reason"):
                if value.get(meta_key) is not None:
                    entry[meta_key] = value.get(meta_key)
        if entry:
            output[str(key)] = entry
    return output


def _marketing_score(layer6: dict[str, Any]) -> tuple[Decimal | None, Decimal | None, str]:
    scores = _as_dict(layer6.get("scores"))
    score, confidence = _score_from_value(scores.get("marketing_value_score"))
    if score is not None:
        return score, confidence, "layer6_flags_and_scores.scores.marketing_value_score"
    score, confidence = _score_from_value(layer6.get("marketing_value_score"))
    if score is not None:
        return score, confidence, "layer6_flags_and_scores.marketing_value_score"
    return None, None, ""


def _apply_qa_score(*, base_score: Decimal, qa_payload: dict[str, Any]) -> tuple[Decimal, bool, dict[str, Any]]:
    correction = _as_dict(qa_payload.get("score_correction"))
    corrected = _decimal_or_none(correction.get("corrected_marketing_value_score"))
    if _truthy(correction.get("apply")) and corrected is not None:
        return corrected, True, correction
    return base_score, False, correction


def _build_dimensions(
    *,
    row: dict[str, Any],
    payload: dict[str, Any],
    qa_payload: dict[str, Any],
    base_score: Decimal,
    final_score: Decimal,
    score_path: str,
    qa_adjusted: bool,
    qa_correction: dict[str, Any],
    confidence: Decimal | None,
) -> dict[str, Any]:
    layer1 = _as_dict(payload.get("layer1_visual_content"))
    layer5 = _as_dict(payload.get("layer5_recommendations"))
    layer6 = _as_dict(payload.get("layer6_flags_and_scores"))
    title = _clean_text(row.get("title") or row.get("video_title") or row.get("content_url"), 500)
    dimensions: dict[str, Any] = {
        "schema_version": "kol_llm_deep_analysis_from_final_v1_v1",
        "source": {
            "source_cache_id": row["final_cache_id"],
            "source_evidence_id": row["evidence_id"],
            "qa_source_cache_id": row.get("qa_cache_id"),
            "target_id": str(row["evidence_id"]),
            "derive_method": FINAL_DERIVE_METHOD,
            "qa_derive_method": QA_DERIVE_METHOD if qa_payload else None,
            "source_url": row.get("content_url"),
            "title": title,
            "kol_pool_id": row["kol_pool_id"],
            "handle": row.get("handle"),
            "display_name": row.get("display_name"),
            "platform": row.get("platform"),
            "final_model": row.get("final_model"),
            "qa_model": row.get("qa_model"),
            "final_cost": row.get("final_cost"),
            "qa_cost": row.get("qa_cost"),
        },
        "qa_source_cache_id": row.get("qa_cache_id"),
        "llm_v6_fit": {
            "score": final_score,
            "base_marketing_value_score": base_score,
            "score_path": score_path,
            "qa_adjusted": qa_adjusted,
            "confidence": confidence,
            "note": "Independent LLM/video deep-fit signal; not viltrox_fit_score.",
        },
        "scores": _normalised_scores(layer6),
        "layer1_summary": {
            "content_summary": layer1.get("content_summary"),
            "scene_timeline": _as_list(layer1.get("scene_timeline"))[:12],
            "product_presence": layer1.get("product_presence"),
            "brand_exposure": layer1.get("brand_exposure"),
            "competitor_presence": layer1.get("competitor_presence"),
            "production_observations": layer1.get("production_observations"),
        },
        "recommendations": {
            "cooperation_recommendation": layer5.get("cooperation_recommendation"),
            "buyout_or_license_recommendation": layer5.get("buyout_or_license_recommendation"),
            "next_brief_adjustments": layer5.get("next_brief_adjustments"),
            "must_request_from_kol": layer5.get("must_request_from_kol"),
            "budget_action": layer5.get("budget_action"),
            "why": layer5.get("why"),
        },
        "risk": {
            "risk_flags": layer6.get("risk_flags"),
            "final_verdict": layer6.get("final_verdict"),
            "key_hook": layer6.get("key_hook"),
        },
    }
    if qa_payload:
        dimensions["qa"] = {
            "qa_source_cache_id": row.get("qa_cache_id"),
            "qa_pass": qa_payload.get("qa_pass"),
            "confidence": qa_payload.get("confidence"),
            "summary": qa_payload.get("summary"),
            "checks": qa_payload.get("checks"),
            "issues": qa_payload.get("issues"),
            "score_correction": qa_correction,
            "recommended_review_action": qa_payload.get("recommended_review_action"),
        }
    return _json_ready(dimensions)


def _fetch_cache_row(conn: psycopg.Connection[Any], final_cache_id: int) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            WITH qa AS (
              SELECT q.*,
                     ROW_NUMBER() OVER (PARTITION BY q.target_id ORDER BY q.updated_at DESC, q.id DESC) AS rn
              FROM vkpi_analysis_cache q
              WHERE q.target_type = 'video'
                AND q.derive_method = %s
                AND q.status = 'ready'
            )
            SELECT c.id AS final_cache_id,
                   c.result AS final_result,
                   c.model AS final_model,
                   c.cost AS final_cost,
                   e.id AS evidence_id,
                   e.kol_pool_id,
                   e.content_url,
                   e.title,
                   e.video_title,
                   p.handle,
                   p.display_name,
                   p.platform,
                   qa.id AS qa_cache_id,
                   qa.result AS qa_result,
                   qa.model AS qa_model,
                   qa.cost AS qa_cost
            FROM vkpi_analysis_cache c
            JOIN vkpi_kol_video_evidence e
              ON e.id::text = c.target_id
             AND c.target_type = 'video'
            JOIN vkpi_kol_pool p
              ON p.id = e.kol_pool_id
            LEFT JOIN qa
              ON qa.target_id = c.target_id
             AND qa.rn = 1
            WHERE c.id = %s
              AND c.derive_method = %s
              AND c.status = 'ready'
            LIMIT 1
            """,
            (QA_DERIVE_METHOD, int(final_cache_id), FINAL_DERIVE_METHOD),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def _prepare(row: dict[str, Any]) -> dict[str, Any]:
    source_url = _clean_text(row.get("content_url"), 2000)
    if not source_url:
        return {"status": "skipped", "reason": "missing_source_url", "final_cache_id": row.get("final_cache_id")}
    payload = _final_payload(row.get("final_result"))
    layer6 = _as_dict(payload.get("layer6_flags_and_scores"))
    base_score, confidence, score_path = _marketing_score(layer6)
    if base_score is None:
        return {"status": "skipped", "reason": "missing_marketing_value_score", "final_cache_id": row.get("final_cache_id")}
    qa = _qa_payload(row.get("qa_result"))
    final_score, qa_adjusted, qa_correction = _apply_qa_score(base_score=base_score, qa_payload=qa)
    dimensions = _build_dimensions(
        row=row,
        payload=payload,
        qa_payload=qa,
        base_score=base_score,
        final_score=final_score,
        score_path=score_path,
        qa_adjusted=qa_adjusted,
        qa_correction=qa_correction,
        confidence=confidence,
    )
    return {
        "status": "ready",
        "kol_pool_id": int(row["kol_pool_id"]),
        "source_url": source_url,
        "source_evidence_id": int(row["evidence_id"]),
        "analysis_kind": ANALYSIS_KIND,
        "llm_v6_fit": final_score,
        "llm_dimensions_11": dimensions,
        "method": METHOD,
        "provider": PROVIDER,
        "confidence": confidence,
        "source_cache_id": int(row["final_cache_id"]),
    }


def _existing_result_ids(conn: psycopg.Connection[Any], source_cache_id: int) -> list[int]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id
            FROM vkpi_kol_llm_deep_analysis_results
            WHERE analysis_kind=%s
              AND source_cache_id=%s
            ORDER BY id
            """,
            (ANALYSIS_KIND, int(source_cache_id)),
        )
        return [int(row["id"]) for row in cur.fetchall()]


def _fit_snapshot(conn: psycopg.Connection[Any], kol_pool_id: int) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT viltrox_fit_score, viltrox_fit_reason FROM vkpi_kol_pool WHERE id=%s",
            (int(kol_pool_id),),
        )
        row = cur.fetchone() or {}
    return dict(row)


def upsert_deep_analysis_from_final_v1_cache(conn: psycopg.Connection[Any], final_cache_id: int) -> dict[str, Any]:
    """Upsert one independent deep-analysis row from a ready final_v1 cache row."""

    row = _fetch_cache_row(conn, int(final_cache_id))
    if not row:
        return {"status": "skipped", "reason": "cache_not_found", "source_cache_id": int(final_cache_id)}
    prepared = _prepare(row)
    if prepared.get("status") != "ready":
        return prepared
    existing_ids = _existing_result_ids(conn, int(final_cache_id))
    if len(existing_ids) > 1:
        return {
            "status": "skipped",
            "reason": "duplicate_existing_source_cache_id",
            "source_cache_id": int(final_cache_id),
            "existing_ids": existing_ids,
        }

    kol_pool_id = int(prepared["kol_pool_id"])
    before = _fit_snapshot(conn, kol_pool_id)
    with conn.transaction():
        with conn.cursor(row_factory=dict_row) as cur:
            params = {
                "kol_pool_id": kol_pool_id,
                "source_url": prepared["source_url"],
                "source_evidence_id": prepared["source_evidence_id"],
                "analysis_kind": ANALYSIS_KIND,
                "llm_v6_fit": prepared["llm_v6_fit"],
                "llm_dimensions_11": Jsonb(prepared["llm_dimensions_11"]),
                "method": METHOD,
                "provider": PROVIDER,
                "confidence": prepared["confidence"],
                "source_cache_id": int(final_cache_id),
                "status": "ready",
            }
            if existing_ids:
                cur.execute(
                    """
                    UPDATE vkpi_kol_llm_deep_analysis_results
                    SET kol_pool_id=%(kol_pool_id)s,
                        source_url=%(source_url)s,
                        source_evidence_id=%(source_evidence_id)s,
                        analysis_kind=%(analysis_kind)s,
                        llm_v6_fit=%(llm_v6_fit)s,
                        llm_dimensions_11=%(llm_dimensions_11)s,
                        method=%(method)s,
                        provider=%(provider)s,
                        confidence=%(confidence)s,
                        source_cache_id=%(source_cache_id)s,
                        status=%(status)s
                    WHERE id=%(id)s
                    RETURNING id
                    """,
                    {**params, "id": existing_ids[0]},
                )
                action = "updated"
            else:
                cur.execute(
                    """
                    INSERT INTO vkpi_kol_llm_deep_analysis_results (
                        kol_pool_id,
                        source_url,
                        source_evidence_id,
                        analysis_kind,
                        llm_v6_fit,
                        llm_dimensions_11,
                        method,
                        provider,
                        confidence,
                        source_cache_id,
                        status
                    ) VALUES (
                        %(kol_pool_id)s,
                        %(source_url)s,
                        %(source_evidence_id)s,
                        %(analysis_kind)s,
                        %(llm_v6_fit)s,
                        %(llm_dimensions_11)s,
                        %(method)s,
                        %(provider)s,
                        %(confidence)s,
                        %(source_cache_id)s,
                        %(status)s
                    )
                    RETURNING id
                    """,
                    params,
                )
                action = "inserted"
            result_row = cur.fetchone() or {}
            after = _fit_snapshot(conn, kol_pool_id)
            if before != after:
                raise RuntimeError(f"viltrox_fit_score_changed_ids={[kol_pool_id]}; rolled back")

    return {
        "status": "ready",
        "action": action,
        "deep_result_id": int(result_row["id"]) if result_row.get("id") is not None else None,
        "source_cache_id": int(final_cache_id),
        "source_evidence_id": prepared["source_evidence_id"],
        "kol_pool_id": kol_pool_id,
        "llm_v6_fit": float(prepared["llm_v6_fit"]),
        "confidence": float(prepared["confidence"]) if prepared["confidence"] is not None else None,
        "viltrox_fit_score_changed_ids": [],
    }
