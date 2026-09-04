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
    "300w studio lighting": (
        "300w studio lighting", "300 w studio lighting", "300w light",
        "300 w light", "300w lighting", "300 w lighting", "300w cob light",
    ),
    "studio lighting": (
        "studio lighting", "studio light", "video light", "continuous light",
        "portable lighting", "cob light", "摄影灯", "影视灯", "影棚灯", "补光灯",
    ),
    "teleconverter": ("teleconverter", "teleconverter lens", "teleplus", "增距镜", "增倍镜"),
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


# A requested person is a separate hard-evidence dimension from the scene they
# cover.  Keeping this registry independent prevents a wedding planner, sports
# fan, or food critic from passing merely because their profile mentions the
# requested subject.  These aliases describe public occupations only; they do
# not imply product ownership or commercial performance.
_CONTROLLED_ROLE_ALIASES: dict[str, tuple[str, ...]] = {
    "photographer": (
        "professional photographer", "working photographer", "photographer",
        "photographers", "摄影师", "专业摄影师",
    ),
    "videographer": (
        "professional videographer", "working videographer", "videographer",
        "videographers", "摄像师", "视频制作人",
    ),
    "filmmaker": (
        "professional filmmaker", "solo filmmaker", "independent filmmaker",
        "filmmaker", "filmmakers", "电影制作人", "独立电影制作人",
    ),
    "cinematographer": (
        "cinematographer", "cinematographers", "director of photography",
        "摄影指导", "电影摄影师",
    ),
    "camera operator": (
        "camera operator", "camera operators", "摄影机操作员", "相机操作员",
    ),
    "director": (
        "film director", "film directors", "movie director", "movie directors",
        "commercial director", "commercial directors", "director", "directors",
        "导演", "电影导演",
    ),
    "content creator": (
        "content creator", "content creators", "video creator", "video creators",
        "video content creator", "short video creator", "creator", "creators",
        "influencer", "influencers", "blogger", "bloggers", "内容创作者", "视频创作者",
        "短视频创作者", "创作者", "视频博主", "博主", "达人",
    ),
    "reviewer": (
        "reviewer", "reviewers", "gear reviewer", "gear reviewers",
        "product reviewer", "product reviewers", "评测博主", "测评博主", "评测人",
    ),
    "educator": (
        "educator", "educators", "photography educator", "video educator",
        "摄影教育者", "摄影讲师", "视频讲师",
    ),
    "storyteller": (
        "storyteller", "storytellers", "visual storyteller", "visual storytellers",
        "sports storyteller", "sports storytellers", "视觉叙事者", "体育故事创作者",
    ),
    "reporter": (
        "reporter", "reporters", "sideline reporter", "sideline reporters",
        "sports reporter", "sports reporters", "journalist", "journalists",
        "记者", "场边记者", "体育记者",
    ),
    "retoucher": (
        "retoucher", "retouchers", "photo retoucher", "photo retouchers",
        "image retoucher", "image retouchers", "修图师", "摄影后期师",
    ),
    "stylist": (
        "stylist", "stylists", "fashion stylist", "fashion stylists",
        "造型师", "时尚造型师",
    ),
    "chef": ("chef", "chefs", "厨师", "主厨"),
}


