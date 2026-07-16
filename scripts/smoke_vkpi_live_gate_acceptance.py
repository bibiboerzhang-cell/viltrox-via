#!/usr/bin/env python3
"""P2.12 live provider gate acceptance smoke.

Default mode is offline: it verifies provider visibility, redaction, and budget
gate behavior without calling YouTube or Apify. Set VKPI_P2_12_LIVE=1 to run one
minimal live provider call after the offline checks are green.
"""
from __future__ import annotations
from stdout_utils import out as stdout_out

import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def _assert(condition: bool, message: str, payload: Any = None) -> None:
    if not condition:
        suffix = "" if payload is None else f": {payload}"
        raise AssertionError(message + suffix)


def _provider_rows() -> list[dict[str, Any]]:
    from app.services.vkpi import settings as vkpi_settings

    status = vkpi_settings.provider_statuses()
    _assert(status.get("full_key_readable") is False, "provider status must not expose full keys", status)
    rows = status.get("providers") or []
    _assert(isinstance(rows, list) and rows, "provider rows missing", status)
    payload_text = json.dumps(status, ensure_ascii=False, default=str)
    for forbidden in ("APIFY_TOKEN", "YOUTUBE_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        _assert(forbidden not in payload_text, f"provider status leaked env key name {forbidden}")
    for row in rows:
        _assert(row.get("key_visible") is False, "provider key visibility must stay false", row)
        mask = str(row.get("key_mask") or "")
        if mask:
            _assert(mask.endswith("...") and len(mask) <= 24, "provider key mask too revealing", row)
    return [dict(row) for row in rows]


async def _assert_youtube_probe_supported() -> None:
    from app.services.system import provider_health

    result = await provider_health.probe_provider("youtube", api_key="")
    _assert(result.get("provider") == "youtube", "youtube probe should be registered", result)
    _assert(result.get("error") == "missing key", "empty youtube probe should not call network", result)


def _assert_budget_gate() -> None:
    from app.services.vkpi import industry_snapshot_collector as collector

    original_config = collector._platform_config
    collector._platform_config = lambda platform: {"crawl_enabled": True, "monthly_budget_usd": 0}
    try:
        result = collector.provider_gate({"platform": "youtube", "crawl_enabled": True}, force=False)
    finally:
        collector._platform_config = original_config
    _assert(result.get("allowed") is False, "budget=0 should close provider gate", result)
    _assert(result.get("provider_status") == "budget_disabled", "budget gate should report budget_disabled", result)


def _assert_live_guard_script_shape() -> None:
    guard = (ROOT / "scripts" / "smoke_vkpi_crawler_live_mapping_guard.py").read_text(encoding="utf-8")
    configure = (ROOT / "scripts" / "configure_platform_crawl.py").read_text(encoding="utf-8")
    _assert("--live" in guard and "--ignore-gates" in guard, "live guard must require explicit live flags")
    _assert("print(json.dumps(readiness" in guard, "live guard should emit redacted readiness")
    _assert("--apply 时必须传 --staff-id" in configure, "configure script must require audited apply")
    _assert("source scripts/runtime_env.sh" in configure, "configure script must document runtime env source")


def _run_optional_live() -> dict[str, Any]:
    from app.platform.industry_crawlers import get_crawler
    from app.services.vkpi.industry_snapshot_collector import calculate_kpis

    platform = os.environ.get("VKPI_P2_12_LIVE_PLATFORM", "youtube").strip().lower()
    handle = os.environ.get("VKPI_P2_12_LIVE_HANDLE", "@viltroxofficial").strip()
    crawler = get_crawler(platform)
    _assert(crawler is not None, "live crawler not registered", {"platform": platform})
    _assert(bool(getattr(crawler, "configured", False)), "live crawler not configured", {"platform": platform})
    try:
        profile_payload = crawler.crawl_channel_profile(handle, channel_id="", max_posts=1)
    except TypeError:
        profile_payload = crawler.crawl_channel_profile(handle, channel_id="")
    raw_data = {
        "source": f"p2_12_{platform}_live_acceptance",
        "profile": profile_payload,
        "videos": [],
        "kpi_status": profile_payload.get("sync_status") or profile_payload.get("provider_status"),
    }
    kpis = calculate_kpis(raw_data)
    provider_status = str(profile_payload.get("provider_status") or "")
    _assert(provider_status in {"ok", "no_results"}, "live provider call did not reach provider cleanly", {"provider_status": provider_status, "sync_status": profile_payload.get("sync_status")})
    return {
        "mode": "live",
        "platform": platform,
        "provider_status": provider_status,
        "sync_status": profile_payload.get("sync_status"),
        "items": len(profile_payload.get("items") or []),
        "mapped_keys": sorted(key for key, value in kpis.items() if value is not None)[:12],
    }


def main() -> None:
    import asyncio

    rows = _provider_rows()
    names = {str(row.get("provider") or "") for row in rows}
    required = {"apify", "anthropic", "google", "openai", "youtube"}
    _assert(required.issubset(names), "required provider rows missing", {"required": sorted(required), "actual": sorted(names)})

    asyncio.run(_assert_youtube_probe_supported())
    _assert_budget_gate()
    _assert_live_guard_script_shape()

    live_result: dict[str, Any] | None = None
    if os.environ.get("VKPI_P2_12_LIVE") == "1":
        live_result = _run_optional_live()

    stdout_out(json.dumps({"providers": sorted(names), "live_result": live_result}, ensure_ascii=False, default=str))
    stdout_out("VKPI_LIVE_GATE_ACCEPTANCE_SMOKE_OK")


if __name__ == "__main__":
    main()
