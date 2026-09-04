"""Evidence matching and contract projection for growth-candidate scoring."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any

from app.domains.kol import targeted_search_contract
from app.domains.kol.growth_candidate_metrics import (
    _dedupe_terms,
    _iter_values,
    _normal_text,
    _percentile_01,
)


_GENERIC_NON_PROOF_TERMS = frozenset(
    {
        "viltrox",
        "唯卓仕",
        "sony",
        "索尼",
        "nikon",
        "尼康",
        "canon",
        "佳能",
        "fujifilm",
        "fuji",
        "富士",
        "panasonic",
        "松下",
        "camera",
        "相机",
        "gear",
        "equipment",
        "creator",
        "content creator",
        "photographer",
        "photography",
        "videographer",
        "filmmaker",
        "kol",
        "influencer",
        "lens",
        "镜头",
    }
)
_PRODUCT_GROUP_NAMES = frozenset(
    {
        "product",
        "product_use",
        "product_use_fit",
        "product_capability",
        "capability",
        "capabilities",
        "product_terms",
        "product_use_terms",
        "product_capability_terms",
    }
)
_SCENE_GROUP_NAMES = frozenset(
    {
        "scene",
        "segment",
        "segment_use_case",
        "use_case",
        "use_cases",
        "industry",
        "industries",
        "persona",
        "personas",
        "scene_terms",
        "segment_terms",
        "use_case_terms",
        "industry_terms",
        "persona_terms",
    }
)
_AUDIENCE_GROUP_NAMES = frozenset(
    {"audience", "audience_fit", "audience_terms", "target_audience", "target_audiences"}
)
_ROLE_GROUP_NAMES = frozenset(
    {"role", "roles", "people_role", "creator_role", "required_role_terms"}
)
_DIRECT_CONTENT_FIELDS = (
    "title",
    "description",
    "caption",
    "captions",
    "transcript",
    "subtitle",
    "video",
    "representative_evidence",
    "content_evidence",
)
_PROFILE_FIELDS = (
    "bio",
    "profile_text",
    "primary_topic",
    "secondary_topic",
    "content_style",
    "type_reason",
)
_PRODUCT_ALIAS_GROUPS: tuple[frozenset[str], ...] = (
    frozenset(
        {
            "flash",
            "camera flash",
            "on camera flash",
            "speedlight",
            "strobe",
            "ttl flash",
            "hss flash",
            "闪光灯",
        }
    ),
    frozenset({"field monitor", "camera monitor", "external monitor", "监视器", "监看器"}),
    frozenset({"macro lens", "macro", "微距镜头", "微距"}),
    frozenset(
        {"anamorphic lens", "anamorphic", "cinema lens", "变形宽银幕镜头", "电影镜头"}
    ),
)
_SCENE_ALIAS_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"motorsport", "motor sport", "racing", "race", "automotive", "赛车", "机车", "摩托"}),
    frozenset({"food", "chef", "culinary", "restaurant", "cooking", "餐饮", "美食", "厨师", "烹饪"}),
    frozenset({"wedding", "bridal", "婚礼"}),
    frozenset({"pet", "pets", "dog", "dogs", "animal", "宠物"}),
    frozenset({"travel", "destination", "旅行", "旅拍"}),
    frozenset({"fitness", "gym", "健身"}),
    frozenset({"sports", "sport", "体育", "运动摄影"}),
    frozenset({"real estate", "property", "interior", "房产", "房地产", "室内"}),
    frozenset({"commercial", "advertising", "campaign", "商业广告", "广告"}),
    frozenset({"music video", "mv", "音乐视频"}),
    frozenset({"documentary", "纪录片"}),
)
_KNOWN_PRODUCT_PHRASES = tuple(
    sorted({term for group in _PRODUCT_ALIAS_GROUPS for term in group}, key=len, reverse=True)
)
_CONTROLLED_SOURCES = frozenset(
    {"server allowlisted alias evidence", "server capability use map"}
)
_EVIDENCE_KINDS = ("product", "scene", "role")

_EvidenceRows = dict[str, list[dict[str, Any]]]
_RequiredTerms = dict[str, list[str]]


def _group_kind(value: Any) -> str:
    group = _normal_text(value).replace(" ", "_")
    if group in _PRODUCT_GROUP_NAMES:
        return "product"
    if group in _SCENE_GROUP_NAMES:
        return "scene"
    if group in _AUDIENCE_GROUP_NAMES:
        return "audience"
    if group in _ROLE_GROUP_NAMES:
        return "role"
    return ""


def _alias_group(term: str, *, kind: str) -> frozenset[str] | None:
    canonical = targeted_search_contract.canonical_controlled_term(kind, term)
    aliases = targeted_search_contract.controlled_aliases_for(kind, canonical)
    return frozenset(aliases) if aliases else None


def _is_specific_proof_term(term: Any) -> bool:
    normalized = _normal_text(term)
    if not normalized or normalized in _GENERIC_NON_PROOF_TERMS:
        return False
    if normalized.startswith("viltrox ") and not re.search(
        r"\b(?:flash|speedlight|strobe|monitor|\d{1,3}mm|macro|anamorphic|ttl|hss)\b",
        normalized,
    ):
        return False
    return True


def _terms_equivalent(evidence_term: str, required_term: str, *, kind: str) -> bool:
    evidence = _normal_text(evidence_term)
    required = _normal_text(required_term)
    if not evidence or not required:
        return False
    if kind != "role" and not (
        _is_specific_proof_term(evidence) and _is_specific_proof_term(required)
    ):
        return False
    if evidence == required:
        aliases = targeted_search_contract.controlled_aliases_for(kind, required)
        return not aliases or evidence in {_normal_text(alias) for alias in aliases}
    evidence_group = _alias_group(evidence, kind=kind)
    required_group = _alias_group(required, kind=kind)
    return bool(evidence_group and required_group and evidence_group == required_group)


def _match_evidence_rows(item: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = item.get("match_evidence")
    if isinstance(raw, Mapping):
        raw = raw.get("items") or raw.get("evidence") or [raw]
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return []
    return [dict(row) for row in raw if isinstance(row, Mapping)]


def _evidence_terms(row: Mapping[str, Any]) -> list[str]:
    values: list[Any] = []
    for key in ("term", "matched_term", "value", "terms", "matched_terms"):
        if key in row:
            values.extend(_iter_values(row.get(key)))
    return _dedupe_terms(values)


def _evidence_strength(rows: Sequence[Mapping[str, Any]]) -> float | None:
    if not rows:
        return None
    strengths: list[float] = []
    fields: set[str] = set()
    for row in rows:
        field = _normal_text(row.get("field") or row.get("source_field") or row.get("source"))
        fields.add(field or "unknown")
        if any(token in field for token in _DIRECT_CONTENT_FIELDS):
            base = 0.92
        elif any(token in field for token in _PROFILE_FIELDS):
            base = 0.72
        elif "llm" in field or "inference" in field or "model" in field:
            base = 0.45
        else:
            base = 0.60
        confidence = _percentile_01(row.get("confidence"))
        strengths.append(min(base, confidence) if confidence is not None else base)
    breadth_bonus = min(0.08, 0.04 * max(0, len(rows) - 1))
    field_bonus = min(0.05, 0.025 * max(0, len(fields) - 1))
    return round(min(1.0, max(strengths) + breadth_bonus + field_bonus), 6)


def _locked_group(
    locked_spec: Mapping[str, Any],
    *,
    kind: str,
    canonical: str,
) -> Mapping[str, Any] | None:
    return next(
        (
            value
            for value in locked_spec.get("groups") or []
            if isinstance(value, Mapping)
            and _normal_text(value.get("kind")) == kind
            and _normal_text(value.get("canonical_term")) == canonical
        ),
        None,
    )


def _controlled_evidence_proves(
    row: Mapping[str, Any],
    *,
    kind: str,
    required_terms: Sequence[str],
    locked_spec: Mapping[str, Any] | None,
) -> bool:
    source = _normal_text(row.get("source"))
    if source not in _CONTROLLED_SOURCES or not isinstance(locked_spec, Mapping):
        return False
    if _group_kind(row.get("evidence_group")) != kind:
        return False
    canonical = _normal_text(row.get("canonical_term"))
    observed = _normal_text(row.get("observed_term") or row.get("term"))
    if not canonical or not observed or not any(
        canonical == _normal_text(expected)
        or _terms_equivalent(canonical, expected, kind=kind)
        for expected in required_terms
    ):
        return False
    group = _locked_group(locked_spec, kind=kind, canonical=canonical)
    if not isinstance(group, Mapping):
        return False
    if source == "server capability use map":
        if kind != "product":
            return False
        allowed = group.get("use_suitability_terms")
    else:
        allowed = group.get("aliases")
    return observed in {_normal_text(value) for value in _iter_values(allowed)}


def _required_terms(
    locked: Mapping[str, list[str]],
    *,
    product_evidence_required: bool,
) -> _RequiredTerms:
    return {
        "product": list(locked.get("product") or []) if product_evidence_required else [],
        "scene": list(locked.get("scene") or []),
        "role": list(locked.get("role") or []),
    }


def _term_proves_kind(
    row: Mapping[str, Any],
    term: str,
    *,
    kind: str,
    explicit_kind: str,
    required_terms: Sequence[str],
    locked_spec: Mapping[str, Any] | None,
    controlled_source: bool,
    product_evidence_required: bool,
) -> bool:
    if kind == "product" and not product_evidence_required:
        return False
    if kind == "role" and not controlled_source:
        return False
    if controlled_source:
        return _controlled_evidence_proves(
            row,
            kind=kind,
            required_terms=required_terms,
            locked_spec=locked_spec,
        )
    if required_terms:
        return any(_terms_equivalent(term, expected, kind=kind) for expected in required_terms)
    return explicit_kind == kind


def _collect_evidence(
    item: Mapping[str, Any],
    required: _RequiredTerms,
    *,
    locked_spec: Mapping[str, Any] | None,
    product_evidence_required: bool,
) -> tuple[_EvidenceRows, list[str]]:
    matched: _EvidenceRows = {kind: [] for kind in _EVIDENCE_KINDS}
    ignored_terms: list[str] = []
    for row in _match_evidence_rows(item):
        explicit_kind = _group_kind(
            row.get("group") or row.get("evidence_group") or row.get("category")
        )
        controlled_source = _normal_text(row.get("source")) in _CONTROLLED_SOURCES
        for term in _evidence_terms(row):
            if not controlled_source and not _is_specific_proof_term(term):
                ignored_terms.append(term)
                continue
            for kind in _EVIDENCE_KINDS:
                if not _term_proves_kind(
                    row,
                    term,
                    kind=kind,
                    explicit_kind=explicit_kind,
                    required_terms=required[kind],
                    locked_spec=locked_spec,
                    controlled_source=controlled_source,
                    product_evidence_required=product_evidence_required,
                ):
                    continue
                evidence = dict(row)
                evidence["matched_term"] = term
                matched[kind].append(evidence)
    return matched, ignored_terms


def _row_matches_required(row: Mapping[str, Any], *, kind: str, required_term: str) -> bool:
    if (
        _normal_text(row.get("source")) in _CONTROLLED_SOURCES
        and _normal_text(row.get("canonical_term")) == _normal_text(required_term)
    ):
        return True
    return any(
        _terms_equivalent(value, required_term, kind=kind)
        for value in (row.get("canonical_term"), row.get("matched_term"), row.get("term"))
        if _normal_text(value)
    )


def _rows_for_required(
    matched: _EvidenceRows,
    *,
    kind: str,
    required_term: str,
) -> list[dict[str, Any]]:
    return [
        row
        for row in matched[kind]
        if _row_matches_required(row, kind=kind, required_term=required_term)
    ]


def _required_strengths(
    matched: _EvidenceRows,
    *,
    kind: str,
    required_terms: Sequence[str],
) -> list[float | None]:
    return [
        _evidence_strength(_rows_for_required(matched, kind=kind, required_term=term))
        for term in required_terms
    ]


def _role_strength(
    matched: _EvidenceRows,
    required_roles: Sequence[str],
    *,
    role_match_mode: str,
) -> tuple[float | None, list[float | None], bool]:
    strengths = _required_strengths(matched, kind="role", required_terms=required_roles)
    role_all = role_match_mode == "all"
    if not strengths:
        return _evidence_strength(matched["role"]), strengths, role_all
    available = [value for value in strengths if value is not None]
    if role_all and len(available) == len(strengths):
        return min(available), strengths, role_all
    if not role_all and available:
        return max(available), strengths, role_all
    return None, strengths, role_all


def _scene_strength(
    matched: _EvidenceRows,
    required_scenes: Sequence[str],
) -> tuple[float | None, list[float | None]]:
    strengths = _required_strengths(matched, kind="scene", required_terms=required_scenes)
    if not strengths:
        return _evidence_strength(matched["scene"]), strengths
    available = [value for value in strengths if value is not None]
    if len(available) == len(strengths):
        return min(available), strengths
    return None, strengths


def _product_use_score(
    *,
    passed: bool,
    product_evidence_required: bool,
    product_strength: float | None,
    scene_strength: float | None,
) -> float | None:
    if not passed or not product_evidence_required:
        return None
    weighted = (
        0.55 * product_strength + 0.45 * scene_strength
        if scene_strength is not None
        else product_strength
    )
    return round(100.0 * weighted, 6)


def _proof_strength(
    required: _RequiredTerms,
    *,
    passed: bool,
    product_evidence_required: bool,
    product_strength: float | None,
    scene_strength: float | None,
    role_strength: float | None,
) -> float:
    if not passed:
        return 0.0
    values = [scene_strength] if required["scene"] else []
    if required["role"]:
        values.append(role_strength)
    if product_evidence_required:
        values.append(product_strength)
    return round(sum(value for value in values if value is not None) / len(values), 6)


def _matched_contract_fields(matched: _EvidenceRows) -> dict[str, Any]:
    return {
        "matched_product_terms": _dedupe_terms(
            row.get("matched_term") for row in matched["product"]
        ),
        "matched_scene_terms": _dedupe_terms(
            row.get("matched_term") for row in matched["scene"]
        ),
        "matched_role_terms": _dedupe_terms(
            row.get("matched_term") for row in matched["role"]
        ),
        "matched_fields": sorted(
            {
                str(row.get("field") or row.get("source_field") or row.get("source") or "unknown")
                for kind in _EVIDENCE_KINDS
                for row in matched[kind]
            }
        ),
    }


def _missing_contract_fields(
    required: _RequiredTerms,
    *,
    product_evidence_required: bool,
    product_strength: float | None,
    scene_strength: float | None,
    role_strength: float | None,
    required_scene_strengths: Sequence[float | None],
    required_role_strengths: Sequence[float | None],
    role_all: bool,
) -> dict[str, Any]:
    return {
        "missing_groups": [
            name
            for name, value, required_group in (
                ("product_use_fit", product_strength, product_evidence_required),
                ("segment_use_case", scene_strength, bool(required["scene"])),
                ("people_role", role_strength, bool(required["role"])),
            )
            if required_group and value is None
        ],
        "missing_scene_terms": [
            term
            for term, strength in zip(required["scene"], required_scene_strengths)
            if strength is None
        ],
        "missing_role_terms": [
            term
            for term, strength in zip(required["role"], required_role_strengths)
            if strength is None and (role_all or role_strength is None)
        ],
    }


def _evidence_contract(
    required: _RequiredTerms,
    matched: _EvidenceRows,
    ignored_terms: Sequence[str],
    *,
    passed: bool,
    product_evidence_required: bool,
    role_all: bool,
    strengths: tuple[float | None, float | None, float | None],
    required_scene_strengths: Sequence[float | None],
    required_role_strengths: Sequence[float | None],
) -> dict[str, Any]:
    product_strength, scene_strength, role_strength = strengths
    return {
        "passed": passed,
        "product_evidence_required": product_evidence_required,
        "required_product_terms": required["product"],
        "required_scene_terms": required["scene"],
        "required_role_terms": required["role"],
        "role_match_mode": "all" if role_all else "any",
        **_matched_contract_fields(matched),
        **_missing_contract_fields(
            required,
            product_evidence_required=product_evidence_required,
            product_strength=product_strength,
            scene_strength=scene_strength,
            role_strength=role_strength,
            required_scene_strengths=required_scene_strengths,
            required_role_strengths=required_role_strengths,
            role_all=role_all,
        ),
        "ignored_generic_or_brand_terms": _dedupe_terms(ignored_terms),
        "brand_history_used": False,
        "brand_history_weight": 0.0,
    }


def product_scene_evidence(
    item: Mapping[str, Any],
    locked: Mapping[str, list[str]],
    *,
    locked_spec: Mapping[str, Any] | None = None,
    product_evidence_required: bool = True,
    role_match_mode: str = "any",
) -> tuple[float | None, dict[str, Any], float]:
    required = _required_terms(
        locked,
        product_evidence_required=product_evidence_required,
    )
    matched, ignored_terms = _collect_evidence(
        item,
        required,
        locked_spec=locked_spec,
        product_evidence_required=product_evidence_required,
    )
    product_strength = _evidence_strength(matched["product"])
    role_strength, role_strengths, role_all = _role_strength(
        matched,
        required["role"],
        role_match_mode=role_match_mode,
    )
    scene_strength, scene_strengths = _scene_strength(matched, required["scene"])
    passed = (
        (not required["scene"] or scene_strength is not None)
        and (not required["role"] or role_strength is not None)
        and (not product_evidence_required or product_strength is not None)
    )
    score = _product_use_score(
        passed=passed,
        product_evidence_required=product_evidence_required,
        product_strength=product_strength,
        scene_strength=scene_strength,
    )
    proof_strength = _proof_strength(
        required,
        passed=passed,
        product_evidence_required=product_evidence_required,
        product_strength=product_strength,
        scene_strength=scene_strength,
        role_strength=role_strength,
    )
    contract = _evidence_contract(
        required,
        matched,
        ignored_terms,
        passed=passed,
        product_evidence_required=product_evidence_required,
        role_all=role_all,
        strengths=(product_strength, scene_strength, role_strength),
        required_scene_strengths=scene_strengths,
        required_role_strengths=role_strengths,
    )
    return score, contract, proof_strength
