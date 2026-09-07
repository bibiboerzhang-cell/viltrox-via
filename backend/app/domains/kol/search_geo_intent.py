"""Pure, conservative separation of creator location and audience geography.

These are operator constraints, never inferred facts about a discovered person.
Shooting locations and content language do not establish creator residence.
"""
from __future__ import annotations

import re
from typing import Any

from app.platform.country_codes import COUNTRY_CODE_ALIASES, COUNTRY_NAMES


SCHEMA = "search_geo_intent_v1"
COUNTRY_ALIASES = {
    **COUNTRY_CODE_ALIASES,
    **{code.lower(): code for code in COUNTRY_NAMES},
    "españa": "ES", "méxico": "MX", "italia": "IT", "brasil": "BR",
    "pt": "PT", "portugal": "PT", "葡萄牙": "PT",
    "ru": "RU", "russia": "RU", "russian federation": "RU", "俄罗斯": "RU",
    "viet nam": "VN", "印尼": "ID", "tr": "TR", "turkey": "TR", "türkiye": "TR", "土耳其": "TR",
    "pl": "PL", "poland": "PL", "波兰": "PL",
    "sa": "SA", "saudi arabia": "SA", "沙特": "SA",
    "ae": "AE", "united arab emirates": "AE", "uae": "AE", "阿联酋": "AE",
    "sg": "SG", "singapore": "SG", "新加坡": "SG",
    "nz": "NZ", "new zealand": "NZ", "新西兰": "NZ",
}
_CONTEXT_CODES = frozenset({"ae", "au", "ca", "de", "id", "in", "it", "pl", "pt", "sa"})
_LOWERCASE_SAFE_CODES = frozenset({"br", "fr", "gb", "jp", "kr", "mx", "nl", "nz", "ru", "sg", "th", "tr", "uk", "vn"})
_AUDIENCE = r"(?:audiences?|viewers?|followers?|customers?|buyers?|consumers?|target\s+markets?|markets?|受众|观众|粉丝|客户|消费者|买家|市场)"
_CREATOR = r"(?:creators?|photographers?|filmmakers?|videographers?|influencers?|bloggers?|authors?|创作者|作者|博主|摄影师|达人)"
_AUDIENCE_AFTER = re.compile(rf"^\s*(?:-\s*|[- ]based\s+)?(?:的\s*)?{_AUDIENCE}", re.I)
_AUDIENCE_BEFORE = re.compile(rf"{_AUDIENCE}[^,;，；。]{{0,36}}$|(?:面向|覆盖|触达|目标市场为?)\s*$", re.I)
_AUDIENCE_DIRECTION = re.compile(r"(?:面向|覆盖|触达|\btargeting)\s*$", re.I)
_LOCATION_AFTER = re.compile(rf"^\s*(?:[- ]based\s+|的\s*)?{_CREATOR}", re.I)
_LOCATION_BEFORE = re.compile(r"(?:based|resid(?:ence|ent|ing)|liv(?:e|es|ing)|located|country|来自|居住|定居|所在地|所在国家)[^,;，；。]{0,24}$", re.I)
_CREATOR_RELATION = re.compile(rf"{_CREATOR}|\b(?:based|residence|resident|residing|living|located)\b|来自|居住|定居|所在地|所在国家", re.I)
_SHOOTING_BEFORE = re.compile(r"(?:\b(?:shoot(?:s|ing)?|photographing|film(?:s|ing)?|travel(?:ling|ing)?|videos?\s+(?:of|about))\b|拍摄|拍|游览|旅行)[^,;，；。]{0,24}$", re.I)
_SHOOTING_AFTER = re.compile(r"^\s*(?:的\s*)?(?:street\s+(?:scenes|views)|landscapes?|cityscapes?|travel\s+videos?|街景|景色|风景|风光)(?!\s*photographers?)", re.I)
_NO_REQUIREMENT = re.compile(r"(?:need\s+not|not\s+(?:need|required|necessary)|does\s+not\s+need|no\s+need|regardless\s+of|不要求|不需要|无需|不用|不必)[^,;，；。]{0,45}$", re.I)
_EXCLUSION = re.compile(r"(?:\bnot\b|\bnon[- ]|\bexclud\w*|\bavoid\w*|\bexcept\b|不要|不找|排除|非)[^,;，；。]{0,32}$", re.I)
_OPTIONAL = re.compile(r"(?:\b(?:may|might|perhaps|possibly)\b|可能|不一定|例如|比如)[^,;，；。]{0,30}$", re.I)
_INFERENCE = re.compile(r"(?:do\s+not\s+infer|don't\s+infer|不能推断|不要推断|不推断)[^,;，；。]*$", re.I)
_CLAUSE_BREAK = re.compile(r"[,;，；。]|\s+\b(?:and|but)\b\s+", re.I)
_COUNTRY_LIST_CONNECTOR = re.compile(r"(?:\s|[,、/&]|\band\b|\bor\b|和|与|或|及)+", re.I)
_UNRESTRICTED_CREATOR = re.compile(
    rf"(?:全球|全世界|不限国家|不限地区|不分国家|不分地区|作者所在地不限|创作者所在地不限)|"
    rf"\b(?:worldwide|global)\b(?:\s+{_CREATOR})?|\bany\s+country\b|"
    r"\b(?:creator\s+)?residence\s+(?:does\s+not\s+matter|unrestricted)\b",
    re.I,
)
_UNRESTRICTED_AUDIENCE = re.compile(rf"(?:全球|不限|不分)\s*{_AUDIENCE}|\b(?:global|worldwide|any)\s+{_AUDIENCE}", re.I)


