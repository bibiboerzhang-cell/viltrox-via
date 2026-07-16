"""V-KPI metric definitions.

This module is the single source of truth for what a metric MEANS.
Every metric_value row captures the definition_version so historical reports
can be re-verified later.

Bump DEFINITION_VERSION when:
  - a metric formula changes
  - a new metric is added that affects existing dashboards
  - currency unit / source filter rule changes

Aligned with "完整系统" §三 (7 drilldown standards).
"""
from __future__ import annotations

from typing import Any

DEFINITION_VERSION = "v4"

METRICS: dict[str, dict[str, Any]] = {
    "gmv": {
        "label": "GMV",
        "label_zh": "本周销售额",
        "unit": "cents",
        "currency": "USD",
        "formula": (
            "SUM(vkpi_sales_attributions.revenue_cents) WHERE occurred_at IN [period] "
            "AND source_platform='shopify' AND confidence IN ('confirmed','refund') "
            "AND linked snapshot has provider_auth_mode='shopify-hmac', "
            "provider_verified_at, raw_payload_hash, eligible financial_status, and is not cancelled; "
            "no verified rows => awaiting_source (not zero)"
        ),
        "drilldown_source": "sales_attribution",
        "evidence_type_default": "shopify_order",
    },
    "cost": {
        "label": "Cost",
        "label_zh": "成本",
        "unit": "cents",
        "currency": "USD",
        "formula": (
            "SUM(vkpi_cost_ledger.amount_cents) WHERE status='actual' "
            "AND approved_at IS NOT NULL AND incurred_at IN [period]; "
            "no approved actual rows => awaiting_source (not zero)"
        ),
        "drilldown_source": "cost_ledger",
        "evidence_type_default": "cost_invoice",
    },
    "net_contribution": {
        "label": "Net Contribution",
        "label_zh": "净贡献",
        "unit": "cents",
        "currency": "USD",
        "formula": "gmv - cost only when both canonical inputs are real; otherwise unavailable",
        "drilldown_source": "derived",
        "evidence_type_default": "",
    },
    "roi": {
        "label": "ROI",
        "label_zh": "ROI",
        "unit": "ratio",
        "currency": "",
        "formula": "gmv / cost only when both inputs are real and cost > 0; otherwise unavailable (never synthetic zero)",
        "drilldown_source": "derived",
        "evidence_type_default": "",
    },
    "new_kol": {
        "label": "New KOLs",
        "label_zh": "新增 KOL",
        "unit": "count",
        "currency": "",
        "formula": "COUNT(vkpi_kol_claims) WHERE created_at IN [period]",
        "drilldown_source": "claim",
        "evidence_type_default": "claim",
    },
    "published_content": {
        "label": "Published Content",
        "label_zh": "已发布内容",
        "unit": "count",
        "currency": "",
        "formula": "COUNT(vkpi_project_stage_events) WHERE to_stage IN ('published','posted') AND effective_at IN [period]",
        "drilldown_source": "stage_event",
        "evidence_type_default": "stage_event",
    },
    "valid_clicks": {
        "label": "Valid Clicks",
        "label_zh": "有效点击",
        "unit": "count",
        "currency": "",
        "formula": "COUNT(vkpi_link_clicks) WHERE is_bot = 0 AND clicked_at IN [period]",
        "drilldown_source": "link_click",
        "evidence_type_default": "link_click",
    },
    "views": {
        "label": "Views",
        "label_zh": "播放量",
        "unit": "count",
        "currency": "",
        "formula": "SUM(vkpi_content_posts.views) WHERE published_at/created_at IN [period]",
        "drilldown_source": "content_post",
        "evidence_type_default": "content_post",
    },
    "active_projects": {
        "label": "Active Projects",
        "label_zh": "项目进度",
        "unit": "count",
        "currency": "",
        "formula": "COUNT(vkpi_projects) WHERE stage_status != 'deleted' AND stage NOT IN terminal stages",
        "drilldown_source": "project",
        "evidence_type_default": "project",
    },
    "alerts": {
        "label": "Open Alerts",
        "label_zh": "未处理提醒",
        "unit": "count",
        "currency": "",
        "formula": "COUNT(vkpi_alerts) WHERE status = 'open'",
        "drilldown_source": "alert",
        "evidence_type_default": "alert",
    },
}


def metric_definition(metric_key: str) -> dict[str, Any]:
    """Return the definition dict for a metric_key, or empty if unknown."""
    return METRICS.get(metric_key, {})


def is_known_metric(metric_key: str) -> bool:
    return metric_key in METRICS


def all_metric_keys() -> list[str]:
    return list(METRICS.keys())
