"""Read-only market data and LLM provider preflight for V-KPI.

This module is the first narrow gate before Google/Reddit/news/LLM live
experiments. Defaults are intentionally conservative: no provider calls, no
database writes, no sync jobs, and no task enqueueing.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.platform import llm_gateway


PROJECT_ROOT = Path(__file__).resolve().parents[4]
ENV_PATH = Path(os.environ.get("VILTROX_ENV_PATH", PROJECT_ROOT / ".env"))
DEFAULT_PROMPT = (
    "用中文给 Viltrox 市场团队做一条只基于证据的趋势测试摘要。"
    "不要编造事实；如果没有证据，请输出需要补充的数据源。"
)


PROVIDER_SOURCES: list[dict[str, Any]] = [
    {
        "source_key": "google_gemini_llm",
        "label": "Google Gemini LLM",
        "category": "llm_summary",
        "provider": "google",
        "env_any": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "live_probe": "provider_health_google_models",
        "purpose": "market_trend_summary_test",
        "default_live_limit": 1,
        "write_targets": ["vkpi_llm_calls", "vkpi_ai_cost_ledger"],
        "execution_gate": "manual_single_llm_smoke_only",
        "notes": "用于行业趋势/竞品/平台建议的证据摘要；默认不调用，必须预算闸门通过。",
    },
    {
        "source_key": "google_youtube_data",
        "label": "Google YouTube Data API",
        "category": "market_video_search",
        "provider": "youtube",
        "env_any": ["YOUTUBE_API_KEY", "GOOGLE_YOUTUBE_API_KEY"],
        "live_probe": "provider_health_youtube_channels",
        "purpose": "youtube_market_watch_test",
        "default_live_limit": 1,
        "write_targets": ["vkpi_market_sources", "vkpi_market_mentions"],
        "execution_gate": "manual_single_query_or_quota_fallback",
        "notes": "用于 YouTube 评测/竞品/热点视频观察；撞 quota 后才考虑 Apify fallback。",
    },
    {
        "source_key": "google_news_rss",
        "label": "Google News RSS",
        "category": "news_signal",
        "provider": "google_news",
        "env_any": [],
        "live_probe": "allowlisted_http_fetch_after_review",
        "purpose": "google_news_market_smoke",
        "default_live_limit": 5,
        "write_targets": ["vkpi_market_sources", "vkpi_market_mentions"],
        "execution_gate": "manual_allowlisted_rss_smoke_only",
        "notes": "用于 Google News RSS 小样本，只能按 allowlisted query 手动 smoke，不默认轮询。",
    },
    {
        "source_key": "google_programmable_search",
        "label": "Google Programmable Search",
        "category": "web_search",
        "provider": "google_search",
        "env_all": ["GOOGLE_CSE_CX"],
        "env_any": ["GOOGLE_CSE_API_KEY", "GOOGLE_SEARCH_API_KEY"],
        "live_probe": "not_implemented",
        "purpose": "web_market_news_test",
        "default_live_limit": 3,
        "write_targets": ["vkpi_market_sources", "vkpi_market_mentions"],
        "execution_gate": "allowlisted_query_smoke_only",
        "notes": "用于 Google 新闻/网页趋势小样本；当前只做配置预检，不自动抓取。",
    },
    {
        "source_key": "google_trends",
        "label": "Google Trends",
        "category": "trend_signal",
        "provider": "google_trends",
        "env_any": [],
        "live_probe": "not_implemented",
        "purpose": "trend_interest_test",
        "default_live_limit": 0,
        "write_targets": ["vkpi_market_sources", "vkpi_market_mentions"],
        "execution_gate": "design_contract_first",
        "notes": "Google Trends 没有稳定官方低风险 API；先设计合规路径，不默认接非官方抓取。",
    },
    {
        "source_key": "reddit_community",
        "label": "Reddit community watch",
        "category": "community_signal",
        "provider": "reddit",
        "env_all": ["REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT"],
        "env_any": ["APIFY_TOKEN"],
        "live_probe": "not_implemented",
        "purpose": "reddit_market_watch_test",
        "default_live_limit": 5,
        "write_targets": ["vkpi_market_sources", "vkpi_market_mentions"],
        "execution_gate": "oauth_or_apify_best_effort_after_review",
        "notes": "用于镜头/竞品/VOC 讨论观察；需要关键词和 subreddit 白名单。",
    },
    {
        "source_key": "rss_industry_news",
        "label": "Industry RSS/news feeds",
        "category": "news_signal",
        "provider": "rss",
        "env_any": [],
        "live_probe": "allowlisted_http_fetch_after_review",
        "purpose": "rss_market_news_test",
        "default_live_limit": 10,
        "write_targets": ["vkpi_market_sources", "vkpi_market_mentions"],
        "execution_gate": "allowlisted_sources_only",
        "notes": "低成本新闻源；需要先固定域名、RSS URL、缓存策略和去重字段。",
    },
    {
        "source_key": "apify_market_fallback",
        "label": "Apify fallback",
        "category": "provider_fallback",
        "provider": "apify",
        "env_any": ["APIFY_TOKEN"],
        "live_probe": "provider_health_apify_user",
        "purpose": "apify_market_fallback_test",
        "default_live_limit": 1,
        "write_targets": ["vkpi_market_sources", "vkpi_market_mentions"],
        "execution_gate": "only_after_official_api_quota_or_missing_capability",
        "notes": "只作为官方 API 不可用或 quota 耗尽后的兜底，不用于放大全量刷新。",
    },
    {
        "source_key": "openai_llm",
        "label": "OpenAI LLM",
        "category": "llm_summary",
        "provider": "openai",
        "env_any": ["OPENAI_API_KEY"],
        "live_probe": "provider_health_openai_models",
        "purpose": "market_trend_summary_test",
        "default_live_limit": 1,
        "write_targets": ["vkpi_llm_calls", "vkpi_ai_cost_ledger"],
        "execution_gate": "manual_single_llm_smoke_only",
        "notes": "可作为 GPT 摘要路径；默认不调用，必须预算闸门通过。",
    },
    {
        "source_key": "anthropic_llm",
        "label": "Claude LLM",
        "category": "llm_summary",
        "provider": "anthropic",
        "env_any": ["ANTHROPIC_API_KEY"],
        "live_probe": "provider_health_anthropic_models",
        "purpose": "market_trend_summary_test",
        "default_live_limit": 1,
        "write_targets": ["vkpi_llm_calls", "vkpi_ai_cost_ledger"],
        "execution_gate": "manual_single_llm_smoke_only",
        "notes": "可作为 Claude 总结路径；默认不调用，必须预算闸门通过。",
    },
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _env_file_values() -> dict[str, str]:
    try:
        lines = ENV_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return {}
    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _safe_env_status(names: list[str], env_file_values: dict[str, str] | None = None) -> dict[str, dict[str, Any]]:
    file_values = env_file_values if env_file_values is not None else _env_file_values()
    status: dict[str, dict[str, Any]] = {}
    for name in names:
        process_value = os.environ.get(name, "").strip()
        file_value = str(file_values.get(name) or "").strip()
        source = "process" if process_value else ("env_file" if file_value else "")
        status[name] = {
            "configured": bool(process_value or file_value),
            "source": source,
            "value_exposed": False,
        }
    return status


def _source_status(source: dict[str, Any], env_file_values: dict[str, str] | None = None) -> dict[str, Any]:
    env_any = list(source.get("env_any") or [])
    env_all = list(source.get("env_all") or [])
    env_names = sorted(set(env_any + env_all))
    env_status = _safe_env_status(env_names, env_file_values)
    any_ok = True if not env_any else any(bool(env_status[name]["configured"]) for name in env_any)
    all_ok = all(bool(env_status[name]["configured"]) for name in env_all)
    configured = any_ok and all_ok
    if configured:
        readiness = "configured"
    elif any(bool(item.get("configured")) for item in env_status.values()):
        readiness = "partial"
    elif not env_names:
        readiness = "no_secret_required"
    else:
        readiness = "not_configured"
    return {
        **source,
        "env_status": env_status,
        "configured_env_keys": [name for name, item in env_status.items() if item.get("configured")],
        "readiness": readiness,
        "configured": configured,
        "provider_calls_allowed_by_default": False,
        "write_db_allowed_by_default": False,
        "sync_allowed_by_default": False,
        "task_enqueue_allowed_by_default": False,
    }


def build_provider_preflight(
    *,
    prompt: str = DEFAULT_PROMPT,
    preferred_llm_provider: str = "google",
    max_output_tokens: int = 160,
) -> dict[str, Any]:
    """Build a no-side-effect market provider and LLM readiness report."""

    env_file_values = _env_file_values()
    sources = [_source_status(source, env_file_values) for source in PROVIDER_SOURCES]
    llm_preflight = llm_gateway.budget_preflight(
        prompt,
        purpose="market_provider_smoke",
        max_output_tokens=max_output_tokens,
        preferred_provider=preferred_llm_provider,
        cost_tag="cron:market_provider_smoke",
    )
    llm_providers = llm_preflight.get("providers") if isinstance(llm_preflight.get("providers"), list) else []
    live_probe_candidates = [
        source
        for source in sources
        if source.get("configured") and str(source.get("live_probe") or "").startswith("provider_health_")
    ]
    checks = {
        "secrets_not_exposed": all(
            item.get("value_exposed") is False
            for source in sources
            for item in (source.get("env_status") or {}).values()
        ),
        "provider_calls_blocked_by_default": all(source.get("provider_calls_allowed_by_default") is False for source in sources),
        "writes_blocked_by_default": all(source.get("write_db_allowed_by_default") is False for source in sources),
        "sync_blocked_by_default": all(source.get("sync_allowed_by_default") is False for source in sources),
        "llm_budget_gate_present": bool(llm_providers),
        "llm_gateway_uses_budget_scope": all("cron:market_provider_smoke" in (provider.get("scopes") or []) for provider in llm_providers),
        "google_llm_path_detected": any(source.get("source_key") == "google_gemini_llm" for source in sources),
        "market_data_paths_detected": all(
            any(source.get("source_key") == key for source in sources)
            for key in ("google_youtube_data", "reddit_community", "rss_industry_news")
        ),
        "live_probe_candidates_are_bounded": all(
            int(source.get("default_live_limit") or 0) <= 1
            and str(source.get("live_probe") or "").startswith("provider_health_")
            for source in live_probe_candidates
        ),
    }
    return {
        "mode": "vkpi_market_provider_preflight_v0",
        "generated_at": _now(),
        "provider_calls": False,
        "llm_calls": False,
        "external_http_calls": False,
        "write_db": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "passed": all(bool(value) for value in checks.values()),
        "checks": checks,
        "summary": {
            "source_count": len(sources),
            "configured_sources": sum(1 for source in sources if source.get("configured")),
            "partial_sources": sum(1 for source in sources if source.get("readiness") == "partial"),
            "not_configured_sources": sum(1 for source in sources if source.get("readiness") == "not_configured"),
            "llm_provider_calls_allowed": bool(llm_preflight.get("provider_calls_allowed")),
            "llm_gate_reason": llm_preflight.get("provider_gate_reason"),
            "live_probe_candidate_count": len(live_probe_candidates),
        },
        "sources": sources,
        "llm_preflight": llm_preflight,
        "next_live_tests": [
            {
                "source_key": source["source_key"],
                "provider": source["provider"],
                "live_probe": source["live_probe"],
                "limit": source["default_live_limit"],
                "gate": source["execution_gate"],
            }
            for source in live_probe_candidates
        ],
    }
