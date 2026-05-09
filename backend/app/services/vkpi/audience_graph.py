"""Audience graph control surface. Heavy graph crawls are feature-flagged off by default."""
from __future__ import annotations

from typing import Any

from app.services.vkpi import platform_crawl_settings


def status() -> dict[str, Any]:
    flags = {row["flag_key"]: bool(row.get("enabled")) for row in platform_crawl_settings.feature_flags().get("flags", [])}
    return {
        "levels": [
            {"level": "L1", "label": "聚合受众匹配", "enabled": flags.get("audience_graph_l1", False)},
            {"level": "L2", "label": "相似受众估算", "enabled": flags.get("audience_graph_l2", False)},
            {"level": "L3", "label": "明细粉丝图谱", "enabled": flags.get("audience_graph_l3", False), "requires_explicit_budget": True},
        ],
        "provider_status": "disabled_until_flag_enabled",
        "privacy_rule": "默认不抓取或保存粉丝个人明细；开启 L3 前必须配置预算、合规说明和平台许可。",
    }


def estimate(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    return {"overlap_score": None, "status": "not_configured", "evidence": [], "message": "粉丝图谱未开启，不返回假重叠率。"}
