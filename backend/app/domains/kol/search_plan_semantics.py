"""Pure, advisory checks of search-plan semantics; never a creator filter.

The caller owns the JSON schema, operator constraints and any decision to run
discovery. These checks do not rewrite a query, infer a market, qualify a
creator, or access services. A local probe can stop on ``needs_review`` without
turning this conservative heuristic into a production hard constraint.
"""
from __future__ import annotations

import copy
import re
import unicodedata
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from app.domains.kol.search_intent_text import affirmative_search_text
from app.domains.kol.targeted_search_terms import controlled_aliases_for


# The current application's explicitly supported market codes, not a claim to
# implement every ISO country or every audience-market taxonomy. Callers with
# a different reviewed taxonomy must provide it explicitly.
DEFAULT_MARKET_CODES = frozenset({
    "US", "GB", "CA", "DE", "FR", "JP", "KR", "AU", "ES", "MX", "IT",
    "BR", "PT", "RU", "TH", "VN", "ID", "TR", "PL", "NL", "SA", "AE",
    "IN", "SG", "NZ",
})
_ROLE_FILLER = frozenset({"creator", "creators", "channel", "channels", "influencer", "influencers", "kol", "kols"})
_TOKENS = re.compile(r"-?\w+(?:[./+'’-]\w+)*", re.UNICODE)
_NEGATION = frozenset({"not", "no", "without", "exclude", "excluding"})
_ANGLE_TOKENS = {
    "instruction": frozenset({"tutorial", "tutorials", "tips", "settings"}),
    "review": frozenset({"review", "reviews", "test", "testing"}),
    "pov": frozenset({"pov"}),
    "behind_scenes": frozenset({"behind", "bts"}),
}

QUERY_INTENT_SCHEMA = "query_cell_intent_v1"
QUERY_EXECUTION_SCHEMA = "query_cell_execution_validation_v1"
_OPERATOR_SOURCES = frozenset({"operator_text", "operator_text_exact", "operator_filter"})
_INTENT_SOURCES = _OPERATOR_SOURCES | {"planner_inferred", "rule_fallback", "legacy_existing_evidence", "not_requested"}
_ANGLE_ALIASES = {
    "instruction": ("tutorial", "tutorials", "how to", "教程", "教学视频"),
    "review": ("review", "reviews", "reviewer", "reviewers", "评测", "测评"),
    "pov": ("pov", "point of view", "第一视角", "第一人称"),
    "behind_scenes": ("behind the scenes", "behind scenes", "bts", "幕后", "拍摄花絮"),
    "comparison": ("comparison", "compare", "对比", "横评"),
}
_ANGLE_QUERY_TERMS = {"instruction": "tutorial", "review": "review", "pov": "POV", "behind_scenes": "behind the scenes", "comparison": "comparison"}
_EVIDENCE_KINDS = frozenset({"product_use_fit", "people_role", "segment_use_case", "market_activation"})


def _intent_text(value: Any) -> str:
    return " ".join(value.split()).strip() if isinstance(value, str) else ""


def _intent_terms(value: Any, *, maximum: int = 8) -> list[str] | None:
    if not isinstance(value, list) or len(value) > maximum:
        return None
    if any(not isinstance(item, str) or not item.strip() or len(item) > 240 for item in value):
        return None
    return list(dict.fromkeys(_intent_text(item) for item in value))


def _phrase_present(query: str, phrase: str) -> bool:
    def fold(value: str) -> str:
        return re.sub(r"[-_\s]+", " ", unicodedata.normalize("NFKC", value).casefold()).strip()

    text, term = fold(query), fold(phrase)
    if not term:
        return False
    if any("\u4e00" <= char <= "\u9fff" for char in term):
        return term in text
    return re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text) is not None


def operator_query_angles(query: Any) -> list[str]:
    """Only controlled, affirmative format words; a role is not itself a format."""
    if not isinstance(query, str):
        return []
    text = affirmative_search_text(query)
    return [kind for kind, aliases in _ANGLE_ALIASES.items()
            if any(_phrase_present(text, alias) for alias in aliases
                   if alias not in {"reviewer", "reviewers"})]


def preserve_operator_query_angles(query: str, angles: Sequence[str]) -> str:
    """Append a missing explicitly requested format without replacing people terms."""
    value = _intent_text(query)
    missing = [kind for kind in angles if kind in _ANGLE_ALIASES
               and not any(_phrase_present(value, alias) for alias in _ANGLE_ALIASES[kind])]
    return " ".join([value, *(_ANGLE_QUERY_TERMS[kind] for kind in missing)]).strip()


