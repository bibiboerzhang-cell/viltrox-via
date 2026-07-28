"""
backend/app/domains/comments/sentiment.py

P1.4: Multi-dimensional sentiment analysis service.

Routes comments through the exact-model ``llm_production`` boundary to produce:
  - sentiment: positive/neutral/negative
  - emotion: joy/surprise/curiosity/frustration/anger/sadness/disgust/fear/neutral
  - brand_attitude: advocate/supportive/neutral/critical/hostile/irrelevant

Compatible with V-KPI ABCD/D infrastructure:
  - one exact provider/model per call (no silent cross-model fallback)
  - strict JSON contract before persistence
  - Budget control (LLM_MONTHLY_BUDGET_USD)
  - audit log via record_call
  - Force offline smoke (VKPI_LLM_GATEWAY_FORCE_OFFLINE)
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.config import CLAUDE_MODEL, GEMINI_MODEL, OPENAI_MODEL
from app.db.connection import get_conn, is_postgres_runtime
from app.platform import llm_production


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
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_vkpi_sentiment_schema() -> None:
    """Create sentiment tables for the local SQLite fallback.

    PostgreSQL tables and indexes are owned by migration 051. Runtime schema
    changes are intentionally forbidden because they take relation locks.
    """
    if is_postgres_runtime():
        return

    conn = get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vkpi_sentiment_results (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          comment_id INTEGER NOT NULL,
          sentiment TEXT,
          sentiment_confidence NUMERIC,
          emotion TEXT,
          emotion_confidence NUMERIC,
          brand_attitude TEXT,
          brand_attitude_confidence NUMERIC,
          llm_provider TEXT,
          llm_model TEXT,
          prompt_version TEXT NOT NULL,
          language_detected TEXT,
          input_tokens INTEGER DEFAULT 0,
          output_tokens INTEGER DEFAULT 0,
          cost_cents INTEGER DEFAULT 0,
          analyzed_at TEXT DEFAULT CURRENT_TIMESTAMP,
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


def _sentiment_llm_binding() -> tuple[str, str]:
    """Resolve one exact configured binding without an implicit provider chain."""
    explicit_provider = str(os.environ.get("VKPI_SENTIMENT_PREFERRED_PROVIDER") or "").strip()
    provider = (explicit_provider or "openai").lower()
    provider = {"gemini": "google", "claude": "anthropic"}.get(provider, provider)
    models = {
        "openai": os.environ.get("VKPI_OPENAI_MODEL") or OPENAI_MODEL,
        "google": os.environ.get("VKPI_GEMINI_MODEL") or GEMINI_MODEL,
        "anthropic": os.environ.get("VKPI_CLAUDE_MODEL") or CLAUDE_MODEL,
    }
    if provider not in models:
        raise ValueError("binding_invalid")
    return provider, str(models[provider]).strip()


def _confidence_value(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and 0.0 <= float(value) <= 1.0
    )


def _valid_sentiment_payload(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if str(value.get("sentiment") or "").lower() not in SENTIMENT_VALUES:
        return False
    if str(value.get("emotion") or "").lower() not in EMOTION_VALUES:
        return False
    if str(value.get("brand_attitude") or "").lower() not in BRAND_ATTITUDE_VALUES:
        return False
    if not all(
        _confidence_value(value.get(key))
        for key in (
            "sentiment_confidence",
            "emotion_confidence",
            "brand_attitude_confidence",
        )
    ):
        return False
    language = value.get("language_detected")
    return isinstance(language, str) and 1 <= len(language.strip()) <= 10


def _run_sentiment_llm(
    prompt: str,
    *,
    comment_id: int,
    staff: dict | None,
) -> dict[str, Any]:
    """Run the governed call; a blocked/invalid result becomes rule_v0 neutral."""
    try:
        provider, model = _sentiment_llm_binding()
    except ValueError:
        return {
            "status": "fallback_to_rule",
            "provider": "rule_v0",
            "model": "rule_v0",
            "json": None,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_cents": 0,
            "reason": "binding_invalid",
            "source_status": "binding_invalid",
            "requested_provider": str(
                os.environ.get("VKPI_SENTIMENT_PREFERRED_PROVIDER") or ""
            ).strip().lower()[:80],
            "requested_model": "",
        }
    try:
        response = llm_production.generate_json(
            prompt,
            provider=provider,
            model=model,
            purpose="vkpi_sentiment",
            max_output_tokens=200,
            cost_tag="vkpi_sentiment",
            triggered_by="comments.sentiment.analyze_comment",
            staff=staff,
            required_keys=(
                "sentiment",
                "sentiment_confidence",
                "emotion",
                "emotion_confidence",
                "brand_attitude",
                "brand_attitude_confidence",
                "language_detected",
            ),
            validator=_valid_sentiment_payload,
            deadline_seconds=45.0,
            metadata={
                "surface": "comment_intelligence",
                "comment_id": int(comment_id),
                "phase": "comment_intelligence",
                "subphase": "sentiment",
                "attempt_index": 1,
                "total": 1,
                "target_label": f"comment:{int(comment_id)}",
            },
        )
    except Exception as exc:
        response = {
            "status": "exception",
            "reason": f"{type(exc).__name__}: {str(exc)[:180]}",
        }
    payload = response.get("json") if isinstance(response, dict) else None
    status = str(response.get("status") or "")
    actual_provider = str(response.get("provider") or "").strip().lower()
    actual_model = str(response.get("model") or "").strip()
    if status == "success" and actual_provider == provider and actual_model == model and _valid_sentiment_payload(payload):
        return response
    failure = response.get("failure") if isinstance(response.get("failure"), dict) else {}
    if status == "success" and actual_provider != provider:
        reason = "exact_provider_mismatch"
    elif status == "success" and actual_model != model:
        reason = "exact_model_mismatch"
    elif status == "success":
        reason = "response_contract_invalid"
    else:
        reason = str(
            failure.get("code")
            or response.get("failure_code")
            or response.get("reason")
            or status
            or "llm_unavailable"
        )[:180]
    return {
        "status": "fallback_to_rule",
        "provider": "rule_v0",
        "model": "rule_v0",
        "json": None,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_cents": 0,
        "reason": reason,
        "source_status": status or "invalid_result",
        "requested_provider": provider,
        "requested_model": model,
    }


def _degraded_sentiment_result(comment_id: int, response: dict[str, Any]) -> dict[str, Any]:
    """Return an honest, retryable neutral view without writing an analysis row."""
    fallback = _validate_response({})
    return {
        "comment_id": int(comment_id),
        "status": "degraded",
        "method": "deterministic_fallback",
        "persisted": False,
        "retryable": True,
        "reason": str(response.get("reason") or "llm_unavailable")[:180],
        "sentiment": fallback["sentiment"],
        "emotion": fallback["emotion"],
        "brand_attitude": fallback["brand_attitude"],
        "confidence": {
            "sentiment": fallback["sentiment_confidence"],
            "emotion": fallback["emotion_confidence"],
            "brand_attitude": fallback["brand_attitude_confidence"],
        },
        "language_detected": fallback["language_detected"],
        "llm_provider": "rule_v0",
        "llm_model": "rule_v0",
        "cost_cents": 0,
    }


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
        "status": "ok" / "degraded" / "fail" / "duplicate",
      }
    """
    ensure_vkpi_sentiment_schema()
    conn = get_conn()
    
    # Check if already analyzed (unless forced)
    if not force_reanalyze:
        existing = conn.execute(
            "SELECT id, llm_provider, llm_model FROM vkpi_sentiment_results "
            "WHERE comment_id=? AND prompt_version=?",
            (comment_id, PROMPT_VERSION),
        ).fetchone()
        # Older releases persisted AI-off neutral placeholders as rule_v0/rule_v0.
        # They are not a completed analysis and must not block a later real model.
        try:
            existing_provider = str(existing["llm_provider"] or "").strip().lower() if existing else ""
            existing_model = str(existing["llm_model"] or "").strip().lower() if existing else ""
        except (KeyError, IndexError, TypeError):
            existing_provider = existing_model = ""
        retryable_placeholder = bool(
            existing and existing_provider == "rule_v0" and existing_model == "rule_v0"
        )
        if existing and not retryable_placeholder:
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
    
    response = _run_sentiment_llm(prompt, comment_id=comment_id, staff=staff)

    # AI-off/readiness/budget/contract failures retain the deterministic neutral
    # result instead of pretending a model completed or switching models.
    response_status = str(response.get("status") or "")
    if response_status == "success":
        parsed = response.get("json") if isinstance(response.get("json"), dict) else {}
        validated = _validate_response(parsed)
    elif response_status == "fallback_to_rule":
        return _degraded_sentiment_result(comment_id, response)
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
          llm_provider = EXCLUDED.llm_provider,
          llm_model = EXCLUDED.llm_model,
          language_detected = EXCLUDED.language_detected,
          input_tokens = EXCLUDED.input_tokens,
          output_tokens = EXCLUDED.output_tokens,
          cost_cents = EXCLUDED.cost_cents,
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
            if not isinstance(result, dict):
                result = {
                    "comment_id": cid,
                    "status": "unknown",
                    "error": "sentiment analysis returned a non-object result",
                }
            status = str(result.get("status") or "unknown").strip().lower()
            summary["by_status"][status] = summary["by_status"].get(status, 0) + 1
            if status not in {"ok", "duplicate"}:
                error_item = dict(result)
                error_item.setdefault("comment_id", cid)
                error_item["status"] = status
                error_item.setdefault(
                    "error",
                    str(error_item.get("reason") or f"sentiment analysis {status}"),
                )
                summary["errors"].append(error_item)
        except Exception as exc:
            summary["by_status"]["exception"] = summary["by_status"].get("exception", 0) + 1
            summary["errors"].append(
                {"comment_id": cid, "status": "exception", "error": str(exc)}
            )
    
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
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    where = [
        "c.fetched_at >= ?",
        "(s.id IS NULL OR (COALESCE(s.llm_provider, '') = 'rule_v0' "
        "AND COALESCE(s.llm_model, '') = 'rule_v0'))",
    ]
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
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    
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
