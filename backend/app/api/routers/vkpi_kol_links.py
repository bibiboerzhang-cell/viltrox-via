"""V-KPI KOL lifecycle, claim, and link center routes."""
from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.db.connection import get_conn
from app.services.kol.account_dossier import analyze_kol_account, get_kol_dossier, scan_kol_account
from app.services.vkpi import kol_claims, kol_history_match, kol_product_fit as kol_product_fit_service, link_center, scope
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-kol-links"])


def _scope_403(exc: Exception) -> HTTPException:
    return HTTPException(status_code=403, detail=str(exc) or "scope denied")


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value or default))
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def _json_loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(str(value or ""))
        return parsed if parsed is not None else fallback
    except Exception:
        return fallback


def _clamp_score(value: Any) -> int:
    return max(0, min(100, _int(value)))


def _grade(score: int) -> str:
    if score >= 90:
        return "S"
    if score >= 80:
        return "A"
    if score >= 65:
        return "B"
    if score >= 50:
        return "C"
    return "D"


def _latest_kol_context(kol_id: int) -> dict[str, Any]:
    conn = get_conn()
    kol = conn.execute("SELECT * FROM kols WHERE id=?", (int(kol_id),)).fetchone()
    if not kol:
        raise LookupError("kol not found")
    snapshot = conn.execute(
        "SELECT * FROM kol_account_snapshots WHERE kol_id=? ORDER BY scanned_at DESC, id DESC LIMIT 1",
        (int(kol_id),),
    ).fetchone()
    snapshots = conn.execute(
        "SELECT follower_count, content_count, avg_views, scanned_at FROM kol_account_snapshots WHERE kol_id=? ORDER BY scanned_at DESC, id DESC LIMIT 2",
        (int(kol_id),),
    ).fetchall()
    report = conn.execute(
        "SELECT * FROM kol_analysis_reports WHERE kol_id=? ORDER BY created_at DESC, id DESC LIMIT 1",
        (int(kol_id),),
    ).fetchone()
    post_count = conn.execute("SELECT COUNT(*) AS c FROM kol_posts WHERE kol_id=?", (int(kol_id),)).fetchone()
    return {
        "kol": dict(kol),
        "snapshot": dict(snapshot) if snapshot else {},
        "snapshots": [dict(row) for row in snapshots],
        "report": dict(report) if report else {},
        "post_count": _int(post_count["c"] if post_count else 0),
    }


def _dimension(score: int, source: str, reason: str, status: str = "ready") -> dict[str, Any]:
    return {"score": _clamp_score(score), "source": source, "reason": reason, "status": status}


