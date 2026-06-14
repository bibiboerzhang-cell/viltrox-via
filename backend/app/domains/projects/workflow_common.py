"""Shared constants and helpers for V-KPI project workflow."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

PROJECT_STAGES = [
    "discovery",
    "claimed",
    "contacted",
    "replied",
    "negotiating",
    "agreed",
    "confirmed",
    "sample_preparing",
    "shipped",
    "received",
    "delivered",
    "content_due",
    "published",
    "posted",
    "metrics_tracking",
    "measured",
    "closed",
    "stalled",
    "released",
    "lost",
    "cancelled",
]

PRIMARY_STEP_FLOW = [
    "discovery",
    "contacted",
    "replied",
    "agreed",
    "shipped",
    "received",
    "published",
    "measured",
    "closed",
]

STAGE_LABELS = {
    "discovery": "KOL discovered",
    "claimed": "KOL claimed",
    "contacted": "Contacted",
    "replied": "Replied",
    "negotiating": "Negotiating",
    "agreed": "Cooperation agreed",
    "confirmed": "Cooperation confirmed",
    "sample_preparing": "Sample preparing",
    "shipped": "Shipped",
    "received": "Received",
    "delivered": "Delivered",
    "content_due": "Content due",
    "published": "Content published",
    "posted": "Content posted",
    "metrics_tracking": "Metrics tracking",
    "measured": "Measured",
    "closed": "Closed",
    "stalled": "Stalled",
    "released": "Released",
    "lost": "Lost",
    "cancelled": "Cancelled",
}

SIDE_STAGES = {"stalled", "released", "lost", "cancelled"}
TERMINAL_STAGES = {"closed", "released", "lost", "cancelled"}
STAGE_ALIASES = {
    "confirmed": "agreed",
    "delivered": "received",
    "posted": "published",
    # 双词表案(2026-06-12 全盘扫描 P0):前端推进/合同自动推进发项目词表,
    # assignment 聚合只认 assignment 词表——写侧统一归一,读侧另有兼容。
    "shipped": "device_sent",
    "received": "arrived",
    "published": "content_posted",
    "measured": "reviewed",
}

ALLOWED_TRANSITIONS = {
    "discovery": {"claimed", "contacted", "released", "lost"},
    "claimed": {"contacted", "released", "lost"},
    "contacted": {"replied", "agreed", "stalled", "released", "lost"},
    "replied": {"negotiating", "agreed", "stalled", "released", "lost"},
    "negotiating": {"agreed", "stalled", "released", "lost"},
    "agreed": {"sample_preparing", "shipped", "stalled", "lost", "cancelled"},
    "confirmed": {"sample_preparing", "shipped", "stalled", "lost", "cancelled"},
    "sample_preparing": {"shipped", "stalled", "lost", "cancelled"},
    "shipped": {"received", "delivered", "stalled", "lost"},
    "received": {"published", "posted", "content_due", "stalled", "lost"},
    "delivered": {"published", "posted", "content_due", "stalled", "lost"},
    "content_due": {"published", "posted", "stalled", "lost"},
    "published": {"metrics_tracking", "measured", "closed"},
    "posted": {"metrics_tracking", "measured", "closed"},
    "metrics_tracking": {"measured", "closed"},
    "measured": {"closed"},
    "stalled": {"contacted", "replied", "agreed", "shipped", "received", "released", "lost"},
    "closed": set(),
    "released": set(),
    "lost": set(),
    "cancelled": set(),
}

REQUIRED_STAGE_FIELDS = {
    "shipped": ("tracking_number",),
    "published": ("source_ref_id",),
    "posted": ("source_ref_id",),
}

def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)

def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default

def _loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}

def _amount_cents(value: Any) -> int:
    try:
        return int(round(float(value or 0) * 100))
    except (TypeError, ValueError):
        return 0

def normalize_stage(stage: str) -> str:
    clean = str(stage or "").strip().lower()
    return STAGE_ALIASES.get(clean, clean)


# ── Assignment 层(vkpi_project_kol_assignments)状态词归一(P0-1 状态词统一)──
# 双词表分裂实证(2026-06-14 真库):assignment.stage 里混入项目层词
#   discovery 23 / shipped 7 / received 2 / measured 1,且项目层有 content_published。
# 扁平、幂等(canonical 词不作 key,重复套用是 no-op)。读侧消歧,不改写库、不碰 viltrox_fit_score。
CANONICAL_ASSIGNMENT_STAGES = {
    "discovered", "contacted", "replied", "agreed",
    "device_sent", "arrived", "content_posted", "reviewed", "closed", "churned",
}
CANONICAL_ASSIGNMENT_VOCAB = CANONICAL_ASSIGNMENT_STAGES | SIDE_STAGES
ASSIGNMENT_STAGE_READ_NORMALIZATION = {
    "discovery": "discovered",
    "shipped": "device_sent",
    "received": "arrived",
    "delivered": "arrived",
    "published": "content_posted",
    "content_published": "content_posted",
    "posted": "content_posted",
    "measured": "reviewed",
    "confirmed": "agreed",
}


def normalize_assignment_stage(stage: str) -> str:
    """读 assignment.stage 时归一到 assignment 规范词表(消除项目层词溢出)。幂等。"""
    clean = str(stage or "").strip().lower()
    return ASSIGNMENT_STAGE_READ_NORMALIZATION.get(clean, clean)

def _validate_transition(from_stage: str, to_stage: str, body: dict[str, Any]) -> None:
    from_clean = normalize_stage(from_stage)
    to_clean = normalize_stage(to_stage)
    if to_clean not in PROJECT_STAGES:
        raise ValueError("unsupported stage")
    is_adjacent_primary_step = False
    if from_clean in PRIMARY_STEP_FLOW and to_clean in PRIMARY_STEP_FLOW:
        is_adjacent_primary_step = abs(PRIMARY_STEP_FLOW.index(from_clean) - PRIMARY_STEP_FLOW.index(to_clean)) == 1
    if from_clean and to_clean not in ALLOWED_TRANSITIONS.get(from_clean, set()) and from_clean != to_clean and not is_adjacent_primary_step:
        raise ValueError(f"invalid transition: {from_stage} -> {to_stage}")
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    missing = []
    for field in REQUIRED_STAGE_FIELDS.get(to_clean, ()):
        value = body.get(field) or metadata.get(field)
        if not str(value or "").strip():
            missing.append(field)
    if missing:
        raise ValueError(f"missing required stage field: {', '.join(missing)}")

def staff_id(staff: dict[str, Any] | None) -> int:
    if not staff:
        return 0
    return _int(staff.get("id") or staff.get("staff_id") or staff.get("user_id"))

def stage_config() -> dict[str, Any]:
    return {
        "stages": [{"key": key, "label": STAGE_LABELS[key], "order": idx} for idx, key in enumerate(PROJECT_STAGES)],
        "allowed_transitions": {key: sorted(values) for key, values in ALLOWED_TRANSITIONS.items()},
        "primary_step_flow": PRIMARY_STEP_FLOW,
        "terminal_stages": sorted(TERMINAL_STAGES),
        "primary_flow": "staff -> kol -> project -> link -> click -> sales -> cost -> kpi",
    }

def architecture_summary() -> dict[str, Any]:
    return {
        "system": "V-KPI internal marketing management",
        "core_flow": "staff -> KOL -> project -> short link -> click -> Shopify/Amazon sales -> cost -> KPI",
        "core_tables": [
            "vkpi_projects",
            "vkpi_project_stage_events",
            "vkpi_kol_claims",
            "vkpi_links",
            "vkpi_link_clicks",
            "vkpi_sales_attributions",
            "vkpi_cost_ledger",
            "vkpi_kpi_ledger",
            "vkpi_alerts",
        ],
        "reused_vos_layers": ["staff", "kols", "orders", "platform_ingest_events", "ai_usage_log", "provider_status"],
    }
