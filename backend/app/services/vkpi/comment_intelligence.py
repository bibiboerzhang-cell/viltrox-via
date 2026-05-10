"""P2.1 comment intelligence pipeline.

This module turns the P1 building blocks into one operational chain:
comments collection -> sentiment analysis -> post pillar classification.

It intentionally does not add a new ledger table yet. The canonical evidence
remains in vkpi_comments, vkpi_sentiment_results, vkpi_post_pillars, and
vkpi_comments_collection_runs.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.db.connection import get_conn
from app.services.vkpi import comments_collector, pillars, sentiment


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


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
) -> dict[str, Any]:
    """Run the P1 comment intelligence chain for one post."""
    post_table = post_table or "industry_posts"
    summary: dict[str, Any] = {
        "post_id": int(post_id),
        "post_table": post_table,
        "status": "ok",
        "started_at": _now_iso(),
        "steps": {},
    }

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