def _text(value: Any) -> str:
    return " ".join(value.split()).strip() if isinstance(value, str) else ""


def normalize_geo_country(value: Any) -> str:
    return COUNTRY_ALIASES.get(_text(value).lower(), "")


def _mentions(query: str) -> list[tuple[int, int, str, str]]:
    matches: list[tuple[int, int, str, str]] = []
    occupied: list[tuple[int, int]] = []
    for alias, code in sorted(COUNTRY_ALIASES.items(), key=lambda item: -len(item[0])):
        pattern = re.escape(alias)
        cjk = any("一" <= char <= "鿿" for char in alias)
        if not cjk:
            pattern = rf"(?<![a-z0-9]){pattern}(?![a-z0-9])"
        for match in re.finditer(pattern, query, re.I):
            start, end = match.span()
            if any(start < stop and end > begin for begin, stop in occupied):
                continue
            if len(alias) == 2 and not cjk:
                left, right = query[max(0, start - 32):start], query[end:end + 32]
                if alias == "pl" and re.match(r"\s*(?:-\s*)?(?:mount|卡口)", right, re.I):
                    continue
                contextual = re.search(r"\b(?:in|from|country|market)\s*[:=]?\s*$", left, re.I)
                if alias in _CONTEXT_CODES and not contextual and not _AUDIENCE_AFTER.search(right):
                    continue
                if alias not in _CONTEXT_CODES | _LOWERCASE_SAFE_CODES and match.group() != alias.upper():
                    continue
            occupied.append((start, end))
            matches.append((start, end, code, match.group()))
    # Preserve the established explicit "London photographers" meaning, but
    # never infer residence from "photographing London" or travel footage.
    for city, code in (("london", "GB"), ("伦敦", "GB"), ("atlanta", "US"), ("亚特兰大", "US")):
        pattern = re.escape(city) if any("一" <= ch <= "鿿" for ch in city) else rf"\b{city}\b"
        for match in re.finditer(pattern, query, re.I):
            left, right = query[max(0, match.start() - 40):match.start()], query[match.end():match.end() + 45]
            if (_LOCATION_AFTER.search(right) or _LOCATION_BEFORE.search(left)) and not _SHOOTING_BEFORE.search(left):
                matches.append((*match.span(), code, match.group()))
    return sorted(matches)


def _query_geography(query: str) -> dict[str, Any]:
    out: dict[str, Any] = {"creator": [], "audience": [], "creator_evidence": [], "audience_evidence": [], "reasons": []}
    audience_global = _UNRESTRICTED_AUDIENCE.search(query)
    # "Global audience" does not mean "global creators".
    creator_text = _UNRESTRICTED_AUDIENCE.sub(" ", query)
    out["creator_unrestricted"] = bool(_UNRESTRICTED_CREATOR.search(creator_text))
    out["audience_unrestricted"] = bool(audience_global)
    breaks = list(_CLAUSE_BREAK.finditer(query))
    mentions = _mentions(query)
    for index, (start, end, code, matched) in enumerate(mentions):
        # A shared relation applies to a plain country list ("US and UK
        # audiences"), but never across an intervening creator/scene phrase.
        first = last = index
        while first > 0 and _COUNTRY_LIST_CONNECTOR.fullmatch(query[mentions[first - 1][1]:mentions[first][0]]):
            first -= 1
        while last + 1 < len(mentions) and _COUNTRY_LIST_CONNECTOR.fullmatch(query[mentions[last][1]:mentions[last + 1][0]]):
            last += 1
        start, end = mentions[first][0], mentions[last][1]
        begin = max((item.end() for item in breaks if item.end() <= start), default=0)
        stop = min((item.start() for item in breaks if item.start() >= end), default=len(query))
        left, right = query[begin:start], query[end:stop]
        # The nearest geographic relation wins; a prior creator phrase must
        # not steal the country in "UK creators reaching US audiences".
        audience_before = _AUDIENCE_BEFORE.search(left)
        creator_before = list(_CREATOR_RELATION.finditer(left))
        nearest_creator = creator_before[-1].start() if creator_before else -1
        audience = bool(
            _AUDIENCE_AFTER.search(right)
            or _AUDIENCE_DIRECTION.search(left)
            or (not _LOCATION_AFTER.search(right) and audience_before and audience_before.start() > nearest_creator)
        )
        role = "audience" if audience else "creator"
        if _INFERENCE.search(left):
            continue
        if _NO_REQUIREMENT.search(left):
            out[f"{role}_unrestricted"] = True
            continue
        if _EXCLUSION.search(left):
            out["reasons"].append(f"unsupported_{role}_exclusion")
            continue
        if _OPTIONAL.search(left):
            continue
        if not audience and (_SHOOTING_BEFORE.search(left) or _SHOOTING_AFTER.search(right)):
            continue
        if code not in out[role]:
            out[role].append(code)
            out[f"{role}_evidence"].append(matched)
    if len(out["creator"]) > 1:
        out["reasons"].append("multiple_creator_countries")
    return out