# Scene aliases are intentionally more specific for ambiguous categories such
# as events/stage: a bare generic word does not become hard evidence.
_CONTROLLED_SCENE_ALIASES: dict[str, tuple[str, ...]] = {
    "motorsport": (
        "motorsport", "motor sport", "motorsport photographer", "racing", "race photography",
        "race photographer", "automotive", "automotive photographer", "car photography",
        "car photographer", "car show photographer", "pit lane photographer", "race paddock photographer",
        "赛车", "赛车摄影", "汽车摄影", "车展摄影", "赛道", "赛道摄影", "机车", "摩托",
    ),
    "food": ("food", "chef", "culinary", "restaurant", "cooking", "餐饮", "美食", "厨师", "烹饪"),
    "wedding": ("wedding", "bridal", "婚礼"),
    "event": (
        "event photographer", "event photography", "corporate event photographer",
        "conference photographer", "red carpet photographer", "活动摄影", "活动摄影师",
        "会议摄影", "发布会摄影", "红毯摄影", "event videographer", "event videographers",
    ),
    "stage": (
        "stage photographer", "stage photography", "concert photographer", "concert photography",
        "live music photographer", "performance photographer", "theater photographer", "theatre photographer",
        "舞台摄影", "舞台摄影师", "演唱会摄影", "演出摄影", "剧场摄影",
    ),
    "wildlife": (
        "wildlife", "wildlife photographer", "wildlife photography", "birding", "bird photographer", "bird photography",
        "野生动物", "野生动物摄影", "野生动物摄影师", "鸟类摄影", "鸟类摄影师",
    ),
    "portrait": (
        "portrait", "portrait photographer", "portrait photography", "environmental portrait photographer",
        "人像", "人像摄影", "人像摄影师", "环境人像摄影",
    ),
    "street": (
        "street", "street photographer", "street photographers", "street photography",
        "street documentary", "街拍", "街头摄影", "街头纪实",
    ),
    "landscape": ("landscape", "landscape photographer", "landscape photography", "风光", "风景摄影"),
    "fashion": (
        "fashion", "fashion photographer", "fashion photography", "fashion stylist",
        "fashion styling", "时尚", "时尚摄影", "时尚造型",
    ),
    "night": (
        "night photographer", "night photographers", "night photography",
        "low-light photography", "城市夜景", "夜景摄影",
    ),
    "pet": ("pet", "pets", "dog", "dogs", "animal", "宠物"),
    "travel": ("travel", "destination", "旅行", "旅拍"),
    "fitness": ("fitness", "gym", "健身"),
    "sports": ("sports", "sport", "basketball", "football", "soccer", "体育", "赛事", "球赛", "运动摄影", "篮球", "足球"),
    "real_estate": ("real estate", "real-estate", "property", "interior", "房产", "房地产", "室内"),
    "commercial": ("commercial", "advertising", "campaign", "商业广告", "广告"),
    "product_launch": ("product launch", "product launch campaign", "launch campaign"),
    "product_photography": ("product photography", "product photographer", "产品摄影", "静物摄影"),
    "jewelry_macro": ("jewelry macro", "jewellery macro", "jewelry photography", "珠宝微距", "珠宝摄影"),
    "macro": ("macro photography", "macro photographer", "微距摄影"),
    "lighting": ("off-camera flash", "off camera flash", "off-camera lighting", "离机布光", "离机闪光"),
    "review": (
        "review", "reviewing", "reviewer", "reviewers", "gear review", "gear reviewer",
        "product review", "product reviewer", "评测", "测评", "器材评测",
    ),
    "music_video": ("music video", "mv", "音乐视频"),
    "documentary": ("documentary", "纪录片"),
    "video_creator": (
        "video creator", "video creators", "video content creator", "short video creator",
        "视频创作者", "短视频创作者",
    ),
    "cinematography": (
        "cinematographer", "cinematographers", "director of photography", "camera operator",
        "电影摄影师", "摄影指导", "摄影机操作员", "相机操作员",
    ),
    "film_direction": ("film director", "film directors", "movie director", "电影导演"),
    "filmmaking_role": ("filmmaker", "filmmakers", "filmmaking", "导演"),
    "photography_role": (
        "professional photographer", "working photographer", "photographer", "photographers", "摄影师",
    ),
    "videography_role": (
        "professional videographer", "working videographer", "videographer", "videographers", "摄像师",
    ),
    "film_production": (
        "independent film", "independent filmmaker", "independent filmmakers", "独立电影",
    ),
    "film_photography": (
        "film photographer", "film photographers", "film photography", "analog photography",
        "analogue photography", "胶片摄影", "胶片摄影师", "胶片", "底片",
    ),
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
    "film director", "movie director", "camera monitor reviewer", "video gear reviewer",
    "wedding videographer", "event videographer", "commercial videographer",
    "product filmmaker", "documentary filmmaker", "电影摄影师", "摄影指导",
    "摄影机操作员", "电影导演", "独立电影制作人", "婚礼摄像师", "活动摄像师",
    "商业摄像师", "产品视频制作人", "纪录片制作人",
)

