"""
backend/app/domains/comments/sentiment.py

P1.4: Multi-dimensional sentiment analysis service.

Routes comments through llm_gateway.invoke() to produce:
  - sentiment: positive/neutral/negative
  - emotion: joy/surprise/curiosity/frustration/anger/sadness/disgust/fear/neutral
  - brand_attitude: advocate/supportive/neutral/critical/hostile/irrelevant

Compatible with V-KPI ABCD/D infrastructure:
  - llm_gateway.invoke() for LLM (4 provider fallback)
  - Budget control (LLM_MONTHLY_BUDGET_USD)
  - audit log via record_call
  - Force offline smoke (VKPI_LLM_GATEWAY_FORCE_OFFLINE)
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Any

from app.db.connection import get_conn
from app.platform import llm_gateway


PROMPT_VERSION = "v1.0"

# Enum validation
SENTIMENT_VALUES = {"positive", "neutral", "negative"}
EMOTION_VALUES = {
    "joy", "surprise", "curiosity", "frustration",
    "anger", "sadness", "disgust", "fear", "neutral",
}
BRAND_ATTITUDE_VALUES = {
    "advocate", "supportive", "neutral",
    "critical", "hostile", "irrelevant",
}


PROMPT_TEMPLATE = """You are analyzing a social media comment for Viltrox, a camera lens brand.

Comment: "{comment_text}"
Comment language hint: {detected_language_or_unknown}
Context:
  - Platform: {platform}
  - Post type: {post_type}
  - About: {kol_or_topic_brief}

Analyze in 3 dimensions:

1. Sentiment (overall emotional tone):
   - positive / neutral / negative

2. Emotion (specific emotion):
   - joy / surprise / curiosity / frustration / anger / sadness / disgust / fear / neutral

