#!/usr/bin/env python3
"""Extract final_v1 video cache into independent KOL LLM deep results.

Dry-run is the default. Use --commit to write.

This script only reads vkpi_analysis_cache/vkpi_kol_video_evidence and writes
vkpi_kol_llm_deep_analysis_results when explicitly committed. It never calls
LLMs, never enqueues worker jobs, and never updates vkpi_kol_pool fit fields.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - local dependency guard.
    load_dotenv = None  # type: ignore[assignment]

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402


FINAL_DERIVE_METHOD = "video_analysis_final_v1"
QA_DERIVE_METHOD = "video_analysis_final_v1_keyframe_qa"
ANALYSIS_KIND = "video_final_v1"
METHOD = "video_final_v1_cache_extract_v1"
PROVIDER = "gemini"


@dataclass(frozen=True)
class PreparedResult:
    final_cache_id: int
    kol_pool_id: int
    source_url: str
    source_evidence_id: int
    llm_v6_fit: Decimal
    confidence: Decimal | None
    llm_dimensions_11: dict[str, Any]
    action: str
    handle: str
    platform: str
    qa_cache_id: int | None


@dataclass(frozen=True)
class SkippedResult:
    final_cache_id: int
    kol_pool_id: int | None
    source_evidence_id: int | None
    handle: str
    reason: str


def _load_env() -> None:
    if load_dotenv is not None:
        load_dotenv(ROOT / ".env")
        return
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _database_url() -> str:
    return os.environ.get("DATABASE_URL", "").strip() or "postgresql://viltrox2@127.0.0.1:54329/viltrox2"


def _connect() -> psycopg.Connection[Any]:
    return psycopg.connect(_database_url(), row_factory=dict_row)


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
        return {str(key): _json_ready(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    return value


def _clean_text(value: Any, limit: int = 1200) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text[:limit]


def _final_payload(result: Any) -> dict[str, Any]:
    root = _as_dict(result)
    nested = _as_dict(root.get(FINAL_DERIVE_METHOD))
    if _as_dict(nested.get("layer1_visual_content")):
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


def _apply_qa_score(
    *,
    base_score: Decimal,
    qa_payload: dict[str, Any],
) -> tuple[Decimal, bool, dict[str, Any]]:
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
    qa_cache_id = row.get("qa_cache_id")
    title = _clean_text(row.get("title") or row.get("video_title") or row.get("content_url"), 500)
    dimensions: dict[str, Any] = {
        "schema_version": "kol_llm_deep_analysis_from_final_v1_v1",
        "source": {
            "source_cache_id": row["final_cache_id"],
            "source_evidence_id": row["evidence_id"],
            "qa_source_cache_id": qa_cache_id,
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
        "qa_source_cache_id": qa_cache_id,
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
            "qa_source_cache_id": qa_cache_id,
            "qa_pass": qa_payload.get("qa_pass"),
            "confidence": qa_payload.get("confidence"),
            "summary": qa_payload.get("summary"),
            "checks": qa_payload.get("checks"),
            "issues": qa_payload.get("issues"),
            "score_correction": qa_correction,
            "recommended_review_action": qa_payload.get("recommended_review_action"),
        }
    return _json_ready(dimensions)


def fetch_rows(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            WITH qa AS (
              SELECT q.*,
                     ROW_NUMBER() OVER (PARTITION BY q.target_id ORDER BY q.updated_at DESC, q.id DESC) AS rn
              FROM vkpi_analysis_cache q
              WHERE q.target_type = 'video'
                AND q.derive_method = %(qa_derive_method)s
                AND q.status = 'ready'
            )
            SELECT c.id AS final_cache_id,
                   c.result AS final_result,
                   c.model AS final_model,
                   c.cost AS final_cost,
                   c.updated_at AS final_updated_at,
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
                   qa.cost AS qa_cost,
                   qa.updated_at AS qa_updated_at
            FROM vkpi_analysis_cache c
            JOIN vkpi_kol_video_evidence e
              ON e.id::text = c.target_id
             AND c.target_type = 'video'
            JOIN vkpi_kol_pool p
              ON p.id = e.kol_pool_id
            LEFT JOIN qa
              ON qa.target_id = c.target_id
             AND qa.rn = 1
            WHERE c.target_type = 'video'
              AND c.derive_method = %(final_derive_method)s
              AND c.status = 'ready'
            ORDER BY c.id
            """,
            {"final_derive_method": FINAL_DERIVE_METHOD, "qa_derive_method": QA_DERIVE_METHOD},
        )
        return [dict(row) for row in cur.fetchall()]


