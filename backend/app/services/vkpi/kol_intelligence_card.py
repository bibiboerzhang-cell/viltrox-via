"""Read-only single KOL intelligence card aggregation.

P2 starts with an API shape that gathers existing evidence into one response.
This module intentionally does not call providers, enqueue jobs, call LLMs, or
write derived rows.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_conn
from app.services.vkpi import (
    brand_signal_detector,
    comment_intelligence,
    eleven_dimensions,
    kol_competitor_detector,
    kol_history_match,
    kol_pool,
    kol_product_fit,
    refresh_tier,
)


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


def _text(value: Any) -> str:
    return str(value or "").strip()


def _loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(str(value or ""))
    except Exception:
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
        pass
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


def _handle_variants(item: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for value in (item.get("handle"), item.get("profile_url")):
        text = _text(value).lower()
        if not text:
            continue
        candidates = [text]
        if "/" in text:
            candidates = []
            at_match = re.search(r"@[a-z0-9_.-]+", text)
            if at_match:
                candidates.append(at_match.group(0))
            last_segment = text.split("?", 1)[0].rstrip("/").split("/")[-1]
            if last_segment:
                candidates.append(last_segment)
        for candidate in candidates:
            values.append(candidate)
            if candidate.startswith("@"):
                values.append(candidate[1:])
            elif "@" not in candidate and "/" not in candidate:
                values.append(f"@{candidate}")
    deduped: list[str] = []
    for value in values:
        clean = value.strip().strip("/")
        if clean and clean not in deduped:
            deduped.append(clean)
    return deduped[:12]


def _submission_video_analysis_rows(item: dict[str, Any], *, limit: int = 12) -> list[dict[str, Any]]:
    if not _table_exists("submissions"):
        return []
    platform = _text(item.get("platform")).lower()
    variants = _handle_variants(item)
    where = ["COALESCE(video_analysis, '') <> ''"]
    params: list[Any] = []
    if platform:
        where.append("LOWER(COALESCE(platform, '')) = ?")
        params.append(platform)
    handle_clauses: list[str] = []
    for variant in variants:
        handle_clauses.append("LOWER(COALESCE(extracted_handle, '')) = ?")
        params.append(variant.lstrip("@"))
        handle_clauses.append("LOWER(COALESCE(extracted_handle, '')) = ?")
        params.append(variant if variant.startswith("@") else f"@{variant}")
        handle_clauses.append("LOWER(COALESCE(url, '')) LIKE ?")
        params.append(f"%{variant}%")
    if handle_clauses:
        where.append(f"({' OR '.join(handle_clauses)})")
    conn = get_conn()
    rows = conn.execute(
        f"""
        SELECT id, created_at, platform, url, extracted_handle, title, video_analysis
        FROM submissions
        WHERE {" AND ".join(where)}
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (*params, max(1, min(50, int(limit or 12)))),
    ).fetchall()
    return [dict(row) for row in rows]


def _analysis_confidence(analysis: dict[str, Any]) -> float:
    confidence = _text(analysis.get("confidence")).lower()
    if confidence in {"high", "strong"}:
        return 0.85
    if confidence in {"medium", "moderate"}:
        return 0.65
    if confidence in {"low", "weak"}:
        return 0.35
    overall = _safe_float(analysis.get("quality_overall"))
    if overall > 1:
        return round(min(1.0, overall / 100.0), 2)
    if overall > 0:
        return round(min(1.0, overall), 2)
    if bool(analysis.get("analyzed")):
        return 0.5
    return 0.0