3. Brand attitude (towards Viltrox/the lens brand):
   - advocate (actively recommends Viltrox / Viltrox-related products)
   - supportive (positive but doesn't recommend)
   - neutral (no brand stance)
   - critical (constructive criticism with specific issue)
   - hostile (hostile/negative without constructive direction)
   - irrelevant (comment unrelated to brand)

Respond in valid JSON only, no markdown, no preamble:
{{
  "sentiment": "positive",
  "sentiment_confidence": 0.92,
  "emotion": "joy",
  "emotion_confidence": 0.85,
  "brand_attitude": "advocate",
  "brand_attitude_confidence": 0.78,
  "language_detected": "en"
}}

Confidence values: 0.0 to 1.0.
Choose values from the provided enums only.
For unparseable or empty input, return all "neutral" with 0.5 confidence."""


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_vkpi_sentiment_schema() -> None:
    """Create P1.4 sentiment tables when migration runner has not applied them."""
    conn = get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vkpi_sentiment_results (
          id BIGSERIAL PRIMARY KEY,
          comment_id BIGINT NOT NULL,
          sentiment VARCHAR(20),
          sentiment_confidence NUMERIC(3,2),
          emotion VARCHAR(20),
          emotion_confidence NUMERIC(3,2),
          brand_attitude VARCHAR(20),
          brand_attitude_confidence NUMERIC(3,2),
          llm_provider VARCHAR(50),
          llm_model VARCHAR(100),
          prompt_version VARCHAR(20) NOT NULL,
          language_detected VARCHAR(10),
          input_tokens INT DEFAULT 0,
          output_tokens INT DEFAULT 0,
          cost_cents INT DEFAULT 0,
          analyzed_at TIMESTAMPTZ DEFAULT NOW(),
          CONSTRAINT vkpi_sentiment_uniq UNIQUE (comment_id, prompt_version)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_sentiment_comment ON vkpi_sentiment_results(comment_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_sentiment_brand ON vkpi_sentiment_results(brand_attitude, analyzed_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_sentiment_negative ON vkpi_sentiment_results(analyzed_at DESC) WHERE sentiment = 'negative'")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_sentiment_hostile ON vkpi_sentiment_results(analyzed_at DESC) WHERE brand_attitude = 'hostile'")
    conn.commit()


def _build_prompt(comment_text: str, *, platform: str = "unknown",
                  post_type: str = "post", kol_brief: str = "",
                  language_hint: str = "auto") -> str:
    """Build LLM prompt for sentiment analysis."""
    return PROMPT_TEMPLATE.format(
        comment_text=comment_text[:1500],  # cap to control cost
        detected_language_or_unknown=language_hint or "unknown",
        platform=platform,
        post_type=post_type,
        kol_or_topic_brief=kol_brief or "Viltrox lens / camera content",
    )


def _validate_response(parsed: dict) -> dict:
    """Validate LLM JSON response, fall back to neutral if invalid."""
    result = {
        "sentiment": "neutral",
        "sentiment_confidence": 0.5,
        "emotion": "neutral",
        "emotion_confidence": 0.5,
        "brand_attitude": "neutral",
        "brand_attitude_confidence": 0.5,
        "language_detected": parsed.get("language_detected") or "unknown",
    }
    
    # Sentiment
    val = parsed.get("sentiment", "").lower()
    if val in SENTIMENT_VALUES:
        result["sentiment"] = val
        try:
            conf = float(parsed.get("sentiment_confidence", 0.5))
            result["sentiment_confidence"] = max(0.0, min(1.0, conf))
        except (TypeError, ValueError):
            result["sentiment_confidence"] = 0.5
    
    # Emotion
    val = parsed.get("emotion", "").lower()
    if val in EMOTION_VALUES:
        result["emotion"] = val
        try:
            conf = float(parsed.get("emotion_confidence", 0.5))
            result["emotion_confidence"] = max(0.0, min(1.0, conf))
        except (TypeError, ValueError):
            result["emotion_confidence"] = 0.5
    
    # Brand attitude
    val = parsed.get("brand_attitude", "").lower()
    if val in BRAND_ATTITUDE_VALUES:
        result["brand_attitude"] = val
        try:
            conf = float(parsed.get("brand_attitude_confidence", 0.5))
            result["brand_attitude_confidence"] = max(0.0, min(1.0, conf))
        except (TypeError, ValueError):
            result["brand_attitude_confidence"] = 0.5
    
    # Language
    lang = (parsed.get("language_detected") or "").lower().strip()
    if lang and len(lang) <= 10:
        result["language_detected"] = lang
    
    return result


def _parse_llm_response(text: str) -> dict:
    """Parse LLM response, handling markdown wrappers."""
    if not text or not isinstance(text, str):
        return {}
    
    s = text.strip()
    
    # Strip markdown code blocks
    if s.startswith("```"):
        # Remove leading ```json or ```
        first_nl = s.find("\n")
        if first_nl > 0:
            s = s[first_nl + 1:]
        # Remove trailing ```
        if s.endswith("```"):
            s = s[:-3]
        s = s.strip()
    
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return {}


def analyze_comment(
    comment_id: int,
    *,
    force_reanalyze: bool = False,
    staff: dict | None = None,
) -> dict:
    """
    Analyze single comment for sentiment.
    
    Returns:
      {
        "comment_id": int,
        "sentiment": str,
        "emotion": str,
        "brand_attitude": str,
        "confidence": dict,
        "language_detected": str,
        "llm_provider": str,
        "status": "ok" / "skip" / "fail" / "duplicate",
      }
    """
    ensure_vkpi_sentiment_schema()
    conn = get_conn()
    
    # Check if already analyzed (unless forced)
    if not force_reanalyze:
        existing = conn.execute(
            "SELECT id FROM vkpi_sentiment_results WHERE comment_id=? AND prompt_version=?",
            (comment_id, PROMPT_VERSION),
        ).fetchone()
        if existing:
            return {
                "comment_id": comment_id,
                "status": "duplicate",
                "existing_id": existing["id"],
            }
    
    # Fetch comment
    comment = conn.execute(
        """
        SELECT id, comment_text, platform, language_detected, account_id, post_id
        FROM vkpi_comments WHERE id = ?
        """,
        (comment_id,),
    ).fetchone()
    
    if not comment:
        return {
            "comment_id": comment_id,
            "status": "fail",
            "error": "comment not found",
        }
    
    text = (comment["comment_text"] or "").strip()
    if not text:
        # Empty comment, default neutral
        return _persist_result(
            comment_id=comment_id,
            result={
                "sentiment": "neutral", "sentiment_confidence": 1.0,
                "emotion": "neutral", "emotion_confidence": 1.0,
                "brand_attitude": "irrelevant", "brand_attitude_confidence": 1.0,
                "language_detected": "unknown",
            },
            llm_provider="rule_v0",
            llm_model="default",
            input_tokens=0,
            output_tokens=0,
            cost_cents=0,
        )
    
    # Build prompt
    prompt = _build_prompt(
        text,
        platform=comment["platform"] or "unknown",
        post_type="comment",
        language_hint=comment["language_detected"] or "auto",
    )
    
    # Call LLM via gateway
    response = llm_gateway.invoke(
        prompt,
        purpose="vkpi_sentiment",
        max_output_tokens=200,
        preferred_provider=os.environ.get("VKPI_SENTIMENT_PREFERRED_PROVIDER") or None,
        staff=staff,
    )
    
    # Current llm_gateway returns status=success for provider calls and
    # status=fallback_to_rule when budget/offline gates block spend. The
    # fallback path is still a valid deterministic neutral result for smoke and
    # no-budget runs.
    response_status = str(response.get("status") or "")
    if response_status == "success":
        parsed = _parse_llm_response(response.get("text", ""))
        validated = _validate_response(parsed)
    elif response_status == "fallback_to_rule":
        validated = _validate_response({})
    else:
        return {
            "comment_id": comment_id,
            "status": "fail",
            "error": response.get("error") or response.get("reason") or "llm gateway failed",
            "provider": response.get("provider"),
        }
    
    return _persist_result(
        comment_id=comment_id,
        result=validated,
        llm_provider=response.get("provider", "unknown"),
        llm_model=response.get("model", "unknown"),
        input_tokens=response.get("input_tokens", 0),
        output_tokens=response.get("output_tokens", 0),
        cost_cents=response.get("cost_cents", 0),
    )


def _persist_result(
    *,
    comment_id: int,
    result: dict,
    llm_provider: str,
    llm_model: str,
    input_tokens: int,
    output_tokens: int,
    cost_cents: int,
) -> dict:
    """Write sentiment result to vkpi_sentiment_results."""
    ensure_vkpi_sentiment_schema()
    conn = get_conn()
    
    cursor = conn.execute(
        """
        INSERT INTO vkpi_sentiment_results (
          comment_id, sentiment, sentiment_confidence,
          emotion, emotion_confidence,
          brand_attitude, brand_attitude_confidence,
          llm_provider, llm_model, prompt_version, language_detected,
          input_tokens, output_tokens, cost_cents,
          analyzed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (comment_id, prompt_version) DO UPDATE SET
          sentiment = EXCLUDED.sentiment,
          sentiment_confidence = EXCLUDED.sentiment_confidence,
          emotion = EXCLUDED.emotion,
          emotion_confidence = EXCLUDED.emotion_confidence,
          brand_attitude = EXCLUDED.brand_attitude,
          brand_attitude_confidence = EXCLUDED.brand_attitude_confidence,
          analyzed_at = EXCLUDED.analyzed_at
        """,
        (
            comment_id,
            result["sentiment"], result["sentiment_confidence"],
            result["emotion"], result["emotion_confidence"],
            result["brand_attitude"], result["brand_attitude_confidence"],
            llm_provider, llm_model, PROMPT_VERSION, result["language_detected"],
            input_tokens, output_tokens, cost_cents,
            _now_iso(),
        ),
    )
    
    # Update vkpi_comments to link sentiment_id (for P1.6 weekly report join)
    sentiment_row = conn.execute(
        "SELECT id FROM vkpi_sentiment_results WHERE comment_id=? AND prompt_version=?",
        (comment_id, PROMPT_VERSION),
    ).fetchone()
    
    if sentiment_row:
        conn.execute(
            "UPDATE vkpi_comments SET sentiment_id=? WHERE id=?",
            (sentiment_row["id"], comment_id),
        )
    conn.commit()
    
    return {
        "comment_id": comment_id,
        "status": "ok",
        "sentiment": result["sentiment"],
        "emotion": result["emotion"],
        "brand_attitude": result["brand_attitude"],
        "confidence": {
            "sentiment": result["sentiment_confidence"],
            "emotion": result["emotion_confidence"],
            "brand_attitude": result["brand_attitude_confidence"],
        },
        "language_detected": result["language_detected"],
        "llm_provider": llm_provider,
        "cost_cents": cost_cents,
    }


def analyze_batch(
    comment_ids: list[int],
    *,
    staff: dict | None = None,
) -> dict:
    """Batch analyze multiple comments."""
    summary = {"total": len(comment_ids), "by_status": {}, "errors": []}
    
    for cid in comment_ids:
        try:
            result = analyze_comment(cid, staff=staff)
            status = result.get("status", "unknown")
            summary["by_status"][status] = summary["by_status"].get(status, 0) + 1
            if status == "fail":
                summary["errors"].append(result)
        except Exception as exc:
            summary["by_status"]["exception"] = summary["by_status"].get("exception", 0) + 1
            summary["errors"].append({"comment_id": cid, "error": str(exc)})
    
    return summary


def backfill_historical(
    *,
    platform: str = "",
    days: int = 30,
    limit: int = 1000,
    staff: dict | None = None,
) -> dict:
    """Backfill sentiment for comments without analysis."""
    ensure_vkpi_sentiment_schema()
    conn = get_conn()
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    where = ["c.fetched_at >= ?", "s.id IS NULL"]
    params: list = [cutoff]
    if platform:
        where.append("c.platform = ?")
        params.append(platform)
    
    where_sql = " AND ".join(where)
    rows = conn.execute(
        f"""
        SELECT c.id
        FROM vkpi_comments c
        LEFT JOIN vkpi_sentiment_results s
          ON s.comment_id = c.id AND s.prompt_version = ?
        WHERE {where_sql}
        ORDER BY c.fetched_at DESC
        LIMIT ?
        """,
        (PROMPT_VERSION, *params, limit),
    ).fetchall()
    
    comment_ids = [r["id"] for r in rows]
    return analyze_batch(comment_ids, staff=staff)


def stats(*, days: int = 30) -> dict:
    """Sentiment analysis statistics."""
    ensure_vkpi_sentiment_schema()
    conn = get_conn()
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    by_sentiment = conn.execute(
        """
        SELECT sentiment, COUNT(*) as n
        FROM vkpi_sentiment_results
        WHERE analyzed_at >= ?
        GROUP BY sentiment
        """,
        (cutoff,),
    ).fetchall()
    
    by_brand_attitude = conn.execute(
        """
        SELECT brand_attitude, COUNT(*) as n
        FROM vkpi_sentiment_results
        WHERE analyzed_at >= ?
        GROUP BY brand_attitude
        """,
        (cutoff,),
    ).fetchall()
    
    cost = conn.execute(
        """
        SELECT 
          SUM(cost_cents) as total_cents,
          SUM(input_tokens) as total_input_tokens,
          SUM(output_tokens) as total_output_tokens,
          COUNT(*) as analyses
        FROM vkpi_sentiment_results
        WHERE analyzed_at >= ?
        """,
        (cutoff,),
    ).fetchone()
    
    return {
        "days": days,
        "by_sentiment": [dict(r) for r in by_sentiment],
        "by_brand_attitude": [dict(r) for r in by_brand_attitude],
        "cost_cents": cost["total_cents"] if cost else 0,
        "total_analyses": cost["analyses"] if cost else 0,
    }