def _intent_source(cell: Mapping[str, Any]) -> str:
    source = cell.get("segment_source")
    return source if isinstance(source, str) and source in _INTENT_SOURCES else "rule_fallback"


def _term_intent(cell: Mapping[str, Any], kind: str) -> dict[str, Any]:
    terms = _intent_terms(cell.get(f"required_{kind}_terms", [])) or []
    source = _intent_source(cell) if terms else "not_requested"
    return {"terms": terms, "mode": "all" if cell.get(f"{kind}_match_mode") == "all" else "any",
            "source": source, "status": "specified" if terms else "not_requested",
            "required_in_query": bool(terms and source in _OPERATOR_SOURCES and cell.get("segment_locked") is True)}


def _target_evidence(cell: Mapping[str, Any]) -> list[dict[str, Any]]:
    output = []
    for kind in _intent_terms(cell.get("required_evidence_groups", [])) or []:
        source = _intent_source(cell)
        if kind == "market_activation":
            source = "workflow_objective"
        elif kind == "product_use_fit":
            source = "catalog" if cell.get("product_evidence_basis") == "resolved_product" else "operator_text"
        output.append({"kind": kind, "source": source, "stage": "post_retrieval",
                       "status": "not_evaluated", "eligibility_authority": "existing_qualification_policy"})
    return output


def build_query_cell_intent(cell: Mapping[str, Any], *, operator_query: Any, angle_scope: str = "cell") -> dict[str, Any]:
    """Build a server plan specification, never evidence that a creator qualifies."""
    angles = operator_query_angles(operator_query)
    return {"schema": QUERY_INTENT_SCHEMA, "contract_status": "specified",
            "scene": _term_intent(cell, "scene"), "role": _term_intent(cell, "role"),
            "angle": {"terms": angles, "mode": "all", "source": "operator_text" if angles else "not_requested",
                      "scope": angle_scope if angles else "not_requested",
                      "status": ("specified" if angle_scope == "cell" else "unresolved") if angles else "not_requested",
                      "required_in_query": bool(angles and angle_scope == "cell")},
            "target_evidence": _target_evidence(cell), "claim_status": "descriptive_only",
            "candidate_qualification": "not_evaluated"}


def _invalid_intent(reason: str) -> dict[str, Any]:
    return {"schema": QUERY_INTENT_SCHEMA, "contract_status": "invalid", "issues": [reason],
            "claim_status": "descriptive_only", "candidate_qualification": "not_evaluated"}


