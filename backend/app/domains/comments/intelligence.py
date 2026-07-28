"""P2 comment intelligence pipeline.

This module turns the P1 building blocks into one operational chain:
comments collection -> sentiment analysis -> post pillar classification.

It intentionally does not add a new ledger table yet. The canonical evidence
remains in vkpi_comments, vkpi_sentiment_results, vkpi_post_pillars, and
vkpi_comments_collection_runs.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.connection import get_conn, is_postgres_runtime
from app.domains.comments import collector as comments_collector
from app.domains.comments.intelligence_rules import _rule_sentiment, _rule_tags, rule_v0_comment_summary
import app.domains.comments.sentiment as sentiment
from app.domains.content import pillars

_rule_v0_comment_summary = rule_v0_comment_summary


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_vkpi_comment_intelligence_schema() -> None:
    """Create the pipeline run ledger for the local SQLite fallback.

    PostgreSQL schema ownership belongs to migration 054; runtime request and
    worker paths must never acquire DDL relation locks.
    """
    if is_postgres_runtime():
        return

    conn = get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vkpi_comment_intelligence_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          run_uid TEXT UNIQUE NOT NULL,
          post_id INTEGER NOT NULL,
          post_table TEXT NOT NULL DEFAULT 'industry_posts',
          status TEXT NOT NULL DEFAULT 'running',
          triggered_by TEXT,
          staff_id INTEGER,
          retry_of_run_id INTEGER,
          params_json TEXT,
          steps_json TEXT,
          error_message TEXT,
          started_at TEXT DEFAULT CURRENT_TIMESTAMP,
          finished_at TEXT,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vkpi_ci_runs_post ON vkpi_comment_intelligence_runs(post_id, post_table, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vkpi_ci_runs_status ON vkpi_comment_intelligence_runs(status, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vkpi_ci_runs_retry ON vkpi_comment_intelligence_runs(retry_of_run_id)"
    )
    conn.commit()


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _table_exists(table_name: str) -> bool:
    conn = get_conn()
    try:
        row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone()
        return bool(row)
    except Exception:
        row = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        return bool(row)


def _actor_staff_id(staff: dict | None) -> int | None:
    if not isinstance(staff, dict):
        return None
    for key in ("id", "staff_id"):
        try:
            value = staff.get(key)
            if value is not None:
                return int(value)
        except Exception:
            continue
    return None


def _start_run(
    *,
    post_id: int,
    post_table: str,
    triggered_by: str,
    staff: dict | None,
    retry_of_run_id: int | None,
    params: dict[str, Any],
) -> dict[str, Any]:
    ensure_vkpi_comment_intelligence_schema()
    conn = get_conn()
    run_uid = f"ci_run_{uuid.uuid4().hex}"
    row = conn.execute(
        """
        INSERT INTO vkpi_comment_intelligence_runs (
          run_uid, post_id, post_table, status, triggered_by,
          staff_id, retry_of_run_id, params_json, started_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id, run_uid
        """,
        (
            run_uid,
            int(post_id),
            post_table,
            "running",
            triggered_by,
            _actor_staff_id(staff),
            retry_of_run_id,
            _json(params),
            _now_iso(),
        ),
    ).fetchone()
    conn.commit()
    return {"id": int(row["id"]), "run_uid": str(row["run_uid"])}


def _finish_run(
    run_id: int,
    *,
    status: str,
    steps: dict[str, Any],
    error: str = "",
) -> None:
    ensure_vkpi_comment_intelligence_schema()
    conn = get_conn()
    conn.execute(
        """
        UPDATE vkpi_comment_intelligence_runs
        SET status = ?, steps_json = ?, error_message = ?, finished_at = ?
        WHERE id = ?
        """,
        (status, _json(steps), (error or "")[:2000], _now_iso(), int(run_id)),
    )
    conn.commit()


def _comment_ids_for_post(
    post_id: int,
    post_table: str,
    *,
    force_reprocess: bool = False,
    limit: int = 100,
) -> list[int]:
    comments_collector.ensure_vkpi_comments_schema()
    sentiment.ensure_vkpi_sentiment_schema()
    conn = get_conn()

    where = ["c.post_id = ?", "c.post_table = ?"]
    params: list[Any] = [int(post_id), post_table]
    if not force_reprocess:
        where.append(
            "(s.id IS NULL OR (COALESCE(s.llm_provider, '') = 'rule_v0' "
            "AND COALESCE(s.llm_model, '') = 'rule_v0'))"
        )

    rows = conn.execute(
        f"""
        SELECT c.id
        FROM vkpi_comments c
        LEFT JOIN vkpi_sentiment_results s
          ON s.comment_id = c.id AND s.prompt_version = ?
        WHERE {" AND ".join(where)}
        ORDER BY c.fetched_at DESC, c.id DESC
        LIMIT ?
        """,
        (sentiment.PROMPT_VERSION, *params, max(1, int(limit or 100))),
    ).fetchall()
    return [int(r["id"]) for r in rows]


def _sync_comment_pillar_links(post_id: int, post_table: str) -> dict[str, Any]:
    """Copy a post's primary pillar id onto its comments for fast UI reads."""
    pillars.ensure_vkpi_pillar_schema()
    comments_collector.ensure_vkpi_comments_schema()
    conn = get_conn()

    primary = conn.execute(
        """
        SELECT pillar_id
        FROM vkpi_post_pillars
        WHERE post_id = ? AND post_table = ? AND is_primary = TRUE
        ORDER BY classified_at DESC, id DESC
        LIMIT 1
        """,
        (int(post_id), post_table),
    ).fetchone()
    if not primary:
        return {"status": "skip", "updated": 0, "reason": "primary pillar missing"}

    conn.execute(
        """
        UPDATE vkpi_comments
        SET pillar_id = ?
        WHERE post_id = ? AND post_table = ?
        """,
        (int(primary["pillar_id"]), int(post_id), post_table),
    )
    updated = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM vkpi_comments
        WHERE post_id = ? AND post_table = ? AND pillar_id = ?
        """,
        (int(post_id), post_table, int(primary["pillar_id"])),
    ).fetchone()
    conn.commit()
    updated_item = dict(updated) if updated else {}
    return {
        "status": "ok",
        "updated": int(updated_item.get("n") or 0),
        "pillar_id": int(primary["pillar_id"]),
    }


def process_post(
    post_id: int,
    post_table: str = "industry_posts",
    *,
    max_comments: int | None = None,
    collect_comments: bool = True,
    analyze_sentiment: bool = True,
    classify_pillar: bool = True,
    force_reprocess: bool = False,
    comment_limit: int = 100,
    staff: dict | None = None,
    triggered_by: str = "pipeline",
    retry_of_run_id: int | None = None,
) -> dict[str, Any]:
    """Run the P1 comment intelligence chain for one post."""
    ensure_vkpi_comment_intelligence_schema()
    post_table = post_table or "industry_posts"
    params = {
        "max_comments": max_comments,
        "collect_comments": collect_comments,
        "analyze_sentiment": analyze_sentiment,
        "classify_pillar": classify_pillar,
        "force_reprocess": force_reprocess,
        "comment_limit": comment_limit,
    }
    run = _start_run(
        post_id=int(post_id),
        post_table=post_table,
        triggered_by=triggered_by,
        staff=staff,
        retry_of_run_id=retry_of_run_id,
        params=params,
    )
    summary: dict[str, Any] = {
        "run_id": run["id"],
        "run_uid": run["run_uid"],
        "post_id": int(post_id),
        "post_table": post_table,
        "status": "ok",
        "started_at": _now_iso(),
        "steps": {},
    }

    try:
        if collect_comments:
            collection = comments_collector.collect_post_comments(
                int(post_id),
                post_table=post_table,
                max_comments=max_comments,
                staff=staff,
                triggered_by=triggered_by,
            )
            summary["steps"]["collection"] = collection
            if collection.get("status") in {"fail"}:
                summary["status"] = "partial"

        comment_ids = _comment_ids_for_post(
            int(post_id),
            post_table,
            force_reprocess=force_reprocess,
            limit=comment_limit,
        )
        summary["comment_ids_considered"] = len(comment_ids)

        if analyze_sentiment:
            analysis = sentiment.analyze_batch(comment_ids, staff=staff)
            summary["steps"]["sentiment"] = analysis
            if analysis.get("errors"):
                summary["status"] = "partial"

        if classify_pillar:
            classification = pillars.classify_post(
                int(post_id),
                post_table=post_table,
                force_reclassify=force_reprocess,
                staff=staff,
            )
            summary["steps"]["pillar"] = classification
            if classification.get("status") == "ok":
                summary["steps"]["comment_pillar_links"] = _sync_comment_pillar_links(
                    int(post_id), post_table
                )
            elif classification.get("status") not in {"duplicate"}:
                summary["status"] = "partial"

        summary["finished_at"] = _now_iso()
        _finish_run(run["id"], status=summary["status"], steps=summary["steps"])
        return summary
    except Exception as exc:
        summary["status"] = "fail"
        summary["error"] = str(exc)[:1000]
        summary["finished_at"] = _now_iso()
        _finish_run(
            run["id"],
            status="fail",
            steps=summary.get("steps") or {},
            error=summary["error"],
        )
        return summary



def process_recent_posts(
    *,
    platform: str = "",
    days: int = 7,
    limit: int = 25,
    collect_comments: bool = False,
    analyze_sentiment: bool = True,
    classify_pillar: bool = True,
    force_reprocess: bool = False,
    staff: dict | None = None,
) -> dict[str, Any]:
    """Process recent industry posts through the intelligence chain."""
    conn = get_conn()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, int(days or 7)))).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    where = ["created_at >= ?"]
    params: list[Any] = [cutoff]
    if platform:
        where.append("platform = ?")
        params.append(platform.lower())

    rows = conn.execute(
        f"""
        SELECT id, platform
        FROM vkpi_industry_posts
        WHERE {" AND ".join(where)}
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (*params, max(1, int(limit or 25))),
    ).fetchall()

    summary: dict[str, Any] = {
        "status": "ok",
        "total_posts": len(rows),
        "by_status": {},
        "results": [],
    }
    for row in rows:
        result = process_post(
            int(row["id"]),
            post_table="industry_posts",
            collect_comments=collect_comments,
            analyze_sentiment=analyze_sentiment,
            classify_pillar=classify_pillar,
            force_reprocess=force_reprocess,
            staff=staff,
            triggered_by="pipeline_batch",
        )
        status = str(result.get("status") or "unknown")
        summary["by_status"][status] = summary["by_status"].get(status, 0) + 1
        summary["results"].append(result)

    return summary