def fetch_existing_by_source_cache(conn: psycopg.Connection[Any]) -> dict[int, list[int]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT source_cache_id, ARRAY_AGG(id ORDER BY id) AS ids
            FROM vkpi_kol_llm_deep_analysis_results
            WHERE analysis_kind = %(analysis_kind)s
              AND source_cache_id IS NOT NULL
            GROUP BY source_cache_id
            """,
            {"analysis_kind": ANALYSIS_KIND},
        )
        return {int(row["source_cache_id"]): [int(item) for item in row["ids"]] for row in cur.fetchall()}


def build_plan(
    rows: list[dict[str, Any]],
    existing: dict[int, list[int]],
) -> tuple[list[PreparedResult], list[SkippedResult]]:
    prepared: list[PreparedResult] = []
    skipped: list[SkippedResult] = []
    duplicate_existing = {cache_id: ids for cache_id, ids in existing.items() if len(ids) > 1}
    if duplicate_existing:
        details = ", ".join(f"{cache_id}:{ids}" for cache_id, ids in sorted(duplicate_existing.items())[:10])
        raise RuntimeError(f"duplicate target rows by source_cache_id; aborting for idempotency: {details}")

    for row in rows:
        cache_id = int(row["final_cache_id"])
        kol_pool_id = int(row["kol_pool_id"]) if row.get("kol_pool_id") is not None else None
        evidence_id = int(row["evidence_id"]) if row.get("evidence_id") is not None else None
        handle = str(row.get("handle") or row.get("display_name") or "")
        if kol_pool_id is None or evidence_id is None:
            skipped.append(SkippedResult(cache_id, kol_pool_id, evidence_id, handle, "missing_kol_or_evidence"))
            continue
        source_url = _clean_text(row.get("content_url"), 2000)
        if not source_url:
            skipped.append(SkippedResult(cache_id, kol_pool_id, evidence_id, handle, "missing_source_url"))
            continue
        payload = _final_payload(row.get("final_result"))
        layer6 = _as_dict(payload.get("layer6_flags_and_scores"))
        base_score, confidence, score_path = _marketing_score(layer6)
        if base_score is None:
            skipped.append(SkippedResult(cache_id, kol_pool_id, evidence_id, handle, "missing_marketing_value_score"))
            continue
        qa_payload = _qa_payload(row.get("qa_result"))
        final_score, qa_adjusted, qa_correction = _apply_qa_score(base_score=base_score, qa_payload=qa_payload)
        dimensions = _build_dimensions(
            row=row,
            payload=payload,
            qa_payload=qa_payload,
            base_score=base_score,
            final_score=final_score,
            score_path=score_path,
            qa_adjusted=qa_adjusted,
            qa_correction=qa_correction,
            confidence=confidence,
        )
        action = "update" if cache_id in existing else "insert"
        prepared.append(
            PreparedResult(
                final_cache_id=cache_id,
                kol_pool_id=kol_pool_id,
                source_url=source_url,
                source_evidence_id=evidence_id,
                llm_v6_fit=final_score,
                confidence=confidence,
                llm_dimensions_11=dimensions,
                action=action,
                handle=handle,
                platform=str(row.get("platform") or ""),
                qa_cache_id=int(row["qa_cache_id"]) if row.get("qa_cache_id") is not None else None,
            )
        )
    return prepared, skipped


def _bucket(score: Decimal) -> str:
    if score == 0:
        return "0"
    if score < 40:
        return "1-39"
    if score < 60:
        return "40-59"
    if score < 75:
        return "60-74"
    if score < 90:
        return "75-89"
    return "90-100"


def print_report(rows: list[dict[str, Any]], prepared: list[PreparedResult], skipped: list[SkippedResult]) -> None:
    scores = [item.llm_v6_fit for item in prepared]
    action_counts = Counter(item.action for item in prepared)
    platform_counts = Counter(item.platform or "unknown" for item in prepared)
    bucket_counts = Counter(_bucket(score) for score in scores)
    qa_items = [item for item in prepared if item.qa_cache_id is not None]
    kol_ids = {item.kol_pool_id for item in prepared}
    print("mode: dry-run (no writes)")
    print(f"source final_v1 ready rows: {len(rows)}")
    print(f"would_write: {len(prepared)}")
    print(f"would_insert: {action_counts.get('insert', 0)}")
    print(f"would_update: {action_counts.get('update', 0)}")
    print(f"skipped: {len(skipped)}")
    print(f"with_qa: {len(qa_items)}")
    print(f"kol_coverage_writable: {len(kol_ids)}")
    if scores:
        avg = sum(scores, Decimal("0")) / Decimal(len(scores))
        print(f"llm_v6_fit_min: {min(scores)}")
        print(f"llm_v6_fit_max: {max(scores)}")
        print(f"llm_v6_fit_avg: {avg.quantize(Decimal('0.01'))}")
    print("score_distribution:")
    for key in ["0", "1-39", "40-59", "60-74", "75-89", "90-100"]:
        print(f"  {key}: {bucket_counts.get(key, 0)}")
    print("platform_distribution:")
    for key, value in sorted(platform_counts.items()):
        print(f"  {key}: {value}")
    print("qa_rows:")
    if qa_items:
        for item in qa_items:
            print(
                f"  evidence={item.source_evidence_id} kol={item.kol_pool_id} "
                f"handle={item.handle} final_cache={item.final_cache_id} qa_cache={item.qa_cache_id} "
                f"score={item.llm_v6_fit}"
            )
    else:
        print("  (none)")
    print("skipped_rows:")
    if skipped:
        for item in skipped:
            print(
                f"  final_cache={item.final_cache_id} evidence={item.source_evidence_id} "
                f"kol={item.kol_pool_id} handle={item.handle} reason={item.reason}"
            )
    else:
        print("  (none)")
    print("sample_writes:")
    for item in sorted(prepared, key=lambda row: (row.source_evidence_id, row.final_cache_id))[:12]:
        print(
            f"  {item.action} final_cache={item.final_cache_id} evidence={item.source_evidence_id} "
            f"kol={item.kol_pool_id} handle={item.handle} score={item.llm_v6_fit} "
            f"confidence={item.confidence if item.confidence is not None else ''}"
        )


def _fit_snapshot(conn: psycopg.Connection[Any], kol_ids: list[int]) -> dict[int, Any]:
    if not kol_ids:
        return {}
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, viltrox_fit_score
            FROM vkpi_kol_pool
            WHERE id = ANY(%s::bigint[])
            """,
            (kol_ids,),
        )
        return {int(row["id"]): row["viltrox_fit_score"] for row in cur.fetchall()}


