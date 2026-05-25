"""Read-only single KOL intelligence card aggregation.

P2 starts with an API shape that gathers existing evidence into one response.
This module intentionally does not call providers, enqueue jobs, call LLMs, or
write derived rows.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from app.db.connection import get_conn
from app.domains.kol import history_match as kol_history_match
from app.domains.kol import product_fit as kol_product_fit
from app.domains.kol import pool as kol_pool
from app.services.vkpi import refresh_tier


from app.domains.kol.intelligence_card_helpers import *  # noqa: F403


def _handle_variants(item: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for value in (item.get("handle"), item.get("profile_url")):
        text = _text(value).lower()
        if not text:
            continue
        candidates = [text]
        if "/" in text:
            candidates = []
            at_match = re.search(r"@[a-z0-9_.-]+", text)
            if at_match:
                candidates.append(at_match.group(0))
            last_segment = text.split("?", 1)[0].rstrip("/").split("/")[-1]
            if last_segment:
                candidates.append(last_segment)
        for candidate in candidates:
            values.append(candidate)
            if candidate.startswith("@"):
                values.append(candidate[1:])
            elif "@" not in candidate and "/" not in candidate:
                values.append(f"@{candidate}")
    deduped: list[str] = []
    for value in values:
        clean = value.strip().strip("/")
        if clean and clean not in deduped:
            deduped.append(clean)
    return deduped[:12]


def _submission_video_analysis_rows(item: dict[str, Any], *, limit: int = 12) -> list[dict[str, Any]]:
    if not _table_exists("submissions"):
        return []
    platform = _text(item.get("platform")).lower()
    variants = _handle_variants(item)
    where = ["COALESCE(video_analysis, '') <> ''"]
    params: list[Any] = []
    if platform:
        where.append("LOWER(COALESCE(platform, '')) = ?")
        params.append(platform)
    handle_clauses: list[str] = []
    for variant in variants:
        handle_clauses.append("LOWER(COALESCE(extracted_handle, '')) = ?")
        params.append(variant.lstrip("@"))
        handle_clauses.append("LOWER(COALESCE(extracted_handle, '')) = ?")
        params.append(variant if variant.startswith("@") else f"@{variant}")
        handle_clauses.append("LOWER(COALESCE(url, '')) LIKE ?")
        params.append(f"%{variant}%")
    if handle_clauses:
        where.append(f"({' OR '.join(handle_clauses)})")
    conn = get_conn()
    rows = conn.execute(
        f"""
        SELECT id, created_at, platform, url, extracted_handle, title, video_analysis
        FROM submissions
        WHERE {" AND ".join(where)}
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (*params, max(1, min(50, int(limit or 12)))),
    ).fetchall()
    return [dict(row) for row in rows]


def _analysis_confidence(analysis: dict[str, Any]) -> float:
    confidence = _text(analysis.get("confidence")).lower()
    if confidence in {"high", "strong"}:
        return 0.85
    if confidence in {"medium", "moderate"}:
        return 0.65
    if confidence in {"low", "weak"}:
        return 0.35
    overall = _safe_float(analysis.get("quality_overall"))
    if overall > 1:
        return round(min(1.0, overall / 100.0), 2)
    if overall > 0:
        return round(min(1.0, overall), 2)
    if bool(analysis.get("analyzed")):
        return 0.5
    return 0.0


