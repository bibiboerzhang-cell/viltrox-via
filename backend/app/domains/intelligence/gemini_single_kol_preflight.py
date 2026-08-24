"""P4.55 Gemini single-KOL preflight and controlled live-run harness.

The default preflight path only inspects cached V-KPI evidence. A real Gemini
call is only possible through run_kol_pool_gemini_single() with explicit flags,
valid cached URL readiness, and passing budget gates.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.core.coerce import _text
from app.core.gemini_models import DEFAULT_FINAL_V1_CHAIN, DEFAULT_VIDEO_GEMINI_MODEL
from app.domains.intelligence.gemini_single_kol_candidates import (
    DURABLE_EVIDENCE_SCAN_LIMIT,
    _cached_video_candidates,
    _canonical_youtube_url,
    _int,
    _lower,
    _url_readiness,
)
from app.domains.kol import pool as kol_pool
from app.platform import llm_gateway


GEMINI_SINGLE_KOL_SCOPE = "cron:p4_gemini_single_kol"
STRICT_GOOGLE_COST_AUTHORITY = "llm_production_google_generate_content_v1"
AnalyzerFn = Callable[..., Awaitable[dict[str, Any]]]


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _field_contract() -> dict[str, Any]:
    return {
        "mode": "gemini_video_analysis_field_contract_v1",
        "schema_version": "final_v1",
        "model_chain": list(DEFAULT_FINAL_V1_CHAIN),
        "cost_scope": GEMINI_SINGLE_KOL_SCOPE,
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
    safe_candidate_limit = max(1, min(100, int(candidate_limit or 24)))
    item_payload = kol_pool.get_item(
        int(kol_pool_id),
        include_raw_for_derivation=True,
        # Load the requested candidate window below.  get_item's public detail
        # projection is intentionally fixed at three rows and can include
        # image evidence, so it cannot satisfy this preflight's 1..100 limit.
        include_video_evidence=False,
    )
    item = dict(item_payload.get("item") or {})
    raw_platform_data = item_payload.pop("_raw_platform_data_for_derivation", None)
    # get_item always emits ``video_evidence=[]`` when include_video_evidence
    # is false.  That empty public placeholder is not proof that no durable
    # evidence exists, so never use it to suppress the bounded reader.
    item.pop("video_evidence", None)
    durable_video_evidence = kol_pool._video_evidence_for_kol(
        int(kol_pool_id),
        # The shared reader also returns image/carousel evidence and applies
        # LIMIT before this module can filter to videos.  Scan its bounded
        # maximum so a leading image cannot hide a later durable video, then
        # truncate the scored video candidates to safe_candidate_limit below.
        limit=DURABLE_EVIDENCE_SCAN_LIMIT,
        include_inactive=False,
    )
    candidates = _cached_video_candidates(
        item,
        raw_platform_data=raw_platform_data,
        durable_video_evidence=(
            durable_video_evidence
            if isinstance(durable_video_evidence, list)
            else []
        ),
        limit=safe_candidate_limit,
    )
    top_candidate = candidates[0] if candidates else None
    url_readiness = _url_readiness(top_candidate)
    budget_preflight: dict[str, Any] = {}
    if include_budget_preflight:
        budget_preflight = llm_gateway.budget_preflight(
            _budget_prompt(item, top_candidate),
            purpose="p4_gemini_single_kol",
            max_output_tokens=1600,
            preferred_provider="google",
            model_override=DEFAULT_VIDEO_GEMINI_MODEL,
            model_fallbacks=tuple(
                ("google", model) for model in DEFAULT_FINAL_V1_CHAIN[1:]
            ),
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
            "name": "cached_top1_youtube_then_viltrox_relevance_then_engagement_v1",
            "candidate_count": len(candidates),
            "limit": safe_candidate_limit,
            "sources": [
                "vkpi_kol_video_evidence",
                "vkpi_kol_pool.raw_platform_data",
                "vkpi_kol_pool.profile_fallback",
            ],
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
            "risk": "runtime_model_readiness_can_change_after_preflight",
            "severity": "low",
            "mitigation": "The live strict adapter repeats model-specific readiness and budget gates before every provider attempt.",
        },
        {
            "risk": "youtube_availability_not_network_checked",
            "severity": "low" if readiness.get("valid_video_url") else "high",
            "mitigation": "The preflight only validates URL shape. The paid call remains the first real availability check.",
        },
        {
            "risk": "actual_provider_cost_unknown_until_call",
            "severity": "medium",
            "mitigation": "The strict adapter reserves first, records confirmed usage once, and keeps uncertain attempts reserved for reconciliation.",
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


def _strict_ledger_summary(result: dict[str, Any]) -> dict[str, Any]:
    """Summarize the strict adapter's ledger evidence without writing again."""

    attempts = [
        dict(attempt)
        for attempt in (result.get("llm_attempts") or [])
        if isinstance(attempt, dict)
    ]
    states = [_text(attempt.get("state")) for attempt in attempts]
    reported_authority = _text(result.get("cost_authority"))
    authoritative = bool(
        attempts
        and reported_authority == STRICT_GOOGLE_COST_AUTHORITY
        and all(
            _text(attempt.get("authority")) == STRICT_GOOGLE_COST_AUTHORITY
            for attempt in attempts
        )
    )
    recorded_states = {
        "budget_blocked",
        "model_mismatch",
        "provider_blocked",
        "released",
        "settled",
        "unknown",
        "usage_missing",
    }
    actual_cost_usd = 0.0
    for attempt in attempts:
        if _text(attempt.get("state")) != "settled":
            continue
        try:
            actual_cost_usd += max(0.0, float(attempt.get("actual_cost_usd") or 0.0))
        except (TypeError, ValueError):
            continue
    return {
        "recorded": authoritative and any(state in recorded_states for state in states),
        "authority": reported_authority,
        "authoritative": authoritative,
        "wrapper_record_cost_called": False,
        "attempt_count": len(attempts),
        "settled_attempt_count": states.count("settled"),
        "released_attempt_count": states.count("released"),
        "unknown_attempt_count": states.count("unknown"),
        "states": states,
        "actual_cost_usd": round(actual_cost_usd, 8),
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
    url, _video_id = _canonical_youtube_url(
        top_candidate.get("video_url") or top_candidate.get("url")
    )
    if not url:
        return _blocked_run(preflight, reason="invalid_video_url", execute=execute, allow_provider_calls=allow_provider_calls)
    title = _text(top_candidate.get("title")) or _text((preflight.get("item") or {}).get("display_name"))
    creator_handle = _text((preflight.get("item") or {}).get("handle"))
    is_default_analyzer = analyzer is None
    if is_default_analyzer:
        from app.services.ai.analyzers.gemini_video import analyze_youtube_with_gemini

        analyzer = analyze_youtube_with_gemini

    started = time.monotonic()
    result: dict[str, Any]
    llm_context = {
        "purpose": "p4_gemini_single_kol",
        "cost_tag": GEMINI_SINGLE_KOL_SCOPE,
        "execution_class": llm_gateway.PRODUCTION_EXECUTION_CLASS,
        "metadata": {
            "surface": "gemini_single_kol_preflight",
            "task_binding": "audit_video_analysis",
            "target_type": "kol_pool",
            "target_id": int(kol_pool_id),
            "target_label": f"kol_pool:{int(kol_pool_id)}",
            "phase": "video_analysis",
            "schema_version": "final_v1",
        },
    }
    try:
        analysis_call = (
            analyzer(
                url,
                title,
                creator_handle,
                schema_version="final_v1",
                final_v1_models=list(DEFAULT_FINAL_V1_CHAIN),
                llm_context=llm_context,
            )
            if is_default_analyzer
            else analyzer(url, title, creator_handle)
        )
        result = await asyncio.wait_for(
            analysis_call,
            timeout=max(30, min(3600, int(timeout_seconds or 900))),
        )
    except TimeoutError as exc:
        result = {"analyzed": False, "method": "gemini_single_kol_timeout", "error": f"timeout_after_{timeout_seconds}s", "exception": str(exc)}
    except Exception as exc:
        result = {"analyzed": False, "method": "gemini_single_kol_exception", "error": f"{type(exc).__name__}: {str(exc)[:500]}"}
    latency_ms = int((time.monotonic() - started) * 1000)
    analyzed = bool(result.get("analyzed"))
    ledger = _strict_ledger_summary(result)
    ledger_recorded = bool(ledger.get("recorded"))
    return {
        "mode": "controlled_p4_55_gemini_single_kol_run",
        "generated_at": _utcnow(),
        "execution_status": "completed" if analyzed else "provider_error",
        "executed": True,
        "execute_requested": True,
        "allow_provider_calls": True,
        "provider_calls": True,
        "llm_calls": True,
        "write_db": ledger_recorded,
        "business_write_db": False,
        "ledger_write_db": ledger_recorded,
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
            "ledger_recorded": ledger_recorded,
            "single_accounting_authority": bool(ledger.get("authoritative")),
            "wrapper_cost_recorded": False,
        },
    }