def _structured_geography(body: dict[str, Any], role: str) -> tuple[list[str], bool, list[str], bool, str]:
    filters = body.get("filters") if isinstance(body.get("filters"), dict) else {}
    names = ("creator_countries", "country", "market") if role == "creator" else ("audience_markets",)
    raw_values = [body[name] for name in names if name in body and body[name] not in (None, "")]
    if role == "creator" and "countries" in filters and filters["countries"] not in (None, ""):
        raw_values.append(filters["countries"])
    sets: list[list[str]] = []
    modes: list[str] = []
    reasons: list[str] = []
    unrestricted = False
    for raw in raw_values:
        mode = "require"
        if isinstance(raw, dict):
            mode = raw.get("mode", "require")
            if mode not in ("require", "include_unknown", "exclude"):
                reasons.append(f"unsupported_{role}_mode")
                continue
            raw = raw.get("values")
        modes.append(mode)
        values = raw if isinstance(raw, list) else [raw]
        codes: list[str] = []
        for value in values:
            if _text(value).lower() in {"global", "worldwide", "all", "any country", "全球", "不限国家", "*"}:
                unrestricted = True
                continue
            code = normalize_geo_country(value)
            if not code:
                reasons.append(f"unsupported_{role}_country")
            elif code not in codes:
                codes.append(code)
        if not values:
            unrestricted = True
        if codes:
            sets.append(codes)
    if sets and any(set(values) != set(sets[0]) for values in sets[1:]):
        reasons.append(f"conflicting_{role}_filters")
    if len(set(modes)) > 1:
        reasons.append(f"conflicting_{role}_filter_modes")
    return (sets[0] if sets else []), bool(raw_values), reasons, unrestricted, (modes[0] if modes else "require")


def resolve_geo_intent(query: Any, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Separate operator-owned residence and audience constraints; perform no IO.

    Legacy ``market``/``country``/``filters.countries`` remain creator-location
    inputs. No plan/provider metadata is consulted. Ambiguous results are
    surfaced for the caller to block/clarify, never silently picked.
    """
    text = _text(query)
    query_geo = _query_geography(text)
    payload = body if isinstance(body, dict) else {}
    result: dict[str, Any] = {"schema": SCHEMA, "evidence": {}, "ambiguity_reasons": list(query_geo["reasons"])}
    for role, field in (("creator", "creator_countries"), ("audience", "audience_markets")):
        selected, requested, reasons, unrestricted, mode = _structured_geography(payload, role)
        stated = query_geo[role]
        result["ambiguity_reasons"].extend(reasons)
        # Structured exclusions are supported by existing three-state gates.
        # A simultaneous positive text constraint needs a compound predicate;
        # do not erase either side or turn the excluded country into a target.
        if stated and selected and mode == "exclude":
            result["ambiguity_reasons"].append(f"conflicting_{role}_text_and_filter_mode")
        if stated and selected and set(stated) != set(selected):
            result["ambiguity_reasons"].append(f"conflicting_{role}_text_and_filter")
        unbounded = bool(unrestricted or query_geo[f"{role}_unrestricted"])
        values = selected or stated
        if unbounded and values:
            result["ambiguity_reasons"].append(f"conflicting_{role}_unrestricted_and_country")
        ambiguous = any(role in reason for reason in result["ambiguity_reasons"])
        text_requested = bool(stated or query_geo[f"{role}_unrestricted"] or any(role in reason for reason in query_geo["reasons"]))
        result[field] = [] if ambiguous else list(values)
        result[f"{role}_mode"] = mode
        result[f"{role}_source"] = (
            "operator_text_and_filter" if text_requested and requested else
            "operator_filter" if requested else "operator_text" if text_requested else "not_requested"
        )
        result[f"{role}_status"] = "ambiguous" if ambiguous else "unrestricted" if unbounded else "specified" if values else "unknown"
        result[f"{role}_unknown"] = result[f"{role}_status"] in {"unknown", "ambiguous"}
        result["evidence"][field] = list(query_geo[f"{role}_evidence"])[:8]
    result["ambiguity_reasons"] = list(dict.fromkeys(result["ambiguity_reasons"]))
    return result


__all__ = ["COUNTRY_ALIASES", "SCHEMA", "normalize_geo_country", "resolve_geo_intent"]
