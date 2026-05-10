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
from datetime import datetime, timedelta
from typing import Any

from app.db.connection import get_conn
from app.services.vkpi import comments_collector, pillars, sentiment


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_vkpi_comment_intelligence_schema() -> None:
    """Create pipeline run ledger when migrations are absent."""
    conn = get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vkpi_comment_intelligence_runs (
          id BIGSERIAL PRIMARY KEY,
          run_uid TEXT UNIQUE NOT NULL,
          post_id BIGINT NOT NULL,
          post_table VARCHAR(50) NOT NULL DEFAULT 'industry_posts',
          status VARCHAR(20) NOT NULL DEFAULT 'running',
          triggered_by VARCHAR(50),
          staff_id INT,
          retry_of_run_id BIGINT,
          params_json TEXT,
          steps_json TEXT,
          error_message TEXT,
          started_at TIMESTAMPTZ DEFAULT NOW(),
          finished_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ DEFAULT NOW()
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
        where.append("s.id IS NULL")

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
    return {
        "status": "ok",
        "updated": int((updated or {}).get("n") or 0),
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
    cutoff = (datetime.utcnow() - timedelta(days=max(1, int(days or 7)))).strftime(
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