_CONTROLLED_CAPABILITY_USE_MAP: dict[str, tuple[str, ...]] = {
    "on-camera flash": (
        "flash reviewer", "lighting reviewer", "flash photographer", "strobe photographer",
        "portrait lighting", "portrait photographer", "wedding photographer",
        "event photographer", "nightlife photographer", "club photographer",
        "red carpet photographer", "motorsport photographer", "automotive photographer",
        "car photographer", "car show photographer", "pit lane photographer",
        "food photographer", "restaurant photographer", "culinary photographer",
        "闪光摄影师", "人像布光", "婚礼摄影师", "活动摄影师", "夜店摄影师",
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
        "cinematographer", "filmmaker", "film director", "movie director",
        "music video filmmaker", "documentary filmmaker", "电影摄影师", "电影导演",
        "视频创作者", "音乐视频导演", "纪录片导演",
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
    "studio lighting": (
        "studio lighting reviewer", "lighting reviewer", "lighting educator",
        "portrait photographer", "wedding photographer", "fashion photographer",
        "food photographer", "product photographer", "commercial photographer",
        "cinematographer", "commercial videographer", "product filmmaker",
        "灯光评测", "灯光教学", "人像摄影师", "婚礼摄影师", "时尚摄影师",
        "美食摄影师", "产品摄影师", "商业摄影师", "电影摄影师",
    ),
    "300w studio lighting": (
        "studio lighting reviewer", "lighting reviewer", "lighting educator",
        "portrait photographer", "wedding photographer", "fashion photographer",
        "food photographer", "product photographer", "commercial photographer",
        "cinematographer", "commercial videographer", "product filmmaker",
        "灯光评测", "灯光教学", "人像摄影师", "婚礼摄影师", "时尚摄影师",
        "美食摄影师", "产品摄影师", "商业摄影师", "电影摄影师",
    ),
    "teleconverter": (
        "wildlife photographer", "bird photographer", "sports photographer",
        "motorsport photographer", "野生动物摄影师", "鸟类摄影师", "体育摄影师", "赛车摄影师",
    ),
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
    if kind == "role":
        return _CONTROLLED_ROLE_ALIASES
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
        "evidence_group": {
            "product": "product_use_fit",
            "scene": "segment_use_case",
            "role": "people_role",
        }.get(kind, ""),
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


def required_role_terms_for(value: Any) -> list[str]:
    """Resolve the requested occupation from one server-built people query.

    The order keeps specific roles ahead of broad creator labels.  We inspect
    the server's local QueryCell phrase, never client-provided synonym arrays.
    """

    text = _normal_term(value)
    if not text:
        return []
    priority = (
        "reviewer", "camera operator", "cinematographer", "director",
        "reporter", "retoucher", "stylist", "storyteller", "educator",
        "chef", "videographer", "filmmaker", "photographer", "content creator",
    )
    padded = f" {text} "
    for canonical in priority:
        aliases = _CONTROLLED_ROLE_ALIASES.get(canonical, ())
        if any(
            (
                _normal_term(alias) in text
                if any("\u4e00" <= char <= "\u9fff" for char in alias)
                else f" {_normal_term(alias)} " in padded
            )
            for alias in aliases
            if _normal_term(alias)
        ):
            return [canonical]
    return []


def build_locked_term_groups(
    *,
    capability: Any,
    segment: Any,
    segment_label: Any = "",
    scene_terms: Any = (),
    role_terms: Any = (),
) -> dict[str, Any]:
    """Create the server-owned product/scene/person contract for one QueryCell."""

    requested_scenes = (
        list(scene_terms)
        if isinstance(scene_terms, (list, tuple, set)) and scene_terms
        else [segment]
    )
    scene_values = [
        canonical_controlled_term("scene", value)
        or (segment_label if len(requested_scenes) == 1 else value)
        or value
        for value in requested_scenes
    ]
    groups = [
        group
        for group in [
            _locked_group("product", capability),
            *(
                _locked_group("role", value)
                for value in (
                    list(role_terms)
                    if isinstance(role_terms, (list, tuple, set))
                    else [role_terms]
                )
            ),
            *(_locked_group("scene", value) for value in scene_values),
        ]
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
    seen: set[tuple[str, str]] = set()
    for raw in raw_groups[:8]:
        if not isinstance(raw, dict):
            continue
        kind = _text(raw.get("kind")).lower()
        canonical = _text(raw.get("canonical_term"))[:120]
        identity = (kind, _normal_term(canonical))
        if kind not in {"product", "scene", "role"} or not canonical or identity in seen:
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
            seen.add(identity)
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
        scene_terms=payload.get("required_scene_terms") or (),
        role_terms=payload.get("required_role_terms") or (),
    )


__all__ = [
    "LOCKED_TERM_GROUPS_SCHEMA",
    "LOCKED_TERM_GROUPS_VERSION",
    "LOCKED_TERM_GROUPS_SOURCE",
    "controlled_aliases_for",
    "controlled_capability_use_terms_for",
    "canonical_controlled_term",
    "required_role_terms_for",
    "build_locked_term_groups",
    "project_locked_term_groups",
    "rebuild_locked_term_groups_for_cell",
]
