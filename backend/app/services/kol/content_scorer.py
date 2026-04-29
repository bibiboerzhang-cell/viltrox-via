"""
services/kol/content_scorer.py — Phase A Claude-backed pseudo scoring.
"""
from __future__ import annotations

import json

from app.db.connection import get_conn


async def score_kol_content(content_id: int) -> dict:
    """
    Score a KOL content row and persist a Phase A deterministic placeholder.

    The Claude prompt path will replace the placeholder once provider usage
    logging is wired into all AI clients.
    """
    conn = get_conn()
    row = conn.execute("SELECT * FROM kol_content WHERE id = ?", (int(content_id),)).fetchone()
    if not row:
        raise ValueError("content not found")
    views = int(row["views"] or 0)
    likes = int(row["likes"] or 0)
    comments = int(row["comments"] or 0)
    shares = int(row["shares"] or 0)
    engagement = 0 if views <= 0 else (likes + comments + shares) / views
    score = max(0, min(100, int(engagement * 1000) + min(30, views // 1000)))
    summary = "Phase A score based on supplied metrics. Claude scoring is reserved for the async worker path."
    topics = ["phase-a", str(row["platform"] or "unknown")]
    conn.execute(
        """
        UPDATE kol_content
        SET ai_quality_score = ?, ai_summary = ?, ai_topics_json = ?
        WHERE id = ?
        """,
        (score, summary, json.dumps(topics), int(content_id)),
    )
    conn.commit()
    return {"content_id": int(content_id), "quality_score": score, "summary": summary, "topics": topics}