def _assessment_payload(kol_id: int) -> dict[str, Any]:
    ctx = _latest_kol_context(kol_id)
    kol = ctx["kol"]
    snapshot = ctx["snapshot"]
    report = ctx["report"]
    snapshots = ctx["snapshots"]
    post_count = ctx["post_count"]
    followers = _int(snapshot.get("follower_count"), _int(kol.get("follower_count")))
    avg_views = _int(snapshot.get("avg_views"), _int(kol.get("avg_views")))
    content_count = _int(snapshot.get("content_count"), post_count)
    engagement_rate = _float(snapshot.get("engagement_rate"))
    account_score = _int(report.get("account_score"))
    audience_fit = _int(report.get("audience_fit"))
    product_fit = _int(report.get("product_fit"))
    risk_level = str(report.get("risk_level") or "").lower()
    scan_status = str(snapshot.get("scan_status") or "").lower()

    audience_score = audience_fit or min(100, int(min(55, followers / 2500) + min(30, avg_views / 4000) + min(15, content_count / 8)))
    engagement_score = min(100, int(engagement_rate * 1200)) if engagement_rate else 0
    content_score = account_score or min(100, int(min(55, avg_views / 3000) + min(25, content_count / 4) + min(20, engagement_rate * 350)))
    consistency_score = 78 if snapshot and "error" not in scan_status else 42 if snapshot else 0
    if risk_level == "low":
        safety_score = 90
    elif risk_level == "medium":
        safety_score = 65
    elif risk_level == "high":
        safety_score = 35
    else:
        safety_score = 55 if snapshot else 0
    growth_status = "estimated"
    growth_score = 0
    if len(snapshots) >= 2:
        latest_followers = _int(snapshots[0].get("follower_count"))
        previous_followers = _int(snapshots[1].get("follower_count"))
        if previous_followers > 0:
            delta = (latest_followers - previous_followers) / previous_followers
            growth_score = _clamp_score(55 + int(delta * 300))
            growth_status = "ready"
    if not growth_score and snapshot:
        growth_score = 52
    professional_score = product_fit or min(100, account_score + 5 if account_score else 0)
    completeness = sum(
        1
        for value in (
            followers,
            avg_views,
            content_count,
            engagement_rate,
            kol.get("contact_email") or kol.get("contact_links_json"),
            report.get("raw_json"),
        )
        if value
    )
    authenticity_score = min(100, 38 + completeness * 9 + (8 if engagement_rate else 0))
    authenticity_status = "estimated" if snapshot else "missing"

    dimensions = {
        "audience": _dimension(audience_score, "kol_analysis_reports.audience_fit / snapshots", "粉丝量级、平均播放和内容量综合估算。", "ready" if audience_fit else "estimated"),
        "engagement": _dimension(engagement_score, "kol_account_snapshots.engagement_rate", "使用最新抓取互动率换算。", "ready" if engagement_rate else "missing"),
        "content": _dimension(content_score, "kol_analysis_reports.account_score / posts", "使用账号报告分；缺失时用播放和内容数量估算。", "ready" if account_score else "estimated"),
        "consistency": _dimension(consistency_score, "kol_account_snapshots.scan_status", "基于抓取状态和是否有最新快照。", "estimated"),
        "safety": _dimension(safety_score, "kol_analysis_reports.risk_level", "基于账号报告风险等级。", "ready" if risk_level else "estimated"),
        "growth": _dimension(growth_score, "kol_account_snapshots follower delta", "两次快照可计算增长；单快照时仅保守估算。", growth_status if growth_score else "missing"),
        "professional": _dimension(professional_score, "kol_analysis_reports.product_fit", "用产品匹配与账号内容质量作为专业度代理。", "ready" if product_fit else "estimated"),
        "authenticity": _dimension(authenticity_score, "data completeness proxy", "用抓取完整度、互动率和联系方式完整度估算真实性。", authenticity_status),
    }
    overall = round(sum(item["score"] for item in dimensions.values()) / len(dimensions))
    risk_flags: list[dict[str, Any]] = []
    if risk_level and risk_level not in {"low", "none"}:
        risk_flags.append({"type": "risk_level", "severity": risk_level, "label": f"风险等级 {risk_level}"})
    if not snapshot:
        risk_flags.append({"type": "missing_snapshot", "severity": "medium", "label": "缺少账号抓取快照"})
    if not report:
        risk_flags.append({"type": "missing_report", "severity": "medium", "label": "缺少账号分析报告"})
    return {
        "kol_id": int(kol_id),
        "score": overall,
        "grade": _grade(overall),
        "method": "local_assessment_v1_existing_tables",
        "dimensions": dimensions,
        "risk_flags": risk_flags,
        "recommended_action": report.get("recommended_action") or ("先运行深度评估补齐账号报告。" if not report else ""),
        "source_tables": ["kols", "kol_account_snapshots", "kol_analysis_reports", "kol_posts"],
    }


def _tokenize(text: str) -> set[str]:
    return {part for part in re.split(r"[^a-z0-9\u4e00-\u9fff]+", str(text or "").lower()) if len(part) >= 2}


