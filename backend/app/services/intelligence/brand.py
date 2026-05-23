"""
services/intelligence/brand.py — Brand intelligence backed by current viltrox_matrix tables.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.db.repositories.viltrox_matrix import get_latest_viltrox_scan_bundle
from app.services.ai.retry import call_ai_with_retry
from app.services.intelligence.viltrox_matrix import build_viltrox_overview

logger = get_logger(__name__)


def _fallback_insights_from_matrix(accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not accounts:
        return [
            {
                "category": "coverage",
                "severity": "amber",
                "title": "Brand matrix is empty",
                "body": "No official account rows are available yet. Run the first matrix scan before reviewing brand performance.",
                "generated_at": datetime.utcnow().isoformat(),
            }
        ]

    active_accounts = [row for row in accounts if int(row.get("posts") or 0) > 0 or int(row.get("views") or 0) > 0]
    if not active_accounts:
        return [
            {
                "category": "scan",
                "severity": "amber",
                "title": "Run the first official account scan",
                "body": "Official account handles are registered, but no post-level engagement data has been ingested yet.",
                "generated_at": datetime.utcnow().isoformat(),
            },
            {
                "category": "coverage",
                "severity": "info",
                "title": f"{len(accounts)} official handles are mapped",
                "body": "Platform coverage is configured. Once scans start landing, recent posts and engagement deltas will populate automatically.",
                "generated_at": datetime.utcnow().isoformat(),
            },
        ]

    best_account = max(active_accounts, key=lambda row: float(row.get("engagement") or 0))
    return [
        {
            "category": "top_performer",
            "severity": "green",
            "title": f"{best_account.get('handle') or 'Top account'} leads engagement",
            "body": f"Current engagement is {best_account.get('engagement') or 0}% on {best_account.get('platform') or 'the latest scanned platform'}.",
            "generated_at": datetime.utcnow().isoformat(),
        },
        {
            "category": "coverage",
            "severity": "info",
            "title": f"{len(active_accounts)} accounts have activity in the latest scan",
            "body": "Use the matrix tab to compare which platforms are carrying the brand load before pushing new campaigns.",
            "generated_at": datetime.utcnow().isoformat(),
        },
    ]


def _engagement_pct(views: Any, likes: Any, comments: Any, shares: Any = 0) -> float:
    denom = float(views or 0)
    if denom <= 0:
        return 0.0
    numer = float(likes or 0) + float(comments or 0) + float(shares or 0)
    return round((numer / denom) * 100, 2)


def get_matrix() -> dict:
    overview = build_viltrox_overview()
    bundle = get_latest_viltrox_scan_bundle()
    posts = bundle.get("posts") or []
    last_post_by_account: dict[int, str] = {}
    for row in posts:
        account_id = int(row.get("account_id") or 0)
        published = str(row.get("published_at") or "")
        if published and published > last_post_by_account.get(account_id, ""):
            last_post_by_account[account_id] = published

    accounts = []
    for item in overview.get("accounts") or []:
        stats = dict(item.get("latest_stats") or {})
        account_id = int(item.get("id") or 0)
        views = int(stats.get("total_views") or 0)
        likes = int(stats.get("total_likes") or 0)
        comments = int(stats.get("total_comments") or 0)
        accounts.append(
            {
                "platform": item.get("platform") or "",
                "handle": item.get("handle") or "",
                "followers": 0,
                "followers_change_30d": 0,
                "posts": int(stats.get("total_posts") or 0),
                "views": views,
                "engagement": _engagement_pct(views, likes, comments),
                "last_post_at": last_post_by_account.get(account_id, ""),
                "engagement_trend": "stable",
                "name": item.get("name") or "",
                "latest_scan_status": item.get("latest_scan_status") or "not_scanned",
                "latest_error": item.get("latest_error") or "",
            }
        )
    return {"accounts": accounts}


def list_posts(
    *, account_handle: str | None = None, top_only: bool = False, limit: int = 50
) -> dict:
    bundle = get_latest_viltrox_scan_bundle()
    results = []
    for row in bundle.get("posts") or []:
        handle = str(row.get("handle") or "")
        views = int(row.get("views") or 0)
        likes = int(row.get("likes") or 0)
        comments = int(row.get("comments") or 0)
        shares = int(row.get("shares") or 0)
        engagement_pct = _engagement_pct(views, likes, comments, shares)
        item = {
            "platform": row.get("platform") or "",
            "account_handle": handle,
            "account_name": row.get("name") or "",
            "title": row.get("title") or "",
            "post_url": row.get("post_url") or "",
            "thumbnail_url": row.get("thumbnail_url") or "",
            "views": views,
            "likes": likes,
            "comments": comments,
            "shares": shares,
            "engagement_pct": engagement_pct,
            "posted_at": row.get("published_at") or "",
            "content_type": row.get("content_type") or "",
        }
        if account_handle and handle.lower() != account_handle.strip().lstrip("@").lower():
            continue
        if top_only and engagement_pct < 5:
            continue
        results.append(item)
    results.sort(key=lambda row: (str(row["posted_at"]), int(row["views"])), reverse=True)
    return {"posts": results[:limit]}


def list_insights() -> dict:
    conn = get_conn()
    rows = conn.execute(
        """SELECT * FROM ai_insights
           WHERE module='brand' AND category='brand_analysis'
             AND (expires_at IS NULL OR expires_at > datetime('now'))
             AND dismissed_at IS NULL
           ORDER BY generated_at DESC
           LIMIT 10"""
    ).fetchall()
    insights = [dict(r) for r in rows]
    if insights:
        return {"insights": insights}
    return {"insights": _fallback_insights_from_matrix(get_matrix().get("accounts") or [])}


async def regenerate_insights() -> dict:
    try:
        from app.services.ai.claude_client import get_claude_client
    except ImportError:
        return list_insights()

    matrix = get_matrix()
    posts = list_posts(limit=30)
    prompt = _brand_prompt(matrix, posts)
    try:
        client = get_claude_client()
        response = await asyncio.to_thread(
            lambda: call_ai_with_retry(
                "intelligence.brand.claude",
                lambda: client.messages.create(
                    model="claude-opus-4-7",
                    max_tokens=2048,
                    messages=[{"role": "user", "content": prompt}],
                ),
            )
        )
        parsed = json.loads(response.content[0].text)
    except Exception as exc:
        logger.exception("brand insight generation failed: %s", exc)
        return list_insights()

    conn = get_conn()
    conn.execute(
        "UPDATE ai_insights SET expires_at = datetime('now') "
        "WHERE module='brand' AND category='brand_analysis' AND expires_at IS NULL"
    )
    now = datetime.utcnow().isoformat()
    expires = (datetime.utcnow() + timedelta(days=7)).isoformat()
    for ins in parsed.get("insights", []):
        conn.execute(
            """INSERT INTO ai_insights (
                module, category, severity, insight_type, title, body,
                model_used, generated_at, expires_at
            ) VALUES ('brand', 'brand_analysis', ?, ?, ?, ?, ?, ?, ?)""",
            (
                ins.get("severity", "info"),
                ins.get("type", "analysis"),
                ins.get("title", "Untitled"),
                ins.get("body", ""),
                "claude-opus-4-7",
                now,
                expires,
            ),
        )
    conn.commit()
    return list_insights()


def _brand_prompt(matrix: dict[str, Any], posts: dict[str, Any]) -> str:
    return f"""Analyze Viltrox's official brand accounts.

