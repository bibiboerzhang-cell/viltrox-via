"""Presentation-only target-persona wording for targeted KOL search."""
from __future__ import annotations

from collections.abc import Iterable
import re
from typing import Any


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _dedupe(values: Iterable[Any]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = _text(value)
        if item and item.casefold() not in seen:
            seen.add(item.casefold())
            output.append(item)
    return output


_CREATOR_ROLE_RE = re.compile(
    r"摄影师|摄像师|摄影指导|操作员|导演|创作者|博主|达人|厨师|"
    r"教育者|讲师|叙事者|记者|修图师|后期师|造型师|"
    r"(?<![a-z])(?:photographers?|videographers?|filmmakers?|cinematographers?|"
    r"directors?|camera\s+operators?|content\s+creators?|creators?|bloggers?|influencers?|chefs?|reviewers?|"
    r"educators?|storytellers?|sideline\s+reporters?|reporters?|journalists?|retouchers?|stylists?)(?![a-z])",
    re.IGNORECASE,
)


def has_creator_role(value: str) -> bool:
    return _CREATOR_ROLE_RE.search(value) is not None


_EXACT_OPERATOR_SCENES: tuple[tuple[str, str, str], ...] = (
    ("dental", r"(?<![a-z])dental(?:\s+photography)?(?![a-z])|牙科摄影", "dental"),
    ("underwater", r"(?<![a-z])underwater(?![a-z])|水下摄影", "underwater"),
    ("architecture", r"(?<![a-z])architectur(?:e|al)(?![a-z])|建筑摄影", "architecture"),
    ("atlanta", r"(?<![a-z])atlanta(?![a-z])|亚特兰大", "atlanta"),
)


_PEOPLE_REQUEST_PREFIX_RE = re.compile(
    r"^(?:(?:please\s+)?(?:find|show(?:\s+me)?|search(?:\s+for)?|look(?:ing)?\s+for|"
    r"identify|discover|recommend|need|want)\s+|(?:请)?(?:帮我)?(?:找|寻找|搜索|推荐)(?:一下)?\s*)",
    re.IGNORECASE,
)
_FOLLOWER_PHRASE_RE = re.compile(
    r"\b(?:with\s+|over\s+|under\s+|at\s+least\s+|more\s+than\s+|less\s+than\s+)?"
    r"\d[\d,.]*[km]?\+?\s*(?:followers?|audience)\b|"
    r"(?:粉丝|关注者)(?:数)?\s*(?:超过|低于|不少于|至少)?\s*\d[\d,.万千]*",
    re.IGNORECASE,
)
_PEOPLE_GLUE_RE = re.compile(
    r"\b(?:find|show|me|please|search|looking|look|for|identify|discover|recommend|"
    r"need|want|some|a|an|the|who|that|which|is|are|was|were|and|but|both|also|instead|in|at|from|on|with|to|"
    r"best|good|top|new|latest|popular|leading|experienced|professional|already|"
    r"natural[- ]light|documentary[- ]style|style|equivalent|channels?|"
    r"do|does|doing|review|reviewing|"
    r"shoots?|shooting|covers?|covering|films?|filming|makes?|making|creates?|creating|"
    r"instagram|youtube|tiktok)\b",
    re.IGNORECASE,
)
_CHINESE_GLUE_RE = re.compile(
    r"(?:请|帮我|给|找|寻找|搜索|推荐|一下|一些|一批|同时|还要|又会|也会|"
    r"兼具|兼做|都做|都拍|会拍|拍摄|场边故事|比赛|美国|英国|中国|加拿大|"
    r"澳大利亚|德国|法国|日本|韩国|英语|英文|中文|闪光灯|闪光|监视器|镜头|"
    r"摄影灯|影视灯|影棚灯|补光灯|短视频|视频团队|视频|团队里|里|会用|外接|擅长|"
    r"不同行业|行业|用户|比如|方向|粉丝多|等|"
    r"长焦|经验丰富|专业|整套|焦段|光圈|epic|nikon|canon|sony|fujifilm|lab|"
    r"拍|做|既|又|的)",
    re.IGNORECASE,
)
_PRODUCT_OR_CAMPAIGN_RE = re.compile(
    r"\b(?:product\s+(?:launch(?:\s+campaign)?|campaign)|prospective|launch|campaign|"
    r"(?:camera|field|external)\s+monitors?|monitors?|"
    r"camera\s+lens|gear|equipment|lens|nikon|canon|sony|fujifilm|"
    r"flash|strobe|studio\s+light(?:ing)?|viltrox|lab|pro|air|evo|epic|"
    r"united\s+states|u\.?s\.?a?|united\s+kingdom|u\.?k\.?|g\.?b\.?|"
    r"china|canada|australia|germany|france|japan|korea|english|chinese|"
    r"\d+(?:\.\d+)?\s*(?:mm|w(?:atts?)?)|f\s*/?\s*\d+(?:\.\d+)?|[a-z]+\d+[a-z0-9-]*)\b",
    re.IGNORECASE,
)

_CONTROLLED_ROLES = frozenset({
    "photographer", "videographer", "filmmaker", "cinematographer",
    "camera operator", "director", "content creator", "chef", "reviewer",
    "educator", "storyteller", "reporter", "retoucher", "stylist",
})


def explicit_creator_roles(value: str) -> list[str]:
    """Return each operator-stated controlled occupation in source order."""

    text = _text(value).lower()
    patterns = (
        ("camera operator", r"camera\s+operators?|摄影机操作员|相机操作员"),
        ("cinematographer", r"cinematographers?|directors?\s+of\s+photography|摄影指导|电影摄影师"),
        ("photographer", r"photographers?|摄影师"),
        ("videographer", r"videographers?|摄像师"),
        ("filmmaker", r"filmmakers?"),
        ("director", r"directors?|导演"),
        ("content creator", r"content\s+creators?|video\s+creators?|creators?|bloggers?|influencers?|创作者|博主|达人"),
        ("chef", r"chefs?|厨师"),
        ("reviewer", r"reviewers?|评测(?:博主|达人|创作者|人)?|测评(?:博主|达人|创作者|人)?"),
        ("educator", r"educators?|讲师|教育者"),
        ("storyteller", r"storytellers?|叙事者"),
        ("reporter", r"(?:sideline\s+)?reporters?|journalists?|记者"),
        ("retoucher", r"retouchers?|修图师|后期师"),
        ("stylist", r"stylists?|造型师"),
    )
    found = [
        (match.start(), match.end(), role)
        for role, pattern in patterns
        if (match := re.search(rf"(?<![a-z])(?:{pattern})(?![a-z])", text, re.IGNORECASE))
    ]
    selected: list[tuple[int, int, str]] = []
    for start, end, role in sorted(found, key=lambda row: (row[0], -(row[1] - row[0]))):
        if not any(start < kept_end and end > kept_start for kept_start, kept_end, _ in selected):
            selected.append((start, end, role))
    return _dedupe(role for _, _, role in sorted(selected))


def _remove_covered_term(value: str, term: Any) -> str:
    item = _text(term).lower()
    if not item:
        return value
    if any("\u4e00" <= char <= "\u9fff" for char in item):
        return value.replace(item, " ")
    return re.sub(rf"(?<![a-z0-9]){re.escape(item)}s?(?![a-z0-9])", " ", value)


def _exact_slug(value: str, *, prefix: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value).strip("_")[:64]
    return f"{prefix}_{slug or 'operator_term'}"


def _singular_role(value: str) -> str:
    role = _text(value).lower()
    for plural, singular in (
        ("photographers", "photographer"), ("videographers", "videographer"),
        ("filmmakers", "filmmaker"), ("cinematographers", "cinematographer"),
        ("directors", "director"), ("camera operators", "camera operator"),
        ("content creators", "content creator"), ("creators", "creator"),
        ("bloggers", "blogger"), ("influencers", "influencer"),
        ("chefs", "chef"), ("reviewers", "reviewer"), ("educators", "educator"),
        ("storytellers", "storyteller"), ("sideline reporters", "sideline reporter"),
        ("reporters", "reporter"),
        ("journalists", "journalist"), ("retouchers", "retoucher"),
        ("stylists", "stylist"),
    ):
        if role == plural:
            return singular
    return role


def _singular_unknown_role(value: str) -> str:
    words = _text(value).lower().split()
    if not words:
        return ""
    last = words[-1]
    if last.endswith("ies") and len(last) > 4:
        words[-1] = f"{last[:-3]}y"
    elif last.endswith("s") and not last.endswith(("ss", "news", "series")):
        words[-1] = last[:-1]
    return " ".join(words)


def _scene_record(term: str, query_term: str) -> dict[str, Any]:
    return {
        "key": _exact_slug(term, prefix="custom"),
        "label": term,
        "query_term": query_term,
        "required_scene_terms": [term],
        "exact_term": term,
        "source": "operator_text_exact",
        "locked": True,
    }


def _role_record(role: str, query_term: str, canonical_role: str) -> dict[str, Any]:
    return {
        "key": _exact_slug(role, prefix="custom_role"),
        "label": role,
        "query_term": query_term,
        "required_scene_terms": [],
        "required_role_terms": [canonical_role],
        "exact_term": role,
        "role_only": True,
        "source": "operator_text_exact",
        "locked": True,
    }


def _known_exact_records(text: str) -> list[dict[str, Any]]:
    records = [
        _scene_record(term, contextual_creator_term(f"{term} photographer", text))
        for _slug, pattern, term in _EXACT_OPERATOR_SCENES
        if re.search(pattern, text, flags=re.IGNORECASE) and has_creator_role(text)
    ]
    if re.search(r"(?<![a-z])camera\s+assistants?(?![a-z])", text) or "摄影助理" in text:
        assistant = _role_record("camera assistant", "camera assistant", "camera assistant")
        assistant["key"] = "custom_camera_assistant"
        records.append(assistant)
    return records


def _intent_core(text: str, *, strip_count: bool = False) -> str:
    core = _FOLLOWER_PHRASE_RE.sub(" ", _PEOPLE_REQUEST_PREFIX_RE.sub("", text))
    if strip_count:
        core = re.sub(r"^\d{1,4}\s+(?=[a-z\u4e00-\u9fff])", "", core)
    return core


def _ordered_covered_terms(values: Iterable[Any]) -> list[str]:
    return sorted(_dedupe(values), key=lambda item: len(_text(item)), reverse=True)


def _uncovered_modifier(core: str, covered: Iterable[Any]) -> tuple[str, Any]:
    role_match = _CREATOR_ROLE_RE.search(core)
    uncovered = _CREATOR_ROLE_RE.sub(" ", core) if role_match else core
    for term in covered:
        uncovered = _remove_covered_term(uncovered, term)
    uncovered = _CHINESE_GLUE_RE.sub(" ", uncovered)
    uncovered = _text(
        re.sub(r"[^a-z0-9\u4e00-\u9fff-]+", " ", _PEOPLE_GLUE_RE.sub(" ", uncovered))
    ).strip("- ")
    return uncovered, role_match


def _record_fallback_role(records: Iterable[dict[str, Any]]) -> str:
    return next(
        (
            _text(term)
            for record in records
            for term in (record.get("required_role_terms") or [])
            if _text(term)
        ),
        "photographer",
    )


def _append_known_remainder(
    text: str,
    records: list[dict[str, Any]],
    covered_terms: Iterable[Any],
) -> list[dict[str, Any]]:
    core = _PRODUCT_OR_CAMPAIGN_RE.sub(" ", _intent_core(text))
    covered = _ordered_covered_terms([
        *covered_terms,
        *(record.get("exact_term") for record in records),
    ])
    uncovered, role_match = _uncovered_modifier(core, covered)
    if not uncovered or len(uncovered) > 120 or any(char.isdigit() for char in uncovered):
        return records
    role = _singular_role(role_match.group(0)) if role_match else ""
    role = role or _record_fallback_role(records)
    query_term = contextual_creator_term(f"{uncovered} {role}", text)
    records.append(_scene_record(uncovered, query_term))
    return records


def _normalized_roles(core: str) -> list[str]:
    roles = explicit_creator_roles(core)
    creator_chef = "chef" in roles and "厨师" in core and (
        "content creator" in roles or "方向" in core
    )
    if creator_chef:
        roles = [role for role in roles if role != "chef"] or ["content creator"]
    generic_multi_head = (
        "content creator" in roles
        and len(roles) > 2
        and re.search(r"(?:content\s+)?creators?\s+who\s+(?:are\s+)?both\b", core)
    )
    if generic_multi_head:
        roles = [role for role in roles if role != "content creator"]
    return roles


def _multi_role_records(roles: list[str]) -> list[dict[str, Any]]:
    return [{
        "key": "custom_multi_role",
        "label": " ".join(roles),
        "query_term": " ".join(roles),
        "required_scene_terms": [],
        "required_role_terms": roles,
        "exact_term": roles[0],
        "role_only": True,
        "source": "operator_text_exact",
        "locked": True,
    }]


def _is_unanchored_creator_request(text: str, role: str, canonical_role: str) -> bool:
    return bool(
        canonical_role == "content creator"
        and role in {"content creator", "creator", "创作者", "达人"}
        and re.search(
            r"(?<![a-z])(?:good|top|best|some)(?![a-z])|一些|一批",
            text,
            flags=re.IGNORECASE,
        )
    )


def _controlled_role_records(
    text: str,
    roles: list[str],
    role: str,
    uncovered: str,
    covered: list[str],
) -> list[dict[str, Any]] | None:
    canonical_role = roles[0] if roles else role
    eligible = not uncovered and role and not covered and canonical_role in _CONTROLLED_ROLES
    if not eligible:
        return None
    if _is_unanchored_creator_request(text, role, canonical_role):
        return []
    query_role = (
        "content creator"
        if canonical_role == "content creator" and role in {"creator", "创作者"}
        else role
    )
    return [_role_record(role, query_role, canonical_role)]


def _unknown_occupation_records(uncovered: str, core: str) -> list[dict[str, Any]]:
    words = re.findall(r"[a-z0-9-]+|[\u4e00-\u9fff]+", uncovered)
    occupation_shape = bool(
        words
        and len(words) <= 6
        and (words[-1].endswith("s") or re.search(r"[师员者手家人]$", words[-1]))
    )
    if not occupation_shape:
        return []
    exact_role = _singular_unknown_role(uncovered)
    return [_role_record(exact_role, core, exact_role)]


def _generic_exact_records(
    text: str,
    covered_terms: Iterable[Any],
) -> list[dict[str, Any]]:
    core = _intent_core(text, strip_count=True)
    roles = _normalized_roles(core)
    if len(roles) > 1:
        return _multi_role_records(roles)
    core = _PRODUCT_OR_CAMPAIGN_RE.sub(" ", core)
    covered = sorted(list(covered_terms), key=lambda item: len(_text(item)), reverse=True)
    uncovered, role_match = _uncovered_modifier(core, covered)
    role = _singular_role(role_match.group(0)) if role_match else ""
    controlled = _controlled_role_records(text, roles, role, uncovered, covered)
    if controlled is not None:
        return controlled
    if not uncovered or len(uncovered) > 120 or (role and any(char.isdigit() for char in uncovered)):
        return []
    if role:
        return [_scene_record(uncovered, _text(f"{uncovered} {role}"))]
    return _unknown_occupation_records(uncovered, core)


def exact_operator_intent_records(
    value: str,
    *,
    covered_terms: Iterable[Any] = (),
) -> list[dict[str, Any]]:
    """Keep safe explicit niche/location/occupation words as exact-only intent."""

    text = _text(value).lower()
    records = _known_exact_records(text)
    if records:
        return _append_known_remainder(text, records, covered_terms)
    return _generic_exact_records(text, covered_terms)


def _with_creator_role(term: str, role: str) -> str:
    return re.sub(
        r"(?:photographer|videographer|filmmaker|cinematographer|content creator|camera operator|"
        r"director|creator|educator|storyteller|reporter|retoucher|stylist)$",
        role,
        term,
    )


def _refine_scene_term(term: str, low: str) -> str:
    if any(marker in low for marker in ("篮球", "basketball")):
        term = re.sub(r"sports photographer$", "basketball photographer", term)
    if any(marker in low for marker in ("拍鸟", "鸟类", "鸟摄影", "birding", "bird photographer")):
        term = re.sub(r"wildlife photographer$", "bird photographer", term)
    equivalent = re.search(
        r"(?:\d{1,3}\s*mm\s*(?:full[- ]?frame\s+)?equivalent|"
        r"equivalent(?:\s+to)?\s*\d{1,3}\s*mm)",
        low,
        flags=re.IGNORECASE,
    )
    if equivalent and term == "professional photographer":
        return f"{equivalent.group(0)} professional photographer"
    return term


def _specialist_context_role(low: str) -> str:
    if any(marker in low for marker in ("sideline reporter", "场边记者")):
        return "sideline reporter"
    if re.search(r"(?<![a-z])(?:reporters?|journalists?)(?![a-z])", low) or "记者" in low:
        return "reporter"
    if re.search(r"(?<![a-z])(?:retouchers?)(?![a-z])", low) or any(
        marker in low for marker in ("修图师", "摄影后期师")
    ):
        return "retoucher"
    if re.search(r"(?<![a-z])(?:stylists?)(?![a-z])", low) or "造型师" in low:
        return "stylist"
    if re.search(r"(?<![a-z])(?:storytellers?)(?![a-z])", low) or any(
        marker in low for marker in ("视觉叙事者", "体育故事创作者")
    ):
        return "storyteller"
    if any(marker in low for marker in ("teach", "teaches", "teaching", "educator", "教育者", "讲师")):
        return "educator"
    return ""


def _creator_context_role(low: str) -> str:
    short_form = (
        any(marker in low for marker in ("短视频", "视频", "short video", "video blogger", "reels", "tiktok"))
        and any(marker in low for marker in ("博主", "达人", "blogger", "influencer", "content creator", "内容创作者"))
    )
    creator = any(
        marker in low
        for marker in (
            "视频创作者", "内容创作者", "创作者", "视频博主", "博主", "达人",
            "video creator", "video blogger", "content creator",
        )
    ) or re.search(
        r"(?<![a-z])(?:creator|creators|blogger|bloggers|influencer|influencers)(?![a-z])",
        low,
    )
    if short_form or creator:
        return "content creator"
    if any(marker in low for marker in ("摄影机操作员", "相机操作员", "camera operator")):
        return "camera operator"
    if any(marker in low for marker in ("摄影指导", "cinematographer", "director of photography", "directors of photography")):
        return "cinematographer"
    return ""


def _production_context_role(low: str) -> str:
    if any(marker in low for marker in ("电影导演", "film director", "movie director")):
        return "director"
    if re.search(r"(?<![a-z])director(?:s)?(?![a-z])", low) or "导演" in low:
        return "director"
    if any(marker in low for marker in ("filmmaker", "director of photography")):
        return "filmmaker"
    if any(marker in low for marker in ("摄像", "videographer", "video producer", "视频制作")):
        return "videographer"
    if "厨师" in low and any(marker in low for marker in ("方向", "创作者", "博主", "达人")):
        return "content creator"
    if any(marker in low for marker in ("chef", "chefs", "厨师")):
        return "chef content creator"
    return ""


def _contextual_role(low: str) -> str:
    return (
        _specialist_context_role(low)
        or _creator_context_role(low)
        or _production_context_role(low)
    )


def contextual_creator_term(query_term: str, source_text: str) -> str:
    """Keep an operator-stated occupation ahead of a scene's default role."""

    low = _text(source_text).lower()
    term = _text(query_term)
    explicit_review = term.endswith("reviewer") and any(
        marker in low for marker in ("review", "reviewing", "reviewer", "评测", "测评")
    )
    if explicit_review:
        return term
    term = _refine_scene_term(term, low)
    role = _contextual_role(low)
    return _with_creator_role(term, role) if role else term


def compound_creator_term(records: list[dict[str, Any]], clause: str) -> str:
    """Build one people query for scene intersections and exact occupations."""

    descriptors: list[str] = []
    for record in records:
        if record.get("role_only") is True:
            continue
        key = _text(record.get("key"))
        descriptor = {
            "motorsport": "automotive" if "汽车" in clause or "automotive" in clause else "motorsport",
            "food": "food", "wedding": "wedding", "event": "event",
            "stage": "stage performance",
            "wildlife": "bird" if any(value in clause for value in ("拍鸟", "鸟类", "bird")) else "wildlife",
            "portrait": "portrait", "street": "street", "landscape": "landscape",
            "fashion": "fashion", "night": "night", "pet": "pet", "travel": "travel",
            "fitness": "fitness",
            "sports": "basketball" if "篮球" in clause or "basketball" in clause else "sports",
            "real_estate": "real estate", "commercial": "commercial",
            "product_launch": "product launch",
            "product_photography": "product", "jewelry_macro": "jewelry macro",
            "macro": "macro", "lighting": "off-camera lighting",
            "review": "camera gear review", "music_video": "music video",
            "documentary": "documentary", "film_production": "independent film",
            "film_photography": "film photography",
        }.get(key, _text(record.get("exact_term")))
        if descriptor and descriptor not in descriptors:
            descriptors.append(descriptor)
    exact_roles = _dedupe(
        term for record in records for term in (record.get("required_role_terms") or [])
        if record.get("role_only") is True
    )
    if exact_roles:
        role = " ".join(exact_roles)
    elif any(marker in clause for marker in ("teach", "teaches", "teaching", "educator", "教程", "教学")):
        role = "educator"
    else:
        role = contextual_creator_term("photographer", clause)
    return _text(" ".join([*descriptors, role]))


def _plural_role(value: Any) -> str:
    role = _text(value)
    for singular, plural in (
        ("photographer", "photographers"),
        ("videographer", "videographers"),
        ("filmmaker", "filmmakers"),
        ("cinematographer", "cinematographers"),
        ("content creator", "content creators"),
        ("camera operator", "camera operators"),
        ("director", "directors"),
        ("educator", "educators"),
        ("storyteller", "storytellers"),
        ("reporter", "reporters"),
        ("retoucher", "retouchers"),
        ("stylist", "stylists"),
        ("reviewer", "reviewers"),
        ("chef", "chefs"),
    ):
        if role.endswith(singular):
            return role[: -len(singular)] + plural
    return role


def build_target_persona_text(
    *,
    segments: Iterable[dict[str, Any]],
    product_focus: Iterable[Any],
    product_present: bool,
    capability: str,
    affirmative_query: str,
) -> str:
    roles = _dedupe(_plural_role(row.get("query_term")) for row in segments)
    if not roles:
        role_markers = (
            "photographer", "videographer", "filmmaker", "cinematographer",
            "content creator", "camera operator",
        )
        roles = _dedupe(
            _plural_role(value)
            for value in product_focus
            if any(marker in _text(value).lower() for marker in role_markers)
        )[:3]
    subject = (
        roles[0]
        if len(roles) == 1
        else f"{', '.join(roles[:-1])} and {roles[-1]}"
        if roles
        else "content creators"
    )
    subject = subject[:1].upper() + subject[1:]
    if product_present:
        return f"{subject} whose public work demonstrates relevant {capability} use cases."
    brief = _text(affirmative_query)[:240]
    return f"{subject} matching the operator's stated content brief{f': {brief}' if brief else ''}."


__all__ = [
    "build_target_persona_text",
    "compound_creator_term",
    "contextual_creator_term",
    "exact_operator_intent_records",
    "explicit_creator_roles",
    "has_creator_role",
]
