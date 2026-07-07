"""Evidence helpers for read-only KOL intelligence cards."""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.domains.kol import competitor_detector as kol_competitor_detector
from app.domains.kol import eleven_dimensions
from app.domains.market import brand_signal_detector
from app.db.connection import get_conn
from app.domains.comments import intelligence as comment_intelligence

logger = get_logger(__name__)

VIDEO_ANALYSIS_FIELD_KEYS = (
    "target_audience",
    "production_quality",
    "quality_scores",
    "quality_overall",
    "quality_summary",
    "competitor_products",
    "brand_integration_depth",
    "marketing_potential",
    "reference_value",
    "timestamps",
    "improvements",
    "content_genre",
    "content_topic",
    "content_summary",
    "products_found",
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value or "").replace(",", "").strip()))
    except (TypeError, ValueError):
        return int(default or 0)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value or "").replace(",", "").strip())
    except (TypeError, ValueError):
        return float(default or 0.0)


from app.core.coerce import _text


def _loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(str(value or ""))
    except Exception:
        logger.debug("Failed to decode intelligence-card JSON payload", exc_info=True)
        return fallback
    return parsed if parsed is not None else fallback


def _as_list(value: Any) -> list[Any]:
    parsed = _loads(value, [])
    return parsed if isinstance(parsed, list) else []


def _brief_list(value: Any, *, limit: int = 8) -> list[Any]:
    rows = _as_list(value)
    return rows[: max(0, min(50, int(limit or 8)))]


def _status_payload(status: str, *, reason: str = "", error: Exception | None = None, **extra: Any) -> dict[str, Any]:
    payload = {"status": status, **extra}
    if reason:
        payload["reason"] = reason
    if error is not None:
        payload["error"] = f"{type(error).__name__}: {str(error)[:240]}"
    return payload


def _table_exists(table_name: str) -> bool:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        if row:
            return True
    except Exception:
        logger.debug("Postgres table lookup failed for %s; trying sqlite fallback", table_name, exc_info=True)
    try:
        row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone()
        return bool(row)
    except Exception:
        return False


def _item_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _safe_int(item.get("id")),
        "pool_uid": _text(item.get("pool_uid")),
        "platform": _text(item.get("platform")).lower(),
        "handle": _text(item.get("handle")),
        "profile_url": _text(item.get("profile_url")),
        "display_name": _text(item.get("display_name")),
        "avatar_url": _text(item.get("avatar_url")),
        "bio": _text(item.get("bio")),
        "country": _text(item.get("country")),
        "email": _text(item.get("email")),
        "followers": _safe_int(item.get("followers")),
        "posts_count": _safe_int(item.get("posts_count")),
        "avg_views": _safe_int(item.get("avg_views")),
        "avg_likes": _safe_int(item.get("avg_likes")),
        "avg_comments": _safe_int(item.get("avg_comments")),
        "engagement_rate": _safe_float(item.get("engagement_rate")),
        "primary_topic": _text(item.get("primary_topic")),
        "content_style": _text(item.get("content_style")),
        "viltrox_fit_score": _safe_int(item.get("viltrox_fit_score")),
        "viltrox_fit_reason": _text(item.get("viltrox_fit_reason")),
        "linked_main_kol_id": item.get("linked_main_kol_id"),
        "source_type": _text(item.get("source_type")),
        "source_ref": _text(item.get("source_ref")),
        "sync_status": _text(item.get("sync_status")),
        "last_seen_at": _text(item.get("last_seen_at")),
        "updated_at": _text(item.get("updated_at")),
    }


def _dimensions11(kol_pool_id: int) -> dict[str, Any]:
    try:
        payload = eleven_dimensions.compose_dimensions_11(kol_pool_id)
    except Exception as exc:
        return _status_payload("unavailable", reason="dimensions11_unavailable", error=exc)
    blocks = {
        "block1_content": payload.get("block1_content"),
        "block2_performance": payload.get("block2_performance"),
        "block3_business": payload.get("block3_business"),
        "block4_specialty": payload.get("block4_specialty"),
    }
    return {
        "status": "ready",
        "method": payload.get("method"),
        "overall_score": _safe_int(payload.get("overall_score")),
        "confidence": payload.get("confidence") if isinstance(payload.get("confidence"), dict) else {},
        "blocks": blocks,
        "provider_calls": False,
        "llm_calls": False,
    }