def list_runs(
    *,
    post_id: int | None = None,
    status: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    ensure_vkpi_comment_intelligence_schema()
    conn = get_conn()
    where = ["1=1"]
    params: list[Any] = []
    if post_id is not None:
        where.append("post_id = ?")
        params.append(int(post_id))
    if status:
        where.append("status = ?")
        params.append(status)
    rows = conn.execute(
        f"""
        SELECT id, run_uid, post_id, post_table, status, triggered_by,
               staff_id, retry_of_run_id, error_message, started_at,
               finished_at, created_at
        FROM vkpi_comment_intelligence_runs
        WHERE {" AND ".join(where)}
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (*params, max(1, min(500, int(limit or 100)))),
    ).fetchall()
    return {"count": len(rows), "runs": [dict(r) for r in rows]}


def get_run(run_id: int) -> dict[str, Any] | None:
    ensure_vkpi_comment_intelligence_schema()
    conn = get_conn()
    row = conn.execute(
        """
        SELECT *
        FROM vkpi_comment_intelligence_runs
        WHERE id = ?
        """,
        (int(run_id),),
    ).fetchone()
    if not row:
        return None
    item = dict(row)
    for key in ("params_json", "steps_json"):
        item[key.replace("_json", "")] = json.loads(item.get(key) or "{}")
    return item


def retry_run(run_id: int, *, staff: dict | None = None) -> dict[str, Any]:
    original = get_run(run_id)
    if not original:
        return {"status": "fail", "error": f"run {run_id} not found"}
    params = original.get("params") or {}
    return process_post(
        int(original["post_id"]),
        post_table=str(original.get("post_table") or "industry_posts"),
        max_comments=params.get("max_comments"),
        collect_comments=bool(params.get("collect_comments", True)),
        analyze_sentiment=bool(params.get("analyze_sentiment", True)),
        classify_pillar=bool(params.get("classify_pillar", True)),
        force_reprocess=True,
        comment_limit=int(params.get("comment_limit") or 100),
        staff=staff,
        triggered_by="retry",
        retry_of_run_id=int(run_id),
    )


def overview(*, days: int = 7, recent_limit: int = 8) -> dict[str, Any]:
    """Return dashboard-ready visibility for the comment intelligence pipeline."""
    ensure_vkpi_comment_intelligence_schema()
    comments_collector.ensure_vkpi_comments_schema()
    sentiment.ensure_vkpi_sentiment_schema()
    pillars.ensure_vkpi_pillar_schema()

    conn = get_conn()
    safe_days = max(1, min(180, int(days or 7)))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=safe_days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    safe_recent = max(1, min(50, int(recent_limit or 8)))

    run_rows = conn.execute(
        """
        SELECT status, COUNT(*) AS n
        FROM vkpi_comment_intelligence_runs
        WHERE created_at >= ?
        GROUP BY status
        """,
        (cutoff,),
    ).fetchall()
    by_status = {str(r["status"]): int(r["n"] or 0) for r in run_rows}
    total_runs = sum(by_status.values())
    successful_runs = by_status.get("ok", 0)
    failure_runs = by_status.get("fail", 0) + by_status.get("partial", 0)
    success_rate = round(successful_runs / total_runs, 4) if total_runs else None

    recent_runs = conn.execute(
        """
        SELECT id, run_uid, post_id, post_table, status, triggered_by,
               retry_of_run_id, error_message, started_at, finished_at, created_at
        FROM vkpi_comment_intelligence_runs
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (safe_recent,),
    ).fetchall()

    recent_failures = conn.execute(
        """
        SELECT id, run_uid, post_id, post_table, status, triggered_by,
               error_message, created_at
        FROM vkpi_comment_intelligence_runs
        WHERE status IN ('fail', 'partial')
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (safe_recent,),
    ).fetchall()

    comment_coverage_row = conn.execute(
        """
        SELECT
          COUNT(*) AS total_comments,
          SUM(CASE WHEN sentiment_id IS NOT NULL THEN 1 ELSE 0 END) AS with_sentiment,
          SUM(CASE WHEN pillar_id IS NOT NULL THEN 1 ELSE 0 END) AS with_pillar
        FROM vkpi_comments
        WHERE fetched_at >= ?
        """,
        (cutoff,),
    ).fetchone()
    comment_coverage = dict(comment_coverage_row) if comment_coverage_row else {}
    total_comments = int(comment_coverage.get("total_comments") or 0)
    with_sentiment = int(comment_coverage.get("with_sentiment") or 0)
    with_pillar = int(comment_coverage.get("with_pillar") or 0)

    if _table_exists("vkpi_industry_posts"):
        post_coverage_row = conn.execute(
            """
            SELECT
              COUNT(DISTINCT p.id) AS total_posts,
              COUNT(DISTINCT pp.post_id) AS with_primary_pillar
            FROM vkpi_industry_posts p
            LEFT JOIN vkpi_post_pillars pp
              ON pp.post_id = p.id
              AND pp.post_table = 'industry_posts'
              AND pp.is_primary = TRUE
            WHERE p.created_at >= ?
            """,
            (cutoff,),
        ).fetchone()
        post_coverage = dict(post_coverage_row) if post_coverage_row else {}
    else:
        post_coverage = {}
    total_posts = int(post_coverage.get("total_posts") or 0)
    posts_with_pillar = int(post_coverage.get("with_primary_pillar") or 0)

    pending_sentiment = max(total_comments - with_sentiment, 0)
    pending_pillar_links = max(total_comments - with_pillar, 0)
    health = "ok"
    if failure_runs:
        health = "degraded"
    if total_runs and successful_runs == 0:
        health = "attention"

    sentiment_distribution = conn.execute(
        """
        SELECT COALESCE(sentiment, 'unknown') AS label, COUNT(*) AS count
        FROM vkpi_sentiment_results
        WHERE analyzed_at >= ?
        GROUP BY COALESCE(sentiment, 'unknown')
        ORDER BY count DESC, label ASC
        """,
        (cutoff,),
    ).fetchall()
    emotion_distribution = conn.execute(
        """
        SELECT COALESCE(emotion, 'unknown') AS label, COUNT(*) AS count
        FROM vkpi_sentiment_results
        WHERE analyzed_at >= ?
        GROUP BY COALESCE(emotion, 'unknown')
        ORDER BY count DESC, label ASC
        LIMIT 10
        """,
        (cutoff,),
    ).fetchall()
    brand_attitude_distribution = conn.execute(
        """
        SELECT COALESCE(brand_attitude, 'unknown') AS label, COUNT(*) AS count
        FROM vkpi_sentiment_results
        WHERE analyzed_at >= ?
        GROUP BY COALESCE(brand_attitude, 'unknown')
        ORDER BY count DESC, label ASC
        """,
        (cutoff,),
    ).fetchall()
    pillar_distribution = conn.execute(
        """
        SELECT
          p.pillar_key,
          p.display_name,
          p.layer,
          COUNT(*) AS count,
          SUM(CASE WHEN pp.is_primary THEN 1 ELSE 0 END) AS primary_count
        FROM vkpi_post_pillars pp
        JOIN vkpi_pillars p ON p.id = pp.pillar_id
        WHERE pp.classified_at >= ?
        GROUP BY p.pillar_key, p.display_name, p.layer
        ORDER BY count DESC, primary_count DESC, p.display_name ASC
        LIMIT 12
        """,
        (cutoff,),
    ).fetchall()

    rule_v0 = rule_v0_comment_summary(cutoff=cutoff, sample_limit=safe_recent)
    return {
        "days": safe_days,
        "health": health,
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "runs": {
            "total": total_runs,
            "by_status": by_status,
            "success_rate": success_rate,
            "recent": [dict(r) for r in recent_runs],
            "recent_failures": [dict(r) for r in recent_failures],
        },
        "coverage": {
            "comments_total": total_comments,
            "comments_with_sentiment": with_sentiment,
            "comments_with_pillar": with_pillar,
            "sentiment_coverage": round(with_sentiment / total_comments, 4) if total_comments else None,
            "comment_pillar_coverage": round(with_pillar / total_comments, 4) if total_comments else None,
            "pending_sentiment": pending_sentiment,
            "pending_comment_pillar_links": pending_pillar_links,
            "posts_total": total_posts,
            "posts_with_primary_pillar": posts_with_pillar,
            "post_pillar_coverage": round(posts_with_pillar / total_posts, 4) if total_posts else None,
            "sample_cap": safe_recent,
            "sample_status": rule_v0["contract"]["status"],
        },
        "comment_contract": rule_v0["contract"],
        "rule_v0": rule_v0,
        "distributions": {
            "sentiment": [dict(r) for r in sentiment_distribution],
            "emotion": [dict(r) for r in emotion_distribution],
            "brand_attitude": [dict(r) for r in brand_attitude_distribution],
            "pillars": [dict(r) for r in pillar_distribution],
        },
    }