def _normalize_product_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _display_product_sku(sku: str) -> str:
    text = str(sku or "").strip()
    replacements = {
        "F12": "F1.2",
        "F17": "F1.7",
        "F18": "F1.8",
        "F35": "F3.5",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text.replace("-", " ")


def _normalize_handle_for_match(value: Any) -> str:
    raw = str(value or "").strip().lower()
    raw = re.sub(r"^https?://", "", raw)
    raw = raw.replace("www.", "")
    parts = [part for part in re.split(r"[/?#]", raw) if part]
    if parts:
        raw = parts[-1]
    return raw.lstrip("@")


def _kol_pool_profile_deep_for_kol(kol: dict[str, Any]) -> dict[str, Any]:
    conn = get_conn()
    row = conn.execute(
        """
        SELECT kp.id AS kol_pool_id, kp.pool_uid, kp.platform, kp.handle,
               pd.id AS profile_deep_id, pd.dimensions_11_json
        FROM vkpi_kol_pool kp
        JOIN vkpi_kol_profile_deep pd ON pd.kol_pool_id=kp.id
        WHERE kp.linked_main_kol_id=?
        ORDER BY kp.updated_at DESC, kp.id DESC
        LIMIT 1
        """,
        (int(kol.get("id") or 0),),
    ).fetchone()
    if row:
        return dict(row)

    platform = str(kol.get("platform") or "").strip().lower()
    handle = _normalize_handle_for_match(kol.get("channel_url") or kol.get("profile_url") or kol.get("channel_name") or kol.get("media_name"))
    if platform == "ig":
        platform = "instagram"
    if not platform or not handle:
        return {}
    row = conn.execute(
        """
        SELECT kp.id AS kol_pool_id, kp.pool_uid, kp.platform, kp.handle,
               pd.id AS profile_deep_id, pd.dimensions_11_json
        FROM vkpi_kol_pool kp
        JOIN vkpi_kol_profile_deep pd ON pd.kol_pool_id=kp.id
        WHERE LOWER(kp.platform)=LOWER(?) AND LOWER(kp.handle)=LOWER(?)
        ORDER BY kp.updated_at DESC, kp.id DESC
        LIMIT 1
        """,
        (platform, handle),
    ).fetchone()
    return dict(row) if row else {}


def _dimensions11_product_fit_items(profile_deep: dict[str, Any]) -> list[dict[str, Any]]:
    payload = _json_loads(profile_deep.get("dimensions_11_json"), {})
    block4 = payload.get("block4_specialty") if isinstance(payload, dict) and isinstance(payload.get("block4_specialty"), dict) else {}
    raw_fit = block4.get("product_fit") if isinstance(block4.get("product_fit"), dict) else {}
    raw_conf = block4.get("product_fit_confidence") if isinstance(block4.get("product_fit_confidence"), dict) else {}
    items: list[dict[str, Any]] = []
    for sku, score in raw_fit.items():
        sku_text = str(sku or "").strip()
        normalized = _normalize_product_key(sku_text)
        confidence = max(0.0, min(1.0, _float(raw_conf.get(sku_text), 0.0)))
        fit_score = _clamp_score(score)
        if not sku_text or not normalized or not fit_score or confidence < 0.35:
            continue
        items.append(
            {
                "sku": sku_text,
                "normalized": normalized,
                "score": fit_score,
                "confidence": confidence,
                "profile_deep_id": profile_deep.get("profile_deep_id"),
                "kol_pool_id": profile_deep.get("kol_pool_id"),
                "method": str(payload.get("method") or "rule_dimensions_11_v0"),
            }
        )
    return sorted(items, key=lambda item: (int(item["score"]), float(item["confidence"])), reverse=True)


def _dimensions11_match_for_product(product_text: str, items: list[dict[str, Any]]) -> dict[str, Any] | None:
    normalized = _normalize_product_key(product_text)
    if not normalized:
        return None
    for item in items:
        sku_norm = str(item.get("normalized") or "")
        if sku_norm and (sku_norm in normalized or normalized in sku_norm):
            return item
    return None


def _product_fit_preview_payload_for_pool(kol_pool_id: int, limit: int = 5, *, kol_id: int | None = None) -> dict[str, Any]:
    safe_limit = max(1, min(20, int(limit or 5)))
    preview = kol_product_fit_service.build_kol_product_fit_preview(
        kol_pool_id=int(kol_pool_id),
        limit=safe_limit,
    )
    items: list[dict[str, Any]] = []
    for item in preview.get("items") or []:
        catalog = item.get("matched_catalog_product") or {}
        catalog_products = item.get("matched_catalog_products") or []
        evidence_pro = item.get("evidence_pro") or []
        evidence_con = item.get("evidence_con") or []
        top_catalog = catalog or (catalog_products[0] if catalog_products else {})
        product_label = str(
            top_catalog.get("marketing_name")
            or top_catalog.get("model_name")
            or item.get("product_family_name")
            or ""
        )
        items.append(
            {
                "launch_id": None,
                "product_family_uid": item.get("product_family_uid"),
                "product_family_name": item.get("product_family_name"),
                "product_sku": top_catalog.get("sku") or item.get("product_family_name"),
                "product_name": product_label,
                "launch_name": "Product Fit 规则引擎",
                "score": item.get("score"),
                "rank": item.get("rank"),
                "percentile_rank": item.get("percentile_rank"),
                "method": "kol_product_fit_v1",
                "status": "ready" if top_catalog else "estimated_no_catalog_match",
                "reasons": [str(row.get("detail") or "") for row in evidence_pro[:3] if row.get("detail")],
                "concerns": [str(row.get("detail") or "") for row in evidence_con[:3] if row.get("detail")],
                "evidence": [
                    f"{row.get('source_table', '')}:{row.get('source_id', '')}".strip(":")
                    for row in evidence_pro[:5]
                    if row.get("source_table") or row.get("source_id")
                ],
                "catalog_product": top_catalog,
                "matched_catalog_product": catalog,
                "matched_catalog_products": catalog_products,
                "mount": top_catalog.get("mount"),
                "price_usd": top_catalog.get("price_usd"),
                "product_url": top_catalog.get("product_url"),
                "specs": top_catalog.get("specs") or {},
                "source_confidence": top_catalog.get("source_confidence"),
                "score_breakdown": item.get("score_breakdown") or {},
            }
        )
    return {
        "kol_id": int(kol_id or kol_pool_id),
        "kol_pool_id": int(kol_pool_id),
        "method": "kol_product_fit_v1",
        "summary": preview.get("summary") or {},
        "items": items[:safe_limit],
    }


def _product_fit_payload(kol_id: int, limit: int = 5) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    ctx = _latest_kol_context(kol_id)
    kol = ctx["kol"]
    snapshot = ctx["snapshot"]
    report = ctx["report"]
    profile_deep = _kol_pool_profile_deep_for_kol(kol)
    safe_limit = max(1, min(20, int(limit or 5)))
    if profile_deep.get("kol_pool_id"):
        try:
            return _product_fit_preview_payload_for_pool(int(profile_deep["kol_pool_id"]), safe_limit, kol_id=int(kol_id))
        except Exception as exc:
            fit_engine_error = str(exc)
    else:
        fit_engine_error = "missing_kol_pool_profile_deep"
    raw_report = _json_loads(report.get("raw_json"), {})
    kol_text = " ".join(
        str(value or "")
        for value in (
            kol.get("channel_name"),
            kol.get("media_name"),
            kol.get("niche"),
            kol.get("primary_category"),
            kol.get("promoted_product"),
            raw_report.get("user_persona"),
            raw_report.get("product_fit_summary"),
            raw_report.get("summary_zh"),
        )
    )
    kol_tokens = _tokenize(kol_text)
    base = _int(report.get("product_fit"), _int(report.get("account_score"), 45))
    dimensions11_items = _dimensions11_product_fit_items(profile_deep) if profile_deep else []
    launches = get_conn().execute(
        """
        SELECT *
        FROM vkpi_product_launches
        WHERE deleted_at IS NULL
        ORDER BY updated_at DESC, id DESC
        LIMIT 100
        """
    ).fetchall()
    rows: list[dict[str, Any]] = []
    matched_dimension_skus: set[str] = set()
    for launch in launches:
        item = dict(launch)
        product_text = " ".join(
            str(value or "")
            for value in (
                item.get("name"),
                item.get("product_sku"),
                item.get("product_name"),
                item.get("category"),
                item.get("target_market"),
                item.get("target_platforms_json"),
                item.get("target_audience_json"),
                item.get("goals_json"),
            )
        )
        product_tokens = _tokenize(product_text)
        overlap = sorted(kol_tokens & product_tokens)
        platform_bonus = 0
        target_platforms = _json_loads(item.get("target_platforms_json"), [])
        if isinstance(target_platforms, list) and str(kol.get("platform") or "").lower() in {str(v).lower() for v in target_platforms}:
            platform_bonus = 8
        dimensions_match = _dimensions11_match_for_product(product_text, dimensions11_items)
        dimensions_bonus = round((int(dimensions_match.get("score") or 0) / 100) * float(dimensions_match.get("confidence") or 0) * 20) if dimensions_match else 0
        local_score = _clamp_score(base + min(18, len(overlap) * 4) + platform_bonus + (6 if snapshot else 0) + dimensions_bonus)
        score = max(local_score, _clamp_score(dimensions_match.get("score") if dimensions_match else 0))
        reasons = [
            "基于账号报告 product_fit / account_score。",
            "匹配 KOL 主题与产品/目标受众关键词。",
            "平台命中产品目标平台时加权。",
        ]
        evidence = overlap[:8]
        method = "local_product_fit_v1_existing_tables"
        status = "estimated"
        if dimensions_match:
            matched_dimension_skus.add(str(dimensions_match.get("sku") or ""))
            reasons.insert(0, f"11维规则画像匹配 {dimensions_match.get('sku')}，confidence={dimensions_match.get('confidence')}")
            evidence.insert(0, f"vkpi_kol_profile_deep:{dimensions_match.get('profile_deep_id')}")
            method = "local_product_fit_v1_plus_dimensions11"
            status = "ready"
        rows.append(
            {
                "launch_id": item.get("id"),
                "product_sku": item.get("product_sku") or item.get("name"),
                "product_name": item.get("product_name") or item.get("name") or item.get("product_sku"),
                "launch_name": item.get("name"),
                "score": score,
                "method": method,
                "status": status,
                "reasons": reasons,
                "evidence": evidence,
            }
        )
    existing_skus = {_normalize_product_key(row.get("product_sku") or row.get("product_name")) for row in rows}
    for item in dimensions11_items:
        if item["sku"] in matched_dimension_skus:
            continue
        if item["normalized"] in existing_skus:
            continue
        rows.append(
            {
                "launch_id": None,
                "product_sku": item["sku"],
                "product_name": _display_product_sku(item["sku"]),
                "launch_name": "11维规则画像",
                "score": _clamp_score(item["score"]),
                "method": item["method"],
                "status": "ready",
                "reasons": [f"来自 11 维规则产品适配，confidence={item['confidence']}"],
                "evidence": [f"vkpi_kol_profile_deep:{item.get('profile_deep_id')}"],
            }
        )
    if not rows:
        rows.append(
            {
                "launch_id": None,
                "product_sku": str(report.get("suggested_products_json") or "待配置产品"),
                "product_name": str(raw_report.get("product_fit_summary") or "待配置产品"),
                "launch_name": "未配置产品上市数据",
                "score": _clamp_score(base),
                "method": "local_product_fit_v1_existing_tables",
                "status": "missing_product_launches",
                "reasons": ["当前没有 vkpi_product_launches 产品数据，只能展示账号报告里的产品方向。"],
                "evidence": [],
            }
        )
    rows.sort(key=lambda row: int(row.get("score") or 0), reverse=True)
    return {
        "kol_id": int(kol_id),
        "method": "local_product_fit_v1_fallback",
        "fit_engine_error": fit_engine_error,
        "items": rows[:safe_limit],
    }


def _contact_rows(kol_id: int, include_wrong: bool = False) -> dict[str, Any]:
    ctx = _latest_kol_context(kol_id)
    kol = ctx["kol"]
    rows: list[dict[str, Any]] = []
    if str(kol.get("contact_email") or "").strip():
        rows.append(
            {
                "id": f"email-{kol_id}",
                "contact_type": "email",
                "contact_value": str(kol.get("contact_email") or "").strip(),
                "layer": 1,
                "source": "kols.contact_email",
                "confidence": 95,
                "evidence": "profile_business_email",
                "verified": True,
                "status": "active",
            }
        )
    if str(kol.get("contact_phone") or "").strip():
        rows.append(
            {
                "id": f"phone-{kol_id}",
                "contact_type": "phone",
                "contact_value": str(kol.get("contact_phone") or "").strip(),
                "layer": 1,
                "source": "kols.contact_phone",
                "confidence": 80,
                "evidence": "profile_contact_phone",
                "verified": False,
                "status": "active",
            }
        )
    links = _json_loads(kol.get("contact_links_json"), [])
    if isinstance(links, list):
        for index, link in enumerate(links):
            if not isinstance(link, dict):
                continue
            value = str(link.get("value") or link.get("url") or "").strip()
            if not value:
                continue
            rows.append(
                {
                    "id": f"link-{kol_id}-{index}",
                    "contact_type": str(link.get("label") or "link"),
                    "contact_value": value,
                    "layer": _int(link.get("layer"), 1),
                    "source": str(link.get("source") or "kols.contact_links_json"),
                    "confidence": _int(link.get("confidence"), 70),
                    "evidence": str(link.get("evidence") or link.get("url") or ""),
                    "verified": bool(link.get("verified")),
                    "status": str(link.get("status") or "active"),
                }
            )
    if not include_wrong:
        rows = [row for row in rows if row.get("status") != "wrong"]
    return {
        "kol_id": int(kol_id),
        "contacts": rows,
        "summary": {
            "total": len(rows),
            "layers": {str(layer): sum(1 for row in rows if _int(row.get("layer")) == layer) for layer in range(1, 6)},
            "method": "existing_kol_fields_contact_bridge",
            "missing_layers": [layer for layer in range(2, 5) if not any(_int(row.get("layer")) == layer for row in rows)],
        },
    }


_PLATFORM_ALIASES = {
    "instagram": ("instagram", "ig", "ins"),
    "youtube": ("youtube", "yt", "youtuber"),
    "tiktok": ("tiktok", "tt", "抖音"),
    "facebook": ("facebook", "fb"),
    "x": ("twitter", "x.com", "x "),
    "reddit": ("reddit",),
}

_REGION_ALIASES = {
    "US": ("美国", "美区", "usa", "united states", " u.s.", " us "),
    "DE": ("德国", "germany", "deutschland", " de "),
    "GB": ("英国", "uk", "united kingdom", " gb "),
    "JP": ("日本", "japan", " jp "),
    "KR": ("韩国", "korea", " kr "),
    "CA": ("加拿大", "canada", " ca "),
    "AU": ("澳大利亚", "australia", " au "),
    "MY": ("马来西亚", "malaysia", " my "),
}

_SEARCH_STOP_WORDS = {
    "kol",
    "kols",
    "红人",
    "达人",
    "博主",
    "测评人",
    "找",
    "寻找",
    "搜索",
    "推荐",
    "合作",
    "可合作",
    "中腰部",
    "头部",
    "长尾",
    "有联系方式",
    "联系方式",
    "无风险",
    "低风险",
    "安全",
}

_DIRECTION_KEYWORDS = (
    "35mm",
    "56mm",
    "street",
    "portrait",
    "photography",
    "review",
    "tutorial",
    "comparison",
    "cinematic",
    "vlog",
    "sony",
    "fuji",
    "nikon",
    "sigma",
    "tamron",
    "街拍",
    "人像",
    "摄影",
    "测评",
    "教程",
    "对比",
    "中腰部",
    "竞品",
    "镜头",
    "相机",
    "视频",
)


def _parse_natural_query(query: str, platform_hint: str = "") -> dict[str, Any]:
    q = f" {str(query or '').lower()} "
    platform = ""
    for candidate, aliases in _PLATFORM_ALIASES.items():
        if any(alias in q for alias in aliases):
            platform = candidate
            break
    if not platform and platform_hint and platform_hint != "all":
        platform = str(platform_hint or "").lower()

    country = ""
    for code, aliases in _REGION_ALIASES.items():
        if any(alias in q for alias in aliases):
            country = code
            break

    level = ""
    if any(item in q for item in ("头部", "top", "mega")):
        level = "top"
    elif any(item in q for item in ("中腰部", "mid", "micro", "中部", "腰部")):
        level = "mid"
    elif any(item in q for item in ("长尾", "tail", "nano")):
        level = "tail"

    keyword_set: set[str] = set()
    for keyword in _DIRECTION_KEYWORDS:
        if keyword.lower() in q:
            keyword_set.add(keyword.lower())
    for token in _tokenize(q):
        if any(stop and stop in token for stop in _SEARCH_STOP_WORDS):
            continue
        if any(alias.strip() and alias.strip() in token for aliases in _REGION_ALIASES.values() for alias in aliases):
            continue
        if any(alias.strip() and alias.strip() in token for aliases in _PLATFORM_ALIASES.values() for alias in aliases):
            continue
        if token.isdigit():
            continue
        keyword_set.add(token)

    return {
        "platform": platform,
        "country": country,
        "level": level,
        "requires_contact": any(item in q for item in ("联系方式", "email", "邮箱", "contact", "可联系")),
        "requires_low_risk": any(item in q for item in ("无风险", "低风险", "安全", "clean risk", "low risk")),
        "requires_collaboration": any(item in q for item in ("合作过", "已合作", "之前合作", "历史合作", "合作历史", "roi", "复用")),
        "keywords": sorted(keyword_set),
    }


def _row_text(row: dict[str, Any]) -> str:
    return " ".join(
        str(value or "")
        for value in (
            row.get("channel_name"),
            row.get("media_name"),
            row.get("owner_name"),
            row.get("handle"),
            row.get("channel_url"),
            row.get("niche"),
            row.get("primary_category"),
            row.get("promoted_product"),
            row.get("channel_tags"),
            row.get("country"),
            row.get("country_code"),
            row.get("platform"),
            row.get("contact_email"),
            row.get("contact_links_json"),
        )
    ).lower()


def _row_has_contact(row: dict[str, Any]) -> bool:
    if str(row.get("contact_email") or "").strip() or str(row.get("contact_phone") or "").strip():
        return True
    links = _json_loads(row.get("contact_links_json"), [])
    return isinstance(links, list) and any(isinstance(item, dict) and (item.get("value") or item.get("url")) for item in links)


def _row_level(followers: int) -> str:
    if followers >= 500000:
        return "top"
    if followers >= 50000:
        return "mid"
    return "tail"


def _natural_match_score(row: dict[str, Any], parsed: dict[str, Any]) -> tuple[int, list[str]]:
    text = _row_text(row)
    followers = _int(row.get("snapshot_follower_count"), _int(row.get("follower_count")))
    score = _clamp_score(row.get("account_score") or row.get("score") or row.get("product_fit") or row.get("audience_fit") or 42)
    reasons: list[str] = []

    keywords = [str(item).lower() for item in parsed.get("keywords") or []]
    keyword_hits = [keyword for keyword in keywords if keyword and keyword in text]
    if keyword_hits:
        score += min(24, len(keyword_hits) * 8)
        reasons.append(f"关键词命中：{', '.join(keyword_hits[:4])}")

    platform = str(parsed.get("platform") or "").lower()
    if platform and platform == str(row.get("platform") or "").lower():
        score += 10
        reasons.append(f"平台匹配 {platform}")

    country = str(parsed.get("country") or "").upper()
    row_country = str(row.get("country") or row.get("country_code") or "").upper()
    if country and (country == row_country or country.lower() in text):
        score += 8
        reasons.append(f"地区匹配 {country}")

    level = str(parsed.get("level") or "")
    if level and followers and _row_level(followers) == level:
        score += 8
        reasons.append(f"量级匹配 {level}")

    if parsed.get("requires_contact") and _row_has_contact(row):
        score += 8
        reasons.append("已有联系方式")

    if parsed.get("requires_collaboration") and (_int(row.get("project_count")) or _float(row.get("revenue_cents"))):
        score += 6
        reasons.append("已有合作或收益记录")

    risk = str(row.get("risk_level") or row.get("risk_label") or "").lower()
    if parsed.get("requires_low_risk") and risk in {"", "low", "none", "clean"}:
        score += 6
        reasons.append("暂无高风险信号")

    if row.get("snapshot_scanned_at"):
        score += 4
        reasons.append("有账号快照")

    return _clamp_score(score), reasons


def _natural_search_payload(body: dict[str, Any], staff: dict[str, Any] | None = None) -> dict[str, Any]:
    query = str(body.get("query") or body.get("q") or "").strip()
    limit = max(1, min(200, _int(body.get("limit"), 100)))
    parsed = _parse_natural_query(query, str(body.get("platform") or ""))
    platform = str(parsed.get("platform") or "")
    raw_rows = kol_claims.list_kols(search="", platform=platform, limit=500, staff=staff).get("kols") or []
    rows: list[dict[str, Any]] = []
    for raw in raw_rows:
        row = dict(raw)
        text = _row_text(row)
        followers = _int(row.get("snapshot_follower_count"), _int(row.get("follower_count")))
        has_structured_filter = any(
            parsed.get(key)
            for key in ("platform", "country", "level", "requires_contact", "requires_low_risk", "requires_collaboration")
        )
        if parsed.get("requires_contact") and not _row_has_contact(row):
            continue
        level = str(parsed.get("level") or "")
        if level and followers and _row_level(followers) != level:
            continue
        keywords = [str(keyword).lower() for keyword in parsed.get("keywords") or []]
        if keywords and not has_structured_filter and not any(keyword in text for keyword in keywords):
            continue
        country = str(parsed.get("country") or "").upper()
        row_country = str(row.get("country") or row.get("country_code") or "").upper()
        if country and row_country and country != row_country and country.lower() not in text:
            continue
        score, reasons = _natural_match_score(row, parsed)
        row["natural_match_score"] = score
        row["natural_match_reasons"] = reasons
        row["score"] = max(_clamp_score(row.get("score") or row.get("account_score") or row.get("product_fit")), score)
        rows.append(row)
    pool_rows = kol_history_match.search_pool_for_natural(query, parsed, limit=limit)
    seen_keys = {
        (
            str(row.get("platform") or "").lower(),
            kol_history_match.normalize_history_handle(row.get("handle") or row.get("channel_name") or ""),
        )
        for row in rows
    }
    for row in pool_rows:
        key = (
            str(row.get("platform") or "").lower(),
            kol_history_match.normalize_history_handle(row.get("handle") or row.get("channel_name") or ""),
        )
        if key in seen_keys:
            continue
        rows.append(row)
        seen_keys.add(key)
    rows.sort(key=lambda item: (_int(item.get("natural_match_score")), _int(item.get("snapshot_follower_count"), _int(item.get("follower_count")))), reverse=True)
    notes = ["规则解析版，复用现有 kols / snapshots / reports / vkpi_kol_pool 字段；未新增后端表。"]
    if not rows:
        notes.append("没有命中时不会伪造推荐，请先补候选池或放宽关键词。")
    return {
        "query": query,
        "parsed": parsed,
        "items": rows[:limit],
        "method": "local_natural_search_v1_existing_kols",
        "degraded": True,
        "notes": notes,
    }


@router.post("/kol/search/natural")
def natural_kol_search(body: dict, staff=Depends(require_tab("vkpi", "read"))):
    return _natural_search_payload(body or {}, staff=staff)


@router.post("/kols/lookup")
async def lookup_kol(body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        result = kol_claims.lookup(body, staff=staff)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    kol = result.get("kol") or {}
    kol_id = int(kol.get("id") or 0) if isinstance(kol, dict) else 0
    if not kol_id:
        return result
    try:
        kol_claims.assert_kol_access(kol_id, staff, allow_unclaimed=True)
    except scope.ScopeDenied as exc:
        result["dossier"] = {}
        result["can_claim"] = False
        result["access_status"] = "claimed_by_other"
        result["access_message"] = str(exc) or "kol claimed by another staff"
        return result
    scan_result = None
    analysis_result = None
    if body.get("scan_account") or body.get("scan_if_missing"):
        max_posts = max(1, min(int(body.get("max_posts") or 24), 80))
        scan_result = await scan_kol_account(kol_id, max_posts=max_posts)
        if int(scan_result.get("content_count") or 0) > 0:
            analysis_result = await analyze_kol_account(kol_id, product_sku=str(body.get("product_sku") or ""))
    result["dossier"] = get_kol_dossier(kol_id)
    if scan_result is not None:
        result["scan_result"] = scan_result
    if analysis_result is not None:
        result["analysis_result"] = analysis_result
    return result


@router.get("/kols")
def list_kols(
    search: str = "",
    platform: str = "",
    staff_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    return kol_claims.list_kols(search=search, platform=platform, staff_id=staff_id, limit=limit, staff=staff)


@router.get("/kols/{kol_id}/dossier")
def kol_dossier(kol_id: int, staff=Depends(require_tab("vkpi", "read"))):
    try:
        kol_claims.assert_kol_access(int(kol_id), staff, allow_unclaimed=True)
        return get_kol_dossier(int(kol_id))
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/kols/{kol_id}/profile")
def kol_profile(kol_id: int, staff=Depends(require_tab("vkpi", "read"))):
    try:
        result = kol_claims.profile(int(kol_id), staff=staff)
        try:
            result["dossier"] = get_kol_dossier(int(kol_id))
        except Exception:
            result["dossier"] = {}
        return result
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/kols/{kol_id}/assessment")
def kol_assessment(kol_id: int, staff=Depends(require_tab("vkpi", "read"))):
    try:
        kol_claims.assert_kol_access(int(kol_id), staff, allow_unclaimed=True)
        return _assessment_payload(int(kol_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/kols/{kol_id}/product-fit")
def kol_product_fit(
    kol_id: int,
    limit: int = Query(default=5, ge=1, le=20),
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        try:
            kol_claims.assert_kol_access(int(kol_id), staff, allow_unclaimed=True)
        except LookupError:
            return _product_fit_preview_payload_for_pool(int(kol_id), limit)
        return _product_fit_payload(int(kol_id), limit=limit)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/kols/{kol_id}/contacts")
def kol_contacts(
    kol_id: int,
    include_wrong: bool = False,
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        kol_claims.assert_kol_access(int(kol_id), staff, allow_unclaimed=True)
        return _contact_rows(int(kol_id), include_wrong=include_wrong)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/kols/{kol_id}/contacts")
def add_kol_contact(kol_id: int, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    contact_type = str(body.get("contact_type") or body.get("type") or "").strip().lower()
    contact_value = str(body.get("contact_value") or body.get("value") or "").strip()
    if not contact_type or not contact_value:
        raise HTTPException(status_code=400, detail="contact_type and contact_value required")
    try:
        kol_claims.assert_kol_access(int(kol_id), staff, allow_unclaimed=True)
        ctx = _latest_kol_context(int(kol_id))
        kol = ctx["kol"]
        links = _json_loads(kol.get("contact_links_json"), [])
        if not isinstance(links, list):
            links = []
        existing_values = {
            str(item.get("value") or item.get("url") or "").strip().lower()
            for item in links
            if isinstance(item, dict)
        }
        payload: dict[str, Any] = {
            "contact_links": links,
            "notes": str(body.get("evidence") or body.get("note") or "").strip(),
        }
        if contact_type in {"email", "manager_email"} and "@" in contact_value:
            payload["contact_email"] = contact_value
        elif contact_type in {"phone", "whatsapp"}:
            payload["contact_phone"] = contact_value
        if contact_value.lower() not in existing_values:
            payload["contact_links"] = [
                *links,
                {
                    "label": contact_type,
                    "value": contact_value,
                    "url": contact_value if contact_value.startswith("http") else "",
                    "layer": _int(body.get("layer"), 5),
                    "source": str(body.get("source") or "manual_input"),
                    "confidence": _int(body.get("confidence"), 100),
                    "evidence": str(body.get("evidence") or ""),
                    "verified": True,
                    "status": "active",
                },
            ]
        kol_claims.update_kol_manual(int(kol_id), payload, staff=staff)
        return _contact_rows(int(kol_id), include_wrong=True)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.patch("/kols/{kol_id}")
def update_kol(kol_id: int, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return kol_claims.update_kol_manual(int(kol_id), body, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/kols/{kol_id}/scan-account")
async def scan_kol(kol_id: int, body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    payload = body or {}
    try:
        kol_claims.assert_kol_access(int(kol_id), staff, allow_unclaimed=True)
        return await scan_kol_account(int(kol_id), max_posts=max(1, min(int(payload.get("max_posts") or 24), 80)))
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/kols/{kol_id}/analyze-account")
async def analyze_kol(kol_id: int, body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    payload = body or {}
    try:
        kol_claims.assert_kol_access(int(kol_id), staff, allow_unclaimed=True)
        return await analyze_kol_account(int(kol_id), product_sku=str(payload.get("product_sku") or ""), snapshot_id=payload.get("snapshot_id"))
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/claims")
def list_claims(
    status: str = "active",
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    return kol_claims.list_claims(status=status, limit=limit, staff=staff)


@router.post("/kols/{kol_id}/claim")
def claim_kol(kol_id: int, body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return kol_claims.claim(kol_id, body or {}, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/claims/{claim_id}/release")
def release_claim(claim_id: int, body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return kol_claims.release(claim_id, body or {}, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/claims/{claim_id}/reassign")
def reassign_claim(claim_id: int, body: dict, staff=Depends(require_tab("vkpi", "admin"))):
    try:
        return kol_claims.reassign(claim_id, body, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/links")
def links(
    status: str = "",
    staff_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    staff=Depends(require_tab("vkpi", "read")),
):
    return link_center.list_links(limit=limit, status=status, staff=staff, staff_id_filter=staff_id)


@router.get("/links/{link_id}")
def link_detail(link_id: int, staff=Depends(require_tab("vkpi", "read"))):
    try:
        return link_center.link_detail(link_id, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/links/{link_id}/clicks")
def link_clicks(
    link_id: int,
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        return link_center.link_clicks(link_id, staff=staff, limit=limit)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/links/{link_id}/orders")
def link_orders(
    link_id: int,
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        return link_center.link_orders(link_id, staff=staff, limit=limit)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/links")
def create_link(body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return link_center.create_link(body, staff=staff)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.patch("/links/{link_id}")
def update_link(link_id: int, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return link_center.update_link(link_id, body, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/links/{link_id}/pause")
def pause_link(link_id: int, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return link_center.set_status(link_id, "paused", staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/links/{link_id}/archive")
def archive_link(link_id: int, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return link_center.set_status(link_id, "archived", staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/links/{link_id}/health-check")
def check_link(link_id: int, staff=Depends(require_tab("vkpi", "read"))):
    try:
        scope.assert_link_access(link_id, staff)
        return link_center.health_check(link_id, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc
