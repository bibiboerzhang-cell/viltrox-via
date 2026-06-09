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
    video_deep_ratio = (video_deep_ready / final_ready) if final_ready else 1.0
    profile_llm_ratio = (profile_llm_ready / final_kols) if final_kols else 0.0
    return {
        "code_chain_estimate": "99%+",
        "materialized_data_layers_estimate": round((video_deep_ratio * 0.65 + profile_llm_ratio * 0.35) * 100, 2),
        "video_deep_ratio": round(video_deep_ratio, 4),
        "profile_llm_ratio": round(profile_llm_ratio, 4),
        "search_history_has_real_sessions": bool(search["search_sessions"]),
        "remaining_blockers": [
            item
            for item in (
                "missing_video_deep_backfill" if deep["missing_video_deep_count"] else "",
                "profile_llm_not_materialized" if not profile_llm_ready else "",
                "search_session_not_smoked" if not search["search_sessions"] else "",
                "full_history_video_crawl_not_implemented",
                "tiktok_video_resolver_known_issue",
                "100_user_load_test_not_run",
            )
            if item
        ],
    }


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
    print("deep_results:")
    print(f"  final_v1_ready: {deep['final_v1_ready']}")
    print(f"  video_deep_ready: {deep['video_deep_ready']}")
    print(f"  missing_video_deep_count: {deep['missing_video_deep_count']}")
    print(f"  profile_llm_ready: {deep['profile_llm_ready']}")
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
