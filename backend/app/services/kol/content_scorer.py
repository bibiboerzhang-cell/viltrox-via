"""services/kol/content_scorer.py — KOL content scoring."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.config import CLAUDE_MODEL
from app.core.logging import get_logger
from app.db.connection import get_conn
from app.platform import llm_production

logger = get_logger(__name__)

SCORE_SCHEMA_VERSION = "kol_content_score_v2"


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if value is None or (isinstance(value, str) and not value.strip()):
        return {}
    if not isinstance(value, str):
        raise TypeError("ai_analysis_json must be a JSON object or empty")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("ai_analysis_json contains invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("ai_analysis_json must contain a JSON object")
    return dict(parsed)


def _metrics_score(row) -> dict:
    views = int(row["views"] or 0)
    likes = int(row["likes"] or 0)
    comments = int(row["comments"] or 0)
    shares = int(row["shares"] or 0)
    engagement = 0 if views <= 0 else (likes + comments + shares) / views
    score = max(0, min(100, int(engagement * 1000) + min(30, views // 1000)))
    return {
        "score": score,
        "summary_zh": "AI 生产调用当前不可用，这是基于播放和互动数据的后备评分。",
        "summary_en": "Production AI is unavailable; this is a metrics fallback score.",
        "topics": ["metrics_fallback", str(row["platform"] or "unknown")],
        "method": "metrics_fallback",
        "provenance": {
            "source_type": "deterministic_rule",
            "provider": "rule_v0",
            "model": "metrics_v1",
            "method": "metrics_fallback",
            "fallback": True,
            "input_fields": ["views", "likes", "comments", "shares", "platform"],
        },
    }


def _valid_llm_score(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    score = value.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return False
    if not 0 <= float(score) <= 100:
        return False
    if str(value.get("method") or "").strip().lower() != "claude":
        return False
    for key in ("summary_zh", "summary_en"):
        text = value.get(key)
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > 1200:
            return False
    topics = value.get("topics")
    if not isinstance(topics, list) or not 3 <= len(topics) <= 6:
        return False
    return all(
        isinstance(topic, str) and 0 < len(topic.strip()) <= 80
        for topic in topics
    )


def _score_with_claude(content: dict, campaign: dict | None = None, kol: dict | None = None) -> dict:
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
    response: dict[str, Any] = {}
    try:
        response = llm_production.generate_json(
            prompt,
            provider="anthropic",
            model=CLAUDE_MODEL,
            purpose="kol_content_scorer",
            max_output_tokens=900,
            cost_tag="kol_content_scorer",
            triggered_by="kol.content_scorer.score_kol_content",
            required_keys=("score", "summary_zh", "summary_en", "topics", "method"),
            validator=_valid_llm_score,
            deadline_seconds=45.0,
            metadata={
                "surface": "kol_content_scorer",
                "content_id": int(content.get("id") or 0),
                "phase": "evaluation",
                "subphase": "content_score",
                "attempt_index": 1,
                "total": 1,
                "target_label": f"content:{int(content.get('id') or 0)}",
            },
        )
        parsed = response.get("json") if isinstance(response, dict) else None
        actual_provider = str(response.get("provider") or "").strip().lower()
        actual_model = str(response.get("model") or "").strip()
        if (
            str(response.get("status") or "") != "success"
            or actual_provider != "anthropic"
            or actual_model != CLAUDE_MODEL
            or not _valid_llm_score(parsed)
        ):
            raise ValueError("production_response_contract_invalid")
        parsed = dict(parsed)
        parsed["score"] = max(0, min(100, int(round(float(parsed["score"])))))
        parsed["method"] = "claude"
        parsed["provenance"] = {
            "source_type": "llm",
            "provider": actual_provider,
            "model": actual_model,
            "method": "claude",
            "purpose": "kol_content_scorer",
            "fallback": False,
        }
        return parsed
    except Exception:
        logger.warning("kol_content_scorer.claude_failed", exc_info=True)
        result = _metrics_score(content)
        result["method"] = "metrics_fallback_after_claude_error"
        result["provenance"]["method"] = result["method"]
        failure = response.get("failure") if isinstance(response.get("failure"), dict) else {}
        failure_reason = (
            "response_contract_invalid"
            if str(response.get("status") or "") == "success"
            else str(
                failure.get("code")
                or response.get("failure_code")
                or response.get("reason")
                or response.get("status")
                or "production_llm_unavailable"
            )[:180]
        )
        result["provenance"]["failure_reason"] = failure_reason
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
    method = str(scored.get("method") or "unknown")
    provenance = scored.get("provenance") if isinstance(scored.get("provenance"), dict) else {}
    llm_result = method == "claude" and provenance.get("source_type") == "llm"
    analysis_json = _json_object(content.get("ai_analysis_json"))
    analysis_json["content_score"] = {
        "schema_version": SCORE_SCHEMA_VERSION,
        "score": score,
        "summary": summary,
        "topics": topics,
        "method": method,
        "provenance": provenance,
        "generated_at": _utcnow(),
        "persisted_to_ai_fields": llm_result,
    }
    if llm_result:
        conn.execute(
            """
            UPDATE kol_content
            SET ai_quality_score = ?, ai_summary = ?, ai_topics_json = ?, ai_analysis_json = ?
            WHERE id = ?
            """,
            (
                score,
                summary,
                json.dumps(topics, ensure_ascii=False),
                json.dumps(analysis_json, ensure_ascii=False),
                int(content_id),
            ),
        )
    else:
        # Metrics are useful as an immediate fallback, but they are not an AI
        # score. Persist only the explicit provenance envelope and preserve any
        # prior verified ai_* values rather than overwriting them with rule_v0.
        conn.execute(
            "UPDATE kol_content SET ai_analysis_json = ? WHERE id = ?",
            (json.dumps(analysis_json, ensure_ascii=False), int(content_id)),
        )
    conn.commit()
    return {
        "content_id": int(content_id),
        "quality_score": score,
        "summary": summary,
        "topics": topics,
        "method": method,
        "provenance": provenance,
        "persisted_to_ai_fields": llm_result,
        "quality_score_status": "ready" if llm_result else "fallback_not_persisted_as_ai",
    }