def _evidence_key(value: Any) -> str:
    key = "".join(ch.lower() if ch.isalnum() else "_" for ch in _text(value))
    return "_".join(part for part in key.split("_") if part)[:80] or "unknown"


def _competitor_source_table(relation: dict[str, Any]) -> str:
    source = _text(relation.get("source"))
    if source == "vkpi_competitor_relation":
        return "vkpi_competitor_relation"
    return "vkpi_kol_pool"


def _competitor_reasoning(relation: dict[str, Any], evidence: dict[str, Any] | None = None) -> str:
    if evidence:
        detail = _text(evidence.get("evidence") or evidence.get("description"))
        title = _text(evidence.get("title"))
        if detail:
            return detail
        if title:
            return title
    brand = _text(relation.get("competitor_brand") or "competitor")
    depth = _text(relation.get("collaboration_depth") or "none")
    sentiment = _text(relation.get("sentiment") or "neutral")
    total = _safe_int(relation.get("collaboration_count_total"))
    count_90d = _safe_int(relation.get("collaboration_count_90d"))
    return f"{brand} relation detected by rule_v0: depth={depth}, sentiment={sentiment}, 90d={count_90d}, total={total}."


def _competitor_confidence(relation: dict[str, Any], *, has_leaf_evidence: bool) -> float:
    score = _safe_float(relation.get("risk_score"))
    if score > 0:
        return round(min(1.0, max(0.2, score / 10.0)), 2)
    return 0.35 if has_leaf_evidence else 0.0