def _analysis_fields(analysis: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    aliases = {
        "target_audience": ("target_audience", "audience_fit", "audience_segment"),
        "competitor_products": ("competitor_products", "competitors", "other_lens"),
        "brand_integration_depth": ("brand_integration_depth", "brand_depth"),
        "products_found": ("products_found", "products_detected", "viltrox_lens"),
    }
    for key in VIDEO_ANALYSIS_FIELD_KEYS:
        source_keys = aliases.get(key, (key,))
        for source_key in source_keys:
            value = analysis.get(source_key)
            if value not in (None, "", [], {}):
                fields[key] = value
                break
    return fields


def _video_analysis_evidence(row: dict[str, Any]) -> dict[str, Any] | None:
    analysis = _loads(row.get("video_analysis"), {})
    if not isinstance(analysis, dict):
        return None
    fields = _analysis_fields(analysis)
    if not bool(analysis.get("analyzed")) or not fields:
        return None
    source_id = _safe_int(row.get("id"))
    method = _text(analysis.get("method") or "stored_video_analysis")
    return {
        "evidence_id": f"ev_video_analysis_submission_{source_id}",
        "source": "video_analysis",
        "source_table": "submissions",
        "source_id": source_id,
        "source_url": _text(row.get("url")),
        "captured_at": _text(row.get("created_at")),
        "title": _text(row.get("title")),
        "platform": _text(row.get("platform")),
        "handle": _text(row.get("extracted_handle")),
        "method": method,
        "analyzed": True,
        "confidence": _analysis_confidence(analysis),
        "confidence_method": method,
        "reasoning": _text(analysis.get("quality_summary") or analysis.get("content_summary") or analysis.get("notes") or "Stored video analysis row."),
        "raw_data_ref": f"submissions:{source_id}:video_analysis",
        "rebuttal_supported": True,
        "field_names": list(fields.keys()),
        "fields": fields,
        "provider_badge_allowed": bool("gemini" in method.lower()),
    }


def _video_analysis(item: dict[str, Any]) -> dict[str, Any]:
    if not _table_exists("submissions"):
        return {
            "status": "not_configured",
            "method": "read_only_stored_video_analysis_v0",
            "row_count": 0,
            "evidence_count": 0,
            "evidence": [],
            "field_contract": list(VIDEO_ANALYSIS_FIELD_KEYS),
            "provider_calls": False,
            "llm_calls": False,
            "write_db": False,
            "source": "submissions.video_analysis",
        }
    try:
        rows = _submission_video_analysis_rows(item, limit=12)
    except Exception as exc:
        return _status_payload(
            "unavailable",
            reason="video_analysis_query_failed",
            error=exc,
            method="read_only_stored_video_analysis_v0",
            row_count=0,
            evidence_count=0,
            evidence=[],
            field_contract=list(VIDEO_ANALYSIS_FIELD_KEYS),
            provider_calls=False,
            llm_calls=False,
            write_db=False,
            source="submissions.video_analysis",
        )
    evidence = [leaf for leaf in (_video_analysis_evidence(row) for row in rows) if isinstance(leaf, dict)]
    field_counts = Counter(field for leaf in evidence for field in leaf.get("field_names", []))
    return {
        "status": "ready" if evidence else "empty",
        "method": "read_only_stored_video_analysis_v0",
        "row_count": len(rows),
        "analyzed_count": len(evidence),
        "evidence_count": len(evidence),
        "field_counts": dict(field_counts),
        "evidence": evidence[:12],
        "field_contract": list(VIDEO_ANALYSIS_FIELD_KEYS),
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "source": "submissions.video_analysis",
        "empty_reason": "" if evidence else "no_stored_analyzed_video_rows",
    }


def _cached_post_summaries(item: dict[str, Any], *, limit: int = 6) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for post in _cached_posts(item, limit=limit):
        if not isinstance(post, dict):
            continue
        snippet = post.get("snippet") if isinstance(post.get("snippet"), dict) else {}
        stats = post.get("statistics") if isinstance(post.get("statistics"), dict) else {}
        post_uid = _text(post.get("id") or post.get("post_uid") or post.get("shortCode") or post.get("shortcode"))
        url = _text(post.get("post_url") or post.get("url") or post.get("webVideoUrl") or post.get("permalink"))
        if not url and _text(post.get("kind")).lower() == "youtube#video" and post_uid:
            url = f"https://www.youtube.com/watch?v={post_uid}"
        title = _text(post.get("title") or post.get("caption") or post.get("text") or snippet.get("title") or url)
        if not title and not url:
            continue
        rows.append(
            {
                "id": post_uid or url or f"cached-post-{len(rows) + 1}",
                "title": title[:280],
                "url": url,
                "post_url": url,
                "published_at": _text(post.get("published_at") or post.get("publishedAt") or post.get("timestamp") or snippet.get("publishedAt")),
                "views": _safe_int(post.get("views") or post.get("view_count") or post.get("playCount") or stats.get("viewCount")),
                "likes": _safe_int(post.get("likes") or post.get("like_count") or post.get("diggCount") or stats.get("likeCount")),
                "comments": _safe_int(post.get("comments") or post.get("comment_count") or post.get("commentCount") or stats.get("commentCount")),
                "source_kind": "kol_pool_cached_post",
            }
        )
        if len(rows) >= limit:
            break
    return rows


def _memory_card(item: dict[str, Any], competitors: dict[str, Any]) -> dict[str, Any]:
    raw = _loads(item.get("raw_platform_data"), {})
    evidence_summary = raw.get("evidence_summary") if isinstance(raw, dict) and isinstance(raw.get("evidence_summary"), dict) else {}
    try:
        history = kol_history_match.find_history_match(item, platform=_text(item.get("platform"))) or {}
        history_status = "ready" if history else "empty"
    except Exception as exc:
        history = {}
        history_status = "unavailable"
        history_error = f"{type(exc).__name__}: {str(exc)[:240]}"
    else:
        history_error = ""

    recent_posts = history.get("recent_posts") if isinstance(history.get("recent_posts"), list) else []
    if not recent_posts:
        recent_posts = _cached_post_summaries(item, limit=6)
    recent_cooperations = history.get("recent_cooperations") if isinstance(history.get("recent_cooperations"), list) else []
    brand_collaborations = _brief_list(item.get("brand_collaborations_json"))
    recommended_products = _brief_list(item.get("recommended_product_lines_json"))
    potential_concerns = _brief_list(item.get("potential_concerns_json"))
    relations = competitors.get("relations") if isinstance(competitors.get("relations"), list) else []
    competitor_summary = competitors.get("summary") if isinstance(competitors.get("summary"), dict) else {}
    cooperation_count = max(
        _safe_int(history.get("cooperation_count")),
        len(brand_collaborations),
        _safe_int(evidence_summary.get("cooperation_rows")),
    )
    evidence_count = max(_safe_int(history.get("evidence_count")), _safe_int(evidence_summary.get("evidence_count")))
    has_memory = any(
        [
            _text(item.get("source_type")),
            _text(item.get("source_ref")),
            item.get("linked_main_kol_id") is not None,
            cooperation_count,
            evidence_count,
            recent_posts,
            recent_cooperations,
            brand_collaborations,
            relations,
        ]
    )
    status = "ready" if has_memory else history_status
    payload = {
        "status": status,
        "source_type": _text(item.get("source_type") or history.get("source_type")),
        "source_ref": _text(item.get("source_ref") or history.get("source_ref")),
        "linked_main_kol_id": item.get("linked_main_kol_id") or history.get("linked_main_kol_id"),
        "history_match": {
            "status": history_status,
            "matched": bool(history.get("matched")),
            "match_type": _text(history.get("match_type")),
            "match_confidence": _safe_float(history.get("match_confidence")),
            "kol_pool_id": history.get("kol_pool_id"),
            "cooperation_count": cooperation_count,
            "evidence_count": evidence_count,
            "profile_rows": _safe_int(history.get("profile_rows") or evidence_summary.get("kol_profile_rows")),
            "risk_rows": _safe_int(history.get("risk_rows") or evidence_summary.get("risk_rows")),
        },
        "excel_record": {
            "source_type": _text(item.get("source_type")),
            "source_ref": _text(item.get("source_ref")),
            "brand_collaborations": brand_collaborations,
            "recommended_products": recommended_products,
            "potential_concerns": potential_concerns,
            "raw_evidence_summary": evidence_summary,
        },
        "recent_cooperations": recent_cooperations[:5],
        "recent_posts": recent_posts[:6],
        "competitor_memory": {
            "relation_count": len(relations),
            "strongest_brand": _text(competitor_summary.get("competitor_brand")),
            "risk_tier": _text(competitor_summary.get("risk_tier") or "opportunity"),
            "risk_score": _safe_float(competitor_summary.get("risk_score")),
        },
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "source": "vkpi_kol_pool + legacy memory",
    }
    if history_error:
        payload["history_error"] = history_error
    return payload


def _product_identity(item: dict[str, Any]) -> str:
    return _text(
        item.get("sku")
        or item.get("product_sku")
        or item.get("product_key")
        or item.get("product_family_uid")
        or item.get("product_family_name")
        or item.get("family_key")
        or item.get("product_name")
        or item.get("name")
    )


def _catalog_product_is_official(product: dict[str, Any]) -> bool:
    if not _text(product.get("sku")):
        return False
    specs = product.get("specs") if isinstance(product.get("specs"), dict) else {}
    return any(
        [
            _text(product.get("model_name")),
            _text(product.get("marketing_name")),
            _text(product.get("mount")),
            product.get("price_usd") is not None,
            _text(product.get("product_url")),
            bool(specs),
        ]
    )


def _product_fit_catalog_evidence(row: dict[str, Any], product: dict[str, Any], *, index: int) -> dict[str, Any]:
    specs = product.get("specs") if isinstance(product.get("specs"), dict) else {}
    sku = _text(product.get("sku")) or f"catalog-product-{index}"
    mount = _text(product.get("mount") or specs.get("lens_mount"))
    return {
        "evidence_id": f"ev_official_catalog_{sku}",
        "source": "official_catalog",
        "source_table": "vkpi_products",
        "source_id": sku,
        "source_url": _text(product.get("product_url")),
        "confidence": _safe_float(product.get("source_confidence"), 1.0) or 1.0,
        "confidence_method": "catalog_source_confidence",
        "reasoning": f"官方 SKU {sku} 提供产品、卡口和规格证据。",
        "rebuttal_supported": False,
        "sku": sku,
        "model_name": _text(product.get("model_name")),
        "marketing_name": _text(product.get("marketing_name")),
        "mount": mount,
        "price_usd": product.get("price_usd"),
        "specs": specs,
        "product_family_name": _text(row.get("product_family_name")),
        "score": _safe_float(row.get("score")),
    }


def _product_fit_discovery_evidence(row: dict[str, Any], *, index: int) -> dict[str, Any]:
    identity = _product_identity(row) or f"product-family-{index}"
    return {
        "evidence_id": f"ev_product_family_discovery_{index}",
        "source": "rule_engine",
        "source_table": "vkpi_memory_entities",
        "source_id": _text(row.get("product_family_uid") or identity),
        "confidence": 0.35,
        "confidence_method": "rule_v0_low_confidence",
        "reasoning": "只有产品族或历史标签证据，不能当作官方 SKU 适配完成。",
        "rebuttal_supported": True,
        "product_family_name": identity,
        "score": _safe_float(row.get("score")),
        "score_breakdown": row.get("score_breakdown") if isinstance(row.get("score_breakdown"), dict) else {},
    }


def _product_fit_rule_evidence(row: dict[str, Any], source: dict[str, Any], *, index: int) -> dict[str, Any]:
    evidence_type = _text(source.get("type") or source.get("evidence_type") or "rule_evidence")
    payload = source.get("payload") if isinstance(source.get("payload"), dict) else {}
    return {
        "evidence_id": f"ev_rule_engine_product_fit_{index}_{evidence_type}",
        "source": "rule_engine",
        "source_table": _text(source.get("source_table") or payload.get("source_table") or "vkpi_memory_entities"),
        "source_id": source.get("source_id") or payload.get("source_id") or _text(row.get("product_family_uid")),
        "source_url": _text(source.get("source_url") or payload.get("source_url")),
        "confidence": _safe_float(source.get("confidence") or source.get("confidence_score") or payload.get("confidence"), 0.5),
        "confidence_method": "rule_v0",
        "reasoning": _text(source.get("detail") or source.get("reasoning") or evidence_type.replace("_", " ")),
        "rebuttal_supported": True,
        "evidence_type": evidence_type,
        "polarity": _text(source.get("polarity")),
        "severity": _text(source.get("severity")),
        "score_component": _text(source.get("score_component")),
        "product_family_name": _text(row.get("product_family_name")),
    }


def _product_fit_evidence(rows: list[dict[str, Any]]) -> dict[str, Any]:
    official: list[dict[str, Any]] = []
    discovery: list[dict[str, Any]] = []
    rules: list[dict[str, Any]] = []
    seen_skus: set[str] = set()
    for row_index, row in enumerate(rows[:10]):
        if not isinstance(row, dict):
            continue
        products = []
        first_product = row.get("matched_catalog_product") if isinstance(row.get("matched_catalog_product"), dict) else {}
        if first_product:
            products.append(first_product)
        products.extend(product for product in row.get("matched_catalog_products", []) if isinstance(product, dict))
        official_for_row = 0
        for product in products:
            if not _catalog_product_is_official(product):
                continue
            sku = _text(product.get("sku"))
            if sku in seen_skus:
                continue
            seen_skus.add(sku)
            official_for_row += 1
            official.append(_product_fit_catalog_evidence(row, product, index=len(official) + 1))
        if official_for_row == 0:
            discovery.append(_product_fit_discovery_evidence(row, index=row_index + 1))
        evidence_pro = row.get("evidence_pro") if isinstance(row.get("evidence_pro"), list) else []
        evidence_con = row.get("evidence_con") if isinstance(row.get("evidence_con"), list) else []
        for source in [*evidence_pro[:4], *evidence_con[:3]]:
            if isinstance(source, dict):
                rules.append(_product_fit_rule_evidence(row, source, index=len(rules) + 1))
    official_rows = official[:12]
    discovery_rows = discovery[:8]
    rule_rows = rules[:16]
    return {
        "official_catalog": official_rows,
        "discovery": discovery_rows,
        "rule_evidence": rule_rows,
        "evidence": [*official_rows, *discovery_rows, *rule_rows],
        "official_catalog_count": len(official_rows),
        "discovery_count": len(discovery_rows),
        "rule_evidence_count": len(rule_rows),
        "official_catalog_total": len(official),
        "discovery_total": len(discovery),
        "rule_evidence_total": len(rules),
    }


def _product_fit(kol_pool_id: int, *, include_product_fit: bool) -> dict[str, Any]:
    if not include_product_fit:
        return _status_payload("skipped", reason="include_product_fit_false")
    try:
        payload = kol_product_fit.build_kol_product_fit_preview(
            kol_pool_id=kol_pool_id,
            limit=10,
            include_low_evidence=False,
            with_llm_reasons=False,
            persist_run=False,
        )
    except Exception as exc:
        return _status_payload("unavailable", reason="product_fit_preview_unavailable", error=exc)
    rows = payload.get("items") or payload.get("eligible") or payload.get("recommendations") or []
    if not isinstance(rows, list):
        rows = []
    evidence = _product_fit_evidence([row for row in rows if isinstance(row, dict)])
    return {
        "status": "ready" if rows else "empty",
        "method": payload.get("method") or payload.get("mode") or "kol_product_fit_preview",
        "count": len(rows),
        "top": rows[:5],
        **evidence,
        "provider_calls": bool(payload.get("provider_calls", False)),
        "llm_calls": bool(payload.get("llm_calls", False)),
        "write_db": bool(payload.get("write_db", False) or payload.get("persisted", False)),
        "source": "vkpi_memory_entities + vkpi_kol_pool + dimensions11",
    }


def _freshness(kol_pool_id: int) -> dict[str, Any]:
    try:
        return refresh_tier.freshness_for_kol(kol_pool_id)
    except Exception as exc:
        return _status_payload("unavailable", reason="freshness_unavailable", error=exc)


def _decision_support(sections: dict[str, dict[str, Any]]) -> dict[str, Any]:
    gaps: list[str] = []
    ready = 0
    for name, payload in sections.items():
        status = _text(payload.get("status") or ("ready" if name == "freshness" else "")).lower()
        if status in {"ready", "empty"}:
            ready += 1
        elif status in {"unavailable", "error"}:
            gaps.append(f"{name}_{status}")
        elif status == "skipped":
            gaps.append(f"{name}_skipped")
    if gaps:
        readiness = "partial"
    elif ready == len(sections):
        readiness = "ready"
    else:
        readiness = "unknown"
    return {
        "readiness": readiness,
        "ready_sections": ready,
        "total_sections": len(sections),
        "gaps": gaps,
    }


def _confidence_for_section(section: str, payload: dict[str, Any]) -> float:
    if section == "dimensions11":
        confidence = payload.get("confidence") if isinstance(payload.get("confidence"), dict) else {}
        return _safe_float(confidence.get("overall"))
    if section == "product_fit":
        if _safe_int(payload.get("official_catalog_count")):
            return 0.85
        if _safe_int(payload.get("discovery_count")):
            return 0.35
        top = payload.get("top") if isinstance(payload.get("top"), list) else []
        scores = [_safe_float(row.get("confidence") or row.get("fit_confidence")) for row in top if isinstance(row, dict)]
        return max(scores) if scores else 0.0
    if section == "competitors":
        return 1.0 if _safe_int(payload.get("evidence_count")) > 0 else 0.0
    if payload.get("status") == "ready":
        return 1.0
    if payload.get("status") == "empty":
        return 0.0
    return 0.0


def _evidence_count_for_section(section: str, payload: dict[str, Any]) -> int:
    if section == "freshness":
        return 1 if payload.get("last_refresh_at") or payload.get("last_refresh_status") else 0
    if section == "dimensions11":
        confidence = payload.get("confidence") if isinstance(payload.get("confidence"), dict) else {}
        return sum(1 for key in ("block1_content", "block2_performance", "block3_business", "block4_specialty") if _safe_float(confidence.get(key)) > 0)
    if section == "competitors":
        if payload.get("evidence_count") is not None:
            return _safe_int(payload.get("evidence_count"))
        evidence = payload.get("evidence") if isinstance(payload.get("evidence"), list) else []
        if evidence:
            return len(evidence)
        relations = payload.get("relations") if isinstance(payload.get("relations"), list) else []
        return len(relations)
    if section == "brand_signal":
        return _safe_int(payload.get("signal_count"))
    if section == "comment_intelligence":
        return _safe_int(payload.get("evidence_count")) or _safe_int(payload.get("cached_comment_count")) + _safe_int(payload.get("run_count"))
    if section == "video_analysis":
        return _safe_int(payload.get("evidence_count"))
    if section == "memory_card":
        history = payload.get("history_match") if isinstance(payload.get("history_match"), dict) else {}
        competitor_memory = payload.get("competitor_memory") if isinstance(payload.get("competitor_memory"), dict) else {}
        cooperation_count = _safe_int(history.get("cooperation_count"))
        recent_posts = payload.get("recent_posts") if isinstance(payload.get("recent_posts"), list) else []
        recent_cooperations = payload.get("recent_cooperations") if isinstance(payload.get("recent_cooperations"), list) else []
        return cooperation_count + len(recent_posts) + len(recent_cooperations) + _safe_int(competitor_memory.get("relation_count"))
    if section == "product_fit":
        evidence_count = (
            _safe_int(payload.get("official_catalog_count"))
            + _safe_int(payload.get("discovery_count"))
            + _safe_int(payload.get("rule_evidence_count"))
        )
        return evidence_count or _safe_int(payload.get("count"))
    return 0


def _evidence_index(sections: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    sources = {
        "freshness": ("Freshness", "vkpi_kol_refresh_tier"),
        "dimensions11": ("11D Confidence", "vkpi_kol_pool + cached posts"),
        "competitors": ("Competitors", "vkpi_competitor_relation or cached posts"),
        "brand_signal": ("Brand Signal", "vkpi_kol_pool.raw_platform_data"),
        "comment_intelligence": ("Comment Intelligence", "vkpi_comments + vkpi_comment_intelligence_runs"),
        "video_analysis": ("Video Analysis", "submissions.video_analysis"),
        "memory_card": ("Memory Card", "vkpi_kol_pool + legacy memory"),
        "product_fit": ("Product Fit", "vkpi_memory_entities/product families"),
    }
    rows: list[dict[str, Any]] = []
    for section in ("freshness", "dimensions11", "competitors", "brand_signal", "comment_intelligence", "video_analysis", "memory_card", "product_fit"):
        payload = sections.get(section, {})
        label, source = sources[section]
        row = {
            "section": section,
            "label": label,
            "source": source,
            "status": payload.get("status") or ("ready" if section == "freshness" else "unknown"),
            "evidence_count": _evidence_count_for_section(section, payload),
            "confidence": _confidence_for_section(section, payload),
        }
        if section == "freshness" and payload.get("days_old") is not None:
            row["freshness_hours"] = _safe_int(payload.get("days_old")) * 24
        rows.append(row)
    return rows


def build_kol_pool_intelligence_card(kol_pool_id: int, *, include_product_fit: bool = True) -> dict[str, Any]:
    item = kol_pool.get_item(kol_pool_id)["item"]
    competitors = _competitors(kol_pool_id)
    sections = {
        "freshness": _freshness(kol_pool_id),
        "dimensions11": _dimensions11(kol_pool_id),
        "competitors": competitors,
        "brand_signal": _brand_signals(item),
        "comment_intelligence": _comment_intelligence(item),
        "video_analysis": _video_analysis(item),
        "memory_card": _memory_card(item, competitors),
        "product_fit": _product_fit(kol_pool_id, include_product_fit=include_product_fit),
    }
    return {
        "mode": "read_only_kol_intelligence_card_v0",
        "generated_at": _utcnow(),
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "kol_pool_id": int(kol_pool_id),
        "item": _item_summary(item),
        **sections,
        "decision_support": _decision_support(sections),
        "evidence_index": _evidence_index(sections),
    }
