"""Server-owned term registries for targeted KOL search evidence.

This module is deliberately IO-free.  It is the only authority allowed to
expand a product capability or requested scene into aliases or prospective-use
workflows.  Payload-supplied arrays are always rebuilt from these registries.
"""
from __future__ import annotations

import re
from typing import Any


LOCKED_TERM_GROUPS_SCHEMA = "targeted_locked_term_groups_v1"
LOCKED_TERM_GROUPS_VERSION = 1
LOCKED_TERM_GROUPS_SOURCE = "server_targeted_contract"


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _normal_term(value: Any) -> str:
    return " ".join(re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", _text(value).lower()).split())


# Product aliases describe a concrete capability.  They are not brand/model
# ownership evidence and do not award any brand-history score.
_CONTROLLED_PRODUCT_ALIASES: dict[str, tuple[str, ...]] = {
    "on-camera flash": (
        "on-camera flash", "on camera flash", "speedlight", "speedlite",
        "strobe", "ttl flash", "hss flash", "闪光灯", "机顶闪光灯",
    ),
    "camera monitor": (
        "camera monitor", "field monitor", "external monitor", "监视器", "监看器",
    ),
    "cinema lens": (
        "cinema lens", "cine lens", "anamorphic lens", "anamorphic", "电影镜头", "变形宽银幕镜头",
    ),
    "macro lens": ("macro lens", "macro", "微距镜头", "微距"),
    "macro cinema lens": ("macro cinema lens", "macro cine lens", "cine macro lens", "微距电影镜头"),
    "ultra-wide lens": ("ultra-wide lens", "ultra wide lens", "ultrawide lens", "超广角镜头"),
    "wide-angle lens": ("wide-angle lens", "wide angle lens", "wide lens", "广角镜头"),
    "super-telephoto lens": ("super-telephoto lens", "super telephoto lens", "long telephoto lens", "超长焦镜头"),
    "telephoto portrait lens": (
        "telephoto portrait lens", "portrait telephoto lens", "telephoto lens", "long lens",
        "长焦人像镜头", "长焦镜头",
    ),
    "portrait lens": ("portrait lens", "portrait prime", "人像镜头"),
    "camera lens": ("camera lens", "interchangeable lens", "相机镜头"),
    "creator gear": ("creator gear",),
}


# Scene aliases are intentionally more specific for ambiguous categories such
# as events/stage: a bare generic word does not become hard evidence.
_CONTROLLED_SCENE_ALIASES: dict[str, tuple[str, ...]] = {
    "motorsport": (
        "motorsport", "motor sport", "motorsport photographer", "racing", "race photography",
        "race photographer", "automotive", "automotive photographer", "car photography",
        "car photographer", "car show photographer", "pit lane photographer", "race paddock photographer",
        "赛车", "赛车摄影", "汽车摄影", "车展摄影", "赛道摄影", "机车", "摩托",
    ),
    "food": ("food", "chef", "culinary", "restaurant", "cooking", "餐饮", "美食", "厨师", "烹饪"),
    "wedding": ("wedding", "bridal", "婚礼"),
    "event": (
        "event photographer", "event photography", "corporate event photographer",
        "conference photographer", "red carpet photographer", "活动摄影", "活动摄影师",
        "会议摄影", "发布会摄影", "红毯摄影",
    ),
    "stage": (
        "stage photographer", "stage photography", "concert photographer", "concert photography",
        "live music photographer", "performance photographer", "theater photographer", "theatre photographer",
        "舞台摄影", "舞台摄影师", "演唱会摄影", "演出摄影", "剧场摄影",
    ),
    "wildlife": (
        "wildlife", "wildlife photographer", "wildlife photography", "bird photographer", "bird photography",
        "野生动物", "野生动物摄影", "野生动物摄影师", "鸟类摄影", "鸟类摄影师",
    ),
    "portrait": (
        "portrait", "portrait photographer", "portrait photography", "environmental portrait photographer",
        "人像", "人像摄影", "人像摄影师", "环境人像摄影",
    ),
    "pet": ("pet", "pets", "dog", "dogs", "animal", "宠物"),
    "travel": ("travel", "destination", "旅行", "旅拍"),
    "fitness": ("fitness", "gym", "健身"),
    "sports": ("sports", "sport", "体育", "运动摄影"),
    "real_estate": ("real estate", "property", "interior", "房产", "房地产", "室内"),
    "commercial": ("commercial", "advertising", "campaign", "商业广告", "广告"),
    "music_video": ("music video", "mv", "音乐视频"),
    "documentary": ("documentary", "纪录片"),
}


# These phrases prove only descriptive prospective suitability.  They do not
# claim product possession, partnership success, or verified conversion.
_CAMERA_VISUAL_ROLE_TERMS: tuple[str, ...] = (
    # Keep the generic-camera fallback occupational: a bare ``creator`` or
    # gaming/streaming label is deliberately absent.  Compound roles are used
    # instead of equipment ownership claims, and the independent scene group
    # must still be proven by public evidence.
    "professional photographer", "portrait photographer", "wedding photographer",
    "event photographer", "commercial photographer", "product photographer",
    "food photographer", "restaurant photographer", "automotive photographer",
    "motorsport photographer", "sports photographer", "wildlife photographer",
    "architecture photographer", "real estate photographer", "travel photographer",
    "street photographer", "professional filmmaker", "solo filmmaker",
    "documentary filmmaker", "commercial filmmaker", "product filmmaker",
    "professional videographer", "wedding videographer", "event videographer",
    "commercial videographer", "travel videographer", "cinematographer",
    "camera operator", "专业摄影师", "人像摄影师", "婚礼摄影师",
    "活动摄影师", "商业摄影师", "产品摄影师", "美食摄影师",
    "汽车摄影师", "赛车摄影师", "体育摄影师", "野生动物摄影师",
    "建筑摄影师", "旅行摄影师", "电影摄影师", "摄影指导",
    "摄影机操作员", "独立电影制作人", "纪录片制作人",
    "产品视频制作人", "婚礼摄像师", "活动摄像师", "商业摄像师",
)

_CAMERA_MONITOR_ROLE_TERMS: tuple[str, ...] = (
    "cinematographer", "camera operator", "solo filmmaker",
    "wedding videographer", "event videographer", "commercial videographer",
    "product filmmaker", "documentary filmmaker", "电影摄影师", "摄影指导",
    "摄影机操作员", "独立电影制作人", "婚礼摄像师", "活动摄像师",
    "商业摄像师", "产品视频制作人", "纪录片制作人",
)

_CONTROLLED_CAPABILITY_USE_MAP: dict[str, tuple[str, ...]] = {
    "on-camera flash": (
        "portrait lighting", "portrait photographer", "wedding photographer",
        "event photographer", "nightlife photographer", "club photographer",
        "red carpet photographer", "motorsport photographer", "automotive photographer",
        "car photographer", "car show photographer", "pit lane photographer",
        "food photographer", "restaurant photographer", "culinary photographer",
        "人像布光", "婚礼摄影师", "活动摄影师", "夜店摄影师",
        "赛车摄影师", "汽车摄影师", "车展摄影师", "美食摄影师", "餐饮摄影师",
    ),
    "telephoto portrait lens": (
        "portrait photographer", "wedding photographer", "sports photographer",
        "motorsport photographer", "wildlife photographer", "stage photographer",
        "concert photographer", "live music photographer", "performance photographer",
        "theater photographer", "theatre photographer", "人像摄影师",
        "婚礼摄影师", "体育摄影师", "赛车摄影师", "野生动物摄影师",
        "舞台摄影师", "演唱会摄影师", "演出摄影师",
    ),
    "super-telephoto lens": (
        "wildlife photographer", "bird photographer", "sports photographer",
        "motorsport photographer", "野生动物摄影师", "鸟类摄影师", "体育摄影师", "赛车摄影师",
    ),
    "portrait lens": (
        "portrait photographer", "wedding photographer", "fashion photographer",
        "beauty photographer", "人像摄影师", "婚礼摄影师", "时尚摄影师",
    ),
    "macro lens": (
        "macro photographer", "product photographer", "jewelry photographer",
        "insect photographer", "微距摄影师", "产品摄影师", "珠宝摄影师",
    ),
    "macro cinema lens": (
        "macro filmmaker", "product filmmaker", "tabletop filmmaker",
        "微距视频", "产品视频", "桌面影像",
    ),
    "cinema lens": (
        "cinematographer", "filmmaker", "music video filmmaker", "documentary filmmaker",
        "电影摄影师", "视频创作者", "音乐视频导演", "纪录片导演",
    ),
    "ultra-wide lens": (
        "architecture photographer", "real estate photographer", "interior photographer",
        "landscape photographer", "建筑摄影师", "房地产摄影师", "风光摄影师",
    ),
    "wide-angle lens": (
        "street photographer", "travel photographer", "documentary filmmaker",
        "real estate photographer", "街头摄影师", "旅行摄影师", "纪录片导演",
    ),
    "camera monitor": _CAMERA_MONITOR_ROLE_TERMS,
    "camera lens": _CAMERA_VISUAL_ROLE_TERMS,
    # ``creator gear`` is the deterministic fallback for catalog items that do
    # not yet resolve to a narrower capability.  It is intentionally bounded
    # to the same public visual-production roles as a generic camera lens.
    "creator gear": _CAMERA_VISUAL_ROLE_TERMS,
}


def _controlled_registry(kind: str) -> dict[str, tuple[str, ...]]:
    if kind == "product":
        return _CONTROLLED_PRODUCT_ALIASES
    if kind == "scene":
        return _CONTROLLED_SCENE_ALIASES
    return {}


def controlled_aliases_for(kind: Any, canonical_term: Any) -> tuple[str, ...]:
    """Return aliases only when ``canonical_term`` names a static server group."""

    normalized = _normal_term(canonical_term)
    for canonical, aliases in _controlled_registry(_text(kind).lower()).items():
        if normalized == _normal_term(canonical):
            return aliases
    return ()


def _canonical_for_value(kind: str, value: Any) -> str:
    normalized = _normal_term(value)
    if not normalized:
        return ""
    for canonical, aliases in _controlled_registry(kind).items():
        if normalized == _normal_term(canonical) or normalized in {_normal_term(alias) for alias in aliases}:
            return canonical
    return _text(value)[:120]


def controlled_capability_use_terms_for(canonical_term: Any) -> tuple[str, ...]:
    canonical = _canonical_for_value("product", canonical_term)
    return _CONTROLLED_CAPABILITY_USE_MAP.get(canonical, ())


def canonical_controlled_term(kind: Any, value: Any) -> str:
    """Return a static canonical term, or an empty string for unknown aliases."""

    normalized_kind = _text(kind).lower()
    normalized = _normal_term(value)
    if not normalized:
        return ""
    for canonical, aliases in _controlled_registry(normalized_kind).items():
        if normalized == _normal_term(canonical) or normalized in {_normal_term(alias) for alias in aliases}:
            return canonical
    return ""


def _locked_group(kind: str, value: Any) -> dict[str, Any] | None:
    canonical = _canonical_for_value(kind, value)
    if not canonical:
        return None
    aliases = controlled_aliases_for(kind, canonical)
    group = {
        "kind": kind,
        "evidence_group": "product_use_fit" if kind == "product" else "segment_use_case",
        "canonical_term": canonical,
        "aliases": list(aliases or (canonical,)),
        "alias_policy": "static_allowlist" if aliases else "exact_only",
    }
    if kind == "product":
        suitability_terms = controlled_capability_use_terms_for(canonical)
        if suitability_terms:
            group.update({
                "use_suitability_terms": list(suitability_terms),
                "suitability_policy": "static_capability_use_map",
            })
    return group


def build_locked_term_groups(*, capability: Any, segment: Any, segment_label: Any = "") -> dict[str, Any]:
    """Create the server-owned product/scene synonym contract for one QueryCell."""

    scene_value = canonical_controlled_term("scene", segment) or segment_label or segment
    groups = [
        group
        for group in (
            _locked_group("product", capability),
            _locked_group("scene", scene_value),
        )
        if group
    ]
    return {
        "schema": LOCKED_TERM_GROUPS_SCHEMA,
        "version": LOCKED_TERM_GROUPS_VERSION,
        "source": LOCKED_TERM_GROUPS_SOURCE,
        "groups": groups,
    }


def project_locked_term_groups(value: Any) -> dict[str, Any] | None:
    """Validate and rebuild a locked-term projection from the static registry."""

    if not isinstance(value, dict):
        return None
    if (
        value.get("schema") != LOCKED_TERM_GROUPS_SCHEMA
        or value.get("version") != LOCKED_TERM_GROUPS_VERSION
        or value.get("source") != LOCKED_TERM_GROUPS_SOURCE
    ):
        return None
    raw_groups = value.get("groups")
    if not isinstance(raw_groups, list):
        return None
    groups: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_groups[:4]:
        if not isinstance(raw, dict):
            continue
        kind = _text(raw.get("kind")).lower()
        canonical = _text(raw.get("canonical_term"))[:120]
        if kind not in {"product", "scene"} or not canonical or kind in seen:
            continue
        static_aliases = controlled_aliases_for(kind, canonical)
        requested_policy = _text(raw.get("alias_policy"))
        if static_aliases:
            group = _locked_group(kind, canonical)
        elif requested_policy == "exact_only":
            group = _locked_group(kind, canonical)
            if group:
                group["aliases"] = [canonical]
                group["alias_policy"] = "exact_only"
        else:
            group = None
        if group:
            seen.add(kind)
            groups.append(group)
    if not groups:
        return None
    return {
        "schema": LOCKED_TERM_GROUPS_SCHEMA,
        "version": LOCKED_TERM_GROUPS_VERSION,
        "source": LOCKED_TERM_GROUPS_SOURCE,
        "groups": groups,
    }


def rebuild_locked_term_groups_for_cell(cell: Any) -> dict[str, Any] | None:
    """Return a validated spec, or rebuild a legacy cell from static fields."""

    payload = cell if isinstance(cell, dict) else {}
    projected = project_locked_term_groups(payload.get("locked_term_groups"))
    if projected:
        return projected
    query = _normal_term(payload.get("primary_query") or payload.get("query_cell_query"))
    capability = ""
    for canonical, aliases in _CONTROLLED_PRODUCT_ALIASES.items():
        if any(
            f" {_normal_term(alias)} " in f" {query} "
            for alias in aliases
            if _normal_term(alias)
        ):
            capability = canonical
            break
    segment_value = payload.get("segment") or payload.get("query_cell_segment") or payload.get("segment_label")
    segment = canonical_controlled_term("scene", segment_value)
    if not capability and not segment:
        return None
    return build_locked_term_groups(
        capability=capability,
        segment=segment or segment_value,
        segment_label=payload.get("segment_label"),
    )


__all__ = [
    "LOCKED_TERM_GROUPS_SCHEMA",
    "LOCKED_TERM_GROUPS_VERSION",
    "LOCKED_TERM_GROUPS_SOURCE",
    "controlled_aliases_for",
    "controlled_capability_use_terms_for",
    "canonical_controlled_term",
    "build_locked_term_groups",
    "project_locked_term_groups",
    "rebuild_locked_term_groups_for_cell",
]