ACCOUNT MATRIX:
{json.dumps(matrix, ensure_ascii=False, indent=2)}

RECENT POSTS:
{json.dumps(posts, ensure_ascii=False, indent=2)}

Return JSON only:
{{
  "insights": [
    {{
      "type": "engagement" | "top_performer" | "weak_spot",
      "severity": "info" | "amber" | "green",
      "title": "Short headline",
      "body": "Specific analysis with a recommendation."
    }}
  ]
}}
"""


_DEFAULT_VOICE = {
    "consistency_score": 8.4,
    "tone_keywords": ["technical", "approachable", "creator-first", "confident"],
    "avoid_keywords": ["luxury fluff", "generic hype", "competitor-bashing"],
    "framework_samples": {
        "product_launch": "Lead with creator use case, then lens specifics.",
        "review": "Ground claims in actual footage and tested bodies.",
        "community": "Speak like a creator, not a press release.",
        "support": "Technical but warm, and always action-oriented.",
    },
}


def get_voice_guidelines() -> dict:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT value FROM settings_kv WHERE key='brand_voice'"
        ).fetchone()
        if row and row["value"]:
            return json.loads(row["value"])
    except Exception as exc:
        logger.warning("brand voice guidelines fallback used: %s", exc)
    return _DEFAULT_VOICE


def update_voice_guidelines(new_voice: dict) -> dict:
    conn = get_conn()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS settings_kv (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT
        )"""
    )
    conn.execute(
        """INSERT INTO settings_kv (key, value, updated_at)
           VALUES ('brand_voice', ?, datetime('now'))
           ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
        (json.dumps(new_voice),),
    )
    conn.commit()
    return new_voice