def _analysis_fields(analysis: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    aliases = {
        "target_audience": ("target_audience", "audience_fit", "audience_segment"),
        "competitor_products": ("competitor_products", "competitors", "other_lens"),
        "brand_integration_depth": ("brand_integration_depth", "brand_depth"),
        "products_found": ("products_found", "products_detected", "viltrox_lens"),
    }
    for key in VIDEO_ANALYSIS_FIELD_KEYS:
        source_keys = aliases.get(key, (key,))
        for source_key in source_keys:
            value = analysis.get(source_key)
            if value not in (None, "", [], {}):
                fields[key] = value
                break
    return fields


def _video_analysis_evidence(row: dict[str, Any]) -> dict[str, Any] | None:
    analysis = _loads(row.get("video_analysis"), {})
    if not isinstance(analysis, dict):
        return None
    fields = _analysis_fields(analysis)
    if not bool(analysis.get("analyzed")) or not fields:
        return None
    source_id = _safe_int(row.get("id"))
    method = _text(analysis.get("method") or "stored_video_analysis")
    return {
        "evidence_id": f"ev_video_analysis_submission_{source_id}",
        "source": "video_analysis",
        "source_table": "submissions",
        "source_id": source_id,
        "source_url": _text(row.get("url")),
        "captured_at": _text(row.get("created_at")),
        "title": _text(row.get("title")),
        "platform": _text(row.get("platform")),
        "handle": _text(row.get("extracted_handle")),
        "method": method,
        "analyzed": True,
        "confidence": _analysis_confidence(analysis),
        "confidence_method": method,
        "reasoning": _text(analysis.get("quality_summary") or analysis.get("content_summary") or analysis.get("notes") or "Stored video analysis row."),
        "raw_data_ref": f"submissions:{source_id}:video_analysis",
        "rebuttal_supported": True,
        "field_names": list(fields.keys()),
        "fields": fields,
        "provider_badge_allowed": bool("gemini" in method.lower()),
    }


def _video_analysis(item: dict[str, Any]) -> dict[str, Any]:
    if not _table_exists("submissions"):
        return {
            "status": "not_configured",
            "method": "read_only_stored_video_analysis_v0",
            "row_count": 0,
            "evidence_count": 0,
            "evidence": [],
            "field_contract": list(VIDEO_ANALYSIS_FIELD_KEYS),
            "provider_calls": False,
            "llm_calls": False,
            "write_db": False,
            "source": "submissions.video_analysis",
        }
    try:
        rows = _submission_video_analysis_rows(item, limit=12)
    except Exception as exc:
        return _status_payload(
            "unavailable",
            reason="video_analysis_query_failed",
            error=exc,
            method="read_only_stored_video_analysis_v0",
            row_count=0,
            evidence_count=0,
            evidence=[],
            field_contract=list(VIDEO_ANALYSIS_FIELD_KEYS),
            provider_calls=False,
            llm_calls=False,
            write_db=False,
            source="submissions.video_analysis",
        )
    evidence = [leaf for leaf in (_video_analysis_evidence(row) for row in rows) if isinstance(leaf, dict)]
    field_counts = Counter(field for leaf in evidence for field in leaf.get("field_names", []))
    return {
        "status": "ready" if evidence else "empty",
        "method": "read_only_stored_video_analysis_v0",
        "row_count": len(rows),
        "analyzed_count": len(evidence),
        "evidence_count": len(evidence),
        "field_counts": dict(field_counts),
        "evidence": evidence[:12],
        "field_contract": list(VIDEO_ANALYSIS_FIELD_KEYS),
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "source": "submissions.video_analysis",
        "empty_reason": "" if evidence else "no_stored_analyzed_video_rows",
    }


def _cached_post_summaries(item: dict[str, Any], *, limit: int = 6) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for post in _cached_posts(item, limit=limit):
        if not isinstance(post, dict):
            continue
        snippet = post.get("snippet") if isinstance(post.get("snippet"), dict) else {}
        stats = post.get("statistics") if isinstance(post.get("statistics"), dict) else {}
        post_uid = _text(post.get("id") or post.get("post_uid") or post.get("shortCode") or post.get("shortcode"))
        url = _text(post.get("post_url") or post.get("url") or post.get("webVideoUrl") or post.get("permalink"))
        if not url and _text(post.get("kind")).lower() == "youtube#video" and post_uid:
            url = f"https://www.youtube.com/watch?v={post_uid}"
        title = _text(post.get("title") or post.get("caption") or post.get("text") or snippet.get("title") or url)
        if not title and not url:
            continue
        rows.append(
            {
                "id": post_uid or url or f"cached-post-{len(rows) + 1}",
                "title": title[:280],
                "url": url,
                "post_url": url,
                "published_at": _text(post.get("published_at") or post.get("publishedAt") or post.get("timestamp") or snippet.get("publishedAt")),
                "views": _safe_int(post.get("views") or post.get("view_count") or post.get("playCount") or stats.get("viewCount")),
                "likes": _safe_int(post.get("likes") or post.get("like_count") or post.get("diggCount") or stats.get("likeCount")),
                "comments": _safe_int(post.get("comments") or post.get("comment_count") or post.get("commentCount") or stats.get("commentCount")),
                "source_kind": "kol_pool_cached_post",
            }
        )
        if len(rows) >= limit:
            break
    return rows


def _memory_card(item: dict[str, Any], competitors: dict[str, Any]) -> dict[str, Any]:
    raw = _loads(item.get("raw_platform_data"), {})
    evidence_summary = raw.get("evidence_summary") if isinstance(raw, dict) and isinstance(raw.get("evidence_summary"), dict) else {}
    try:
        history = kol_history_match.find_history_match(item, platform=_text(item.get("platform"))) or {}
        history_status = "ready" if history else "empty"
    except Exception as exc:
        history = {}
        history_status = "unavailable"
        history_error = f"{type(exc).__name__}: {str(exc)[:240]}"
    else:
        history_error = ""

    recent_posts = history.get("recent_posts") if isinstance(history.get("recent_posts"), list) else []
    if not recent_posts:
        recent_posts = _cached_post_summaries(item, limit=6)
    recent_cooperations = history.get("recent_cooperations") if isinstance(history.get("recent_cooperations"), list) else []
    brand_collaborations = _brief_list(item.get("brand_collaborations_json"))
    recommended_products = _brief_list(item.get("recommended_product_lines_json"))
    potential_concerns = _brief_list(item.get("potential_concerns_json"))
    relations = competitors.get("relations") if isinstance(competitors.get("relations"), list) else []
    competitor_summary = competitors.get("summary") if isinstance(competitors.get("summary"), dict) else {}
    cooperation_count = max(
        _safe_int(history.get("cooperation_count")),
        len(brand_collaborations),
        _safe_int(evidence_summary.get("cooperation_rows")),
    )
    evidence_count = max(_safe_int(history.get("evidence_count")), _safe_int(evidence_summary.get("evidence_count")))
    has_memory = any(
        [
            _text(item.get("source_type")),
            _text(item.get("source_ref")),
            item.get("linked_main_kol_id") is not None,
            cooperation_count,
            evidence_count,
            recent_posts,
            recent_cooperations,
            brand_collaborations,
            relations,
        ]
    )
    status = "ready" if has_memory else history_status
    payload = {
        "status": status,
        "source_type": _text(item.get("source_type") or history.get("source_type")),
        "source_ref": _text(item.get("source_ref") or history.get("source_ref")),
        "linked_main_kol_id": item.get("linked_main_kol_id") or history.get("linked_main_kol_id"),
        "history_match": {
            "status": history_status,
            "matched": bool(history.get("matched")),
            "match_type": _text(history.get("match_type")),
            "match_confidence": _safe_float(history.get("match_confidence")),
            "kol_pool_id": history.get("kol_pool_id"),
            "cooperation_count": cooperation_count,
            "evidence_count": evidence_count,
            "profile_rows": _safe_int(history.get("profile_rows") or evidence_summary.get("kol_profile_rows")),
            "risk_rows": _safe_int(history.get("risk_rows") or evidence_summary.get("risk_rows")),
        },
        "excel_record": {
            "source_type": _text(item.get("source_type")),
            "source_ref": _text(item.get("source_ref")),
            "brand_collaborations": brand_collaborations,
            "recommended_products": recommended_products,
            "potential_concerns": potential_concerns,
            "raw_evidence_summary": evidence_summary,
        },
        "recent_cooperations": recent_cooperations[:5],
        "recent_posts": recent_posts[:6],
        "competitor_memory": {
            "relation_count": len(relations),
            "strongest_brand": _text(competitor_summary.get("competitor_brand")),
            "risk_tier": _text(competitor_summary.get("risk_tier") or "opportunity"),
            "risk_score": _safe_float(competitor_summary.get("risk_score")),
        },
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "source": "vkpi_kol_pool + legacy memory",
    }
    if history_error:
        payload["history_error"] = history_error
    return payload


def _product_identity(item: dict[str, Any]) -> str:
    return _text(
        item.get("sku")
        or item.get("product_sku")
        or item.get("product_key")
        or item.get("product_family_uid")
        or item.get("product_family_name")
        or item.get("family_key")
        or item.get("product_name")
        or item.get("name")
    )


def _catalog_product_is_official(product: dict[str, Any]) -> bool:
    if not _text(product.get("sku")):
        return False
    specs = product.get("specs") if isinstance(product.get("specs"), dict) else {}
    return any(
        [
            _text(product.get("model_name")),
            _text(product.get("marketing_name")),
            _text(product.get("mount")),
            product.get("price_usd") is not None,
            _text(product.get("product_url")),
            bool(specs),
        ]
    )


def _product_fit_catalog_evidence(row: dict[str, Any], product: dict[str, Any], *, index: int) -> dict[str, Any]:
    specs = product.get("specs") if isinstance(product.get("specs"), dict) else {}
    sku = _text(product.get("sku")) or f"catalog-product-{index}"
    mount = _text(product.get("mount") or specs.get("lens_mount"))
    return {
        "evidence_id": f"ev_official_catalog_{sku}",
        "source": "official_catalog",
        "source_table": "vkpi_products",
        "source_id": sku,
        "source_url": _text(product.get("product_url")),
        "confidence": _safe_float(product.get("source_confidence"), 1.0) or 1.0,
        "confidence_method": "catalog_source_confidence",
        "reasoning": f"官方 SKU {sku} 提供产品、卡口和规格证据。",
        "rebuttal_supported": False,
        "sku": sku,
        "model_name": _text(product.get("model_name")),
        "marketing_name": _text(product.get("marketing_name")),
        "mount": mount,
        "price_usd": product.get("price_usd"),
        "specs": specs,
        "product_family_name": _text(row.get("product_family_name")),
        "score": _safe_float(row.get("score")),
    }


def _product_fit_discovery_evidence(row: dict[str, Any], *, index: int) -> dict[str, Any]:
    identity = _product_identity(row) or f"product-family-{index}"
    return {
        "evidence_id": f"ev_product_family_discovery_{index}",
        "source": "rule_engine",
        "source_table": "vkpi_memory_entities",
        "source_id": _text(row.get("product_family_uid") or identity),
        "confidence": 0.35,
        "confidence_method": "rule_v0_low_confidence",
        "reasoning": "只有产品族或历史标签证据，不能当作官方 SKU 适配完成。",
        "rebuttal_supported": True,
        "product_family_name": identity,
        "score": _safe_float(row.get("score")),
        "score_breakdown": row.get("score_breakdown") if isinstance(row.get("score_breakdown"), dict) else {},
    }


def _product_fit_rule_evidence(row: dict[str, Any], source: dict[str, Any], *, index: int) -> dict[str, Any]:
    evidence_type = _text(source.get("type") or source.get("evidence_type") or "rule_evidence")
    payload = source.get("payload") if isinstance(source.get("payload"), dict) else {}
    return {
        "evidence_id": f"ev_rule_engine_product_fit_{index}_{evidence_type}",
        "source": "rule_engine",
        "source_table": _text(source.get("source_table") or payload.get("source_table") or "vkpi_memory_entities"),
        "source_id": source.get("source_id") or payload.get("source_id") or _text(row.get("product_family_uid")),
        "source_url": _text(source.get("source_url") or payload.get("source_url")),
        "confidence": _safe_float(source.get("confidence") or source.get("confidence_score") or payload.get("confidence"), 0.5),
        "confidence_method": "rule_v0",
        "reasoning": _text(source.get("detail") or source.get("reasoning") or evidence_type.replace("_", " ")),
        "rebuttal_supported": True,
        "evidence_type": evidence_type,
        "polarity": _text(source.get("polarity")),
        "severity": _text(source.get("severity")),
        "score_component": _text(source.get("score_component")),
        "product_family_name": _text(row.get("product_family_name")),
    }


def _product_fit_evidence(rows: list[dict[str, Any]]) -> dict[str, Any]:
    official: list[dict[str, Any]] = []
    discovery: list[dict[str, Any]] = []
    rules: list[dict[str, Any]] = []
    seen_skus: set[str] = set()
    for row_index, row in enumerate(rows[:10]):
        if not isinstance(row, dict):
            continue
        products = []
        first_product = row.get("matched_catalog_product") if isinstance(row.get("matched_catalog_product"), dict) else {}
        if first_product:
            products.append(first_product)
        products.extend(product for product in row.get("matched_catalog_products", []) if isinstance(product, dict))
        official_for_row = 0
        for product in products:
            if not _catalog_product_is_official(product):
                continue
            sku = _text(product.get("sku"))
            if sku in seen_skus:
                continue
            seen_skus.add(sku)
            official_for_row += 1
            official.append(_product_fit_catalog_evidence(row, product, index=len(official) + 1))
        if official_for_row == 0:
            discovery.append(_product_fit_discovery_evidence(row, index=row_index + 1))
        evidence_pro = row.get("evidence_pro") if isinstance(row.get("evidence_pro"), list) else []
        evidence_con = row.get("evidence_con") if isinstance(row.get("evidence_con"), list) else []
        for source in [*evidence_pro[:4], *evidence_con[:3]]:
            if isinstance(source, dict):
                rules.append(_product_fit_rule_evidence(row, source, index=len(rules) + 1))
    official_rows = official[:12]
    discovery_rows = discovery[:8]
    rule_rows = rules[:16]
    return {
        "official_catalog": official_rows,
        "discovery": discovery_rows,
        "rule_evidence": rule_rows,
        "evidence": [*official_rows, *discovery_rows, *rule_rows],
        "official_catalog_count": len(official_rows),
        "discovery_count": len(discovery_rows),
        "rule_evidence_count": len(rule_rows),
        "official_catalog_total": len(official),
        "discovery_total": len(discovery),
        "rule_evidence_total": len(rules),
    }


def _product_fit(kol_pool_id: int, *, include_product_fit: bool) -> dict[str, Any]:
    if not include_product_fit:
        return _status_payload("skipped", reason="include_product_fit_false")
    try:
        payload = kol_product_fit.build_kol_product_fit_preview(
            kol_pool_id=kol_pool_id,
            limit=10,
            include_low_evidence=False,
            with_llm_reasons=False,
            persist_run=False,
        )
    except Exception as exc:
        return _status_payload("unavailable", reason="product_fit_preview_unavailable", error=exc)
    rows = payload.get("items") or payload.get("eligible") or payload.get("recommendations") or []
    if not isinstance(rows, list):
        rows = []
    evidence = _product_fit_evidence([row for row in rows if isinstance(row, dict)])
    return {
        "status": "ready" if rows else "empty",
        "method": payload.get("method") or payload.get("mode") or "kol_product_fit_preview",
        "count": len(rows),
        "top": rows[:5],
        **evidence,
        "provider_calls": bool(payload.get("provider_calls", False)),
        "llm_calls": bool(payload.get("llm_calls", False)),
        "write_db": bool(payload.get("write_db", False) or payload.get("persisted", False)),
        "source": "vkpi_memory_entities + vkpi_kol_pool + dimensions11",
    }


def _freshness(kol_pool_id: int) -> dict[str, Any]:
    try:
        return refresh_tier.freshness_for_kol(kol_pool_id)
    except Exception as exc:
        return _status_payload("unavailable", reason="freshness_unavailable", error=exc)


def _decision_support(sections: dict[str, dict[str, Any]]) -> dict[str, Any]:
    gaps: list[str] = []
    ready = 0
    for name, payload in sections.items():
        status = _text(payload.get("status") or ("ready" if name == "freshness" else "")).lower()
        if status in {"ready", "empty"}:
            ready += 1
        elif status in {"unavailable", "error"}:
            gaps.append(f"{name}_{status}")
        elif status == "skipped":
            gaps.append(f"{name}_skipped")
    if gaps:
        readiness = "partial"
    elif ready == len(sections):
        readiness = "ready"
    else:
        readiness = "unknown"
    return {
        "readiness": readiness,
        "ready_sections": ready,
        "total_sections": len(sections),
        "gaps": gaps,
    }


def _confidence_for_section(section: str, payload: dict[str, Any]) -> float:
    if section == "dimensions11":
        confidence = payload.get("confidence") if isinstance(payload.get("confidence"), dict) else {}
        return _safe_float(confidence.get("overall"))
    if section == "product_fit":
        if _safe_int(payload.get("official_catalog_count")):
            return 0.85
        if _safe_int(payload.get("discovery_count")):
            return 0.35
        top = payload.get("top") if isinstance(payload.get("top"), list) else []
        scores = [_safe_float(row.get("confidence") or row.get("fit_confidence")) for row in top if isinstance(row, dict)]
        return max(scores) if scores else 0.0
    if section == "competitors":
        return 1.0 if _safe_int(payload.get("evidence_count")) > 0 else 0.0
    if payload.get("status") == "ready":
        return 1.0
    if payload.get("status") == "empty":
        return 0.0
    return 0.0


def _evidence_count_for_section(section: str, payload: dict[str, Any]) -> int:
    if section == "freshness":
        return 1 if payload.get("last_refresh_at") or payload.get("last_refresh_status") else 0
    if section == "dimensions11":
        confidence = payload.get("confidence") if isinstance(payload.get("confidence"), dict) else {}
        return sum(1 for key in ("block1_content", "block2_performance", "block3_business", "block4_specialty") if _safe_float(confidence.get(key)) > 0)
    if section == "competitors":
        if payload.get("evidence_count") is not None:
            return _safe_int(payload.get("evidence_count"))
        evidence = payload.get("evidence") if isinstance(payload.get("evidence"), list) else []
        if evidence:
            return len(evidence)
        relations = payload.get("relations") if isinstance(payload.get("relations"), list) else []
        return len(relations)
    if section == "brand_signal":
        return _safe_int(payload.get("signal_count"))
    if section == "comment_intelligence":
        return _safe_int(payload.get("evidence_count")) or _safe_int(payload.get("cached_comment_count")) + _safe_int(payload.get("run_count"))
    if section == "video_analysis":
        return _safe_int(payload.get("evidence_count"))
    if section == "memory_card":
        history = payload.get("history_match") if isinstance(payload.get("history_match"), dict) else {}
        competitor_memory = payload.get("competitor_memory") if isinstance(payload.get("competitor_memory"), dict) else {}
        cooperation_count = _safe_int(history.get("cooperation_count"))
        recent_posts = payload.get("recent_posts") if isinstance(payload.get("recent_posts"), list) else []
        recent_cooperations = payload.get("recent_cooperations") if isinstance(payload.get("recent_cooperations"), list) else []
        return cooperation_count + len(recent_posts) + len(recent_cooperations) + _safe_int(competitor_memory.get("relation_count"))
    if section == "product_fit":
        evidence_count = (
            _safe_int(payload.get("official_catalog_count"))
            + _safe_int(payload.get("discovery_count"))
            + _safe_int(payload.get("rule_evidence_count"))
        )
        return evidence_count or _safe_int(payload.get("count"))
    return 0


def _evidence_index(sections: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    sources = {
        "freshness": ("Freshness", "vkpi_kol_refresh_tier"),
        "dimensions11": ("11D Confidence", "vkpi_kol_pool + cached posts"),
        "competitors": ("Competitors", "vkpi_competitor_relation or cached posts"),
        "brand_signal": ("Brand Signal", "vkpi_kol_pool.raw_platform_data"),
        "comment_intelligence": ("Comment Intelligence", "vkpi_comments + vkpi_comment_intelligence_runs"),
        "video_analysis": ("Video Analysis", "submissions.video_analysis"),
        "memory_card": ("Memory Card", "vkpi_kol_pool + legacy memory"),
        "product_fit": ("Product Fit", "vkpi_memory_entities/product families"),
    }
    rows: list[dict[str, Any]] = []
    for section in ("freshness", "dimensions11", "competitors", "brand_signal", "comment_intelligence", "video_analysis", "memory_card", "product_fit"):
        payload = sections.get(section, {})
        label, source = sources[section]
        row = {
            "section": section,
            "label": label,
            "source": source,
            "status": payload.get("status") or ("ready" if section == "freshness" else "unknown"),
            "evidence_count": _evidence_count_for_section(section, payload),
            "confidence": _confidence_for_section(section, payload),
        }
        if section == "freshness" and payload.get("days_old") is not None:
            row["freshness_hours"] = _safe_int(payload.get("days_old")) * 24
        rows.append(row)
    return rows


def build_kol_pool_intelligence_card(kol_pool_id: int, *, include_product_fit: bool = True) -> dict[str, Any]:
    item = kol_pool.get_item(kol_pool_id)["item"]
    competitors = _competitors(kol_pool_id)
    sections = {
        "freshness": _freshness(kol_pool_id),
        "dimensions11": _dimensions11(kol_pool_id),
        "competitors": competitors,
        "brand_signal": _brand_signals(item),
        "comment_intelligence": _comment_intelligence(item),
        "video_analysis": _video_analysis(item),
        "memory_card": _memory_card(item, competitors),
        "product_fit": _product_fit(kol_pool_id, include_product_fit=include_product_fit),
    }
    return {
        "mode": "read_only_kol_intelligence_card_v0",
        "generated_at": _utcnow(),
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "kol_pool_id": int(kol_pool_id),
        "item": _item_summary(item),
        **sections,
        "decision_support": _decision_support(sections),
        "evidence_index": _evidence_index(sections),
    }
