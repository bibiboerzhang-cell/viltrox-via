"""Pure operator-segment extraction for targeted KOL search."""
from __future__ import annotations

import re
from typing import Any, Iterable

from app.domains.kol.search_intent_text import affirmative_search_text
from app.domains.kol.targeted_search_persona import (
    compound_creator_term,
    contextual_creator_term,
    exact_operator_intent_records,
    explicit_creator_roles,
    has_creator_role,
)


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _dedupe(values: Iterable[Any]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = _text(value)
        key = item.casefold()
        if item and key not in seen:
            seen.add(key)
            output.append(item)
    return output


# Canonical segment -> operator phrases -> first-round creator query phrase.
_SEGMENT_RULES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "motorsport",
        ("赛车", "赛道", "汽车", "汽车摄影", "汽车广告", "车展", "机车", "摩托", "motorsport", "racing", "automotive", "car photography"),
        "motorsport photographer",
    ),
    ("food", ("厨师", "餐饮", "餐厅", "美食", "烹饪", "chef", "chefs", "culinary", "restaurant", "food"), "food photographer"),
    ("wedding", ("婚礼", "wedding", "weddings"), "wedding photographer"),
    (
        "event",
        (
            "活动", "发布会", "会议摄影", "红毯", "event photographer", "event photographers",
            "event photography", "event videographer", "event videographers", "conference photographer",
        ),
        "event photographer",
    ),
    (
        "stage",
        ("舞台", "演唱会", "演出摄影", "剧场", "stage photography", "concert photographer", "live music photographer", "performance photographer", "theater photographer", "theatre photographer"),
        "stage performance photographer",
    ),
    (
        "wildlife",
        (
            "野生动物", "鸟类摄影", "拍鸟", "鸟摄影", "wildlife", "birding",
            "bird photographer", "bird photographers", "bird photography",
        ),
        "wildlife photographer",
    ),
    ("portrait", ("人像", "portrait", "portraits", "portrait photographer", "portrait photographers", "portrait photography"), "portrait photographer"),
    ("street", ("街拍", "街头", "街头摄影", "街头纪实", "street", "street photographer", "street photographers", "street photography"), "street photographer"),
    ("landscape", ("风光", "风景摄影", "landscape photographer", "landscape photography"), "landscape photographer"),
    (
        "fashion",
        (
            "时尚", "时尚摄影", "造型师", "时尚造型师",
            "fashion", "fashion photographer", "fashion photography", "fashion stylist", "fashion stylists",
        ),
        "fashion photographer",
    ),
    ("night", ("城市夜景", "夜景", "夜景摄影", "night", "night photographer", "night photographers", "night photography"), "night photographer"),
    ("pet", ("宠物", "pet", "dog", "animal"), "pet photographer"),
    ("travel", ("旅拍", "旅行", "travel"), "travel photographer"),
    ("fitness", ("健身", "fitness"), "fitness creator"),
    (
        "sports",
        ("体育", "赛事", "球赛", "运动摄影", "篮球", "足球", "网球", "sports", "basketball", "football", "soccer"),
        "sports photographer",
    ),
    ("real_estate", ("房地产", "房产", "real estate", "real-estate", "interior", "interior photographer", "interior photographers"), "real estate photographer"),
    (
        "commercial",
        (
            "商业广告", "广告", "commercial", "advertising", "commercial director",
            "commercial directors", "commercial videographer", "commercial videographers",
        ),
        "commercial photographer",
    ),
    (
        "product_launch",
        ("product launch campaign", "product launch", "launch campaign"),
        "product launch photographer",
    ),
    (
        "product_photography",
        ("产品摄影", "静物摄影", "product photography", "product photographer", "product photographers"),
        "product photographer",
    ),
    (
        "jewelry_macro",
        ("珠宝微距", "珠宝摄影", "jewelry macro", "jewellery macro", "jewelry photography", "jewellery photography"),
        "jewelry macro photographer",
    ),
    ("macro", ("微距摄影", "macro photography", "macro photographer", "macro photographers"), "macro photographer"),
    (
        "lighting",
        ("离机布光", "离机闪光", "off-camera flash", "off camera flash", "off-camera lighting", "off camera lighting"),
        "off-camera flash photographer",
    ),
    (
        "review",
        (
            "评测", "测评", "器材评测", "gear reviewer", "product reviewer",
            "reviewer", "reviewers", "review", "reviews", "reviewing",
            "review channel", "review channels",
        ),
        "camera gear reviewer",
    ),
    ("music_video", ("音乐视频", "mv", "music video"), "music video filmmaker"),
    ("documentary", ("纪录片", "documentary"), "documentary filmmaker"),
    (
        "cinematography",
        (
            "电影摄影师", "摄影指导", "摄影机操作员", "相机操作员", "cinematographer",
            "cinematographers", "director of photography", "directors of photography", "camera operator", "camera operators", "dp",
        ),
        "cinematographer",
    ),
    ("film_direction", ("电影导演", "film director", "film directors", "movie director", "movie directors"), "film director"),
    ("filmmaking_role", ("导演", "filmmaker", "filmmakers"), "filmmaker"),
    ("photography_role", ("摄影师", "photographer", "photographers"), "professional photographer"),
    ("videography_role", ("摄像师", "videographer", "videographers"), "professional videographer"),
    ("film_production", ("独立电影", "independent film", "independent filmmaker"), "independent filmmaker"),
    (
        "video_creator",
        ("视频创作者", "短视频创作者", "video creator", "video creators"),
        "video content creator",
    ),
    (
        "film_photography",
        (
            "胶片", "底片", "35mm film", "film photographer", "film photographers",
            "film photography", "analog photographer", "analog photography",
            "analogue photographer", "analogue photography",
        ),
        "film photographer",
    ),
)

