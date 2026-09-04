"""Pure contract helpers for targeted, first-round KOL discovery.

The default objective is prospective growth: find creators who are likely to
use the product and can activate its target market.  Brand/model mentions are
therefore neither an eligibility requirement nor a ranking signal.  The
legacy ``existing_evidence`` objective remains available for workflows that
explicitly need creators already talking about Viltrox.

This module is deliberately IO-free.  It produces auditable ``QueryCell``
dictionaries for the execution pipeline and parses operator-owned follower
ranges without silently relaxing them.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from app.domains.kol.targeted_search_cell import (
    build_query_cell as _cell,
    first_round_raw_limit as _first_round_raw_limit,
    prospective_primary_query as _prospective_primary_query,
)
from app.domains.kol.targeted_search_terms import (
    LOCKED_TERM_GROUPS_SCHEMA,
    LOCKED_TERM_GROUPS_SOURCE,
    LOCKED_TERM_GROUPS_VERSION,
    build_locked_term_groups,
    canonical_controlled_term,
    controlled_aliases_for,
    controlled_capability_use_terms_for,
    project_locked_term_groups,
    rebuild_locked_term_groups_for_cell,
)


DEFAULT_OBJECTIVE = "prospective_growth"
PROSPECTIVE_GROWTH = DEFAULT_OBJECTIVE
EXISTING_EVIDENCE = "existing_evidence"
SUPPORTED_OBJECTIVES = frozenset({PROSPECTIVE_GROWTH, EXISTING_EVIDENCE})
SUPPORTED_PLATFORMS = frozenset({"youtube", "instagram", "tiktok"})
SEARCH_SPEC_VERSION = "targeted_search_v2"


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


def normalize_objective(*values: Any) -> str:
    """Return a supported objective; unspecified/unknown values fail to the default."""

    aliases = {
        "prospective": PROSPECTIVE_GROWTH,
        "growth": PROSPECTIVE_GROWTH,
        "market_growth": PROSPECTIVE_GROWTH,
        "potential_users": PROSPECTIVE_GROWTH,
        "existing": EXISTING_EVIDENCE,
        "existing_user": EXISTING_EVIDENCE,
        "brand_evidence": EXISTING_EVIDENCE,
    }
    for value in values:
        if isinstance(value, dict):
            value = value.get("objective") or value.get("search_objective") or value.get("searchObjective")
        candidate = _text(value).lower()
        if candidate in SUPPORTED_OBJECTIVES:
            return candidate
        if candidate in aliases:
            return aliases[candidate]
    return DEFAULT_OBJECTIVE


_COUNT_TOKEN = r"(?P<{name}>\d+(?:\.\d+)?)\s*(?P<{name}_suffix>百万|万|千|[kwm])?"
_FOLLOWER_CONTEXT_RE = re.compile(r"粉丝|粉|关注者|followers?|audience", re.IGNORECASE)
_RANGE_SEP = r"(?:-|–|—|~|～|至|到|to)"


def _count_value(number: str, suffix: str = "", *, inherited_suffix: str = "") -> int | None:
    try:
        value = float(number)
    except (TypeError, ValueError):
        return None
    unit = (suffix or inherited_suffix or "").lower()
    multiplier = {
        "": 1,
        "k": 1_000,
        "千": 1_000,
        "w": 10_000,
        "万": 10_000,
        "m": 1_000_000,
        "百万": 1_000_000,
    }.get(unit)
    if multiplier is None:
        return None
    parsed = int(value * multiplier)
    return parsed if 0 <= parsed <= 100_000_000 else None


def _parse_count_token(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        parsed = int(value)
        return parsed if 0 <= parsed <= 100_000_000 else None
    raw = _text(value).lower().replace(",", "")
    match = re.fullmatch(_COUNT_TOKEN.format(name="value"), raw, flags=re.IGNORECASE)
    if not match:
        return None
    return _count_value(match.group("value"), match.group("value_suffix") or "")


def _filter_dict(body_or_filters: Any) -> dict[str, Any]:
    payload = body_or_filters if isinstance(body_or_filters, dict) else {}
    nested = payload.get("filters")
    if isinstance(nested, dict):
        return {**payload, **nested}
    return payload


def _filter_value(filters: dict[str, Any], keys: tuple[str, ...]) -> tuple[Any, bool]:
    for key in keys:
        if key in filters and filters.get(key) not in (None, ""):
            return filters.get(key), True
    return None, False


def _operator_platforms(body: Any, fallback: Iterable[Any]) -> list[str]:
    """Keep an explicit operator platform facet authoritative in the plan."""

    filters = _filter_dict(body)
    raw, explicit = _filter_value(
        filters,
        ("platforms", "new_discovery_platforms", "discovery_platforms", "platform"),
    )
    values = raw if isinstance(raw, (list, tuple, set)) else [raw]
    selected = _dedupe(
        _text(value).lower()
        for value in values
        if _text(value).lower() in SUPPORTED_PLATFORMS
    )
    return selected if explicit and selected else _dedupe(
        _text(value).lower() for value in fallback
    )


def parse_follower_range(query: Any = "", body_or_filters: Any = None) -> dict[str, Any]:
    """Parse an explicit follower interval from filters or Chinese/English text.

    Explicit UI/API values always win over text.  Unknown is kept as ``None``;
    an inverted interval is returned with ``valid=False`` so the caller can ask
    for correction instead of swapping the operator's numbers.
    """

    filters = _filter_dict(body_or_filters)
    raw_min, has_min = _filter_value(
        filters, ("followers_min", "follower_min", "followersMin", "minFollowers")
    )
    raw_max, has_max = _filter_value(
        filters, ("followers_max", "follower_max", "followersMax", "maxFollowers")
    )
    if has_min or has_max:
        low = _parse_count_token(raw_min) if has_min else None
        high = _parse_count_token(raw_max) if has_max else None
        valid = (not has_min or low is not None) and (not has_max or high is not None)
        if valid and low is not None and high is not None and low > high:
            valid = False
        return {
            "followers_min": low,
            "followers_max": high,
            "source": "operator_filter",
            "locked": True,
            "valid": valid,
            "error": "followers_min_exceeds_max" if low is not None and high is not None and low > high
            else ("invalid_follower_value" if not valid else ""),
            "matched_text": "",
        }

    raw = _text(query).lower().replace(",", "")
    if not raw:
        return {
            "followers_min": None,
            "followers_max": None,
            "source": "unspecified",
            "locked": False,
            "valid": True,
            "error": "",
            "matched_text": "",
        }

    first = _COUNT_TOKEN.format(name="first")
    second = _COUNT_TOKEN.format(name="second")
    patterns = (
        re.compile(rf"(?:between\s+)?{first}\s*(?:and|{_RANGE_SEP})\s*{second}", re.IGNORECASE),
        re.compile(rf"{first}\s*{_RANGE_SEP}\s*{second}", re.IGNORECASE),
    )
    for pattern in patterns:
        match = pattern.search(raw)
        if not match:
            continue
        first_suffix = match.group("first_suffix") or ""
        second_suffix = match.group("second_suffix") or ""
        # Avoid interpreting years/ranks as followers unless the phrase says
        # followers or at least one side uses a follower magnitude suffix.
        if not _FOLLOWER_CONTEXT_RE.search(raw) and not (first_suffix or second_suffix):
            continue
        low = _count_value(match.group("first"), first_suffix, inherited_suffix=second_suffix)
        high = _count_value(match.group("second"), second_suffix, inherited_suffix=first_suffix)
        valid = low is not None and high is not None and low <= high
        return {
            "followers_min": low,
            "followers_max": high,
            "source": "operator_text",
            "locked": True,
            "valid": valid,
            "error": "followers_min_exceeds_max" if low is not None and high is not None and low > high
            else ("invalid_follower_value" if not valid else ""),
            "matched_text": match.group(0),
        }

    token = _COUNT_TOKEN.format(name="value")
    lower_patterns = (
        re.compile(
            rf"(?:至少|不低于|大于|超过|more\s+than|over|at\s+least|min(?:imum)?\s*)\s*{token}",
            re.IGNORECASE,
        ),
        re.compile(rf"{token}\s*(?:以上|起|及以上|\+)", re.IGNORECASE),
    )
    upper_patterns = (
        re.compile(rf"(?:不超过|低于|小于|少于|under|below|up\s+to|max(?:imum)?\s*)\s*{token}", re.IGNORECASE),
        re.compile(rf"{token}\s*(?:以下|以内|及以下)", re.IGNORECASE),
    )
    for bound, patterns_for_bound in (("followers_min", lower_patterns), ("followers_max", upper_patterns)):
        for pattern in patterns_for_bound:
            match = pattern.search(raw)
            if not match:
                continue
            suffix = match.group("value_suffix") or ""
            if not _FOLLOWER_CONTEXT_RE.search(raw) and not suffix:
                continue
            value = _count_value(match.group("value"), suffix)
            return {
                "followers_min": value if bound == "followers_min" else None,
                "followers_max": value if bound == "followers_max" else None,
                "source": "operator_text",
                "locked": True,
                "valid": value is not None,
                "error": "" if value is not None else "invalid_follower_value",
                "matched_text": match.group(0),
            }

    return {
        "followers_min": None,
        "followers_max": None,
        "source": "unspecified",
        "locked": False,
        "valid": True,
        "error": "",
        "matched_text": "",
    }


# Canonical segment → phrases people use → first-round creator query phrase.
_SEGMENT_RULES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "motorsport",
        ("赛车", "汽车摄影", "车展", "机车", "摩托", "motorsport", "racing", "automotive", "car photography"),
        "motorsport photographer",
    ),
    ("food", ("厨师", "餐饮", "美食", "烹饪", "chef", "culinary", "food"), "food photographer"),
    ("wedding", ("婚礼", "wedding"), "wedding photographer"),
    (
        "event",
        ("活动", "发布会", "会议摄影", "红毯", "event photographer", "event photography", "conference photographer"),
        "event photographer",
    ),
    (
        "stage",
        ("舞台", "演唱会", "演出摄影", "剧场", "stage photography", "concert photographer", "live music photographer", "performance photographer", "theater photographer", "theatre photographer"),
        "stage performance photographer",
    ),
    (
        "wildlife",
        ("野生动物", "鸟类摄影", "wildlife", "bird photographer", "bird photography"),
        "wildlife photographer",
    ),
    ("portrait", ("人像", "portrait photographer", "portrait photography"), "portrait photographer"),
    ("pet", ("宠物", "pet", "dog", "animal"), "pet photographer"),
    ("travel", ("旅拍", "旅行", "travel"), "travel photographer"),
    ("fitness", ("健身", "fitness"), "fitness creator"),
    ("sports", ("体育", "运动摄影", "sports"), "sports photographer"),
    ("real_estate", ("房地产", "房产", "real estate"), "real estate photographer"),
    ("commercial", ("商业广告", "广告", "commercial", "advertising"), "commercial photographer"),
    ("music_video", ("音乐视频", "mv", "music video"), "music video filmmaker"),
    ("documentary", ("纪录片", "documentary"), "documentary filmmaker"),
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
                "query_term": query_term,
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


def _alias_in_text(text: str, alias: str) -> bool:
    if any("一" <= char <= "鿿" for char in alias):
        return alias in text
    return re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text) is not None


def extract_explicit_segments(query: Any = "", body: Any = None) -> list[dict[str, Any]]:
    """Extract operator-owned industries/use-cases and keep each one independent."""

    payload = body if isinstance(body, dict) else {}
    nested = payload.get("filters") if isinstance(payload.get("filters"), dict) else {}
    explicit_values: list[str] = []
    for source in (payload, nested):
        for key in ("segments", "industries", "industry", "use_cases", "useCases"):
            explicit_values.extend(_list_values(source.get(key)))
    records = [_segment_record(value, source="operator_filter", locked=True) for value in explicit_values]

    raw = _text(query).lower()
    for key, aliases, query_term in _SEGMENT_RULES:
        matched = next((alias for alias in aliases if _alias_in_text(raw, alias)), "")
        if matched:
            records.append({
                "key": key,
                "label": matched,
                "query_term": query_term,
                "source": "operator_text",
                "locked": True,
            })

    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        key = record["key"]
        if key not in seen:
            seen.add(key)
            output.append(record)
    return output


_FOCAL_LENGTH_RE = re.compile(
    r"(?<![a-z0-9])(?P<low>\d{1,3})(?:\s*-\s*(?P<high>\d{1,3}))?\s*mm(?![a-z0-9])",
    flags=re.IGNORECASE,
)
_APERTURE_RE = re.compile(
    r"(?<![a-z0-9])(?:f|t)\s*/?\s*\d+(?:\.\d+)?(?![a-z0-9])",
    flags=re.IGNORECASE,
)


def _lens_focal_span(value: str) -> tuple[int | None, int | None]:
    matches = list(_FOCAL_LENGTH_RE.finditer(value))
    if not matches:
        return None, None
    values: list[int] = []
    for match in matches:
        values.append(int(match.group("low")))
        if match.group("high"):
            values.append(int(match.group("high")))
    return min(values), max(values)


def _lens_focals(value: str) -> list[int]:
    values: set[int] = set()
    for match in _FOCAL_LENGTH_RE.finditer(value):
        values.add(int(match.group("low")))
        if match.group("high"):
            values.add(int(match.group("high")))
    return sorted(values)


def _fast_lens_aperture(value: str) -> bool:
    for match in _APERTURE_RE.finditer(value):
        token = match.group(0).lower().replace(" ", "")
        if not token.startswith("f"):
            continue
        number = re.sub(r"^[ft]/?", "", token)
        try:
            if float(number) <= 1.4:
                return True
        except ValueError:
            continue
    return False


def _prospective_lens_capability(
    value: str,
    *,
    operator_segments: Iterable[Any] = (),
) -> str:
    """Map product facts to a small, deterministic prospective-use taxonomy.

    These phrases deliberately describe the work a creator can do with the
    product.  They never repeat an exact focal length, aperture, brand, or
    model, because those are evidence-of-existing-use anchors rather than
    prospective-user signals.
    """

    normalized = _text(value).lower()
    has_macro = any(term in normalized for term in ("macro", "micro lens", "微距"))
    has_cine = any(term in normalized for term in ("anamorphic", "cine", "cinema", "电影", "影视"))
    focals = _lens_focals(normalized)
    is_multi_focal_set = len(focals) >= 3 and any(
        term in normalized for term in (" set", " kit", "套装", "整套", "全套")
    )
    # A set description can mention that one member supports macro work.  That
    # does not turn every lens in the set into a macro lens.  Whole cine sets
    # retain their shared cinema capability; an individual EPIC 65 Macro still
    # resolves to ``macro cinema lens`` below.
    if has_cine and is_multi_focal_set:
        return "cinema lens"
    if has_macro and has_cine:
        return "macro cinema lens"
    if has_macro:
        return "macro lens"
    if has_cine:
        return "cinema lens"
    if any(term in normalized for term in ("ultra-wide", "ultrawide", "ultra wide", "super wide", "超广")):
        return "ultra-wide lens"
    if any(term in normalized for term in ("telephoto", "长焦")):
        return "telephoto portrait lens"

    focal_min, focal_max = _lens_focal_span(normalized)
    intent_blob = " ".join(
        _text(segment.get("key") or segment.get("query_term") or segment.get("label"))
        if isinstance(segment, dict)
        else _text(segment)
        for segment in operator_segments
    ).lower()
    portrait_intent = any(term in intent_blob for term in ("portrait", "wedding", "人像", "婚礼"))
    # The operator's explicit creator/use-case intent outranks a coarse focal
    # bucket.  In particular, 35mm F1.2 portrait work must not be rewritten as
    # a generic wide-angle search.  We retain physical extremes and dedicated
    # macro/cine categories above rather than pretending every lens fits every
    # stated scene.
    if portrait_intent and focal_min is not None and focal_max is not None:
        if focal_min >= 85:
            return "telephoto portrait lens"
        if 28 <= focal_min and focal_max <= 100:
            return "portrait lens"
    if any(term in normalized for term in ("portrait", "人像")):
        return "portrait lens"
    if (
        focal_min is not None
        and focal_max is not None
        and 28 <= focal_min <= 84
        and focal_max <= 100
        and _fast_lens_aperture(normalized)
    ):
        return "portrait lens"
    if focal_min is not None and focal_max is not None:
        if focal_min <= 20:
            return "ultra-wide lens"
        # 35mm is a boundary focal used for environmental portrait, street and
        # documentary work.  Without a declared scene, keep it neutral instead
        # of hard-coding one use case; shorter products retain wide-angle.
        if focal_max < 35:
            return "wide-angle lens"
        if focal_min >= 200:
            return "super-telephoto lens"
        if focal_min >= 85:
            return "telephoto portrait lens"
        if 50 <= focal_min <= 84 and focal_max <= 100:
            return "portrait lens"
    return "camera lens"


def _product_capability(
    product: Any,
    focus_terms: Iterable[Any],
    *,
    objective: str = PROSPECTIVE_GROWTH,
    operator_segments: Iterable[Any] = (),
) -> str:
    item = product if isinstance(product, dict) else {}
    focus_values = list(focus_terms or [])
    product_blob = " ".join(
        _text(item.get(key)).lower()
        for key in (
            "category_main", "category_detail", "series", "model_name",
            "marketing_name", "description", "specs_line",
        )
    )
    focus_blob = " ".join(_text(term).lower() for term in focus_values)
    blob = _text(f"{product_blob} {focus_blob}")
    if "flash" in blob or "strobe" in blob or "闪光" in blob:
        return "on-camera flash"
    if "monitor" in blob or "监视器" in blob:
        return "camera monitor"
    product_is_lens = (
        "lens" in product_blob
        or "镜头" in product_blob
        or _FOCAL_LENGTH_RE.search(product_blob) is not None
    )
    if product_is_lens or "lens" in focus_blob or "镜头" in focus_blob:
        if objective == PROSPECTIVE_GROWTH:
            # Resolved product facts outrank planner-authored focus prose.  The
            # latter is only a fallback when no product record is available.
            capability_source = product_blob if product_is_lens else focus_blob
            return _prospective_lens_capability(
                capability_source,
                operator_segments=operator_segments,
            )
        if "anamorphic" in blob or "cine" in blob:
            return "cinema lens"
        focal = _FOCAL_LENGTH_RE.search(product_blob)
        return f"{focal.group(0).replace(' ', '')} lens" if focal else "camera lens"
    if objective == EXISTING_EVIDENCE:
        for term in focus_values:
            candidate = _text(term).lower()
            if candidate and "viltrox" not in candidate and len(candidate.split()) <= 3:
                return candidate
    return "creator gear"


def _without_brand_model(value: Any, product: Any, *, drop_focal: bool = False) -> str:
    text = _text(value)
    item = product if isinstance(product, dict) else {}
    drop: set[str] = {"viltrox"}
    identity = " ".join(
        _text(item.get(key)).lower() for key in ("sku", "model_name", "marketing_name", "series")
    )
    for family in ("vintage", "pro", "evo", "epic", "lab", "air"):
        if re.search(rf"(?<![a-z0-9]){family}(?![a-z0-9])", identity):
            drop.add(family)
    for key in ("sku", "model_name", "marketing_name"):
        for token in re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", _text(item.get(key)).lower()):
            if any(char.isdigit() for char in token) or token == "viltrox":
                drop.add(token)
    # A focal length is product identity only after the catalog resolver has
    # actually produced a product/family.  With no product, phrases such as
    # ``50mm equivalent`` are operator-owned scene/format context and must not
    # disappear from the fallback query.
    if drop_focal and item:
        text = _FOCAL_LENGTH_RE.sub(" ", text)
        text = _APERTURE_RE.sub(" ", text)
    kept = [token for token in text.split() if token.lower().strip(",") not in drop]
    return _text(" ".join(kept))


def build_query_cells(
    *,
    query: Any,
    body: Any,
    product: Any,
    product_focus: Iterable[Any],
    platforms: Iterable[Any],
    legacy_queries: Iterable[Any] = (),
) -> list[dict[str, Any]]:
    """Build independent first-round cells; fallbacks never run in round one."""

    objective = normalize_objective(body)
    follower_filter = parse_follower_range(query, body)
    explicit = extract_explicit_segments(query, body)
    focus_values = list(product_focus or [])
    capability = _product_capability(
        product,
        focus_values,
        objective=objective,
        operator_segments=explicit,
    )
    # Empty is an intentional "no operator platform restriction" value.  The
    # provider resolves it to all supported discovery legs; silently choosing
    # YouTube here would turn an optional facet into an unrequested hard gate.
    platform_values = _dedupe(_text(value).lower() for value in platforms)
    legacy = _dedupe(legacy_queries)

    # The explicit legacy mode keeps the already-normalized anchored queries.
    # This branch is intentionally bypassed when the operator named segments:
    # those still need one independent anchored cell per segment.
    if objective == EXISTING_EVIDENCE and not explicit and legacy:
        selected_legacy = legacy[:4]
        raw_limit = _first_round_raw_limit(body, cell_count=len(selected_legacy))
        return [
            _cell(
                index=index,
                key=f"existing_{index}",
                label="existing product evidence",
                source="legacy_existing_evidence",
                locked=True,
                primary=query_value,
                fallbacks=(),
                objective=objective,
                platforms=platform_values,
                raw_limit=raw_limit,
                follower_filter=follower_filter,
                capability=capability,
            )
            for index, query_value in enumerate(selected_legacy, start=1)
        ]

    seeds = explicit
    if not seeds:
        inferred = [
            _without_brand_model(
                value,
                product,
                drop_focal=objective == PROSPECTIVE_GROWTH,
            )
            for value in focus_values
        ]
        inferred = [value for value in _dedupe(inferred) if value]
        seeds = [
            {
                "key": f"persona_{index}",
                "label": value,
                "query_term": value,
                "source": "planner_inferred",
                "locked": False,
            }
            for index, value in enumerate(inferred[:3], start=1)
        ]
    if not seeds:
        fallback = _without_brand_model(
            next(iter(legacy_queries), ""),
            product,
            drop_focal=objective == PROSPECTIVE_GROWTH,
        ) or "content creator"
        seeds = [{
            "key": "general_creator",
            "label": fallback,
            "query_term": fallback,
            "source": "rule_fallback",
            "locked": False,
        }]

    raw_limit = _first_round_raw_limit(body, cell_count=len(seeds))

    anchor = ""
    if objective == EXISTING_EVIDENCE and isinstance(product, dict):
        anchor = _text(product.get("marketing_name") or product.get("model_name") or product.get("sku"))

    cells: list[dict[str, Any]] = []
    for index, segment in enumerate(seeds, start=1):
        segment_term = _without_brand_model(
            segment.get("query_term"),
            product,
            drop_focal=objective == PROSPECTIVE_GROWTH,
        ) or "content creator"
        if objective == PROSPECTIVE_GROWTH:
            # First discover creators with segment-specific educational and
            # gear-decision content.  Product capability remains authoritative
            # below in ``locked_term_groups`` and is verified against public
            # profile/content evidence after retrieval.
            primary = _prospective_primary_query(segment_term)
            fallbacks = _dedupe([
                _text(f"{segment_term} gear review"),
                _text(f"{segment_term} photography tips"),
            ])
        else:
            anchored_query = _text(f"{segment_term} {capability}")
            primary = _text(f"{anchor} {anchored_query}") if anchor else anchored_query
            fallbacks = _dedupe([
                _text(f"{anchor} {segment_term} tutorial"),
                _text(f"{anchor} {segment_term} gear"),
            ])
        cells.append(_cell(
            index=index,
            key=segment["key"],
            label=segment["label"],
            source=segment["source"],
            locked=bool(segment["locked"]),
            primary=primary,
            fallbacks=fallbacks,
            objective=objective,
            platforms=platform_values,
            raw_limit=raw_limit,
            follower_filter=follower_filter,
            capability=capability,
        ))
    return cells


def apply_targeted_contract(
    plan: dict[str, Any],
    *,
    query: Any,
    body: Any = None,
    product: Any = None,
) -> dict[str, Any]:
    """Attach the V2 contract and make QueryCell primaries authoritative."""

    output = dict(plan or {})
    objective = normalize_objective(body, output)
    follower_filter = parse_follower_range(query, body)
    effective_platforms = _operator_platforms(body, output.get("platforms") or [])
    output.update({
        "search_spec_version": SEARCH_SPEC_VERSION,
        "objective": objective,
        "follower_filter": follower_filter,
        "explicit_segments": extract_explicit_segments(query, body),
        "product_anchor_required": objective == EXISTING_EVIDENCE,
        "brand_or_model_ranking_weight": 0 if objective == PROSPECTIVE_GROWTH else None,
        "ranking_claim_status": "descriptive_only",
        "platforms": effective_platforms,
    })
    if objective == PROSPECTIVE_GROWTH:
        # Compatibility fields must not accidentally reintroduce the old
        # brand-owner objective while QueryCell is being adopted downstream.
        output["search_query"] = _without_brand_model(
            output.get("search_query"),
            product,
            drop_focal=True,
        )
        output["search_queries"] = [
            value
            for value in (
                _without_brand_model(query_value, product, drop_focal=True)
                for query_value in (output.get("search_queries") or [])
            )
            if value
        ]
    if output.get("status") != "needs_clarification":
        cells = build_query_cells(
            query=query,
            body=body,
            product=product,
            product_focus=output.get("product_focus") or [],
            platforms=effective_platforms,
            legacy_queries=output.get("search_queries") or [output.get("search_query")],
        )
        output["query_cells"] = cells
        if cells:
            output["first_round_strategy"] = "independent_query_cells"
            output["authoritative_query_field"] = "query_cells"
            # ``search_query`` / ``search_queries`` remain compatibility fields
            # for persisted sessions and local recall.  Online first-round
            # execution must consume QueryCell primaries, never the broad merge.
    else:
        output["query_cells"] = []

    if not follower_filter["valid"]:
        output.update({
            "status": "needs_clarification",
            "reason": follower_filter["error"],
            "include_new_discovery": False,
            "new_discovery_limit": 0,
            "query_cells": [],
            "clarification": {
                "reason": follower_filter["error"],
                "message": "粉丝范围无效，请确认下限不高于上限后再搜索。",
            },
        })
    cells = output.get("query_cells") if isinstance(output.get("query_cells"), list) else []
    capability = _product_capability(
        product,
        output.get("product_focus") or [],
        objective=objective,
        operator_segments=output.get("explicit_segments") or [],
    )
    resolved = product if isinstance(product, dict) else {}
    output["search_brief"] = {
        "search_spec_version": SEARCH_SPEC_VERSION,
        "objective": objective,
        "product": {
            "resolved_sku": _text(resolved.get("sku")),
            "capability": capability,
            "brand_or_model_required": objective == EXISTING_EVIDENCE,
        },
        "explicit_segments": list(output.get("explicit_segments") or []),
        "follower_filter": dict(follower_filter),
        "platforms": effective_platforms,
        "claim_status": "descriptive_only",
        "first_round_strategy": "independent_query_cells",
        "fallback_policy": "shortfall_only",
        "authoritative_query_field": "query_cells",
        "query_cells": cells,
    }
    return output


__all__ = [
    "DEFAULT_OBJECTIVE",
    "PROSPECTIVE_GROWTH",
    "EXISTING_EVIDENCE",
    "SUPPORTED_OBJECTIVES",
    "SEARCH_SPEC_VERSION",
    "LOCKED_TERM_GROUPS_SCHEMA",
    "LOCKED_TERM_GROUPS_VERSION",
    "LOCKED_TERM_GROUPS_SOURCE",
    "normalize_objective",
    "parse_follower_range",
    "extract_explicit_segments",
    "build_query_cells",
    "build_locked_term_groups",
    "project_locked_term_groups",
    "rebuild_locked_term_groups_for_cell",
    "controlled_aliases_for",
    "controlled_capability_use_terms_for",
    "canonical_controlled_term",
    "apply_targeted_contract",
]