def _competitor_evidence_rows(relations: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for relation_index, relation in enumerate(relations[:24]):
        if not isinstance(relation, dict):
            continue
        brand = _text(relation.get("competitor_brand") or relation.get("brand") or "competitor")
        if not brand:
            continue
        evidence_items = relation.get("evidence") if isinstance(relation.get("evidence"), list) else []
        source_table = _competitor_source_table(relation)
        base = {
            "source": "competitor_signal",
            "source_table": source_table,
            "source_id": relation.get("competitor_brand") or brand,
            "confidence_method": "rule_v0",
            "rebuttal_supported": True,
            "competitor_brand": brand,
            "risk_tier": _text(relation.get("risk_tier") or "unknown"),
            "risk_score": _safe_float(relation.get("risk_score")),
            "collaboration_depth": _text(relation.get("collaboration_depth") or "none"),
            "collaboration_recency_days": relation.get("collaboration_recency_days"),
            "collaboration_count_90d": _safe_int(relation.get("collaboration_count_90d")),
            "collaboration_count_total": _safe_int(relation.get("collaboration_count_total")),
            "sentiment": _text(relation.get("sentiment") or "neutral"),
            "platform": _text(relation.get("platform")),
            "handle": _text(relation.get("handle")),
            "computed_at": _text(relation.get("computed_at")),
            "last_evidence_at": _text(relation.get("last_evidence_at")),
        }
        if evidence_items:
            for evidence_index, evidence in enumerate(evidence_items[:6]):
                if not isinstance(evidence, dict):
                    continue
                rows.append(
                    {
                        **base,
                        "evidence_id": f"ev_competitor_{_evidence_key(brand)}_{relation_index + 1}_{evidence_index + 1}",
                        "source_url": _text(evidence.get("url") or evidence.get("post_url") or evidence.get("source_url")),
                        "confidence": _competitor_confidence(relation, has_leaf_evidence=True),
                        "reasoning": _competitor_reasoning(relation, evidence),
                        "raw_data_ref": f"{source_table}:{relation.get('kol_pool_id') or ''}:{brand}",
                        "post_uid": _text(evidence.get("post_uid")),
                        "title": _text(evidence.get("title")),
                        "published_at": _text(evidence.get("published_at")),
                        "matched_keywords": evidence.get("matched_keywords") if isinstance(evidence.get("matched_keywords"), list) else [],
                    }
                )
        elif _safe_int(relation.get("collaboration_count_total")) or _safe_float(relation.get("risk_score")) > 0:
            rows.append(
                {
                    **base,
                    "evidence_id": f"ev_competitor_{_evidence_key(brand)}_{relation_index + 1}",
                    "source_url": "",
                    "confidence": _competitor_confidence(relation, has_leaf_evidence=False),
                    "reasoning": _competitor_reasoning(relation),
                    "raw_data_ref": f"{source_table}:{relation.get('kol_pool_id') or ''}:{brand}",
                }
            )
    return rows[:24]


def _competitors(kol_pool_id: int) -> dict[str, Any]:
    try:
        payload = kol_competitor_detector.evaluate_kol_competitors(kol_pool_id, prefer_persisted=True)
    except Exception as exc:
        return _status_payload("unavailable", reason="competitor_relation_unavailable", error=exc)
    relations = payload.get("relations") if isinstance(payload.get("relations"), list) else []
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    tier_counts = Counter(_text(item.get("risk_tier")) or "unknown" for item in relations if isinstance(item, dict))
    evidence = _competitor_evidence_rows(relations)
    return {
        "status": "ready" if relations else "empty",
        "summary": summary,
        "relations": relations,
        "tier_counts": dict(tier_counts),
        "evidence": evidence,
        "evidence_count": len(evidence),
        "provider_calls": False,
        "write_db": bool(payload.get("write_db")),
        "source": "vkpi_competitor_relation_or_cached_raw",
    }


def _cached_posts(item: dict[str, Any], *, limit: int = 50) -> list[dict[str, Any]]:
    raw = _loads(item.get("raw_platform_data"), {})
    posts = [
        kol_competitor_detector._row_profile_post(item),  # local cached profile fields
        *kol_competitor_detector._extract_posts(raw if isinstance(raw, dict) else {}),
    ]
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, post in enumerate(posts):
        if not isinstance(post, dict):
            continue
        uid = _text(post.get("id") or post.get("post_uid") or post.get("shortCode") or post.get("url") or post.get("title"))
        if not uid:
            uid = f"cached:{index}"
        if uid in seen:
            continue
        seen.add(uid)
        result.append(post)
        if len(result) >= max(1, min(200, int(limit or 50))):
            break
    return result


def _brand_signals(item: dict[str, Any]) -> dict[str, Any]:
    posts = _cached_posts(item, limit=50)
    context = {
        "kol_entity_uid": f"kol_pool:{_safe_int(item.get('id'))}",
        "source_table": "vkpi_kol_pool",
        "source_id": _safe_int(item.get("id")),
        "platform": _text(item.get("platform")),
    }
    signals: dict[str, dict[str, Any]] = {}
    for post in posts:
        for signal in brand_signal_detector.detect_viltrox_signals(post, context=context):
            uid = _text(signal.get("signal_uid"))
            if uid:
                signals[uid] = signal
    rows = list(signals.values())
    type_counts = Counter(_text(row.get("signal_type")) or "unknown" for row in rows)
    role_counts = Counter(_text(row.get("brand_role")) or "unknown" for row in rows)
    return {
        "status": "ready" if rows else "empty",
        "cached_post_count": len(posts),
        "signal_count": len(rows),
        "type_counts": dict(type_counts),
        "role_counts": dict(role_counts),
        "signals": rows[:12],
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "source": "vkpi_kol_pool.raw_platform_data",
    }


def _post_reference_values(post: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for key in (
        "id",
        "post_id",
        "post_uid",
        "video_id",
        "videoId",
        "shortCode",
        "shortcode",
        "aweme_id",
        "pk",
    ):
        text = _text(post.get(key))
        if text:
            values.add(text)
    for key in ("url", "post_url", "webVideoUrl", "permalink", "shareUrl"):
        url = _text(post.get(key))
        if not url:
            continue
        values.add(url)
        youtube_match = re.search(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{6,})", url)
        if youtube_match:
            values.add(youtube_match.group(1))
    return {value[:220] for value in values if value}


def _comment_tags(text: str) -> list[str]:
    try:
        return comment_intelligence._rule_tags(text)
    except Exception:
        return []


def _comment_rule_sentiment(text: str) -> str:
    try:
        return comment_intelligence._rule_sentiment(text)
    except Exception:
        return "neutral"


def _comment_text_excerpt(value: Any) -> str:
    return re.sub(r"\s+", " ", _text(value))[:500]


def _latest_comment_runs_for_pairs(pairs: list[tuple[int, str]], *, limit: int = 12) -> list[dict[str, Any]]:
    if not pairs or not _table_exists("vkpi_comment_intelligence_runs"):
        return []
    unique_pairs: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()
    for post_id, post_table in pairs:
        key = (int(post_id), _text(post_table) or "industry_posts")
        if key in seen:
            continue
        seen.add(key)
        unique_pairs.append(key)
        if len(unique_pairs) >= 40:
            break
    clauses: list[str] = []
    params: list[Any] = []
    for post_id, post_table in unique_pairs:
        clauses.append("(post_id = ? AND post_table = ?)")
        params.extend([post_id, post_table])
    conn = get_conn()
    rows = conn.execute(
        f"""
        SELECT id, run_uid, post_id, post_table, status, triggered_by,
               retry_of_run_id, params_json, steps_json, error_message,
               started_at, finished_at, created_at
        FROM vkpi_comment_intelligence_runs
        WHERE {" OR ".join(clauses)}
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (*params, max(1, min(50, int(limit or 12)))),
    ).fetchall()
    return [dict(row) for row in rows]


def _comment_rows_for_posts(item: dict[str, Any], posts: list[dict[str, Any]], *, limit: int = 50) -> list[dict[str, Any]]:
    if not posts or not _table_exists("vkpi_comments"):
        return []
    refs: list[str] = []
    seen_refs: set[str] = set()
    for post in posts:
        for value in _post_reference_values(post):
            if value in seen_refs:
                continue
            seen_refs.add(value)
            refs.append(value)
            if len(refs) >= 120:
                break
        if len(refs) >= 120:
            break
    if not refs:
        return []

    platform = _text(item.get("platform")).lower()
    where = [f"c.external_post_id IN ({','.join('?' for _ in refs)})"]
    params: list[Any] = list(refs)
    if platform:
        where.append("LOWER(c.platform) = ?")
        params.append(platform)

    select_sentiment = _table_exists("vkpi_sentiment_results")
    select_pillars = _table_exists("vkpi_post_pillars") and _table_exists("vkpi_pillars")
    sentiment_columns = (
        """
        , s.sentiment, s.sentiment_confidence, s.emotion, s.emotion_confidence,
          s.brand_attitude, s.brand_attitude_confidence
        """
        if select_sentiment
        else """
        , NULL AS sentiment, NULL AS sentiment_confidence, NULL AS emotion, NULL AS emotion_confidence,
          NULL AS brand_attitude, NULL AS brand_attitude_confidence
        """
    )
    sentiment_join = (
        "LEFT JOIN vkpi_sentiment_results s ON s.comment_id = c.id AND s.prompt_version = ?"
        if select_sentiment
        else ""
    )
    pillar_columns = ", p.pillar_key, p.display_name AS pillar_name, pp.confidence AS pillar_confidence" if select_pillars else ", NULL AS pillar_key, NULL AS pillar_name, NULL AS pillar_confidence"
    pillar_join = (
        """
        LEFT JOIN vkpi_post_pillars pp
          ON pp.post_id = c.post_id AND pp.post_table = c.post_table AND pp.is_primary = TRUE
        LEFT JOIN vkpi_pillars p ON p.id = pp.pillar_id
        """
        if select_pillars
        else ""
    )
    query_params: list[Any] = []
    if select_sentiment:
        query_params.append(comment_intelligence.sentiment.PROMPT_VERSION)
    query_params.extend(params)
    conn = get_conn()
    rows = conn.execute(
        f"""
        SELECT c.id, c.post_id, c.post_table, c.external_post_id, c.platform,
               c.external_comment_id, c.comment_text, c.author_handle,
               c.likes_count, c.reply_count, c.created_at, c.fetched_at
               {sentiment_columns}
               {pillar_columns}
        FROM vkpi_comments c
        {sentiment_join}
        {pillar_join}
        WHERE {" AND ".join(where)}
          AND COALESCE(c.comment_text, '') <> ''
        ORDER BY COALESCE(c.created_at, c.fetched_at) DESC, c.id DESC
        LIMIT ?
        """,
        (*query_params, max(1, min(200, int(limit or 50)))),
    ).fetchall()
    return [dict(row) for row in rows]


def _comment_run_evidence(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for run in runs[:12]:
        params = _loads(run.get("params_json"), {})
        steps = _loads(run.get("steps_json"), {})
        collection = steps.get("collection") if isinstance(steps, dict) and isinstance(steps.get("collection"), dict) else {}
        sentiment_step = steps.get("sentiment") if isinstance(steps, dict) and isinstance(steps.get("sentiment"), dict) else {}
        pillar_step = steps.get("pillar") if isinstance(steps, dict) and isinstance(steps.get("pillar"), dict) else {}
        evidence.append(
            {
                "evidence_id": f"ev_comment_run_{run.get('id')}",
                "source": "comment_intelligence_run",
                "source_table": "vkpi_comment_intelligence_runs",
                "source_id": run.get("id"),
                "run_uid": _text(run.get("run_uid")),
                "post_id": _safe_int(run.get("post_id")),
                "post_table": _text(run.get("post_table") or "industry_posts"),
                "status": _text(run.get("status") or "unknown"),
                "triggered_by": _text(run.get("triggered_by")),
                "created_at": _text(run.get("created_at")),
                "finished_at": _text(run.get("finished_at")),
                "error_message": _text(run.get("error_message")),
                "max_comments": params.get("max_comments") if isinstance(params, dict) else None,
                "comment_limit": params.get("comment_limit") if isinstance(params, dict) else None,
                "fetched_count": _safe_int(collection.get("fetched_count")),
                "new_count": _safe_int(collection.get("new_count")),
                "sentiment_count": _safe_int(sentiment_step.get("analyzed") or sentiment_step.get("processed")),
                "pillar_status": _text(pillar_step.get("status")),
                "confidence": 1.0 if _text(run.get("status")) == "ok" else 0.5,
                "confidence_method": "comment_pipeline_ledger",
                "rebuttal_supported": True,
            }
        )
    return evidence


def _comment_sample_evidence(rows: list[dict[str, Any]], *, limit: int = 12) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for row in rows[: max(1, min(30, int(limit or 12)))]:
        text = _comment_text_excerpt(row.get("comment_text"))
        sentiment_value = _text(row.get("sentiment")) or _comment_rule_sentiment(text)
        tags = _comment_tags(text)
        evidence.append(
            {
                "evidence_id": f"ev_comment_sample_{row.get('id')}",
                "source": "vkpi_comments",
                "source_table": "vkpi_comments",
                "source_id": row.get("id"),
                "post_id": _safe_int(row.get("post_id")),
                "post_table": _text(row.get("post_table") or "industry_posts"),
                "external_post_id": _text(row.get("external_post_id")),
                "platform": _text(row.get("platform")),
                "author": _text(row.get("author_handle") or "anonymous"),
                "text_excerpt": text,
                "sentiment": sentiment_value,
                "sentiment_confidence": _safe_float(row.get("sentiment_confidence")),
                "brand_attitude": _text(row.get("brand_attitude")),
                "brand_attitude_confidence": _safe_float(row.get("brand_attitude_confidence")),
                "rule_sentiment": _comment_rule_sentiment(text),
                "tags": tags,
                "pillar_key": _text(row.get("pillar_key")),
                "pillar_name": _text(row.get("pillar_name")),
                "pillar_confidence": _safe_float(row.get("pillar_confidence")),
                "likes": _safe_int(row.get("likes_count")),
                "reply_count": _safe_int(row.get("reply_count")),
                "created_at": _text(row.get("created_at")),
                "fetched_at": _text(row.get("fetched_at")),
                "confidence": _safe_float(row.get("sentiment_confidence"), 0.35) or 0.35,
                "confidence_method": "stored_sentiment_or_rule_v0",
                "rebuttal_supported": True,
            }
        )
    return evidence


def _comment_declared_cap(runs: list[dict[str, Any]]) -> int | None:
    declared: list[int] = []
    for run in runs:
        params = _loads(run.get("params_json"), {})
        if not isinstance(params, dict):
            continue
        for key in ("max_comments", "comment_limit"):
            value = params.get(key)
            if value is not None:
                declared.append(_safe_int(value))
                break
    return max(declared) if declared else None


def _comment_intelligence(item: dict[str, Any]) -> dict[str, Any]:
    posts = _cached_posts(item, limit=24)
    if not _table_exists("vkpi_comments"):
        return {
            "status": "not_configured",
            "method": "read_only_cached_comment_intelligence_v0",
            "cached_post_count": len(posts),
            "run_count": 0,
            "evidence_count": 0,
            "contract": {"declared": None, "cached": 0, "cap": 12, "status": "not_configured"},
            "counts": {},
            "samples": [],
            "runs": [],
            "evidence": [],
            "provider_calls": False,
            "llm_calls": False,
            "write_db": False,
            "source": "vkpi_comments + vkpi_comment_intelligence_runs",
        }
    rows = _comment_rows_for_posts(item, posts, limit=80)
    pairs = [(_safe_int(row.get("post_id")), _text(row.get("post_table") or "industry_posts")) for row in rows if _safe_int(row.get("post_id"))]
    runs = _latest_comment_runs_for_pairs(pairs, limit=12)
    samples = _comment_sample_evidence(rows, limit=12)
    run_evidence = _comment_run_evidence(runs)
    sentiments = Counter(_text(row.get("sentiment")) or _comment_rule_sentiment(_comment_text_excerpt(row.get("comment_text"))) for row in rows)
    brand_attitudes = Counter(_text(row.get("brand_attitude")) or "unknown" for row in rows)
    pillars = Counter(_text(row.get("pillar_key")) or "unknown" for row in rows)
    tags = Counter(tag for sample in samples for tag in sample.get("tags", []))
    cached = len(rows)
    declared = _comment_declared_cap(runs)
    cap = 12
    if cached == 0:
        contract_status = "no_cached_comments"
    elif cached > cap:
        contract_status = "sampled_cached"
    else:
        contract_status = "cached_window"
    status = "ready" if cached or runs else "empty"
    return {
        "status": status,
        "method": "read_only_cached_comment_intelligence_v0",
        "cached_post_count": len(posts),
        "run_count": len(runs),
        "cached_comment_count": cached,
        "analyzed_comment_count": sum(1 for row in rows if _text(row.get("sentiment"))),
        "pillar_comment_count": sum(1 for row in rows if _text(row.get("pillar_key"))),
        "contract": {
            "declared": declared,
            "cached": cached,
            "cap": cap,
            "status": contract_status,
        },
        "counts": {
            "sentiment": dict(sentiments),
            "brand_attitude": dict(brand_attitudes),
            "pillars": dict(pillars),
            "questions": tags.get("question", 0),
            "opportunities": tags.get("opportunity", 0),
            "issues": tags.get("issue", 0),
        },
        "samples": samples,
        "runs": run_evidence,
        "evidence": [*run_evidence, *samples],
        "evidence_count": len(run_evidence) + cached,
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "source": "vkpi_comments + vkpi_sentiment_results + vkpi_post_pillars + vkpi_comment_intelligence_runs",
    }

__all__ = [name for name in globals() if name.startswith("_") or name == "VIDEO_ANALYSIS_FIELD_KEYS"]
