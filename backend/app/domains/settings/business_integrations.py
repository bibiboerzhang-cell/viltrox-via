"""Read-only truth contract for real-business integrations shown in Settings.

The contract intentionally separates configured/reference data from verified live
evidence.  It never returns secrets and never upgrades a connection to
``connected`` merely because a credential row or imported reference row exists.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Callable

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists
from app.domains import business_truth
from app.domains.commerce import shopify_connect
from app.domains.market_brain import data_readiness


_R2_REQUIRED_ENV = ("R2_ENDPOINT", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME")
logger = get_logger(__name__)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _row(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    raw = get_conn().execute(sql, params).fetchone()
    return dict(raw) if raw else {}


def _count(table: str, where: str = "", params: tuple[Any, ...] = ()) -> int:
    if not table_exists(table):
        return 0
    suffix = f" WHERE {where}" if where else ""
    return int(_row(f"SELECT COUNT(*) AS n FROM {table}{suffix}", params).get("n") or 0)


def _card(
    key: str,
    title: str,
    state: str,
    summary: str,
    *,
    data_quality: str,
    evidence: dict[str, Any],
    source: str,
    next_action: str,
    operator_status: str | None = None,
) -> dict[str, Any]:
    # Public response vocabulary is deliberately small so the UI cannot invent
    # optimistic aliases such as "ready" or "healthy".
    if state not in {"connected", "pending", "not_configured", "error"}:
        state = "error"
    if data_quality not in {"empty", "unverified", "partial", "real"}:
        data_quality = "unverified"
    if operator_status is None:
        operator_status = {
            "connected": "verified",
            "error": "error",
        }.get(state, "awaiting_configuration")
    if operator_status not in {"verified", "awaiting_authorization", "awaiting_configuration", "error"}:
        operator_status = "error"
    operator_label = {
        "verified": "已验证",
        "awaiting_authorization": "待授权",
        "awaiting_configuration": "待配置",
        "error": "异常",
    }[operator_status]
    return {
        "key": key,
        "title": title,
        "state": state,
        "summary": summary,
        "data_quality": data_quality,
        "evidence": evidence,
        "source": source,
        "next_action": next_action,
        # Stable operator vocabulary for Settings.  ``state`` remains the
        # machine-level evidence state, while this field answers the concrete
        # operator question: verified, waiting for company/provider authority,
        # waiting for configuration/evidence, or broken.
        "operator_status": operator_status,
        "operator_label": operator_label,
    }


def _guarded_card(
    key: str,
    title: str,
    next_action: str,
    factory: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """Keep one stale schema/provider from blanking the whole Settings panel."""

    try:
        return factory()
    except Exception:
        logger.exception("settings.business_integration_status_failed key=%s", key)
        return _card(
            key,
            title,
            "error",
            "状态检查失败；未将凭据、历史数据或配置视为已连接。",
            data_quality="unverified",
            evidence={"diagnostic": "schema_or_runtime_error"},
            source="masked runtime diagnostic",
            next_action=next_action,
            operator_status="error",
        )


def _shopify_card() -> dict[str, Any]:
    status = shopify_connect.connection_status()
    configured = bool(status.get("shop_domain") and status.get("token_configured"))
    order_rows = _count("vkpi_shopify_orders")
    order_count = _count(
        "vkpi_shopify_orders",
        "LOWER(COALESCE(financial_status,'')) IN ('paid','partially_paid','partially_refunded') "
        "AND provider_auth_mode='shopify-hmac' AND provider_verified_at IS NOT NULL "
        "AND NULLIF(TRIM(COALESCE(raw_payload_hash,'')),'') IS NOT NULL AND cancelled_at IS NULL",
    )
    successful_runs = _count("vkpi_shopify_sync_runs", "status='success'")
    latest_run = (
        _row(
            """
            SELECT status, started_at, completed_at, orders_received, error_message
            FROM vkpi_shopify_sync_runs
            ORDER BY started_at DESC, id DESC
            LIMIT 1
            """
        )
        if table_exists("vkpi_shopify_sync_runs")
        else {}
    )
    raw_status = str(status.get("status") or "").lower()
    if raw_status in {"error", "revoked"} or str(latest_run.get("status") or "").lower() == "failed":
        state = "error"
        summary = "凭据或最近同步失败；不会计入真实 GMV。"
    elif configured and (order_count > 0 or successful_runs > 0):
        state = "connected"
        summary = "已出现真实订单或成功同步证据。"
    elif configured:
        state = "pending"
        summary = "凭据已保存，仍待真实 Admin API / Webhook 成功证据。"
    else:
        state = "not_configured"
        summary = "等待 Shopify 授权；当前真实订单与 GMV 均为 0。"
    return _card(
        "shopify",
        "Shopify 订单",
        state,
        summary,
        data_quality="real" if order_count > 0 else ("partial" if configured else "empty"),
        evidence={
            "credential_source": str(status.get("source") or "none"),
            "shop_domain_configured": bool(status.get("shop_domain")),
            "token_configured": bool(status.get("token_configured")),
            "webhook_secret_configured": bool(status.get("webhook_secret_configured")),
            "order_rows": order_rows,
            "orders": order_count,
            "successful_sync_runs": successful_runs,
            "last_run_status": latest_run.get("status") or "never_run",
            "last_run_at": latest_run.get("completed_at") or latest_run.get("started_at"),
        },
        source="vkpi_shopify_credentials + vkpi_shopify_sync_runs + vkpi_shopify_orders",
        next_action="进入 Shopify 授权入口提交凭据；保存只代表待验证，不代表已连接。",
        operator_status=(
            "verified"
            if state == "connected"
            else "error"
            if state == "error"
            else "awaiting_authorization"
            if not configured
            else "awaiting_configuration"
        ),
    )


def _dealer_card() -> dict[str, Any]:
    total = _count("vkpi_dealers")
    public_verified = _count("vkpi_dealers", "source_status='public_listing_verified'")
    addressed = _count("vkpi_dealers", "NULLIF(TRIM(COALESCE(address,'')),'') IS NOT NULL")
    contactable = _count(
        "vkpi_dealers",
        "NULLIF(TRIM(COALESCE(phone,'')),'') IS NOT NULL "
        "OR NULLIF(TRIM(COALESCE(contact_email,'')),'') IS NOT NULL",
    )
    websites = _count(
        "vkpi_dealers",
        "NULLIF(TRIM(COALESCE(website_url,'')),'') IS NOT NULL "
        "OR NULLIF(TRIM(COALESCE(location_source_url,'')),'') IS NOT NULL",
    )
    authorized = _count(
        "vkpi_dealers",
        "LOWER(COALESCE(authorization_status,'')) IN ('authorized','authorized_confirmed','confirmed')",
    )
    geocoded = _count("vkpi_dealers", "lat IS NOT NULL AND lng IS NOT NULL")
    relationship_brands = (
        int(
            _row(
                "SELECT COUNT(DISTINCT dealer_id) AS n "
                "FROM vkpi_dealer_brand_relationships"
            ).get("n")
            or 0
        )
        if table_exists("vkpi_dealer_brand_relationships")
        else 0
    )
    listing_brands = _count(
        "vkpi_dealers",
        "NULLIF(TRIM(COALESCE(brand_listing_url,'')),'') IS NOT NULL",
    )
    brand_mapped = max(relationship_brands, listing_brands)
    directory_complete = bool(
        total > 0
        and addressed == total
        and contactable == total
        and websites == total
        and geocoded == total
        and brand_mapped == total
    )
    if directory_complete:
        state = "connected"
        summary = f"{total} 家门店的地址、联系方式、官网、品牌与地图坐标已齐全。"
    elif total > 0:
        state = "pending"
        summary = (
            f"名录 {total} 家；地图可见 {geocoded}、地址 {addressed}、"
            f"联系方式 {contactable}、官网 {websites}、品牌映射 {brand_mapped}。"
        )
    else:
        state, summary = "not_configured", "尚未建立 Dealer 名录。"
    return _card(
        "dealers",
        "Dealer 名录 / 地图",
        state,
        summary,
        data_quality="real" if directory_complete else ("partial" if total > 0 else "empty"),
        evidence={
            "total": total,
            "address_complete": addressed,
            "contact_complete": contactable,
            "website_complete": websites,
            "brand_mapped": brand_mapped,
            "map_visible": geocoded,
            "public_listing_verified": public_verified,
            # Authorization remains visible as secondary evidence only.  It is
            # not a prerequisite for a useful public Dealer directory/map.
            "authorized_confirmed_secondary": authorized,
        },
        source="vkpi_dealers + vkpi_dealer_brand_relationships",
        next_action="进入 Dealers 按缺口补地址、电话/邮箱、官网、品牌和坐标；授权不是地图上图前置条件。",
        operator_status="verified" if directory_complete else "awaiting_configuration",
    )


def _inventory_card() -> dict[str, Any]:
    total = _count("vkpi_inventory")
    verification_schema_ready = True
    try:
        verified = _count(
            "vkpi_inventory",
            "COALESCE(is_sample,FALSE)=FALSE "
            "AND quantity_status IN ('manual_confirmed','source_confirmed') "
            "AND NULLIF(TRIM(COALESCE(quantity_source_ref,'')),'') IS NOT NULL "
            "AND quantity_source_observed_at IS NOT NULL "
            "AND quantity_evidence_sha256 ~ '^[0-9a-f]{64}$' "
            "AND quantity_verified_by_staff_id IS NOT NULL "
            "AND quantity_verified_organization_id IS NOT NULL "
            "AND quantity_verified_at IS NOT NULL",
        )
    except Exception:
        # A pre-263 deployment has status labels but no durable receipts.  Keep
        # every row unverified rather than blanking the whole Settings panel.
        verified = 0
        verification_schema_ready = False
    reference = _count("vkpi_inventory", "quantity_source='catalog_reference'")
    samples = _count("vkpi_inventory", "COALESCE(is_sample,FALSE)=TRUE")
    non_sample = max(0, total - samples)
    positive_unverified = _count(
        "vkpi_inventory",
        "qty>0 AND COALESCE(is_sample,FALSE)=FALSE AND COALESCE(quantity_status,'unverified')='unverified'",
    )
    if non_sample > 0 and verified == non_sample:
        state, summary = "connected", f"{verified} 条非样本库存均已有数量来源确认。"
    elif total > 0:
        state, summary = "pending", f"{total} 条目录/旧数据中 {verified} 条已核验，不能汇总成真实库存。"
    else:
        state, summary = "not_configured", "真实库存尚未接入。"
    return _card(
        "inventory",
        "真实库存",
        state,
        summary,
        data_quality="real" if state == "connected" else ("unverified" if total > 0 else "empty"),
        evidence={"rows": total, "non_sample_rows": non_sample, "verified_non_sample": verified, "verification_schema_ready": verification_schema_ready, "catalog_reference": reference, "sample_rows": samples, "positive_but_unverified": positive_unverified},
        source="vkpi_inventory.quantity_status + source receipt provenance",
        next_action="进入 Events 库存管理，逐条绑定来源、证据哈希与观测时点；目录 SKU 不能冒充仓库库存。",
    )


def _cost_card() -> dict[str, Any]:
    total = _count("vkpi_product_cost_catalog", "COALESCE(active,TRUE)=TRUE")
    schema_ready = False
    verified = 0
    try:
        verified = _count(
            "vkpi_product_cost_catalog",
            "COALESCE(active,TRUE)=TRUE AND verification_status='verified' "
            "AND NULLIF(TRIM(source_type),'') IS NOT NULL "
            "AND NULLIF(TRIM(source_ref),'') IS NOT NULL "
            "AND source_observed_at IS NOT NULL "
            "AND verified_by_staff_id IS NOT NULL AND verified_at IS NOT NULL",
        )
        schema_ready = table_exists("vkpi_product_cost_catalog")
    except Exception:
        # Pre-migration/stale schemas fail closed instead of presenting historic
        # catalog rows as verified actual cost.
        verified = 0
        schema_ready = False
    if verified > 0:
        state = "connected"
        summary = f"{verified} 条成本已具备来源、观测时间与人工复核证据。"
    elif total:
        state = "pending"
        summary = f"{total} 条历史/人工成本仅作参考，0 条可自动转为 actual。"
    else:
        state = "not_configured"
        summary = "实际成本尚未接入。"
    return _card(
        "costs",
        "实际成本",
        state,
        summary,
        data_quality="real" if verified > 0 else ("unverified" if total else "empty"),
        evidence={"active_reference_rows": total, "verified_rows": verified, "verification_schema_ready": schema_ready},
        source="vkpi_product_cost_catalog.verification_status + provenance",
        next_action="录入参考成本后，由管理员绑定来源、观测时点与授权证据再核验。",
    )


def _attribution_card(shopify_state: str) -> dict[str, Any]:
    orders = _count(
        "vkpi_shopify_orders",
        "LOWER(COALESCE(financial_status,'')) IN ('paid','partially_paid','partially_refunded')",
    )
    attributions = _count("vkpi_sales_attributions")
    confirmed = _count(
        "vkpi_sales_attributions",
        business_truth.verified_shopify_attribution_sql()
        + " AND confidence='confirmed'",
    )
    if orders > 0 and confirmed > 0:
        state, summary = "connected", f"{orders} 单中已有 {confirmed} 条确认归因。"
    elif orders > 0 or attributions > 0 or shopify_state in {"connected", "pending"}:
        state, summary = "pending", f"订单 {orders}、归因 {attributions}；尚未形成确认销售归因闭环。"
    else:
        state, summary = "not_configured", "等待真实订单；GMV / ROI 继续显示待接入。"
    return _card(
        "attribution",
        "销售归因",
        state,
        summary,
        data_quality="real" if state == "connected" else ("partial" if orders > 0 or attributions > 0 else "empty"),
        evidence={"shopify_orders": orders, "attribution_rows": attributions, "confirmed_rows": confirmed},
        source="vkpi_shopify_orders + vkpi_sales_attributions",
        next_action="先完成 Shopify 授权，再验证订单回传、折扣码/UTM 与成本匹配。",
        operator_status=(
            "verified"
            if state == "connected"
            else "awaiting_authorization"
            if shopify_state == "not_configured"
            else "awaiting_configuration"
        ),
    )


def _r2_card() -> dict[str, Any]:
    configured_names = [name for name in _R2_REQUIRED_ENV if str(os.environ.get(name) or "").strip()]
    configured = len(configured_names) == len(_R2_REQUIRED_ENV)
    cached = _count("vkpi_media_cache_assets", "storage_backend='r2'")
    public_base = bool(str(os.environ.get("VKPI_MEDIA_CACHE_R2_PUBLIC_BASE_URL") or os.environ.get("R2_PUBLIC_BASE_URL") or "").strip())
    # Cached rows are historical evidence, not a current upload/download canary.
    state = "pending" if configured else "not_configured"
    summary = (
        f"4/4 密钥项已配置且有 {cached} 条历史 R2 缓存；仍待当前上传/读取 canary。"
        if configured
        else f"仅配置 {len(configured_names)}/4 个必需环境项。"
    )
    return _card(
        "r2",
        "R2 对象存储",
        state,
        summary,
        data_quality="partial" if cached > 0 else "empty",
        evidence={"required_env_configured": len(configured_names), "required_env_total": len(_R2_REQUIRED_ENV), "historical_cached_assets": cached, "public_base_configured": public_base, "live_canary_passed": False},
        source="masked environment booleans + vkpi_media_cache_assets",
        next_action="通过部署密钥管理配置；上线前运行真实 upload/head/download canary。",
    )


def _outcomes_card() -> dict[str, Any]:
    readiness = data_readiness.build_learning_readiness()
    facts = dict(readiness.get("facts") or {})
    finalized = int(facts.get("evidence_backed_finalized_outcomes") or 0)
    actual_evals = int(facts.get("distinct_prediction_outcomes_with_verified_actual") or 0)
    feedback = int(facts.get("real_human_feedback") or 0)
    ready = str(readiness.get("status") or "").lower() == "ready"
    has_evidence = any((finalized, actual_evals, feedback))
    if ready:
        state = "connected"
        summary = "人工结果、真实反馈与带 actual 的预测评估均已达到学习门槛。"
    elif has_evidence:
        state = "pending"
        summary = f"已积累 outcome {finalized}、真实反馈 {feedback}、actual eval {actual_evals}；尚未同时达到 5/5/5。"
    else:
        state = "not_configured"
        summary = "尚无可计入学习成熟度的人工 finalized outcome、真实反馈或 actual eval。"
    return _card(
        "outcomes",
        "真实业务结果 / 学习回传",
        state,
        summary,
        data_quality="real" if ready else ("partial" if has_evidence else "empty"),
        evidence={
            "evidence_backed_finalized_outcomes": finalized,
            "real_human_feedback": feedback,
            "verified_actual_evals": actual_evals,
            "minimum_each": 5,
        },
        source="vkpi_gtm_outcomes + vkpi_recommendation_feedback + vkpi_prediction_evals",
        next_action="在结果复盘中由人工裁决 outcome、提交非演示反馈，并把 actual 绑定到已裁决证据。",
        operator_status="verified" if ready else "awaiting_configuration",
    )


def business_integrations_status() -> dict[str, Any]:
    """Return seven masked, evidence-gated integration cards without mutating data."""
    shopify = _guarded_card(
        "shopify",
        "Shopify 订单",
        "检查授权、同步记录与数据表迁移。",
        _shopify_card,
    )
    cards = [
        shopify,
        _guarded_card("dealers", "Dealer / 门店", "检查 Dealer 表迁移与数据源。", _dealer_card),
        _guarded_card("inventory", "真实库存", "检查库存表迁移与数量来源。", _inventory_card),
        _guarded_card("costs", "实际成本", "检查成本表迁移与来源字段。", _cost_card),
        _guarded_card(
            "attribution",
            "销售归因",
            "检查订单、归因表迁移与 Shopify 授权。",
            lambda: _attribution_card(str(shopify.get("state") or "not_configured")),
        ),
        _guarded_card("r2", "R2 对象存储", "检查部署密钥与媒体缓存表迁移。", _r2_card),
        _guarded_card(
            "outcomes",
            "真实业务结果 / 学习回传",
            "检查 outcome、反馈与 prediction eval 证据链。",
            _outcomes_card,
        ),
    ]
    counts = {state: sum(1 for card in cards if card["state"] == state) for state in ("connected", "pending", "not_configured", "error")}
    operator_counts = {
        status: sum(1 for card in cards if card["operator_status"] == status)
        for status in ("verified", "awaiting_authorization", "awaiting_configuration", "error")
    }
    return {
        "generated_at": _iso_now(),
        "claim_status": "descriptive_only",
        "write_performed": False,
        "secrets_returned": False,
        "counts": counts,
        "operator_counts": operator_counts,
        "integrations": cards,
    }
