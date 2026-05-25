"""P4.55 Gemini single-KOL preflight and controlled live-run harness.

The default preflight path only inspects cached V-KPI evidence. A real Gemini
call is only possible through run_kol_pool_gemini_single() with explicit flags,
valid cached URL readiness, and passing budget gates.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable

from app.domains.kol import pool as kol_pool
from app.platform import llm_gateway
from app.domains.costs import budget_guard


GEMINI_SINGLE_KOL_SCOPE = "cron:p4_gemini_single_kol"
YOUTUBE_RE = re.compile(r"(?:youtube\.com/(?:watch\?v=|shorts/)|youtu\.be/)([A-Za-z0-9_-]{6,})", re.I)
VILTROX_RE = re.compile(r"\bviltrox\b", re.I)
AnalyzerFn = Callable[[str, str, str], Awaitable[dict[str, Any]]]


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _text(value).lower()


def _int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return default


def _loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(str(value or ""))
        return parsed if parsed is not None else fallback
    except Exception:
        return fallback


def _nested_dict(source: dict[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        value = source.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _first_text(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def _first_int(*values: Any) -> int:
    for value in values:
        parsed = _int(value)
        if parsed:
            return parsed
    return 0


def _is_youtube_url(url: str) -> bool:
    return bool(YOUTUBE_RE.search(_text(url)))


def _youtube_video_id(url: str) -> str:
    match = YOUTUBE_RE.search(_text(url))
    return match.group(1) if match else ""


def _items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        for key in ("items", "data", "results", "videos", "posts", "latestPosts", "latest_posts"):
            nested = value.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _looks_like_post(post: dict[str, Any]) -> bool:
    kind = _lower(post.get("kind"))
    if "channel" in kind and "video" not in kind:
        return False
    return any(
        key in post
        for key in (
            "video_url",
            "videoUrl",
            "webVideoUrl",
            "post_url",
            "permalink",
            "shareUrl",
            "content_url",
            "videoMeta",
            "playCount",
            "view_count",
            "statistics",
            "snippet",
            "shortCode",
            "caption",
            "title",
        )
    )


def _raw_posts(raw: Any, *, depth: int = 0) -> Iterable[dict[str, Any]]:
    if depth > 4:
        return
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                if _looks_like_post(item):
                    yield item
                yield from _raw_posts(item, depth=depth + 1)
        return
    if not isinstance(raw, dict):
        return
    for key in ("videos", "posts", "latestPosts", "latest_posts", "items", "data", "results"):
        value = raw.get(key)
        for item in _items(value):
            if _looks_like_post(item):
                yield item
            yield from _raw_posts(item, depth=depth + 1)
    for key in ("profile", "raw", "channel", "account"):
        value = raw.get(key)
        if isinstance(value, (dict, list)):
            yield from _raw_posts(value, depth=depth + 1)


def _candidate_from_post(post: dict[str, Any], *, item: dict[str, Any], index: int, source_kind: str) -> dict[str, Any] | None:
    snippet = _nested_dict(post, "snippet")
    localized = _nested_dict(snippet, "localized")
    stats = _nested_dict(post, "statistics", "stats", "metrics", "public_metrics")
    post_uid = _first_text(
        post.get("id"),
        post.get("post_uid"),
        post.get("post_id"),
        post.get("video_id"),
        post.get("videoId"),
        post.get("shortCode"),
        post.get("shortcode"),
    )
    source_url = _first_text(
        post.get("source_url"),
        post.get("sourceUrl"),
        post.get("post_url"),
        post.get("url"),
        post.get("webVideoUrl"),
        post.get("permalink"),
        post.get("shareUrl"),
        post.get("content_url"),
        post.get("link"),
    )
    video_url = _first_text(post.get("video_url"), post.get("videoUrl"), post.get("webVideoUrl"), source_url)
    kind = _lower(post.get("kind"))
    platform = _lower(item.get("platform"))
    if not video_url and (kind == "youtube#video" or platform == "youtube") and post_uid:
        video_url = f"https://www.youtube.com/watch?v={post_uid}"
    title = _first_text(
        post.get("title"),
        post.get("caption"),
        post.get("text"),
        snippet.get("title"),
        localized.get("title"),
        post.get("description"),
        snippet.get("description"),
        source_url,
    )
    if not any((video_url, source_url, title)):
        return None
    views = _first_int(post.get("views"), post.get("view_count"), post.get("play_count"), post.get("playCount"), stats.get("viewCount"), stats.get("playCount"))
    likes = _first_int(post.get("likes"), post.get("like_count"), post.get("diggCount"), stats.get("likeCount"))
    comments = _first_int(post.get("comments"), post.get("comment_count"), post.get("commentCount"), stats.get("commentCount"))
    candidate_url = video_url or source_url
    reasons: list[str] = [source_kind]
    if _is_youtube_url(candidate_url):
        reasons.append("youtube_url")
    if VILTROX_RE.search(" ".join([title, source_url, video_url])):
        reasons.append("viltrox_text_match")
    if views:
        reasons.append(f"views={views}")
    if likes:
        reasons.append(f"likes={likes}")
    score = 0
    score += 10000 if _is_youtube_url(candidate_url) else 0
    score += 500 if video_url else 0
    score += 250 if VILTROX_RE.search(" ".join([title, source_url, video_url])) else 0
    score += min(views, 10_000_000) // 1000
    score += min(likes, 500_000) // 100
    score += min(comments, 100_000) // 20
    score -= index
    return {
        "rank_basis": "youtube_first_then_engagement_v0",
        "candidate_score": int(score),
        "source_kind": source_kind,
        "platform": _text(item.get("platform")),
        "handle": _text(item.get("handle")),
        "post_uid": post_uid or _youtube_video_id(candidate_url),
        "title": title[:280],
        "url": source_url or video_url,
        "video_url": video_url,
        "source_url": source_url,
        "published_at": _first_text(post.get("published_at"), post.get("publishedAt"), post.get("timestamp"), post.get("createTimeISO"), post.get("date"), snippet.get("publishedAt")),
        "views": views,
        "likes": likes,
        "comments": comments,
        "reasons": reasons,
    }


def _cached_video_candidates(item: dict[str, Any], *, limit: int = 24) -> list[dict[str, Any]]:
    raw = _loads(item.get("raw_platform_data"), {})
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, post in enumerate(_raw_posts(raw)):
        candidate = _candidate_from_post(post, item=item, index=index, source_kind="vkpi_kol_pool.raw_platform_data")
        if not candidate:
            continue
        key = _first_text(candidate.get("video_url"), candidate.get("url"), candidate.get("post_uid"), candidate.get("title"))
        if not key or key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)
    profile_candidate = _candidate_from_post(
        {
            "id": item.get("handle"),
            "title": item.get("display_name") or item.get("handle"),
            "url": item.get("profile_url"),
            "views": item.get("avg_views"),
        },
        item=item,
        index=len(candidates) + 1000,
        source_kind="vkpi_kol_pool.profile_fallback",
    )
    if profile_candidate and _is_youtube_url(_text(profile_candidate.get("video_url") or profile_candidate.get("url"))):
        key = _text(profile_candidate.get("video_url") or profile_candidate.get("url"))
        if key and key not in seen:
            candidates.append(profile_candidate)
    candidates.sort(key=lambda row: int(row.get("candidate_score") or 0), reverse=True)
    return candidates[: max(1, min(100, int(limit or 24)))]


def _url_readiness(candidate: dict[str, Any] | None) -> dict[str, Any]:
    if not candidate:
        return {
            "valid_video_url": False,
            "provider_path": "unsupported_or_missing_video_url",
            "blocked_reason": "no_cached_video_candidates",
            "risk_flags": ["no_cached_video_candidates"],
        }
    url = _text(candidate.get("video_url") or candidate.get("url"))
    flags: list[str] = ["availability_unknown_no_network_check"]
    if not url:
        flags.append("missing_video_url")
    if url and not _is_youtube_url(url):
        flags.append("non_youtube_url")
    if "shorts/" in _lower(url):
        flags.append("likely_short")
    valid = bool(url and _is_youtube_url(url))
    return {
        "valid_video_url": valid,
        "provider_path": "youtube_direct_url_preflight" if valid else "unsupported_or_missing_video_url",
        "youtube_video_id": _youtube_video_id(url),
        "video_url": url,
        "blocked_reason": "" if valid else ("missing_video_url" if not url else "non_youtube_url"),
        "risk_flags": flags,
    }


def _field_contract() -> dict[str, Any]:
    return {
        "mode": "gemini_video_analysis_field_contract_v0",
        "source_policy": "Gemini may only analyze the selected Top1 video after a future explicit paid-call approval.",
        "required_evidence_backlinks": True,
        "fields": [
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
        ],
    }


def _budget_prompt(item: dict[str, Any], candidate: dict[str, Any] | None) -> str:
    return json.dumps(
        {
            "task": "P4.55 Gemini single KOL Top1 video analysis preflight",
            "kol_pool_id": item.get("id"),
            "platform": item.get("platform"),
            "handle": item.get("handle"),
            "video_url": (candidate or {}).get("video_url") or (candidate or {}).get("url"),
            "fields": _field_contract()["fields"],
        },
        ensure_ascii=False,
    )


def _google_budget_provider(preflight: dict[str, Any]) -> dict[str, Any]:
    providers = preflight.get("providers") if isinstance(preflight.get("providers"), list) else []
    for provider in providers:
        if isinstance(provider, dict) and _lower(provider.get("provider")) == "google":
            return provider
    return {}


def _google_estimated_cost(preflight: dict[str, Any]) -> float:
    provider = _google_budget_provider(preflight)
    try:
        return max(0.0, float(provider.get("estimated_cost_usd") or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _blocked_run(preflight: dict[str, Any], *, reason: str, execute: bool, allow_provider_calls: bool) -> dict[str, Any]:
    return {
        "mode": "controlled_p4_55_gemini_single_kol_run",
        "generated_at": _utcnow(),
        "execution_status": "blocked",
        "executed": False,
        "reason": reason,
        "execute_requested": bool(execute),
        "allow_provider_calls": bool(allow_provider_calls),
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "business_write_db": False,
        "ledger_write_db": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "preflight": preflight,
    }


def build_kol_pool_gemini_preflight(kol_pool_id: int, *, candidate_limit: int = 24, include_budget_preflight: bool = True) -> dict[str, Any]:
    item = kol_pool.get_item(int(kol_pool_id))["item"]
    candidates = _cached_video_candidates(item, limit=candidate_limit)
    top_candidate = candidates[0] if candidates else None
    url_readiness = _url_readiness(top_candidate)
    budget_preflight: dict[str, Any] = {}
    if include_budget_preflight:
        budget_preflight = llm_gateway.budget_preflight(
            _budget_prompt(item, top_candidate),
            purpose="p4_gemini_single_kol",
            max_output_tokens=1600,
            preferred_provider="google",
            cost_tag=GEMINI_SINGLE_KOL_SCOPE,
        )
    google_budget = _google_budget_provider(budget_preflight)
    provider_allowed = bool(google_budget.get("provider_calls_allowed"))
    candidate_ready = bool(url_readiness.get("valid_video_url"))
    if not candidate_ready:
        blocked_reason = str(url_readiness.get("blocked_reason") or "candidate_not_ready")
    elif include_budget_preflight and not provider_allowed:
        blocked_reason = f"provider_gate:{budget_preflight.get('provider_gate_reason') or 'blocked'}"
    else:
        blocked_reason = ""
    return {
        "mode": "read_only_p4_55_gemini_single_kol_preflight",
        "generated_at": _utcnow(),
        "kol_pool_id": int(kol_pool_id),
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "policy": {
            "provider_calls_allowed_by_this_endpoint": False,
            "network_checks": False,
            "new_fact_generation": False,
            "paid_call_requires_explicit_future_approval": True,
        },
        "item": {
            "id": int(item.get("id") or kol_pool_id),
            "platform": _text(item.get("platform")),
            "handle": _text(item.get("handle")),
            "display_name": _text(item.get("display_name")),
            "profile_url": _text(item.get("profile_url")),
            "last_seen_at": _text(item.get("last_seen_at")),
            "sync_status": _text(item.get("sync_status")),
        },
        "candidate_strategy": {
            "name": "cached_top1_youtube_first_then_engagement_v0",
            "candidate_count": len(candidates),
            "limit": max(1, min(100, int(candidate_limit or 24))),
            "sources": ["vkpi_kol_pool.raw_platform_data", "vkpi_kol_pool.profile_fallback"],
        },
        "top_candidate": top_candidate or {},
        "candidate_sample": candidates[:5],
        "url_readiness": url_readiness,
        "field_contract": _field_contract(),
        "budget_preflight": budget_preflight,
        "go_no_go": {
            "candidate_ready_for_live_test": candidate_ready,
            "required_provider": "google",
            "provider_calls_allowed": provider_allowed,
            "provider_configured": bool(google_budget.get("configured")),
            "ready_for_manual_live_test": bool(candidate_ready and provider_allowed),
            "blocked_reason": blocked_reason,
        },
        "checks": {
            "preflight_completed": True,
            "candidate_evaluated": bool(candidates) or bool(url_readiness.get("blocked_reason")),
            "url_readiness_checked": True,
            "budget_preflight_readonly": not bool(budget_preflight.get("provider_calls_made")),
            "no_provider_calls": True,
            "no_llm_calls": True,
            "no_write_db": True,
            "no_sync_triggered": True,
            "no_task_enqueued": True,
        },
    }


def _budget_gate_summary(preflight: dict[str, Any]) -> dict[str, Any]:
    budget = preflight.get("budget_preflight") if isinstance(preflight.get("budget_preflight"), dict) else {}
    google = _google_budget_provider(budget)
    checks = google.get("checks") if isinstance(google.get("checks"), list) else []
    failed_scopes = [str(item.get("scope") or "") for item in checks if isinstance(item, dict) and not bool(item.get("allowed"))]
    return {
        "provider_gate_reason": _text(budget.get("provider_gate_reason")),
        "required_provider": "google",
        "provider_configured": bool(google.get("configured")),
        "provider_calls_allowed": bool(google.get("provider_calls_allowed")),
        "estimated_cost_usd": _google_estimated_cost(budget),
        "scopes": google.get("scopes") if isinstance(google.get("scopes"), list) else [],
        "failed_scopes": [scope for scope in failed_scopes if scope],
        "monthly_env_budget_usd": budget.get("monthly_env_budget_usd"),
        "monthly_env_remaining_usd": budget.get("monthly_env_remaining_usd"),
        "force_offline": bool(budget.get("force_offline")),
    }


def _gemini_decision(preflight: dict[str, Any]) -> tuple[str, str, list[str]]:
    strategy = preflight.get("candidate_strategy") if isinstance(preflight.get("candidate_strategy"), dict) else {}
    readiness = preflight.get("url_readiness") if isinstance(preflight.get("url_readiness"), dict) else {}
    go_no_go = preflight.get("go_no_go") if isinstance(preflight.get("go_no_go"), dict) else {}
    budget = _budget_gate_summary(preflight)
    blockers: list[str] = []
    if _int(strategy.get("candidate_count")) <= 0:
        blockers.append("no_cached_video_candidates")
    if not bool(readiness.get("valid_video_url")):
        blockers.append(str(readiness.get("blocked_reason") or "invalid_video_url"))
    if blockers:
        return "no_go_for_this_kol", "candidate_not_ready", list(dict.fromkeys(blockers))
    if not bool(go_no_go.get("provider_calls_allowed")):
        reason = str(go_no_go.get("blocked_reason") or budget.get("provider_gate_reason") or "provider_gate_blocked")
        blockers.append(reason)
        for scope in budget.get("failed_scopes") or []:
            blockers.append(f"budget_scope_blocked:{scope}")
        return "hold", "provider_or_budget_gate_not_ready", list(dict.fromkeys(blockers))
    return "go_manual_single_call", "ready_for_one_explicit_paid_call", []


def build_kol_pool_gemini_go_no_go(kol_pool_id: int, *, candidate_limit: int = 24) -> dict[str, Any]:
    """Build a read-only P4.56 go/no-go report for the next Gemini step."""

    preflight = build_kol_pool_gemini_preflight(
        int(kol_pool_id),
        candidate_limit=candidate_limit,
        include_budget_preflight=True,
    )
    decision, reason, blockers = _gemini_decision(preflight)
    readiness = preflight.get("url_readiness") if isinstance(preflight.get("url_readiness"), dict) else {}
    strategy = preflight.get("candidate_strategy") if isinstance(preflight.get("candidate_strategy"), dict) else {}
    go_no_go = preflight.get("go_no_go") if isinstance(preflight.get("go_no_go"), dict) else {}
    budget = _budget_gate_summary(preflight)
    operator_gates = {
        "requires_execute_flag": True,
        "requires_allow_provider_calls_flag": True,
        "requires_budget_gate": True,
        "requires_single_kol_only": True,
        "batch_allowed": False,
        "business_write_db_allowed": False,
        "sync_allowed": False,
        "task_enqueue_allowed": False,
    }
    risks = [
        {
            "risk": "analyzer_model_order_unverified",
            "severity": "medium",
            "mitigation": "Before a paid call, verify the concrete Gemini model names in runtime config or update the analyzer model list.",
        },
        {
            "risk": "youtube_availability_not_network_checked",
            "severity": "low" if readiness.get("valid_video_url") else "high",
            "mitigation": "The preflight only validates URL shape. The paid call remains the first real availability check.",
        },
        {
            "risk": "actual_provider_cost_unknown_until_call",
            "severity": "medium",
            "mitigation": "Ledger records the budget preflight estimate and marks actual_cost_unknown for the first live run.",
        },
    ]
    next_steps: list[str]
    if decision == "go_manual_single_call":
        next_steps = [
            "Run exactly one KOL with --execute --allow-provider-calls after operator approval.",
            "Record cost, latency, analyzed status, returned fields, and error if any.",
            "Do not start batch design until the single live result is reviewed.",
        ]
    elif decision == "hold":
        next_steps = [
            "Keep provider calls disabled.",
            "Resolve budget/provider blockers listed in this report.",
            "Re-run this go/no-go report before any paid call.",
        ]
    else:
        next_steps = [
            "Pick a different KOL with a cached YouTube video candidate or refresh cache only under approved policy.",
            "Do not call Gemini for this KOL in the current state.",
        ]
    checks = {
        "preflight_completed": bool((preflight.get("checks") or {}).get("preflight_completed")),
        "candidate_evaluated": bool((preflight.get("checks") or {}).get("candidate_evaluated")),
        "budget_gate_checked": bool(preflight.get("budget_preflight")),
        "decision_recorded": bool(decision),
        "no_provider_calls": True,
        "no_llm_calls": True,
        "no_write_db": True,
        "no_sync_triggered": True,
        "no_task_enqueued": True,
        "batch_still_blocked": True,
    }
    return {
        "mode": "read_only_p4_56_gemini_go_no_go_report",
        "generated_at": _utcnow(),
        "kol_pool_id": int(kol_pool_id),
        "decision": decision,
        "decision_reason": reason,
        "blockers": blockers,
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "summary": {
            "candidate_count": _int(strategy.get("candidate_count")),
            "valid_video_url": bool(readiness.get("valid_video_url")),
            "provider_path": readiness.get("provider_path") or "",
            "top_video_url": (preflight.get("top_candidate") or {}).get("video_url") or (preflight.get("top_candidate") or {}).get("url") or "",
            "provider_calls_allowed": bool(go_no_go.get("provider_calls_allowed")),
            "ready_for_manual_live_test": bool(go_no_go.get("ready_for_manual_live_test")),
            "provider_gate_reason": budget.get("provider_gate_reason") or "",
        },
        "budget_gate": budget,
        "operator_gates": operator_gates,
        "risks": risks,
        "next_steps": next_steps,
        "checks": checks,
        "passed": all(bool(value) for value in checks.values()),
        "preflight": preflight,
    }


async def run_kol_pool_gemini_single(
    kol_pool_id: int,
    *,
    execute: bool = False,
    allow_provider_calls: bool = False,
    candidate_limit: int = 24,
    timeout_seconds: int = 900,
    analyzer: AnalyzerFn | None = None,
) -> dict[str, Any]:
    """Run one controlled Gemini single-KOL analysis, or return a blocked plan.

    The default path is intentionally read-only. A real provider call requires
    both execute=True and allow_provider_calls=True, a valid cached YouTube URL,
    and a passing Google budget preflight.
    """

    preflight = build_kol_pool_gemini_preflight(
        int(kol_pool_id),
        candidate_limit=candidate_limit,
        include_budget_preflight=True,
    )
    if not execute:
        return _blocked_run(preflight, reason="execute_not_requested", execute=execute, allow_provider_calls=allow_provider_calls)
    if not allow_provider_calls:
        return _blocked_run(preflight, reason="provider_calls_not_allowed", execute=execute, allow_provider_calls=allow_provider_calls)
    go_no_go = preflight.get("go_no_go") if isinstance(preflight.get("go_no_go"), dict) else {}
    if not bool(go_no_go.get("candidate_ready_for_live_test")):
        return _blocked_run(
            preflight,
            reason=str(go_no_go.get("blocked_reason") or "candidate_not_ready"),
            execute=execute,
            allow_provider_calls=allow_provider_calls,
        )
    if not bool(go_no_go.get("provider_calls_allowed")):
        return _blocked_run(
            preflight,
            reason=str(go_no_go.get("blocked_reason") or "provider_gate_blocked"),
            execute=execute,
            allow_provider_calls=allow_provider_calls,
        )

    top_candidate = preflight.get("top_candidate") if isinstance(preflight.get("top_candidate"), dict) else {}
    url = _text(top_candidate.get("video_url") or top_candidate.get("url"))
    if not url:
        return _blocked_run(preflight, reason="missing_video_url", execute=execute, allow_provider_calls=allow_provider_calls)
    title = _text(top_candidate.get("title")) or _text((preflight.get("item") or {}).get("display_name"))
    creator_handle = _text((preflight.get("item") or {}).get("handle"))
    if analyzer is None:
        from app.services.ai.analyzers.gemini_video import analyze_youtube_with_gemini

        analyzer = analyze_youtube_with_gemini

    started = time.monotonic()
    result: dict[str, Any]
    try:
        result = await asyncio.wait_for(
            analyzer(url, title, creator_handle),
            timeout=max(30, min(3600, int(timeout_seconds or 900))),
        )
    except TimeoutError as exc:
        result = {"analyzed": False, "method": "gemini_single_kol_timeout", "error": f"timeout_after_{timeout_seconds}s", "exception": str(exc)}
    except Exception as exc:
        result = {"analyzed": False, "method": "gemini_single_kol_exception", "error": f"{type(exc).__name__}: {str(exc)[:500]}"}
    latency_ms = int((time.monotonic() - started) * 1000)
    analyzed = bool(result.get("analyzed"))
    estimated_cost = _google_estimated_cost(preflight.get("budget_preflight") if isinstance(preflight.get("budget_preflight"), dict) else {})
    ledger = budget_guard.record_cost(
        scope=GEMINI_SINGLE_KOL_SCOPE,
        cron_task="p4_gemini_single_kol",
        ai_provider="gemini",
        model_name=str(result.get("method") or "gemini_single_kol"),
        cost_usd=max(estimated_cost, 0.01),
        kol_pool_id=int(kol_pool_id),
        metadata={
            "status": "success" if analyzed else "attempted_error",
            "actual_cost_unknown": True,
            "estimated_cost_source": "llm_gateway_budget_preflight",
            "video_url": url,
            "latency_ms": latency_ms,
            "error": result.get("error") or "",
        },
        extra_scopes=["monthly_total", "single_call", "provider:gemini"],
    )
    return {
        "mode": "controlled_p4_55_gemini_single_kol_run",
        "generated_at": _utcnow(),
        "execution_status": "completed" if analyzed else "provider_error",
        "executed": True,
        "execute_requested": True,
        "allow_provider_calls": True,
        "provider_calls": True,
        "llm_calls": True,
        "write_db": True,
        "business_write_db": False,
        "ledger_write_db": True,
        "sync_triggered": False,
        "task_enqueued": False,
        "latency_ms": latency_ms,
        "kol_pool_id": int(kol_pool_id),
        "video_url": url,
        "preflight": preflight,
        "analysis": result,
        "ledger": ledger,
        "checks": {
            "provider_call_was_explicit": True,
            "budget_gate_passed": True,
            "business_write_db_disabled": True,
            "sync_not_triggered": True,
            "task_not_enqueued": True,
            "ledger_recorded": bool(ledger.get("recorded")),
        },
    }