def write_results(conn: psycopg.Connection[Any], prepared: list[PreparedResult], existing: dict[int, list[int]]) -> dict[str, Any]:
    kol_ids = sorted({item.kol_pool_id for item in prepared})
    before = _fit_snapshot(conn, kol_ids)
    inserted = 0
    updated = 0
    with conn.cursor(row_factory=dict_row) as cur:
        for item in prepared:
            params = {
                "kol_pool_id": item.kol_pool_id,
                "source_url": item.source_url,
                "source_evidence_id": item.source_evidence_id,
                "analysis_kind": ANALYSIS_KIND,
                "llm_v6_fit": item.llm_v6_fit,
                "llm_dimensions_11": Jsonb(item.llm_dimensions_11),
                "method": METHOD,
                "provider": PROVIDER,
                "confidence": item.confidence,
                "source_cache_id": item.final_cache_id,
                "status": "ready",
            }
            existing_ids = existing.get(item.final_cache_id, [])
            if existing_ids:
                cur.execute(
                    """
                    UPDATE vkpi_kol_llm_deep_analysis_results
                    SET kol_pool_id = %(kol_pool_id)s,
                        source_url = %(source_url)s,
                        source_evidence_id = %(source_evidence_id)s,
                        analysis_kind = %(analysis_kind)s,
                        llm_v6_fit = %(llm_v6_fit)s,
                        llm_dimensions_11 = %(llm_dimensions_11)s,
                        method = %(method)s,
                        provider = %(provider)s,
                        confidence = %(confidence)s,
                        source_cache_id = %(source_cache_id)s,
                        status = %(status)s
                    WHERE id = %(id)s
                    """,
                    {**params, "id": existing_ids[0]},
                )
                updated += cur.rowcount
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
                    """,
                    params,
                )
                inserted += cur.rowcount
    after = _fit_snapshot(conn, kol_ids)
    changed_ids = [kol_id for kol_id in kol_ids if before.get(kol_id) != after.get(kol_id)]
    if changed_ids:
        conn.rollback()
        raise RuntimeError(f"viltrox_fit_score_changed_ids={changed_ids}; rolled back")
    conn.commit()
    return {"inserted": inserted, "updated": updated, "viltrox_fit_score_changed_ids": changed_ids}


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill independent KOL deep analysis results from final_v1 cache.")
    parser.add_argument("--commit", action="store_true", help="Write results. Omit for dry-run.")
    args = parser.parse_args()
    _load_env()
    with _connect() as conn:
        rows = fetch_rows(conn)
        existing = fetch_existing_by_source_cache(conn)
        prepared, skipped = build_plan(rows, existing)
        print_report(rows, prepared, skipped)
        if not args.commit:
            return
        result = write_results(conn, prepared, existing)
        print("write_result:")
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
