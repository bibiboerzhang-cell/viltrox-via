"""services/kol/content_scorer.py — KOL content scoring."""
from __future__ import annotations

import json

from app.core.config import CLAUDE_MODEL
from app.core.logging import get_logger
from app.db.connection import get_conn
from app.services.ai.retry import call_ai_with_retry

logger = get_logger(__name__)

try:
    from app.services.ai.clients.claude_client import get_claude_client
except Exception:
    def get_claude_client():
        return None


def _clean_json_text(text: str) -> str:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
        cleaned = cleaned.rsplit("```", 1)[0]
    return cleaned.strip()


def _metrics_score(row) -> dict:
    views = int(row["views"] or 0)
    likes = int(row["likes"] or 0)
    comments = int(row["comments"] or 0)
    shares = int(row["shares"] or 0)
    engagement = 0 if views <= 0 else (likes + comments + shares) / views
    score = max(0, min(100, int(engagement * 1000) + min(30, views // 1000)))
    return {
        "score": score,
        "summary_zh": "未配置 Claude，当前为基于播放和互动数据的后备评分。",
        "summary_en": "Claude is not configured; this is a metrics fallback score.",
        "topics": ["metrics_fallback", str(row["platform"] or "unknown")],
        "method": "metrics_fallback",
    }


def _score_with_claude(content: dict, campaign: dict | None = None, kol: dict | None = None) -> dict:
    client = get_claude_client()
    if client is None:
        return _metrics_score(content)
    prompt = f"""
You are scoring KOL campaign content for Viltrox.

Return ONLY valid JSON with:
{{
  "score": 0-100,
  "summary_zh": "one concise Chinese summary",
  "summary_en": "one concise English summary",
  "topics": ["3-6 topic tags"],
  "method": "claude"
}}

KOL:
{json.dumps(kol or {}, ensure_ascii=False)[:1200]}

Campaign:
{json.dumps(campaign or {}, ensure_ascii=False)[:1200]}

Content:
{json.dumps(content, ensure_ascii=False)[:2000]}

Score for relevance to the product, audience fit, authenticity, creative quality inferred from URL/title/metrics, and conversion potential.
"""
    try:
        resp = call_ai_with_retry(
            "kol_content_scorer.claude",
            lambda: client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=900,
                messages=[{"role": "user", "content": prompt}],
            ),
        )
        text = resp.content[0].text if resp.content else ""
        parsed = json.loads(_clean_json_text(text))
        if not isinstance(parsed, dict):
            raise ValueError("Claude returned non-object JSON")
        parsed["score"] = max(0, min(100, int(parsed.get("score") or 0)))
        parsed["method"] = "claude"
        return parsed
    except Exception:
        logger.warning("kol_content_scorer.claude_failed", exc_info=True)
        result = _metrics_score(content)
        result["method"] = "metrics_fallback_after_claude_error"
        return result


async def score_kol_content(content_id: int) -> dict:
    """Score a KOL content row and persist Claude output when configured."""
    conn = get_conn()
    row = conn.execute(
        """
        SELECT co.*, ca.product_sku, ca.notes AS campaign_notes, k.channel_name, k.niche, k.country
        FROM kol_content co
        LEFT JOIN kol_campaigns ca ON ca.id = co.campaign_id
        LEFT JOIN kols k ON k.id = ca.kol_id
        WHERE co.id = ?
        """,
        (int(content_id),),
    ).fetchone()
    if not row:
        raise ValueError("content not found")
    content = dict(row)
    campaign = {
        "product_sku": content.get("product_sku"),
        "notes": content.get("campaign_notes"),
    }
    kol = {
        "channel_name": content.get("channel_name"),
        "niche": content.get("niche"),
        "country": content.get("country"),
    }
    scored = _score_with_claude(content, campaign, kol)
    score = int(scored.get("score") or 0)
    summary = str(scored.get("summary_zh") or scored.get("summary_en") or "")
    topics = scored.get("topics") if isinstance(scored.get("topics"), list) else []
    conn.execute(
        """
        UPDATE kol_content
        SET ai_quality_score = ?, ai_summary = ?, ai_topics_json = ?
        WHERE id = ?
        """,
        (score, summary, json.dumps(topics), int(content_id)),
    )
    conn.commit()
    return {
        "content_id": int(content_id),
        "quality_score": score,
        "summary": summary,
        "topics": topics,
        "method": scored.get("method", "unknown"),
    }
