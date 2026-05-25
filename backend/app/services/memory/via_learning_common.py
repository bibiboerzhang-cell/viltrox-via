"""
services/memory/via_learning.py — Via daily autonomous learning
"""
from __future__ import annotations

import asyncio
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any

from app.db.connection import get_conn
from app.db.repositories.via_control import (
    create_via_policy_version,
    get_via_reward_trace_by_idempotency_key,
    get_via_policy_version,
    insert_via_reward_trace,
    list_active_via_policy_versions,
    list_recent_via_retrieval_evidence,
    list_via_policy_version_history,
    list_via_rollout_alerts,
    list_via_memory_retention_stats,
    list_via_routing_provider_stats,
    list_recent_via_decisions,
    list_recent_via_outcomes,
    list_recent_via_reward_traces,
    list_via_policy_proposals,
    promote_via_policy_version,
    upsert_via_rollout_alert,
    upsert_via_policy_proposal,
)
from app.core.config import (
    VIA_ENABLE_DAILY_LEARNING,
    VIA_LEARNING_BH_MAX_ITEMS,
    VIA_LEARNING_COMMENT_LIMIT,
    VIA_LEARNING_COMMENT_SAMPLE,
    VIA_LEARNING_MAX_POSTS,
    VIA_OFFICIAL_FACEBOOK_HANDLE,
    VIA_OFFICIAL_INSTAGRAM_HANDLE,
    VIA_OFFICIAL_TIKTOK_HANDLE,
    VIA_OFFICIAL_YOUTUBE_HANDLE,
)
from app.services.creator_program import build_creator_program_snapshot
from app.services.intelligence import fetch_bh_viltrox_products, save_bh_snapshot
from app.services.intelligence.account_scan_service import scan_account
from app.services.memory.l3_store import (
    record_creator_memory_fact,
    record_feedback_signal,
    record_market_observation,
    record_product_signal,
    record_region_fact,
)
from app.services.scraping.apify_viltrox_comments import fetch_viltrox_comments, normalize_comment


PRODUCT_PATTERN = re.compile(
    r"\b(?:(?:AF|LAB|PRO|EVO|AIR|EPIC)\s+)?\d{1,3}(?:\.\d+)?mm\s+F\d(?:\.\d+)?(?:\s+(?:LAB|PRO|AIR|EVO|EPIC|FE|XF|Z|L|RF|E|MFT)){0,2}\b",
    flags=re.IGNORECASE,
)
VIA_EVALUATOR_VERSION = "via-offline-evaluator-v1"
_P1_SHADOW_ROLLOUT_RULES: dict[str, dict[str, Any]] = {
    "via.retrieval.selective": {
        "target": "retrieval_plan",
        "min_shadow_samples": 6,
        "promote_shadow_samples": 18,
        "min_change_rate": 0.2,
        "min_acceptance_rate": 0.55,
        "min_avg_reward": 0.45,
        "max_abuse_rate": 0.08,
    },
    "via.model.route": {
        "target": "model_choice",
        "min_shadow_samples": 8,
        "promote_shadow_samples": 20,
        "min_change_rate": 0.18,
        "min_acceptance_rate": 0.55,
        "min_avg_reward": 0.48,
        "max_abuse_rate": 0.08,
    },
}
_P1_LIVE_ROLLOUT_STEPS = [0.05, 0.15, 0.30, 0.60, 1.0]


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:96] or "unknown"


def extract_product_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for match in PRODUCT_PATTERN.findall(text or ""):
        cleaned = "Viltrox " + re.sub(r"\s+", " ", match.strip())
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        candidates.append(cleaned)
    return candidates


def _official_accounts() -> list[dict[str, str]]:
    pairs = [
        ("instagram", VIA_OFFICIAL_INSTAGRAM_HANDLE),
        ("tiktok", VIA_OFFICIAL_TIKTOK_HANDLE),
        ("youtube", VIA_OFFICIAL_YOUTUBE_HANDLE),
        ("facebook", VIA_OFFICIAL_FACEBOOK_HANDLE),
    ]
    return [
        {"platform": platform, "handle": handle.strip()}
        for platform, handle in pairs
        if str(handle or "").strip()
    ]


def _pick_post_metrics(post: dict[str, Any]) -> dict[str, int]:
    return {
        "views": int(post.get("views") or 0),
        "likes": int(post.get("likes") or 0),
        "comments": int(post.get("comments") or 0),
        "shares": int(post.get("shares") or 0),
    }


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except Exception:
            continue
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _load_internal_learning_inputs(window_days: int = 30) -> dict[str, list[dict[str, Any]]]:
    conn = get_conn()
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(window_days)))

    def _filter_recent(rows: list[Any]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            created = _parse_timestamp(item.get("created_at") or item.get("updated_at"))
            if created and created < cutoff:
                continue
            items.append(item)
        return items

    submissions_rows = conn.execute(
        """
        SELECT id, user_id, platform, extracted_handle, product_series, product_label,
               views, likes, comments, shares, created_at, detection_status, title
        FROM submissions
        ORDER BY id DESC
        LIMIT 240
        """
    ).fetchall()
    ingest_rows = conn.execute(
        """
        SELECT id, source_platform, event_type, entity_type, external_id,
               creator_handle, region_code, created_at, ingest_status
        FROM platform_ingest_events
        ORDER BY id DESC
        LIMIT 240
        """
    ).fetchall()
    address_rows = conn.execute(
        """
        SELECT id, user_id, country, state, city, is_default
        FROM user_addresses
        ORDER BY id DESC
        LIMIT 160
        """
    ).fetchall()
    verified_social_rows = conn.execute(
        """
        SELECT id, user_id, platform, handle, verified, verified_at
        FROM user_social_accounts
        WHERE verified=1
        ORDER BY id DESC
        LIMIT 200
        """
    ).fetchall()
    return {
        "submissions": _filter_recent(list(submissions_rows)),
        "ingest_events": _filter_recent(list(ingest_rows)),
        "addresses": [dict(row) for row in address_rows],
        "verified_social_accounts": _filter_recent(list(verified_social_rows)),
    }


__all__ = [name for name in globals() if not name.startswith("__")]