_GENERIC_ROLE_SEGMENTS = frozenset({
    "video_creator", "cinematography", "film_direction", "filmmaking_role",
    "photography_role", "videography_role",
})

_COMPOUND_SCENE_SETS = frozenset({
    frozenset({"street", "night"}),
    frozenset({"motorsport", "commercial"}),
    frozenset({"product_photography", "jewelry_macro"}),
    frozenset({"product_photography", "macro"}),
    frozenset({"jewelry_macro", "macro"}),
    frozenset({"wedding", "portrait"}),
    frozenset({"wedding", "event"}),
    frozenset({"lighting", "portrait"}),
    frozenset({"lighting", "wedding"}),
    frozenset({"lighting", "food"}),
    frozenset({"lighting", "product_photography"}),
})


def _alias_in_text(text: str, alias: str) -> bool:
    if any("一" <= char <= "鿿" for char in alias):
        return alias in text
    return re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text) is not None


def _segment_clauses(text: str) -> list[str]:
    return [
        clause.strip()
        for clause in re.split(
            r"[,，、;/|]+|(?<![a-z0-9])(?:and|or)(?![a-z0-9])|或者|还是|[或和与及]",
            _text(text).lower(),
        )
        if clause.strip()
    ]


def _connector_semantics(text: str) -> tuple[bool, bool]:
    value = _text(text).lower()
    explicit_any = bool(re.search(
        r"(?:或者|还是|或)|(?<![a-z0-9])or(?![a-z0-9])",
        value,
        flags=re.IGNORECASE,
    ))
    explicit_all = bool(re.search(
        r"(?:同时|还要|又会|也会|兼具|兼做|都做|都拍|既.+又)|"
        r"(?<![a-z0-9])(?:both|simultaneously)(?![a-z0-9])|"
        r"(?<![a-z0-9])and\s+(?:who\s+)?(?:also\s+)?"
        r"(?:shoots?|shooting|covers?|covering|films?|filming|makes?|making|creates?|creating)(?![a-z0-9])|"
        r"(?<![a-z0-9])who\s+(?:also\s+)?"
        r"(?:shoots?|shooting|covers?|covering|films?|filming|makes?|making|creates?|creating)(?![a-z0-9])",
        value,
        flags=re.IGNORECASE,
    ))
    return explicit_any, explicit_all


def _matched_domain_keys(value: str) -> list[str]:
    return _dedupe(
        key
        for key, aliases, _query_term in _SEGMENT_RULES
        if key not in _GENERIC_ROLE_SEGMENTS
        and any(_alias_in_text(value, alias) for alias in aliases)
    )


