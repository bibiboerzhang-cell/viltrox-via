"""KOL assessment, product-fit, and contact payload builders."""
from __future__ import annotations

import re
from typing import Any

from app.domains.kol import claims as claims_domain
from app.db.connection import get_conn
from app.services.vkpi import kol_product_fit as kol_product_fit_service
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema

from app.domains.kol.payload_utils import _clamp_score, _float, _grade, _int, _json_loads


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


def assessment_for_request(kol_id: int, *, staff: dict[str, Any]) -> dict[str, Any]:
    claims_domain.assert_kol_access(int(kol_id), staff, allow_unclaimed=True)
    return _assessment_payload(int(kol_id))


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
    raw = re.split(r"[?#]", raw, maxsplit=1)[0]
    parts = [part for part in raw.split("/") if part]
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
    platform = {
        "ig": "instagram",
        "tt": "tiktok",
        "yt": "youtube",
        "fb": "facebook",
        "twitter": "x",
    }.get(platform, platform)
    handle = _normalize_handle_for_match(kol.get("channel_url") or kol.get("profile_url") or kol.get("channel_name") or kol.get("media_name"))
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


def product_fit_for_request(kol_id: int, *, limit: int = 5, staff: dict[str, Any]) -> dict[str, Any]:
    try:
        claims_domain.assert_kol_access(int(kol_id), staff, allow_unclaimed=True)
    except LookupError:
        return _product_fit_preview_payload_for_pool(int(kol_id), limit)
    return _product_fit_payload(int(kol_id), limit=limit)


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
