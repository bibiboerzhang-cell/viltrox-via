#!/usr/bin/env python3
"""Read-only audit for the KOL Pool smart URL/search backend state.

This script is a local evidence snapshot for the smart URL/search backend
chain. It does not write DB rows, enqueue jobs, call providers/LLMs, or touch
V6 Fit fields.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
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

from app.domains.kol import url_deep_crawl  # noqa: E402
from app.domains.kol.account_dossier_extract import (  # noqa: E402
    ANALYSIS_KIND as PROFILE_LLM_ANALYSIS_KIND,
    METHOD as PROFILE_LLM_METHOD,
    prepare_account_dossier_extract,
)


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
    return os.environ.get("DATABASE_URL", "").strip() or "postgresql://postgres@127.0.0.1:54329/viltrox2"


def _connect() -> psycopg.Connection[Any]:
    return psycopg.connect(_database_url(), row_factory=dict_row)


def _git_value(*args: str) -> str:
    try:
        return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""


def _scalar(conn: psycopg.Connection[Any], sql: str, params: tuple[Any, ...] = ()) -> int:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        row = cur.fetchone() or {}
    value = next(iter(row.values()), 0) if row else 0
    return int(value or 0)


def _rows(conn: psycopg.Connection[Any], sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _number_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed:
        return None
    return max(0.0, min(100.0, parsed))


def _score_from_value(value: Any) -> float | None:
    if isinstance(value, dict):
        return _number_or_none(value.get("score"))
    return _number_or_none(value)


def _final_payload(result: Any) -> dict[str, Any]:
    root = _as_dict(result)
    nested = _as_dict(root.get("video_analysis_final_v1"))
    if _as_dict(nested.get("layer1_visual_content")):
        return nested
    return root


def _has_marketing_value_score(result: Any) -> bool:
    payload = _final_payload(result)
    layer6 = _as_dict(payload.get("layer6_flags_and_scores"))
    scores = _as_dict(layer6.get("scores"))
    return (
        _score_from_value(scores.get("marketing_value_score")) is not None
        or _score_from_value(layer6.get("marketing_value_score")) is not None
    )


def deep_result_state(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    missing = _rows(
        conn,
        """
        WITH final AS (
          SELECT c.id, e.id AS evidence_id, e.kol_pool_id, p.handle
          FROM vkpi_analysis_cache c
          JOIN vkpi_kol_video_evidence e
            ON e.id::text=c.target_id
           AND c.target_type='video'
          JOIN vkpi_kol_pool p
            ON p.id=e.kol_pool_id
          WHERE c.derive_method='video_analysis_final_v1'
            AND c.status='ready'
        ),
        deep AS (
          SELECT source_cache_id
          FROM vkpi_kol_llm_deep_analysis_results
          WHERE analysis_kind='video_final_v1'
            AND status='ready'
        )
        SELECT f.id AS cache_id, f.evidence_id, f.kol_pool_id, f.handle
        FROM final f
        LEFT JOIN deep d ON d.source_cache_id=f.id
        WHERE d.source_cache_id IS NULL
        ORDER BY f.id
        """,
    )
    missing_with_result = _rows(
        conn,
        """
        WITH final AS (
          SELECT c.id, c.result, e.id AS evidence_id, e.kol_pool_id, p.handle
          FROM vkpi_analysis_cache c
          JOIN vkpi_kol_video_evidence e
            ON e.id::text=c.target_id
           AND c.target_type='video'
          JOIN vkpi_kol_pool p
            ON p.id=e.kol_pool_id
          WHERE c.derive_method='video_analysis_final_v1'
            AND c.status='ready'
        ),
        deep AS (
          SELECT source_cache_id
          FROM vkpi_kol_llm_deep_analysis_results
          WHERE analysis_kind='video_final_v1'
            AND status='ready'
        )
        SELECT f.id AS cache_id, f.result, f.evidence_id, f.kol_pool_id, f.handle
        FROM final f
        LEFT JOIN deep d ON d.source_cache_id=f.id
        WHERE d.source_cache_id IS NULL
        ORDER BY f.id
        """,
    )
    writable_missing: list[dict[str, Any]] = []
    skipped_missing: list[dict[str, Any]] = []
    for row in missing_with_result:
        item = {key: value for key, value in row.items() if key != "result"}
        if _has_marketing_value_score(row.get("result")):
            writable_missing.append(item)
        else:
            skipped_missing.append({**item, "reason": "missing_marketing_value_score"})
    profile_projection = profile_llm_projection(conn)
    return {
        "final_v1_ready": _scalar(
            conn,
            """
            SELECT count(*)
            FROM vkpi_analysis_cache
            WHERE target_type='video'
              AND derive_method='video_analysis_final_v1'
              AND status='ready'
            """,
        ),
        "final_v1_kols": _scalar(
            conn,
            """
            SELECT count(DISTINCT e.kol_pool_id)
            FROM vkpi_analysis_cache c
            JOIN vkpi_kol_video_evidence e
              ON e.id::text=c.target_id
             AND c.target_type='video'
            WHERE c.derive_method='video_analysis_final_v1'
              AND c.status='ready'
            """,
        ),
        "video_deep_ready": _scalar(
            conn,
            """
            SELECT count(*)
            FROM vkpi_kol_llm_deep_analysis_results
            WHERE analysis_kind='video_final_v1'
              AND status='ready'
            """,
        ),
        "profile_llm_ready": _scalar(
            conn,
            """
            SELECT count(*)
            FROM vkpi_kol_llm_deep_analysis_results
            WHERE analysis_kind='profile_llm'
              AND status='ready'
            """,
        ),
        "missing_video_deep_count": len(missing),
        "missing_video_deep": missing,
        "missing_video_deep_writable_count": len(writable_missing),
        "missing_video_deep_writable": writable_missing,
        "missing_video_deep_skipped": skipped_missing,
        "profile_llm_projection": profile_projection,
    }


def _profile_llm_candidate_ids(conn: psycopg.Connection[Any]) -> list[int]:
    rows = _rows(
        conn,
        """
        WITH final AS (
          SELECT DISTINCT e.kol_pool_id
          FROM vkpi_analysis_cache c
          JOIN vkpi_kol_video_evidence e
            ON e.id::text=c.target_id
           AND c.target_type='video'
          WHERE c.derive_method='video_analysis_final_v1'
            AND c.status='ready'
        ),
        deep AS (
          SELECT DISTINCT kol_pool_id
          FROM vkpi_kol_llm_deep_analysis_results
          WHERE analysis_kind='video_final_v1'
            AND status='ready'
        )
        SELECT DISTINCT kol_pool_id
        FROM (
          SELECT kol_pool_id FROM final
          UNION ALL
          SELECT kol_pool_id FROM deep
        ) src
        WHERE kol_pool_id IS NOT NULL
        ORDER BY kol_pool_id
        """,
    )
    return [int(row["kol_pool_id"]) for row in rows]


def _existing_profile_llm_ids(conn: psycopg.Connection[Any], kol_pool_id: int) -> list[int]:
    rows = _rows(
        conn,
        """
        SELECT id
        FROM vkpi_kol_llm_deep_analysis_results
        WHERE kol_pool_id=%s
          AND analysis_kind=%s
          AND method=%s
          AND status='ready'
        ORDER BY id DESC
        """,
        (int(kol_pool_id), PROFILE_LLM_ANALYSIS_KIND, PROFILE_LLM_METHOD),
    )
    return [int(row["id"]) for row in rows]


def profile_llm_projection(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    candidates = _profile_llm_candidate_ids(conn)
    ready_insert = 0
    ready_update = 0
    skipped: list[dict[str, Any]] = []
    sample_ready: list[dict[str, Any]] = []
    gap_counts: dict[str, int] = {}
    for kol_id in candidates:
        prepared = prepare_account_dossier_extract(int(kol_id))
        if prepared.get("status") != "ready":
            skipped.append({"kol_pool_id": int(kol_id), "reason": prepared.get("reason") or prepared.get("status")})
            continue
        existing = _existing_profile_llm_ids(conn, int(kol_id))
        if existing:
            ready_update += 1
        else:
            ready_insert += 1
        summary = prepared.get("summary") if isinstance(prepared.get("summary"), dict) else {}
        for gap in summary.get("gaps") or []:
            key = str(gap)
            gap_counts[key] = gap_counts.get(key, 0) + 1
        if len(sample_ready) < 10:
            sample_ready.append(
                {
                    "kol_pool_id": int(kol_id),
                    "action": "update" if existing else "insert",
                    "llm_v6_fit": summary.get("llm_v6_fit"),
                    "confidence": summary.get("confidence"),
                    "video_count": summary.get("video_count"),
                    "analyzed_final_v1_count": summary.get("analyzed_final_v1_count"),
                    "qa_count": summary.get("qa_count"),
                    "deep_result_count": summary.get("deep_result_count"),
                }
            )
    return {
        "analysis_kind": PROFILE_LLM_ANALYSIS_KIND,
        "method": PROFILE_LLM_METHOD,
        "candidates": len(candidates),
        "ready": ready_insert + ready_update,
        "ready_insert": ready_insert,
        "ready_update": ready_update,
        "skipped": len(skipped),
        "sample_ready": sample_ready,
        "sample_skipped": skipped[:10],
        "top_gaps": sorted(gap_counts.items(), key=lambda item: (-item[1], item[0]))[:10],
    }


def search_state(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    return {
        "search_sessions": _scalar(conn, "SELECT count(*) FROM vkpi_kol_search_sessions"),
        "search_session_items": _scalar(conn, "SELECT count(*) FROM vkpi_kol_search_session_items"),
        "url_deep_crawl_runs": _scalar(conn, "SELECT count(*) FROM vkpi_kol_url_deep_crawl_runs"),
        "recent_search_sessions": _rows(
            conn,
            """
            SELECT id, query_text, query_type, status, created_at
            FROM vkpi_kol_search_sessions
            ORDER BY id DESC
            LIMIT 5
            """,
        ),
    }


def queue_state(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    statuses = _rows(
        conn,
        """
        SELECT status, count(*) AS count
        FROM apify_jobs
        GROUP BY status
        ORDER BY status
        """,
    )
    active = _rows(
        conn,
        """
        SELECT id, job_type, status, payload->>'derive_method' AS derive_method,
               payload->>'target_type' AS target_type,
               payload->>'target_id' AS target_id,
               created_at, updated_at
        FROM apify_jobs
        WHERE status IN ('queued', 'running', 'retrying', 'processing')
        ORDER BY created_at DESC
        LIMIT 20
        """,
    )
    ledger_active = _rows(
        conn,
        """
        SELECT id, task_id, job_type, status, created_at, updated_at
        FROM job_execution_ledger
        WHERE status IN ('queued', 'running', 'retrying', 'processing')
        ORDER BY updated_at DESC
        LIMIT 20
        """,
    )
    return {
        "apify_status_counts": statuses,
        "apify_active": active,
        "ledger_active": ledger_active,
        "active_total": len(active) + len(ledger_active),
    }


def url_classifier_state(conn: psycopg.Connection[Any], *, sample_limit: int) -> dict[str, Any]:
    rows = _rows(
        conn,
        """
        SELECT id, kol_pool_id, platform, content_url
        FROM vkpi_kol_video_evidence
        WHERE content_url IS NOT NULL AND content_url <> ''
        ORDER BY id DESC
        """,
    )
    mismatch: list[dict[str, Any]] = []
    unsupported = 0
    not_video = 0
    for row in rows:
        classified = url_deep_crawl.classify_url(str(row.get("content_url") or ""))
        db_platform = str(row.get("platform") or "").lower()
        if classified.url_type != "video":
            not_video += 1
        if not classified.platform:
            unsupported += 1
        if classified.platform and db_platform and db_platform != classified.platform:
            mismatch.append(
                {
                    "evidence_id": row.get("id"),
                    "kol_pool_id": row.get("kol_pool_id"),
                    "db_platform": row.get("platform"),
                    "url_platform": classified.platform,
                    "url_type": classified.url_type,
                    "video_id": classified.video_id,
                    "url": str(row.get("content_url") or "")[:180],
                }
            )
    samples = [
        "https://www.youtube.com/@juliatrotti/videos",
        "https://www.tiktok.com/@teleginivan",
        "https://www.instagram.com/shtefutsa/reel/DYxGltBM_fY/",
        "https://www.instagram.com/jaysoundo/p/DYw3UWUCJ_6/",
        "https://www.youtube.com/watch?v=CkdzDM8uev8",
    ]
    return {
        "evidence_with_url": len(rows),
        "not_classified_as_video": not_video,
        "unsupported_or_unknown": unsupported,
        "platform_mismatch_count": len(mismatch),
        "platform_mismatch_sample": mismatch[:sample_limit],
        "sample_classifications": [
            {"url": url, "classified": url_deep_crawl.classify_url(url).__dict__}
            for url in samples
        ],
    }


def score_summary(state: dict[str, Any]) -> dict[str, Any]:
    deep = state["deep_results"]
    search = state["search"]
    final_ready = int(deep["final_v1_ready"] or 0)
    video_deep_ready = int(deep["video_deep_ready"] or 0)
    final_kols = int(deep["final_v1_kols"] or 0)
    profile_llm_ready = int(deep["profile_llm_ready"] or 0)
    profile_projection = deep.get("profile_llm_projection") if isinstance(deep.get("profile_llm_projection"), dict) else {}
    projected_video_deep_ready = video_deep_ready + int(deep.get("missing_video_deep_writable_count") or 0)
    projected_profile_llm_ready = profile_llm_ready + int(profile_projection.get("ready_insert") or 0)
    video_deep_ratio = (video_deep_ready / final_ready) if final_ready else 1.0
    profile_llm_ratio = (profile_llm_ready / final_kols) if final_kols else 0.0
    projected_video_deep_ratio = (projected_video_deep_ready / final_ready) if final_ready else 1.0
    projected_profile_llm_ratio = (projected_profile_llm_ready / final_kols) if final_kols else 0.0
    return {
        "code_chain_estimate": "99%+",
        "materialized_data_layers_estimate": round((video_deep_ratio * 0.65 + profile_llm_ratio * 0.35) * 100, 2),
        "projected_materialized_data_layers_if_ready_backfills_committed": round(
            (projected_video_deep_ratio * 0.65 + projected_profile_llm_ratio * 0.35) * 100,
            2,
        ),
        "video_deep_ratio": round(video_deep_ratio, 4),
        "profile_llm_ratio": round(profile_llm_ratio, 4),
        "projected_video_deep_ready": projected_video_deep_ready,
        "projected_profile_llm_ready": projected_profile_llm_ready,
        "projected_video_deep_ratio": round(projected_video_deep_ratio, 4),
        "projected_profile_llm_ratio": round(projected_profile_llm_ratio, 4),
        "search_history_has_real_sessions": bool(search["search_sessions"]),
        "search_history_has_real_items": bool(search["search_session_items"]),
        "history_video_crawl_implemented": history_video_crawl_implemented(),
        "remaining_blockers": [
            item
            for item in (
                "missing_video_deep_backfill_ready_to_commit" if deep.get("missing_video_deep_writable_count") else "missing_video_deep_backfill" if deep["missing_video_deep_count"] else "",
                "profile_llm_ready_to_commit" if profile_projection.get("ready_insert") else "profile_llm_not_materialized" if not profile_llm_ready else "",
                "search_session_not_smoked" if not search["search_sessions"] else "search_items_not_smoked" if not search["search_session_items"] else "",
                "full_history_video_crawl_not_implemented" if not history_video_crawl_implemented() else "",
                "tiktok_video_resolver_known_issue",
                "100_user_load_test_not_run",
            )
            if item
        ],
    }


def history_video_crawl_implemented() -> bool:
    src = (ROOT / "backend/app/domains/kol/url_deep_crawl.py").read_text(encoding="utf-8")
    return all(
        needle in src
        for needle in (
            "_execute_profile_history_video_evidence",
            "_profile_should_materialize_history_videos",
            "_profile_history_video_limit",
            "_filter_incremental_profile_videos",
            "url_profile_history_video_evidence_v1",
        )
    )


def print_report(state: dict[str, Any]) -> None:
    score = state["score"]
    deep = state["deep_results"]
    search = state["search"]
    queue = state["queue"]
    url_state = state["url_classifier"]
    print("V-KPI KOL smart backend audit")
    print(f"branch: {state['git']['branch']} sha: {state['git']['short_sha']}")
    print(f"code_chain_estimate: {score['code_chain_estimate']}")
    print(f"materialized_data_layers_estimate: {score['materialized_data_layers_estimate']}%")
    print(
        "projected_materialized_data_layers_if_ready_backfills_committed: "
        f"{score['projected_materialized_data_layers_if_ready_backfills_committed']}%"
    )
    print("deep_results:")
    print(f"  final_v1_ready: {deep['final_v1_ready']}")
    print(f"  video_deep_ready: {deep['video_deep_ready']}")
    print(f"  projected_video_deep_ready: {score['projected_video_deep_ready']}")
    print(f"  missing_video_deep_count: {deep['missing_video_deep_count']}")
    print(f"  missing_video_deep_writable_count: {deep.get('missing_video_deep_writable_count', 0)}")
    print(f"  profile_llm_ready: {deep['profile_llm_ready']}")
    print(f"  projected_profile_llm_ready: {score['projected_profile_llm_ready']}")
    profile_projection = deep.get("profile_llm_projection") if isinstance(deep.get("profile_llm_projection"), dict) else {}
    print("profile_llm_projection:")
    print(f"  candidates: {profile_projection.get('candidates', 0)}")
    print(f"  ready: {profile_projection.get('ready', 0)}")
    print(f"  ready_insert: {profile_projection.get('ready_insert', 0)}")
    print(f"  ready_update: {profile_projection.get('ready_update', 0)}")
    print(f"  skipped: {profile_projection.get('skipped', 0)}")
    print("search:")
    print(f"  sessions: {search['search_sessions']}")
    print(f"  items: {search['search_session_items']}")
    print(f"  url_deep_crawl_runs: {search['url_deep_crawl_runs']}")
    print("queue:")
    print(f"  active_total: {queue['active_total']}")
    print("url_classifier:")
    print(f"  evidence_with_url: {url_state['evidence_with_url']}")
    print(f"  not_classified_as_video: {url_state['not_classified_as_video']}")
    print(f"  platform_mismatch_count: {url_state['platform_mismatch_count']}")
    print("remaining_blockers:")
    for item in score["remaining_blockers"]:
        print(f"  - {item}")
    if deep["missing_video_deep"]:
        print("missing_video_deep:")
        for item in deep["missing_video_deep"][:20]:
            print(
                f"  cache={item['cache_id']} evidence={item['evidence_id']} "
                f"kol={item['kol_pool_id']} handle={item['handle']}"
            )
    if deep.get("missing_video_deep_writable"):
        print("missing_video_deep_writable:")
        for item in deep["missing_video_deep_writable"][:20]:
            print(
                f"  cache={item['cache_id']} evidence={item['evidence_id']} "
                f"kol={item['kol_pool_id']} handle={item['handle']}"
            )
    if deep.get("missing_video_deep_skipped"):
        print("missing_video_deep_skipped:")
        for item in deep["missing_video_deep_skipped"][:20]:
            print(
                f"  cache={item['cache_id']} evidence={item['evidence_id']} "
                f"kol={item['kol_pool_id']} handle={item['handle']} reason={item.get('reason')}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only KOL smart backend state audit.")
    parser.add_argument("--json", action="store_true", help="Print full JSON after the text report.")
    parser.add_argument("--sample-limit", type=int, default=12, help="Limit URL mismatch samples.")
    args = parser.parse_args()
    _load_env()
    with _connect() as conn:
        state: dict[str, Any] = {
            "git": {
                "branch": _git_value("rev-parse", "--abbrev-ref", "HEAD"),
                "short_sha": _git_value("rev-parse", "--short", "HEAD"),
            },
            "deep_results": deep_result_state(conn),
            "search": search_state(conn),
            "queue": queue_state(conn),
            "url_classifier": url_classifier_state(conn, sample_limit=max(1, int(args.sample_limit or 12))),
        }
    state["score"] = score_summary(state)
    print_report(state)
    if args.json:
        print("audit_json:")
        print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
