"""Evidence-bound KOL product opportunity ranking helpers.

This module owns creator gear/mount inference and product ranking.  It stays
separate from ``focal_matrix`` so the read/aggregate endpoint does not become a
second monolith.  Callers provide the catalog-specific focal/line parsers;
everything else is pure and side-effect free.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

_CINEMA_TERMS = (
    "cinema", "cinematic", "filmmaker", "filmmaking", "film production",
    "color grading", "camera rig", "anamorphic", "cine lens", "电影", "影视", "调色",
)
_PHOTO_TERMS = (
    "photo", "photography", "portrait", "street photography", "wedding",
    "摄影", "人像", "婚礼", "街拍",
)


def _text(value: Any, limit: int = 300) -> str:
    if isinstance(value, dict):
        value = " ".join(str(v) for v in value.values() if isinstance(v, (str, int, float)))
    elif isinstance(value, list):
        value = " ".join(str(v) for v in value if isinstance(v, (str, int, float)))
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _creator_text(pool: dict[str, Any], videos: list[dict[str, Any]]) -> str:
    parts = [
        _text(pool.get("bio"), 1200),
        _text(pool.get("raw_platform_data"), 1800),
        _text(pool.get("recommended_product_lines_json"), 600),
        _text(pool.get("primary_topic"), 300),
        _text(pool.get("content_style"), 300),
    ]
    for video in videos[:120]:
        parts.extend((_text(video.get("title_blob"), 300), _text(video.get("deep_blob"), 1200)))
    return " ".join(part for part in parts if part).lower()


def infer_creator_mount(blob: str, camera_body: str) -> tuple[str, str, str]:
    """Return mount, evidence and status; conflicting text never yields a recommendation."""
    body = camera_body.lower()
    compact_body = re.sub(r"[^a-z0-9]+", "", body)
    if compact_body.startswith(("sony", "sonya", "sonyfx", "sonyzv")):
        return "FE-mount", "明确 Sony 机身指向 E/FE", "inferred_from_camera"
    if compact_body.startswith(("nikon", "nikonz")):
        return "Z-mount", "明确 Nikon 机身指向 Z", "inferred_from_camera"
    if compact_body.startswith(("fujifilm", "fuji")):
        return "X-mount", "明确 Fujifilm 机身指向 X", "inferred_from_camera"
    if compact_body.startswith(("canonr", "canoneosr", "redkomodo")):
        return "RF-mount", "明确 Canon R/RED Komodo 机身指向 RF", "inferred_from_camera"
    if compact_body.startswith(("panasonic", "lumix", "leica")):
        if any(token in compact_body for token in ("gh4", "gh5", "gh6", "gh7")):
            return "M43", "明确 Panasonic GH 机身指向 M4/3", "inferred_from_camera"
        return "L-mount", "明确 Panasonic/Leica 机身指向 L", "inferred_from_camera"

    body_rules: tuple[tuple[str, str, str], ...] = (
        ("PL-mount", r"\barri\b", "明确 ARRI 机身指向 PL"),
        ("RF-mount", r"\bred\s*komodo\b", "明确 RED Komodo 机身指向 RF"),
        ("DL-mount", r"\bdji\s*(?:ronin\s*4d|dl)\b", "明确 DJI 机身指向 DL"),
        ("M43", r"\b(?:gh[4-7]|olympus)\b", "明确机身指向 M4/3"),
        ("X-mount", r"\bfuji(?:film)?\b", "明确 Fujifilm 机身指向 X"),
        ("Z-mount", r"\bnikon\b", "明确 Nikon 机身指向 Z"),
        ("FE-mount", r"\bsony\b|\bfx\s?(?:3|30|6|9)\b|\ba7\b", "明确 Sony 机身指向 E/FE"),
        ("L-mount", r"\b(?:panasonic|lumix|leica)\b", "明确 Panasonic/Leica 机身指向 L"),
        ("RF-mount", r"\bcanon\s*(?:eos\s*)?r", "明确 Canon R 机身指向 RF"),
        ("EF-mount", r"\bcanon\s*(?:eos\s*)?(?:5d|6d|7d|90d)\b", "明确 Canon DSLR 机身指向 EF"),
    )
    for mount, pattern, evidence in body_rules:
        if re.search(pattern, body, re.IGNORECASE):
            return mount, evidence, "inferred_from_camera"

    text_rules: tuple[tuple[str, str, str], ...] = (
        ("PL-mount", r"\bpl[- ]?mount\b|\barri\b|\bpl cine\b", "文本明确出现 PL/ARRI"),
        ("DL-mount", r"\bdji\s*(?:ronin\s*4d|dl[- ]?mount)\b", "文本明确出现 DJI DL/Ronin 4D"),
        ("M43", r"\b(?:gh[4-7]|m4/?3|micro four thirds|olympus)\b", "机身/文本指向 M4/3"),
        ("X-mount", r"\bfuji(?:film)?\b|\bx[- ]?mount\b|富士", "机身/文本指向 Fujifilm X"),
        ("Z-mount", r"\bnikon\b|\bz[- ]?mount\b|尼康", "机身/文本指向 Nikon Z"),
        ("FE-mount", r"\bsony\b|\bfx\s?(?:3|30|6|9)\b|\ba7\b|\bfe[- ]?mount\b|索尼", "机身/文本指向 Sony E/FE"),
        ("L-mount", r"\b(?:panasonic|lumix|leica|sigma)\b.*\bl[- ]?mount\b|\bl[- ]?mount\b", "文本明确出现 L-mount"),
        ("RF-mount", r"\bcanon\s*(?:eos\s*)?r\s?(?:[358]|5c|6ii|7|8|10|50|100)\b|\brf[- ]?mount\b", "机身/文本指向 Canon RF"),
        ("EF-mount", r"\bcanon\s*(?:eos\s*)?(?:5d|6d|7d|90d)\b|\bef[- ]?mount\b", "机身/文本指向 Canon EF"),
    )
    matches = {
        mount: evidence
        for mount, pattern, evidence in text_rules
        if re.search(pattern, blob.lower(), re.IGNORECASE)
    }
    if len(matches) == 1:
        mount, evidence = next(iter(matches.items()))
        return mount, evidence, "inferred_from_text"
    if len(matches) > 1:
        return "", "文本同时出现多个卡口/竞品品牌: " + "/".join(sorted(matches)), "conflict"
    return "", "未识别到可靠机身/卡口证据", "unknown"


def creator_price_profile(
    camera_body: str,
    lens_brands: list[str],
    lane: str,
    mount: str,
) -> tuple[float | None, str]:
    """Infer only a catalog tier; never claim purchasing power or collaboration budget."""
    gear = f"{camera_body} {' '.join(lens_brands)}".lower()
    if any(token in gear for token in ("arri", "red ", "venice")):
        return 8000.0, "识别到专业电影机身"
    if mount in {"PL-mount", "DL-mount"}:
        return 5000.0, f"识别到 {mount} 专业电影卡口"
    if any(token in gear for token in (
        "sony fx3", "sony fx6", "sony fx9", "sony a1", "a7s iii", "a7r v",
        "canon r5", "canon r5 c", "nikon z8", "nikon z9", "panasonic s1h",
    )):
        return 1800.0, "识别到高阶全画幅/视频机身"
    if any(token in gear for token in (
        "sony a7", "canon r6", "nikon z6", "nikon z7", "panasonic s5", "leica sl",
    )):
        return 1200.0, "识别到全画幅创作机身"
    if any(token in gear for token in (
        "sony a6", "sony zv", "fujifilm x-", "fuji x-", "nikon z50", "canon r7",
    )):
        return 700.0, "识别到 APS-C/轻量创作机身"
    if any(token in gear for token in ("sony gm", "leica", "zeiss", "canon rf", "nikon z")):
        return 1200.0, "识别到高阶常用镜头品牌/产品线"
    if any(token in gear for token in ("sigma", "tamron", "viltrox", "sirui", "samyang", "rokinon")):
        return 700.0, "识别到主流常用镜头品牌"
    if lane == "cinema" and camera_body:
        return 1200.0, "识别到视频机身与电影创作语境"
    return None, "未识别到可用于价格分层的机身/常用镜头"


def creator_context(pool: dict[str, Any], videos: list[dict[str, Any]]) -> dict[str, Any]:
    blob = _creator_text(pool, videos)
    deep_evidence_count = sum(1 for video in videos if video.get("has_deep"))
    camera_body = ""
    lens_brands: list[str] = []
    try:
        from app.domains.kol.creator_gear import gear_from_text

        gear = gear_from_text(blob)
        camera_body = _text(gear.get("camera_body"), 80)
        lens_brands = [_text(v, 60) for v in _as_list(gear.get("lens_brands")) if _text(v, 60)]
    except Exception:
        logger.debug("creator gear extraction unavailable", exc_info=True)
    mount, mount_evidence, mount_status = infer_creator_mount(blob, camera_body)
    has_cinema = any(term in blob for term in _CINEMA_TERMS)
    has_photo = any(term in blob for term in _PHOTO_TERMS)
    lane = "hybrid" if has_cinema and has_photo else "cinema" if has_cinema else "photography" if has_photo else "unknown"
    ceiling, price_evidence = creator_price_profile(camera_body, lens_brands, lane, mount)
    return {
        "camera_body": camera_body or None,
        "mount": mount or None,
        "mount_status": mount_status,
        "mount_evidence": mount_evidence,
        "lens_brands": lens_brands,
        "content_lane": lane,
        "followers": _int_or_none(pool.get("followers")),
        "avg_views": _int_or_none(pool.get("avg_views")),
        "catalog_price_ceiling_proxy_usd": ceiling,
        "price_tier_status": "inferred" if ceiling is not None else "unknown",
        "price_tier_evidence": price_evidence,
        "recommendation_status": "ready" if mount and ceiling is not None else "insufficient_evidence",
        "recommendation_stage": "deep_validated" if deep_evidence_count > 0 else "profile_preliminary",
        "deep_evidence_count": deep_evidence_count,
        "price_proxy_note": "只由已识别机身/常用镜头档次生成,仅用于目录分层,不是购买力/合作预算;粉丝量不参与。",
    }


def product_series(row: dict[str, Any]) -> str:
    explicit = _text(row.get("series"), 30).strip()
    if explicit:
        return explicit
    blob = f"{_text(row.get('sku'), 160)} {_text(row.get('model_name'), 240)}".lower()
    for token in ("evo", "pro", "lab", "air", "epic", "raze", "cine"):
        if token in blob:
            return token.upper() if token != "cine" else "Cine"
    return "Standard"


def effective_product_mount(row: dict[str, Any]) -> str:
    blob = f"{_text(row.get('sku'), 180)} {_text(row.get('model_name'), 260)}".lower()
    if re.search(r"(?:^|[- /])pl(?:[- /]|$)", blob):
        return "PL-mount"
    if "dl mount" in blob or "-dl-" in blob:
        return "DL-mount"
    raw = _text(row.get("mount"), 40).strip()
    aliases = {
        "e-mount": "FE-mount", "sony e": "FE-mount", "fe": "FE-mount",
        "z": "Z-mount", "x": "X-mount", "xf": "X-mount", "m4/3": "M43",
        "micro four thirds": "M43", "pl": "PL-mount", "dl": "DL-mount",
    }
    return aliases.get(raw.lower(), raw)


def price_fit(price: float | None, ceiling: float) -> tuple[int, str]:
    if price is None or price <= 0:
        return 0, "price_unknown"
    ratio = price / max(ceiling, 1.0)
    if ratio <= 0.35:
        return 5, "entry"
    if ratio <= 1.0:
        return 12, "within_band"
    if ratio <= 1.75:
        return 5, "stretch"
    if ratio <= 3.0:
        return -10, "high"
    return -30, "outlier"


def _opportunity_gate(
    row: dict[str, Any],
    gap_focals: set[str],
    creator_mount: str,
    lane: str,
    *,
    product_line_of: Callable[[dict[str, Any]], str],
    product_focals: Callable[[dict[str, Any], str], set[str]],
) -> tuple[str, str, str] | None:
    """The candidate filter chain; returns (line, focal, mount) or None when the row is out."""
    if _text(row.get("status"), 30).lower() != "official":
        return None
    line = product_line_of(row)
    if line not in {"af_lens", "mf_lens", "cine"}:
        return None
    focals = product_focals(row, line)
    if len(focals) != 1:
        return None
    focal = next(iter(focals))
    if focal not in gap_focals:
        return None
    mount = effective_product_mount(row)
    if creator_mount and not mount:
        return None
    if creator_mount and mount and creator_mount != mount:
        return None
    if line == "cine" and lane not in {"cinema", "hybrid"}:
        return None
    return line, focal, mount


def _mount_score(creator_mount: str, mount: str) -> tuple[int, str]:
    if creator_mount and mount == creator_mount:
        return 30, f"与推断卡口 {creator_mount} 匹配"
    if not creator_mount:
        return -5, "机身/卡口未识别,下单前必须人工核验"
    return -8, "产品卡口缺失,需人工核验"


def _content_score(line: str, lane: str) -> tuple[int, str]:
    if line == "cine":
        return 12, "视频/电影创作语境与 Cine 产品线匹配"
    if lane in {"cinema", "hybrid"}:
        return 8, "自动对焦单品适合视频/混合创作"
    if lane == "photography":
        return 12, "摄影内容与自动对焦镜头匹配"
    return 5, "创作类型证据不足,仅按焦段补位"


def _series_score(series_key: str, ceiling: float) -> int:
    if ceiling >= 1500:
        series_scores = {"lab": 12, "pro": 11, "evo": 9, "air": 5, "cine": 6, "epic": 4}
    elif ceiling >= 900:
        series_scores = {"pro": 12, "evo": 11, "lab": 9, "air": 7, "cine": 5, "epic": 3}
    else:
        series_scores = {"evo": 12, "air": 11, "pro": 8, "lab": 5, "cine": 4, "epic": 2}
    return series_scores.get(series_key, 6)


def _score_opportunity(
    line: str,
    mount: str,
    series: str,
    price: float | None,
    context: dict[str, Any],
    creator_mount: str,
    lane: str,
    ceiling: float,
) -> tuple[dict[str, int], list[str], str]:
    """Score parts + ordered reasons + price-fit label, byte-identical to the inline original."""
    score_parts = {"base": 40, "mount": 0, "content": 0, "series": 0, "price": 0, "evidence": 0}
    reasons: list[str] = []
    score_parts["mount"], mount_reason = _mount_score(creator_mount, mount)
    reasons.append(mount_reason)
    score_parts["content"], content_reason = _content_score(line, lane)
    reasons.append(content_reason)
    score_parts["series"] = _series_score(series.lower(), ceiling)
    reasons.append(f"{series} 系列参与多样化候选")
    price_score, price_fit_value = price_fit(price, ceiling)
    score_parts["price"] = price_score
    if price is not None:
        reasons.append(f"目录价 USD {price:,.0f} · 价格带 {price_fit_value}")
    if context.get("camera_body"):
        score_parts["evidence"] += 3
    if context.get("lens_brands"):
        score_parts["evidence"] += 2
        reasons.append("已识别常用镜头品牌: " + "/".join(context["lens_brands"][:3]))
    return score_parts, reasons, price_fit_value


def _opportunity_confidence(matched: bool, context: dict[str, Any]) -> str:
    if matched and context.get("camera_body"):
        return "high"
    if matched:
        return "medium"
    return "low"


def _opportunity_payload(
    row: dict[str, Any],
    focal: str,
    line: str,
    mount: str,
    series: str,
    price: float | None,
    score_parts: dict[str, int],
    reasons: list[str],
    price_fit_value: str,
    context: dict[str, Any],
    creator_mount: str,
    ceiling: float,
    *,
    focal_sort_mm: Callable[[str], float],
    line_labels: dict[str, str],
) -> dict[str, Any]:
    matched = bool(creator_mount and mount == creator_mount)
    name = _text(row.get("marketing_name"), 220) or _text(row.get("model_name"), 220)
    return {
        "focal": focal,
        "mm": focal_sort_mm(focal),
        "sku": _text(row.get("sku"), 160),
        "product_name": name or _text(row.get("sku"), 160),
        "flagship": name or _text(row.get("sku"), 160),
        "series": [series],
        "line": line_labels.get(line, line),
        "lines": [line_labels.get(line, line)],
        "mount": mount or None,
        "price_usd": price,
        "max_price_usd": price,
        "product_url": _text(row.get("product_url"), 500) or None,
        "official_catalog_product_id": _text(row.get("official_catalog_product_id"), 100) or None,
        "sku_count": 1,
        "official_sku_count": 1,
        "value_usd": price or 0.0,
        "recommendation_score": sum(score_parts.values()),
        "score_breakdown": score_parts,
        "compatibility_status": "compatible" if matched else "mount_unknown",
        "confidence": _opportunity_confidence(matched, context),
        "price_fit": price_fit_value,
        "price_distance": abs((price or ceiling) - ceiling * 0.65),
        "reasons": reasons[:5],
    }


def _opportunity_candidate(
    row: dict[str, Any],
    gap_focals: set[str],
    context: dict[str, Any],
    creator_mount: str,
    lane: str,
    ceiling: float,
    *,
    product_line_of: Callable[[dict[str, Any]], str],
    product_focals: Callable[[dict[str, Any], str], set[str]],
    focal_sort_mm: Callable[[str], float],
    line_labels: dict[str, str],
) -> dict[str, Any] | None:
    gate = _opportunity_gate(
        row, gap_focals, creator_mount, lane,
        product_line_of=product_line_of, product_focals=product_focals,
    )
    if gate is None:
        return None
    line, focal, mount = gate
    price = _float_or_none(row.get("price_usd"))
    series = product_series(row)
    score_parts, reasons, price_fit_value = _score_opportunity(
        line, mount, series, price, context, creator_mount, lane, ceiling,
    )
    if price_fit_value == "outlier" and line == "cine" and creator_mount != "PL-mount":
        return None
    return _opportunity_payload(
        row, focal, line, mount, series, price, score_parts, reasons, price_fit_value,
        context, creator_mount, ceiling,
        focal_sort_mm=focal_sort_mm, line_labels=line_labels,
    )


def _select_diversified(candidates: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Score-descending sort, then two passes: series-diverse first, family-deduped fill second."""
    candidates.sort(key=lambda item: (
        item["recommendation_score"],
        1 if item["compatibility_status"] == "compatible" else 0,
        -item["price_distance"],
        item["sku"],
    ), reverse=True)

    selected: list[dict[str, Any]] = []
    seen_series: set[str] = set()
    seen_family: set[tuple[str, str]] = set()
    for diversify in (True, False):
        for item in candidates:
            family = (item["focal"], "/".join(item["series"]).lower())
            series_key = "/".join(item["series"]).lower()
            if family in seen_family or item in selected:
                continue
            if diversify and series_key in seen_series:
                continue
            selected.append(item)
            seen_series.add(series_key)
            seen_family.add(family)
            if len(selected) >= limit:
                return selected
    return selected


def product_opportunities(
    products: list[dict[str, Any]],
    gap_focals: set[str],
    context: dict[str, Any],
    *,
    product_line_of: Callable[[dict[str, Any]], str],
    product_focals: Callable[[dict[str, Any], str], set[str]],
    focal_sort_mm: Callable[[str], float],
    line_labels: dict[str, str],
    limit: int = 12,
) -> list[dict[str, Any]]:
    creator_mount = _text(context.get("mount"), 40)
    lane = _text(context.get("content_lane"), 20) or "unknown"
    ceiling = _float_or_none(context.get("catalog_price_ceiling_proxy_usd"))
    if not creator_mount or ceiling is None:
        return []
    candidates: list[dict[str, Any]] = []
    for row in products:
        candidate = _opportunity_candidate(
            row, gap_focals, context, creator_mount, lane, ceiling,
            product_line_of=product_line_of, product_focals=product_focals,
            focal_sort_mm=focal_sort_mm, line_labels=line_labels,
        )
        if candidate is None:
            continue
        candidates.append(candidate)
    return _select_diversified(candidates, limit)