def _matched_specialist_role_keys(value: str) -> list[str]:
    return _dedupe(
        key
        for key, aliases, _query_term in _SEGMENT_RULES
        if key in {"cinematography", "film_direction", "filmmaking_role"}
        and any(_alias_in_text(value, alias) for alias in aliases)
    )


def _compound_scenes_allowed(keys: Iterable[Any]) -> bool:
    unique = frozenset(_text(key) for key in keys if _text(key))
    return len(unique) > 1 and unique in _COMPOUND_SCENE_SETS


def _more_specific_role_exists(key: str, clause: str) -> bool:
    precedence = {
        "filmmaking_role": {"film_direction", "cinematography"},
        "photography_role": {"cinematography"},
    }
    preferred = precedence.get(key, set())
    return any(
        candidate_key in preferred
        and any(_alias_in_text(clause, alias) for alias in aliases)
        for candidate_key, aliases, _query_term in _SEGMENT_RULES
    )


def _clause_has_domain_segment(clause: str) -> bool:
    return any(
        key not in _GENERIC_ROLE_SEGMENTS
        and any(_alias_in_text(clause, alias) for alias in aliases)
        for key, aliases, _query_term in _SEGMENT_RULES
    )


def _list_values(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return _dedupe(value)
    if not _text(value):
        return []
    return _dedupe(re.split(r"[,，、;/|]+|\s+(?:and|or)\s+", _text(value), flags=re.IGNORECASE))


def _segment_record(value: str, *, source: str, locked: bool) -> dict[str, Any]:
    lowered = _text(value).lower()
    for key, aliases, query_term in _SEGMENT_RULES:
        if any(_alias_in_text(lowered, alias) for alias in aliases):
            return {
                "key": key,
                "label": _text(value),
                "query_term": contextual_creator_term(query_term, lowered),
                "source": source,
                "locked": locked,
            }
    slug = re.sub(r"[^a-z0-9]+", "_", lowered).strip("_") or "custom"
    return {
        "key": slug,
        "label": _text(value),
        "query_term": _text(value),
        "source": source,
        "locked": locked,
    }


def _explicit_filter_values(payload: dict[str, Any]) -> list[str]:
    nested = payload.get("filters") if isinstance(payload.get("filters"), dict) else {}
    output: list[str] = []
    for source in (payload, nested):
        for key in ("segments", "industries", "industry", "use_cases", "useCases"):
            output.extend(_list_values(source.get(key)))
    return output


def _has_trailing_shared_role(clauses: list[str]) -> bool:
    return bool(
        len(clauses) > 1
        and has_creator_role(clauses[-1])
        and not _matched_domain_keys(clauses[-1])
        and not _matched_specialist_role_keys(clauses[-1])
        and any(_matched_domain_keys(clause) for clause in clauses[:-1])
    )


def _has_implicit_compound(clauses: list[str], domain_keys: list[str]) -> bool:
    return bool(
        len(clauses) == 2
        and not has_creator_role(clauses[0])
        and has_creator_role(clauses[1])
        and _compound_scenes_allowed(domain_keys)
    )


def _merged_clauses(raw: str) -> tuple[list[str], bool]:
    clauses = _segment_clauses(raw) or [raw]
    explicit_any, explicit_all = _connector_semantics(raw)
    domain_keys = _matched_domain_keys(" ".join(clauses))
    compound = _has_trailing_shared_role(clauses) or _has_implicit_compound(clauses, domain_keys)
    multi_role = explicit_all and len(explicit_creator_roles(raw)) > 1
    should_merge = not explicit_any and (
        (bool(domain_keys) and (explicit_all or compound)) or multi_role
    )
    return ([_text(" ".join(clauses))] if should_merge else clauses), explicit_all


def _matched_rule_record(
    key: str,
    matched: str,
    query_term: str,
    clause: str,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "key": key,
        "label": matched,
        "query_term": contextual_creator_term(query_term, clause),
        "source": "operator_text",
        "locked": True,
    }
    if key in _GENERIC_ROLE_SEGMENTS:
        record["role_only"] = True
        record["required_scene_terms"] = []
        stated_roles = explicit_creator_roles(clause)
        if stated_roles:
            record["required_role_terms"] = stated_roles
    return record


def _rule_records(clause: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for key, aliases, query_term in _SEGMENT_RULES:
        matched = next((alias for alias in aliases if _alias_in_text(clause, alias)), "")
        if not matched or (key == "documentary" and re.search(r"documentary[- ]style", clause)):
            continue
        if key in _GENERIC_ROLE_SEGMENTS and _clause_has_domain_segment(clause):
            continue
        if key in _GENERIC_ROLE_SEGMENTS and _more_specific_role_exists(key, clause):
            continue
        records.append(_matched_rule_record(key, matched, query_term, clause))
    return records


def _covered_aliases(clause: str) -> Iterable[str]:
    return (
        alias
        for _key, aliases, _query_term in _SEGMENT_RULES
        for alias in aliases
        if _alias_in_text(clause, alias)
    )


def _merge_exact_records(
    records: list[dict[str, Any]],
    exact_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not exact_records:
        return records
    non_generic = [record for record in records if record["key"] not in _GENERIC_ROLE_SEGMENTS]
    return [*exact_records, *non_generic]


def _role_terms(records: Iterable[dict[str, Any]]) -> list[str]:
    return _dedupe(
        term
        for record in records
        for term in (record.get("required_role_terms") or [])
        if record.get("role_only") is True
    )


def _domain_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        record for record in records
        if record["key"] not in _GENERIC_ROLE_SEGMENTS
        and record.get("role_only") is not True
    ]


def _should_compound_records(
    domain_records: list[dict[str, Any]],
    role_terms: list[str],
    component_keys: list[str],
    clause: str,
    explicit_all: bool,
    exact_records: list[dict[str, Any]],
) -> bool:
    return bool(
        domain_records
        and (
            bool(role_terms)
            or (
                len(component_keys) > 1
                and has_creator_role(clause)
                and (explicit_all or _compound_scenes_allowed(component_keys) or bool(exact_records))
            )
        )
    )


def _compound_record(
    records: list[dict[str, Any]],
    domain_records: list[dict[str, Any]],
    role_terms: list[str],
    clause: str,
) -> dict[str, Any]:
    component_keys = _dedupe(record["key"] for record in domain_records)
    scene_terms = _dedupe(
        (record.get("required_scene_terms") or [record["key"]])[0]
        for record in domain_records
    )
    return {
        "key": component_keys[0],
        "label": clause,
        "query_term": compound_creator_term(records, clause),
        "component_segments": scene_terms,
        "required_role_terms": role_terms,
        "segment_match_mode": "all",
        "source": "operator_text",
        "locked": True,
    }


def _clause_records(
    clause: str,
    *,
    explicit_filters: bool,
    explicit_all: bool,
) -> list[dict[str, Any]]:
    records = _rule_records(clause)
    exact_records = exact_operator_intent_records(clause, covered_terms=_covered_aliases(clause))
    records = _merge_exact_records(records, exact_records)
    if explicit_filters:
        records = [record for record in records if record.get("role_only") is not True]
    domains = _domain_records(records)
    roles = _role_terms(records)
    keys = _dedupe(record["key"] for record in domains)
    if _should_compound_records(domains, roles, keys, clause, explicit_all, exact_records):
        return [_compound_record(records, domains, roles, clause)]
    return records


def _deduped_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for record in records:
        identity = (record["key"], _text(record.get("query_term")).casefold())
        if identity not in seen:
            seen.add(identity)
            output.append(record)
    return output


def extract_explicit_segments(query: Any = "", body: Any = None) -> list[dict[str, Any]]:
    """Extract operator-owned industries/use-cases and keep each one independent."""

    payload = body if isinstance(body, dict) else {}
    explicit_values = _explicit_filter_values(payload)
    records = [
        _segment_record(value, source="operator_filter", locked=True)
        for value in explicit_values
    ]
    raw = affirmative_search_text(query).lower()
    clauses, explicit_all = _merged_clauses(raw)
    for clause in clauses:
        records.extend(_clause_records(
            clause,
            explicit_filters=bool(explicit_values),
            explicit_all=explicit_all,
        ))
    return _deduped_records(records)


__all__ = ["extract_explicit_segments"]