def _project_angle(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    terms = _intent_terms(value.get("terms"), maximum=5)
    source = value.get("source")
    if terms is None or any(term not in _ANGLE_ALIASES for term in terms) or not isinstance(source, str) or source not in _INTENT_SOURCES:
        return None
    if value.get("mode") not in ("all", "any") or value.get("scope") not in ("cell", "unresolved", "not_requested"):
        return None
    if terms and source == "not_requested":
        return None
    return {"terms": terms, "mode": value["mode"], "source": source if terms else "not_requested",
            "scope": value["scope"] if terms else "not_requested",
            "status": ("specified" if value["scope"] == "cell" else "unresolved") if terms else "not_requested",
            "required_in_query": bool(terms and source in _OPERATOR_SOURCES and value["scope"] == "cell")}


def project_query_cell_intent(cell: Mapping[str, Any]) -> dict[str, Any] | None:
    """Rebuild projections at the server-cell boundary; input verdicts have no authority.

    The queue/worker must first rebuild operator-owned cells. Source labels are
    provenance, not authentication. Missing legacy contracts stay unverified;
    malformed new contracts are retained as invalid, never downgraded to legacy.
    """
    if "query_intent" not in cell:
        return None
    raw = cell["query_intent"]
    if not isinstance(raw, Mapping) or raw.get("schema") != QUERY_INTENT_SCHEMA:
        return _invalid_intent("query_intent_schema_invalid")
    if raw.get("contract_status") == "invalid":
        return _invalid_intent("query_intent_previously_invalid")
    angle = _project_angle(raw.get("angle"))
    if angle is None:
        return _invalid_intent("query_angle_invalid")
    projected = build_query_cell_intent(cell, operator_query="")
    for kind in ("scene", "role"):
        if raw.get(kind) != projected[kind]:
            return _invalid_intent(f"query_{kind}_contract_mismatch")
    expected = projected["target_evidence"]
    if not expected or raw.get("target_evidence") != expected or any(item["kind"] not in _EVIDENCE_KINDS for item in expected):
        return _invalid_intent("query_target_evidence_invalid")
    projected["angle"] = angle
    return projected


def query_cell_intent_fields(cell: Mapping[str, Any]) -> dict[str, Any]:
    intent = project_query_cell_intent(cell)
    return {"query_intent": intent} if intent is not None else {}


def _term_coverage(field: Mapping[str, Any], query: str, kind: str) -> dict[str, Any]:
    terms = field.get("terms") or []
    matched = []
    for term in terms:
        aliases = _ANGLE_ALIASES.get(term, ()) if kind == "angle" else controlled_aliases_for(kind, term)
        if any(_phrase_present(query, alias) for alias in (aliases or (term,))):
            matched.append(term)
    satisfied = (len(matched) == len(terms) if field.get("mode") == "all" else bool(matched))
    return {"status": "not_requested" if not terms else "covered" if satisfied else "missing",
            "matched_terms": matched, "missing_terms": [term for term in terms if term not in matched],
            "mode": field.get("mode"), "source": field.get("source"),
            "required_in_query": field.get("required_in_query") is True,
            "evidence_verified": False}


def validate_query_cell_execution(cell: Mapping[str, Any], executed_query: Any) -> dict[str, Any]:
    """Check the final provider-bound query, not a plan's earlier validity claim."""
    result = {"schema": QUERY_EXECUTION_SCHEMA, "status": "legacy_unverified", "execution_allowed": True,
              "executed_query": _intent_text(executed_query), "coverage_scope": "query_intent_terms_only",
              "candidate_qualification": "not_evaluated", "target_evidence_status": "not_evaluated", "issues": []}
    intent = project_query_cell_intent(cell)
    if intent is None:
        return result
    if intent["contract_status"] == "invalid" or not result["executed_query"]:
        result.update(status="invalid", execution_allowed=False,
                      issues=list(intent.get("issues") or ["executed_query_missing"]))
        return result
    affirmative_query = affirmative_search_text(result["executed_query"])
    for kind in ("scene", "role", "angle"):
        coverage = _term_coverage(intent[kind], affirmative_query, kind)
        result[kind] = coverage
        if intent[kind].get("status") == "unresolved":
            coverage["status"] = "unresolved"
            result["issues"].append(f"{kind}_scope_unresolved")
        if coverage["status"] == "missing":
            result["issues"].append(f"{kind}_terms_missing_from_executed_query")
            if coverage["required_in_query"]:
                result["execution_allowed"] = False
    result["target_evidence"] = copy.deepcopy(intent["target_evidence"])
    result["status"] = "invalid" if not result["execution_allowed"] else "partial" if result["issues"] else "covered"
    return result


def summarize_query_cell_coverage(cells: Any, runs: Any = (), *, omitted_count: int = 0) -> dict[str, Any]:
    """Report each planned cell independently; query coverage is not qualification."""
    planned = cells if isinstance(cells, list) else []
    observations = runs if isinstance(runs, (list, tuple)) else []
    by_id: dict[str, list[Mapping[str, Any]]] = {}
    for run in observations[:32]:
        if isinstance(run, Mapping):
            by_id.setdefault(_intent_text(run.get("query_cell_id")), []).append(run)
    rows = []
    for cell in planned[:32]:
        if not isinstance(cell, Mapping):
            continue
        cell_id = _intent_text(cell.get("query_cell_id"))
        attempts = by_id.get(cell_id, [])
        dispatched = [run for run in attempts if isinstance(run.get("provider_calls"), int) and run["provider_calls"] > 0]
        checks = [validate_query_cell_execution(cell, run.get("executed_query")) for run in dispatched]
        status = "not_executed"
        if checks:
            status = "covered" if all(check["status"] == "covered" for check in checks) else "partial"
        elif any(run.get("status") == "not_executed_duplicate_query" for run in attempts):
            status = "not_executed_duplicate_query"
        elif attempts:
            status = "blocked_or_unverified"
        if "query_intent" not in cell:
            status = "legacy_unverified"
        rows.append({"query_cell_id": cell_id, "coverage_status": status,
                     "provider_runs_reported": len(dispatched), "retrieved_count": sum(
                         run.get("returned", 0) for run in dispatched if isinstance(run.get("returned"), int)),
                     "qualified_count": None, "candidate_qualification": "not_evaluated"})
    omitted = max(0, omitted_count) if isinstance(omitted_count, int) else 0
    return {"schema": "query_cell_coverage_v1", "cells": rows,
            "query_cells_requested": len(rows) + omitted, "query_cells_omitted": omitted,
            "query_cells_executed": sum(row["provider_runs_reported"] > 0 for row in rows),
            "status": "covered" if rows and not omitted and all(row["coverage_status"] == "covered" for row in rows) else "partial",
            "coverage_scope": "provider_reported_query_execution_not_creator_qualification",
            "claim_status": "descriptive_only"}


def normalize_search_branch(query: str, *, platform: str | None = None) -> dict[str, Any]:
    """Return comparison-only text. Preserve the original query verbatim.

    Remove only declared-platform/creator filler for comparison; retain order,
    numeric/product terms, negation, scene and angle words. Even comparison text
    is never returned as an instruction to rewrite the provider query.
    """
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query_missing_or_not_text")
    folded = unicodedata.normalize("NFKC", query).casefold()
    tokens = _TOKENS.findall(folded)
    filler = set(_ROLE_FILLER)
    if isinstance(platform, str) and platform.strip():
        platform_tokens = _TOKENS.findall(unicodedata.normalize("NFKC", platform).casefold())
        if len(platform_tokens) == 1:
            filler.add(platform_tokens[0])
    # Do not discard a negated role/platform or transform `-creator` to creator.
    kept_indices = {index for index, token in enumerate(tokens)
                    if token not in filler or (index and tokens[index - 1] in _NEGATION)}
    core = [token for index, token in enumerate(tokens) if index in kept_indices]
    return {"original_query": query, "normalized_query": " ".join(tokens),
            "comparison_query": " ".join(core), "comparison_tokens": core,
            "removed_filler_tokens": [token for index, token in enumerate(tokens) if index not in kept_indices]}


def _issue(code: str, severity: str, message: str, *, indices: Sequence[int] = ()) -> dict[str, Any]:
    return {"code": code, "severity": severity, "message": message,
            "branch_indices": list(indices)}


def _market(value: Any, supported_codes: Collection[str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    review = {"original_value": copy.deepcopy(value), "code": None,
              "status": "unknown", "dimension": "unspecified", "evidence_verified": False}
    if value is None or (isinstance(value, str) and value.strip().casefold() == "unknown"):
        return review, []
    if not isinstance(value, str) or not value.strip():
        review["status"] = "invalid"
        return review, [_issue("market_incomplete", "error", "Market must be one explicit supported code, null or unknown; no default is inferred.")]
    raw = value.strip()
    if re.fullmatch(r"[A-Za-z]{2}", raw) is None:
        review["status"] = "invalid"
        return review, [_issue("market_not_atomic_code", "error", "Market contains prose, multiple values or a fragment; it is not repaired by taking its prefix.")]
    code = raw.upper()
    code = "GB" if code == "UK" else code
    supported = {str(item).upper() for item in supported_codes}
    if code not in supported:
        review["status"] = "unrecognized"
        return review, [_issue("market_code_unrecognized", "warning", "Code is outside the caller's supported taxonomy; no market is guessed.")]
    review.update(code=code, status="specified")
    return review, []


def _metadata(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {key: unicodedata.normalize("NFKC", item).casefold().strip()
            for key in ("scene", "angle")
            if isinstance(item := value.get(key), str) and item.strip()}


def _angles(tokens: set[str]) -> set[str]:
    return {angle for angle, words in _ANGLE_TOKENS.items() if tokens & words}


def _pair(left: dict[str, Any], right: dict[str, Any], i: int, j: int) -> dict[str, Any] | None:
    a, b = left["comparison_tokens"], right["comparison_tokens"]
    if not a or not b:
        return None
    left_meta, right_meta = left["declared_intent"], right["declared_intent"]
    declared_difference = any(key in left_meta and key in right_meta and left_meta[key] != right_meta[key]
                              for key in ("scene", "angle"))
    equal_core = a == b
    if declared_difference:
        # Identical words do not demonstrate two declared intents. Preserve both
        # branches and request review, rather than discarding one as duplicate.
        if equal_core:
            return _issue("declared_intent_not_distinguished_in_query", "warning",
                          "Declared scene/angle differs but comparison text is identical; preserve both intents and review their queries.", indices=(i, j))
        return None
    if equal_core:
        return _issue("duplicate_query_branch", "error",
                      "Branch differs only by normalization or declared-platform/creator filler; original branches are preserved.", indices=(i, j))
    set_a, set_b = set(a), set(b)
    # Different explicit angles and negation/numeric terms are meaningful;
    # lexical similarity alone must not classify them as equivalent.
    angles_a, angles_b = _angles(set_a), _angles(set_b)
    if angles_a and angles_b and angles_a != angles_b:
        return None
    differences = set_a ^ set_b
    if any(token in _NEGATION or token.startswith("-") or any(char.isdigit() for char in token)
           for token in differences):
        return None
    intersection = set_a & set_b
    union = set_a | set_b
    lexical_overlap = len(intersection) >= 3 and len(intersection) / len(union) >= 0.75
    # The observed street/night versus street/low-light plan shares a broad
    # retrieval scene, not a proven synonym. Flag for review only; never replace
    # night with low light, delete either word, or claim semantic equivalence.
    def street_lowlight(tokens: set[str]) -> bool:
        return {"street", "photography"} <= tokens and (
            "night" in tokens or "low-light" in tokens or {"low", "light"} <= tokens)
    shared_street_lowlight = street_lowlight(set_a) and street_lowlight(set_b)
    if lexical_overlap or shared_street_lowlight:
        return _issue("possible_query_branch_overlap", "warning",
                      "Branches may repeat a retrieval scene without a distinct angle; similarity is not equivalence and no terms are removed.", indices=(i, j))
    if left_meta and left_meta == right_meta:
        return _issue("scene_angle_reused", "warning",
                      "The same declared scene/angle is reused; this is not proof of duplicate intent. Review the preserved query differences.", indices=(i, j))
    return None


def assess_search_plan_semantics(
    plan: Any,
    *,
    branch_metadata: Sequence[Mapping[str, Any]] | None = None,
    supported_market_codes: Collection[str] = DEFAULT_MARKET_CODES,
) -> dict[str, Any]:
    """Assess a schema-checked plan without changing plan or hard constraints.

    Status: ``invalid`` for definite malformed/duplicate fields, ``needs_review``
    for conservative overlap/taxonomy warnings, otherwise ``qualified`` for this
    limited plan check only. Unknown market stays unknown and is not evidence of
    satisfying an operator's requested market. The caller owns that constraint.
    ``branch_metadata`` is optional, index-aligned scene/angle intent declared by
    the caller; the function never extracts or invents it from user text.
    """
    result: dict[str, Any] = {
        "version": "search_plan_semantics_v1", "status": "invalid", "issues": [],
        "qualification_scope": "plan_fields_and_branch_distinction_not_creator_qualification",
        "original_plan": copy.deepcopy(plan), "branches": [], "market": None,
        "hard_constraints_modified": False, "queries_rewritten": False,
    }
    issues = result["issues"]
    if not isinstance(plan, Mapping):
        issues.append(_issue("plan_not_object", "error", "Expected a schema-checked plan object."))
        return result
    market, market_issues = _market(plan.get("market"), supported_market_codes)
    result["market"] = market
    issues.extend(market_issues)
    queries = plan.get("queries")
    if not isinstance(queries, list) or not queries or len(queries) > 32:
        issues.append(_issue("queries_missing_or_out_of_bounds", "error", "Expected 1 to 32 query branches."))
        return result
    if branch_metadata is not None and (not isinstance(branch_metadata, Sequence)
            or isinstance(branch_metadata, (str, bytes)) or len(branch_metadata) != len(queries)
            or any(not isinstance(item, Mapping) for item in branch_metadata)):
        issues.append(_issue("branch_metadata_misaligned", "error", "Scene/angle metadata must align with each original query."))
        return result
    for index, query in enumerate(queries):
        try:
            branch = normalize_search_branch(query, platform=plan.get("platform"))
        except ValueError:
            issues.append(_issue("query_missing_or_not_text", "error", "Query must contain text.", indices=(index,)))
            continue
        branch["index"] = index
        branch["declared_intent"] = _metadata(branch_metadata[index]) if branch_metadata is not None else {}
        result["branches"].append(branch)
        if not branch["comparison_tokens"]:
            issues.append(_issue("query_contains_only_filler", "error", "Query contains no topic after comparison-only filler removal.", indices=(index,)))
    branches = result["branches"]
    for i, left in enumerate(branches):
        for right in branches[i + 1:]:
            issue = _pair(left, right, left["index"], right["index"])
            if issue is not None:
                issues.append(issue)
    result["status"] = ("invalid" if any(item["severity"] == "error" for item in issues)
                        else "needs_review" if issues else "qualified")
    return result
