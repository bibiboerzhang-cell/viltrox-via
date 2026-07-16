#!/usr/bin/env python3
"""Smoke test for the P1 compatibility layer.

This does not install Reddit/Facebook/comments/sentiment features. It verifies
that the shared compatibility contracts are ready before P1 packages land.
"""
from __future__ import annotations
from stdout_utils import out as stdout_out

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main() -> int:
    failures: list[str] = []

    from app.api.dependencies import perms
    import importlib

    platform_crawl_settings = importlib.import_module("app.domains.settings.platform_crawl")
    from app.services.vkpi import p1_compat

    cases = {
        "vkpi.comments.read": ("vkpi", "read"),
        "vkpi.comments.collect": ("vkpi", "write"),
        "vkpi.comments.batch_collect": ("vkpi", "admin"),
        "vkpi.sentiment.analyze": ("vkpi", "write"),
        "vkpi.sentiment.backfill": ("vkpi", "admin"),
        "vkpi.pillars.classify": ("vkpi", "write"),
        "vkpi.weekly_reports.generate_all": ("vkpi", "admin"),
    }
    for permission_key, expected in cases.items():
        actual = perms._permission_to_tab_level(permission_key)
        if actual != expected:
            failures.append(f"{permission_key} mapped to {actual}, expected {expected}")

    dep = perms.require_permission("vkpi.comments.read")
    if not callable(dep):
        failures.append("require_permission did not return a dependency callable")

    if p1_compat.admin_router_prefix("comments") != "/api/admin/vkpi/comments":
        failures.append("admin_router_prefix('comments') did not use /api/admin/vkpi")

    sample = {
        "id": 12,
        "account_id": 34,
        "platform": "youtube",
        "platform_post_id": "yt_123",
        "raw_platform_data": json.dumps({"source": "smoke"}),
    }
    normalized = p1_compat.normalize_industry_post(sample)
    if normalized.get("external_post_id") != "yt_123":
        failures.append(f"platform_post_id alias failed: {normalized}")
    if normalized.get("raw_data", {}).get("source") != "smoke":
        failures.append(f"raw_platform_data alias failed: {normalized}")

    projection = p1_compat.industry_post_projection_sql()
    for expected_token in ("platform_post_id AS external_post_id", "raw_platform_data AS raw_data_json"):
        if expected_token not in projection:
            failures.append(f"industry projection missing {expected_token}")

    defaults = set(platform_crawl_settings.DEFAULT_PLATFORMS)
    for platform in ("reddit", "facebook"):
        if platform not in defaults:
            failures.append(f"{platform} missing from DEFAULT_PLATFORMS reservation")

    payload = {
        "ok": not failures,
        "marker": "VKPI_P1_COMPAT_SMOKE_OK" if not failures else "VKPI_P1_COMPAT_SMOKE_FAIL",
        "failures": failures,
    }
    stdout_out(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
