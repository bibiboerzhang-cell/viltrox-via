"""Provider-free robust retrieval and ranking for KOL profile recall.

The scores in this module are search ordering scores, not measured business or
model precision.  Missing observations are omitted and the remaining weights
are renormalised; they are never converted to zero-quality observations.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import math
import re
from typing import Any, Iterable

from app.db.connection import get_conn, is_postgres_runtime


logger = logging.getLogger(__name__)


ROBUST_RANK_VERSION = "kol_robust_rank_v1"
LEXICAL_METHOD = "lexical_idf_v1"
HYBRID_METHOD = "hybrid_weighted_rrf_v1"
RRF_K = 60
MAX_LEXICAL_TERMS = 24
MAX_QUERY_ROWS = 2_000

_ASCII_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*", re.IGNORECASE)
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
_FOCAL_RE = re.compile(r"^\d+(?:\.\d+)?mm$", re.IGNORECASE)
_STOPWORDS = {
    "and", "or", "the", "for", "with", "find", "creator", "creators",
    "kol", "达人", "寻找", "查找", "一个", "一些",
}
_PLATFORM_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("youtube", (r"\byoutube\b", r"\byoutuber\b", r"\byt\b", r"油管")),
    ("instagram", (r"\binstagram\b", r"\binsta\b", r"\big\b")),
    ("tiktok", (r"\btiktok\b", r"\btik[ -]?tok\b")),
    ("xiaohongshu", (r"小红书", r"\b(xiaohongshu|rednote)\b")),
    ("douyin", (r"抖音", r"\bdouyin\b")),
    ("bilibili", (r"哔哩哔哩", r"\bbilibili\b", r"b站")),
    ("facebook", (r"\bfacebook\b", r"\bfb\b")),
)
_SYNONYMS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("人像", "portrait"), ("人像", "portrait")),
    (("低光", "暗光", "lowlight", "low-light"), ("低光", "lowlight", "low-light")),
    (("街头", "街拍", "street"), ("街头", "街拍", "street")),
    (("摄影师", "photographer"), ("摄影师", "摄影", "photographer", "photography")),
    (("摄影", "photography"), ("摄影", "photography", "photographer")),
    (("测评", "评测", "reviewer", "review"), ("测评", "评测", "review", "reviewer")),
    (("镜头", "lens"), ("镜头", "lens")),
    (("视频", "videographer"), ("视频", "video", "videographer")),
    (("教程", "tutorial"), ("教程", "tutorial", "guide")),
    (("旅行", "travel"), ("旅行", "travel")),
    (("生活方式", "lifestyle"), ("生活方式", "lifestyle")),
    (("科技", "数码", "technology", "tech"), ("科技", "数码", "technology", "tech")),
)
_SCENE_TERMS = {
    "人像", "portrait", "低光", "lowlight", "low-light", "街头", "街拍", "street",
    "摄影", "摄影师", "photography", "photographer", "review", "reviewer", "测评", "评测",
    "video", "videographer", "视频", "tutorial", "教程", "travel", "旅行", "lifestyle",
    "生活方式", "camera", "lens", "镜头", "gear", "filmmaker", "filmmaking",
}
_MODEL_ANCHORS = {
    "evo", "lab", "air", "epic", "spark", "luna", "vintage",
    "z1", "z1pro", "z1-pro", "z2", "z3",
}
_PRODUCT_ANCHOR_GROUPS: tuple[
    tuple[str, tuple[str, ...], tuple[str, ...]], ...
] = (
    (
        "viltrox_brand",
        ("viltrox", "唯卓仕"),
        ("viltrox", "唯卓仕"),
    ),
    (
        "flash",
        ("flash", "speedlight", "strobe", "闪光灯"),
        ("flash", "camera flash", "speedlight", "strobe", "闪光灯", "闪光"),
    ),
    (
        "monitor",
        ("field monitor", "camera monitor", "monitor", "监视器", "监看器"),
        ("field monitor", "camera monitor", "monitor", "监视器", "监看器"),
    ),
)
_PRODUCT_ANCHOR_TERMS = {
    alias
    for _name, _triggers, aliases in _PRODUCT_ANCHOR_GROUPS
    for alias in aliases
}
_SOURCE_WEIGHTS = {
    "pool": 0.40,
    "evidence": 0.28,
    "analysis": 0.12,
    # Derived profile text is a soft hint only and can never prove a strict
    # product/focal anchor.
    "profile": 0.05,
}
_FACTUAL_SOURCES = frozenset({"pool", "evidence", "analysis"})


@dataclass(frozen=True)
class LexicalTerm:
    token: str
    sources: tuple[str, ...]
    weight: float
    category: str


def explicit_platforms_from_query(value: Any) -> list[str]:
    """Return only platforms literally named by the operator."""

    text = str(value or "").strip().lower()
    if not text:
        return []
    matched: list[str] = []
    for platform, patterns in _PLATFORM_PATTERNS:
        if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns):
            matched.append(platform)
    return matched


def query_requires_factual_anchor(*values: Any) -> bool:
    return bool(_query_anchor_groups(*values))


def _token_category(token: str) -> str:
    if _FOCAL_RE.match(token) or token in _MODEL_ANCHORS or token in _PRODUCT_ANCHOR_TERMS:
        return "anchor"
    if token in _SCENE_TERMS:
        return "scene"
    if token in {name for name, _patterns in _PLATFORM_PATTERNS}:
        return "platform"
    return "general"


def _base_tokens(text: str) -> list[str]:
    lowered = str(text or "").lower()
    tokens = [match.group(0).strip("._-") for match in _ASCII_TOKEN_RE.finditer(lowered)]
    for run in _CJK_RUN_RE.findall(lowered):
        tokens.append(run)
        if len(run) > 2:
            tokens.extend((run[:2], run[-2:]))
    return [token for token in tokens if len(token) >= 2 and token not in _STOPWORDS]


def _query_anchor_groups(*values: Any) -> tuple[frozenset[str], ...]:
    """Return independent factual gates, with aliases grouped as alternatives."""

    text = " ".join(str(value or "").strip().lower() for value in values if value not in (None, ""))
    if not text:
        return ()
    groups: list[frozenset[str]] = []
    focal_tokens = {
        f"{match.group(1)}mm"
        for match in re.finditer(r"(?<![a-z0-9])(\d+(?:\.\d+)?)\s*mm(?![a-z0-9])", text)
    }
    groups.extend(frozenset((token,)) for token in sorted(focal_tokens))
    raw_tokens = set(_base_tokens(text))
    groups.extend(
        frozenset((token,))
        for token in sorted(raw_tokens & _MODEL_ANCHORS)
    )
    for _name, triggers, aliases in _PRODUCT_ANCHOR_GROUPS:
        if any(_term_in_blob(trigger, text) for trigger in triggers):
            groups.append(frozenset(aliases))

    deduped: list[frozenset[str]] = []
    for group in groups:
        if group and group not in deduped:
            deduped.append(group)
    return tuple(deduped)


def build_lexical_terms(effective_query: Any, operator_query: Any = "") -> list[LexicalTerm]:
    """Fuse planner and operator text while retaining term provenance."""

    merged: dict[str, dict[str, Any]] = {}

    def _add(token: str, source: str, weight: float) -> None:
        normalized = str(token or "").strip().lower()
        if len(normalized) < 2 or normalized in _STOPWORDS:
            return
        slot = merged.setdefault(
            normalized,
            {"sources": set(), "weight": 0.0, "category": _token_category(normalized)},
        )
        slot["sources"].add(source)
        slot["weight"] = max(float(slot["weight"]), float(weight))

    effective = str(effective_query or "").strip()
    operator = str(operator_query or "").strip()
    for token in _base_tokens(effective):
        _add(token, "effective_query", 1.0)
    for token in _base_tokens(operator):
        _add(token, "operator_query", 1.25)

    synonym_text = f"{effective} {operator}".lower()
    for triggers, expansions in _SYNONYMS:
        if not any(trigger in synonym_text for trigger in triggers):
            continue
        for token in expansions:
            _add(token, "bilingual_synonym", 0.85)
    for _name, triggers, aliases in _PRODUCT_ANCHOR_GROUPS:
        if not any(_term_in_blob(trigger, synonym_text) for trigger in triggers):
            continue
        for token in aliases:
            _add(token, "product_anchor_alias", 1.10)
    # Canonicalise spaced focal lengths (for example ``26 mm``) and ensure
    # every independent factual gate is actually present in the recall terms.
    for group in _query_anchor_groups(effective, operator):
        for token in group:
            _add(token, "factual_anchor_alias", 1.15)
    for platform in explicit_platforms_from_query(operator):
        _add(platform, "operator_platform", 0.35)

    ordered = sorted(
        merged.items(),
        key=lambda pair: (
            pair[1]["category"] == "anchor",
            "operator_query" in pair[1]["sources"],
            float(pair[1]["weight"]),
            len(pair[0]),
        ),
        reverse=True,
    )[:MAX_LEXICAL_TERMS]
    return [
        LexicalTerm(
            token=token,
            sources=tuple(sorted(meta["sources"])),
            weight=round(float(meta["weight"]), 4),
            category=str(meta["category"]),
        )
        for token, meta in ordered
    ]


def _text_expression(fields: Iterable[str]) -> str:
    return " || ' ' || ".join(f"COALESCE({field}, '')" for field in fields)


def _matching_where(expression: str, terms: list[LexicalTerm]) -> tuple[str, tuple[str, ...]]:
    conditions = " OR ".join(f"LOWER({expression}) LIKE ?" for _term in terms)
    return conditions or "1=0", tuple(f"%{term.token}%" for term in terms)


def _term_in_blob(token: str, blob: str) -> bool:
    if token.isascii():
        return bool(
            re.search(
                rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])",
                blob,
                flags=re.IGNORECASE,
            )
        )
    return token in blob


def _collect_rows(
    conn: Any,
    *,
    source: str,
    sql_prefix: str,
    expression: str,
    sql_suffix: str,
    prefix_params: tuple[Any, ...],
    terms: list[LexicalTerm],
    limit: int,
    documents: dict[int, dict[str, list[str]]],
) -> None:
    where_sql, term_params = _matching_where(expression, terms)
    try:
        rows = conn.execute(
            f"{sql_prefix} AND ({where_sql}) {sql_suffix} LIMIT ?",
            (*prefix_params, *term_params, int(limit)),
        ).fetchall()
    except Exception:
        # One optional source table/column can lag without dropping the factual
        # sources that are available in this checkout.
        logger.debug("KOL lexical recall source %s is unavailable", source, exc_info=True)
        return
    for row in rows:
        payload = dict(row)
        try:
            kol_pool_id = int(payload.get("kol_pool_id") or 0)
        except (TypeError, ValueError):
            continue
        text = str(payload.get("search_text") or "").strip().lower()
        if kol_pool_id > 0 and text:
            documents.setdefault(kol_pool_id, {}).setdefault(source, []).append(text)


def lexical_recall_candidates(
    effective_query: Any,
    *,
    operator_query: Any = "",
    candidate_limit: int = 100,
    conn: Any | None = None,
    hard_filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a bounded, set-oriented lexical recall over factual and soft sources."""

    terms = build_lexical_terms(effective_query, operator_query)
    if not terms:
        return {"items": [], "terms": [], "method": LEXICAL_METHOD, "query_count": 0}
    active_conn = conn or get_conn()
    row_limit = max(100, min(MAX_QUERY_ROWS, int(candidate_limit or 100) * 12))
    documents: dict[int, dict[str, list[str]]] = {}
    query_count = 0
    filters = hard_filters if isinstance(hard_filters, dict) else {}
    hard_clauses: list[str] = []
    hard_params: list[Any] = []
    platforms = [str(value).strip().lower() for value in filters.get("platforms") or [] if str(value).strip()]
    if platforms:
        hard_clauses.append("LOWER(COALESCE(p.platform, '')) IN (" + ",".join("?" for _ in platforms) + ")")
        hard_params.extend(platforms)
    if filters.get("followers_min") not in (None, ""):
        hard_clauses.append("p.followers IS NOT NULL AND p.followers >= ?")
        hard_params.append(int(filters["followers_min"]))
    if filters.get("followers_max") not in (None, ""):
        hard_clauses.append("p.followers IS NOT NULL AND p.followers <= ?")
        hard_params.append(int(filters["followers_max"]))
    country_values = [
        str(value).strip().lower()
        for value in filters.get("_country_values") or filters.get("countries") or []
        if str(value).strip()
    ]
    if country_values:
        hard_clauses.append(
            "LOWER(COALESCE(p.country, '')) IN (" + ",".join("?" for _ in country_values) + ")"
        )
        hard_params.extend(country_values)
    language_values = [
        str(value).strip().lower()
        for value in filters.get("_language_values") or filters.get("languages") or []
        if str(value).strip()
    ]
    if language_values:
        hard_clauses.append(
            "(" + " OR ".join(
                "LOWER(COALESCE(p.language, ''))=? OR LOWER(COALESCE(p.language, '')) LIKE ?"
                for _ in language_values
            ) + ")"
        )
        for value in language_values:
            hard_params.extend((value, f"{value}-%"))
    hard_sql = "".join(f" AND {clause}" for clause in hard_clauses)

    pool_expression = _text_expression(
        ("p.platform", "p.handle", "p.display_name", "p.bio", "p.primary_topic", "p.content_style")
    )
    _collect_rows(
        active_conn,
        source="pool",
        sql_prefix=(
            f"SELECT p.id AS kol_pool_id, {pool_expression} AS search_text "
            f"FROM vkpi_kol_pool p WHERE p.duplicate_of_id IS NULL{hard_sql}"
        ),
        expression=pool_expression,
        sql_suffix="ORDER BY p.id DESC",
        prefix_params=tuple(hard_params),
        terms=terms,
        limit=row_limit,
        documents=documents,
    )
    query_count += 1

    profile_expression = _text_expression(("e.profile_text", "e.type_reason"))
    _collect_rows(
        active_conn,
        source="profile",
        sql_prefix=(
            f"SELECT e.kol_pool_id, {profile_expression} AS search_text "
            "FROM vkpi_kol_profile_index_entries e "
            "JOIN vkpi_kol_pool p ON p.id=e.kol_pool_id "
            f"WHERE p.duplicate_of_id IS NULL{hard_sql} "
            "AND e.collection_name=? AND e.method=? AND e.status='ready'"
        ),
        expression=profile_expression,
        sql_suffix="ORDER BY e.kol_pool_id DESC",
        prefix_params=(*hard_params, "vkpi_kol_profile_index_v1", "vector_recall"),
        terms=terms,
        limit=row_limit,
        documents=documents,
    )
    query_count += 1

    evidence_expression = _text_expression(("e.title", "e.video_title", "e.content_url"))
    active_predicate = "e.is_active IS NOT FALSE" if is_postgres_runtime() else "COALESCE(e.is_active, 1) != 0"
    _collect_rows(
        active_conn,
        source="evidence",
        sql_prefix=(
            f"SELECT e.kol_pool_id, {evidence_expression} AS search_text "
            "FROM vkpi_kol_video_evidence e "
            "JOIN vkpi_kol_pool p ON p.id=e.kol_pool_id "
            f"WHERE p.duplicate_of_id IS NULL{hard_sql} AND {active_predicate}"
        ),
        expression=evidence_expression,
        sql_suffix="ORDER BY e.kol_pool_id DESC, e.id DESC",
        prefix_params=tuple(hard_params),
        terms=terms,
        limit=row_limit,
        documents=documents,
    )
    query_count += 1

    if is_postgres_runtime():
        analysis_expression = _text_expression(
            (
                "c.result #>> '{layer1_visual_content,content_summary}'",
                "c.result #>> '{layer1_visual_content,product_presence}'",
                "c.result #>> '{layer1_visual_content,brand_exposure}'",
                "c.result #>> '{raw_gemini_video,viltrox_products_all}'",
            )
        )
    else:
        analysis_expression = "CAST(c.result AS TEXT)"
    # final_v1 is a factual video-analysis source.  Keep this query bounded to
    # anchor/scene terms so generic planner terms cannot scan the whole cache.
    analysis_terms = [term for term in terms if term.category in {"anchor", "scene"}]
    if analysis_terms:
        _collect_rows(
            active_conn,
            source="analysis",
            sql_prefix=(
                f"SELECT e.kol_pool_id, {analysis_expression} AS search_text "
                "FROM vkpi_analysis_cache c "
                "JOIN vkpi_kol_video_evidence e ON c.target_type='video' AND c.target_id="
                + ("e.id::text " if is_postgres_runtime() else "CAST(e.id AS TEXT) ")
                + "JOIN vkpi_kol_pool p ON p.id=e.kol_pool_id "
                f"WHERE p.duplicate_of_id IS NULL{hard_sql} "
                "AND c.derive_method='video_analysis_final_v1' "
                "AND c.status='ready'"
            ),
            expression=analysis_expression,
            sql_suffix="ORDER BY e.kol_pool_id DESC, e.id DESC",
            prefix_params=tuple(hard_params),
            terms=analysis_terms,
            limit=row_limit,
            documents=documents,
        )
        query_count += 1

    if not documents:
        return {
            "items": [],
            "terms": [term.__dict__ for term in terms],
            "method": LEXICAL_METHOD,
            "query_count": query_count,
        }

    doc_tokens: dict[int, dict[str, set[str]]] = {}
    document_frequency = {term.token: 0 for term in terms}
    for kol_pool_id, source_texts in documents.items():
        doc_tokens[kol_pool_id] = {}
        all_matched: set[str] = set()
        for source, texts in source_texts.items():
            blob = " ".join(texts)
            matched = {term.token for term in terms if _term_in_blob(term.token, blob)}
            doc_tokens[kol_pool_id][source] = matched
            all_matched.update(matched)
        for token in all_matched:
            document_frequency[token] += 1

    population = len(documents)
    idf = {
        token: math.log((population + 1.0) / (frequency + 0.5)) + 1.0
        for token, frequency in document_frequency.items()
    }
    denominator = sum(idf[term.token] * term.weight for term in terms if term.category != "platform") or 1.0
    term_by_token = {term.token: term for term in terms}
    anchor_groups = _query_anchor_groups(effective_query, operator_query)
    anchor_tokens = set().union(*anchor_groups) if anchor_groups else set()
    scene_tokens = {term.token for term in terms if term.category == "scene"}
    items: list[dict[str, Any]] = []
    for kol_pool_id, source_matches in doc_tokens.items():
        source_scores: dict[str, float] = {}
        factual_matched: set[str] = set()
        all_matched: set[str] = set()
        available_source_weight = 0.0
        weighted_score = 0.0
        for source, matched in source_matches.items():
            if not matched:
                continue
            source_coverage = sum(idf[token] * term_by_token[token].weight for token in matched) / denominator
            source_score = min(1.0, source_coverage)
            source_scores[source] = round(source_score, 6)
            source_weight = _SOURCE_WEIGHTS[source]
            weighted_score += source_weight * source_score
            available_source_weight += source_weight
            all_matched.update(matched)
            if source in _FACTUAL_SOURCES:
                factual_matched.update(matched)
        # Keep absolute source caps.  In particular, a profile-only match stays
        # capped near 0.05 instead of renormalising its soft source to 1.0.
        source_weight_total = sum(_SOURCE_WEIGHTS.values()) or 1.0
        lexical_score = weighted_score / source_weight_total if available_source_weight else None
        if lexical_score is None:
            continue
        factual_anchor = sorted(anchor_tokens & factual_matched)
        factual_scene = sorted(scene_tokens & factual_matched)
        if anchor_groups:
            factual_anchor_gate = all(group & factual_matched for group in anchor_groups)
            strict = bool(factual_anchor_gate and (not scene_tokens or factual_scene))
            relaxed_reason = "missing_factual_anchor_or_scene" if not strict else ""
        else:
            factual_non_platform = {
                token for token in factual_matched if term_by_token[token].category != "platform"
            }
            strict = bool(factual_scene and len(factual_non_platform) >= 2)
            relaxed_reason = "derived_or_single_factual_term_only" if not strict else ""
        items.append(
            {
                "kol_pool_id": kol_pool_id,
                "lexical_score": round(float(lexical_score), 6),
                "retrieval_method": LEXICAL_METHOD,
                "retrieval_tier": "strict" if strict else "relaxed",
                "relaxed_reason": relaxed_reason,
                "matched_terms": sorted(all_matched),
                "factual_matched_terms": sorted(factual_matched),
                "factual_anchor_terms": factual_anchor,
                "required_factual_anchor_groups": [sorted(group) for group in anchor_groups],
                "factual_scene_terms": factual_scene,
                "matched_term_sources": {
                    token: list(term_by_token[token].sources) for token in sorted(all_matched)
                },
                "source_scores": source_scores,
                "derived_profile_strict_eligible": False,
            }
        )
    items.sort(
        key=lambda item: (
            item["retrieval_tier"] == "strict",
            float(item["lexical_score"]),
            len(item["factual_matched_terms"]),
            -int(item["kol_pool_id"]),
        ),
        reverse=True,
    )
    return {
        "items": items[: max(1, min(500, int(candidate_limit or 100)))],
        "terms": [term.__dict__ for term in terms],
        "method": LEXICAL_METHOD,
        "query_count": query_count,
        "idf_scope": "bounded_candidate_documents",
        "derived_profile_weight": _SOURCE_WEIGHTS["profile"],
        "strict_anchor_sources": sorted(_FACTUAL_SOURCES),
    }


def _number(value: Any, *, minimum: float | None = None) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or (minimum is not None and parsed < minimum):
        return None
    return parsed


def missingness_aware_weighted_score(
    components: Iterable[tuple[str, float | None, float]],
) -> tuple[float | None, list[str], float]:
    """Weighted mean over observed signals, plus missing names and coverage."""

    rows = list(components)
    total_weight = sum(max(0.0, float(weight)) for _name, _value, weight in rows)
    observed = [(name, value, weight) for name, value, weight in rows if value is not None and weight > 0]
    observed_weight = sum(float(weight) for _name, _value, weight in observed)
    if observed_weight <= 0:
        return None, [name for name, _value, _weight in rows], 0.0
    score = sum(float(value) * float(weight) for _name, value, weight in observed) / observed_weight
    missing = [name for name, value, _weight in rows if value is None]
    coverage = observed_weight / total_weight if total_weight > 0 else 0.0
    return round(score, 6), missing, round(coverage, 6)


def _percentiles(values: list[tuple[int, float]]) -> dict[int, float]:
    if not values:
        return {}
    ordered = sorted(values, key=lambda pair: (pair[1], pair[0]))
    if len(ordered) == 1:
        return {ordered[0][0]: 0.5}
    output: dict[int, float] = {}
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        midpoint = (index + end - 1) / 2.0
        percentile = midpoint / (len(ordered) - 1)
        for row_index in range(index, end):
            output[ordered[row_index][0]] = round(percentile, 6)
        index = end
    return output


def apply_robust_ranking(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Add platform-calibrated ordering and evidence-confidence contracts."""

    by_platform: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        platform = str(item.get("platform") or "unknown").strip().lower() or "unknown"
        by_platform.setdefault(platform, []).append(item)

    calibrated: dict[int, dict[str, float]] = {}
    for platform_items in by_platform.values():
        metric_values: dict[str, list[tuple[int, float]]] = {
            "retrieval": [], "avg_views": [], "engagement": [], "view_rate": [], "comment_rate": [],
        }
        for index, item in enumerate(platform_items):
            retrieval = _number(item.get("retrieval_score"), minimum=0.0)
            avg_views = _number(item.get("avg_views"), minimum=0.0)
            engagement = _number(item.get("engagement_rate"), minimum=0.0)
            followers = _number(item.get("followers"), minimum=0.0)
            avg_comments = _number(item.get("avg_comments"), minimum=0.0)
            if retrieval is not None:
                metric_values["retrieval"].append((index, retrieval))
            if avg_views is not None:
                metric_values["avg_views"].append((index, avg_views))
            if engagement is not None:
                metric_values["engagement"].append((index, engagement))
            if followers and avg_views is not None:
                metric_values["view_rate"].append((index, avg_views / followers))
            if followers and avg_comments is not None:
                metric_values["comment_rate"].append((index, avg_comments / followers))
        percentiles = {metric: _percentiles(values) for metric, values in metric_values.items()}
        for index, item in enumerate(platform_items):
            calibrated[id(item)] = {
                metric: mapping[index] for metric, mapping in percentiles.items() if index in mapping
            }

    confidence_counts = {"high": 0, "medium": 0, "low": 0}
    abstain_count = 0
    for item in items:
        platform_values = calibrated.get(id(item), {})
        quality_score, quality_missing, _quality_coverage = missingness_aware_weighted_score(
            (
                ("avg_views", platform_values.get("avg_views"), 0.40),
                ("engagement_rate", platform_values.get("engagement"), 0.30),
                ("view_rate", platform_values.get("view_rate"), 0.20),
                ("comment_rate", platform_values.get("comment_rate"), 0.10),
            )
        )
        evidence = item.get("evidence_quality") if isinstance(item.get("evidence_quality"), dict) else {}
        video_count = int(_number(evidence.get("video_evidence_count"), minimum=0.0) or 0)
        deep_count = int(_number(evidence.get("deep_analysis_count"), minimum=0.0) or 0)
        evidence_coverage = None
        if video_count > 0:
            # Coverage only; deliberately capped so a 120-video account cannot
            # swamp query relevance or masquerade as accuracy.
            evidence_coverage = min(1.0, 0.7 * min(video_count, 5) / 5 + 0.3 * min(deep_count, 3) / 3)
        type_score = _number(item.get("type_rank_score"), minimum=0.0)
        if type_score is not None:
            type_score /= 100.0
        retrieval_score = _number(item.get("retrieval_score"), minimum=0.0)
        robust_score, missing_signals, signal_coverage = missingness_aware_weighted_score(
            (
                ("retrieval", retrieval_score, 0.62),
                ("platform_retrieval_percentile", platform_values.get("retrieval"), 0.08),
                ("type", type_score, 0.10),
                ("platform_quality", quality_score, 0.10),
                ("evidence_coverage", evidence_coverage, 0.10),
            )
        )
        sufficiency = str((item.get("source_fields") or {}).get("sufficiency") or "").strip().lower()
        sufficiency_confidence = (
            1.0 if sufficiency in {"high", "ready", "sufficient", "complete"}
            else 0.65 if sufficiency in {"medium", "partial", "provisional"}
            else 0.35 if sufficiency in {"low", "insufficient"}
            else 0.5
        )
        evidence_confidence = min(1.0, (min(video_count, 5) / 5) * 0.7 + (min(deep_count, 3) / 3) * 0.3)
        confidence_score = round(
            min(1.0, 0.65 * signal_coverage + 0.25 * sufficiency_confidence + 0.10 * evidence_confidence),
            6,
        )
        confidence_level = "high" if confidence_score >= 0.75 else "medium" if confidence_score >= 0.45 else "low"
        confidence_counts[confidence_level] += 1
        tier = str(item.get("match_tier") or "backfill")
        if retrieval_score is None or tier == "backfill":
            decision_mode = "abstain"
            recommendation_status = "insufficient_query_evidence"
            abstain_count += 1
        elif tier == "relaxed" or confidence_level == "low":
            decision_mode = "human_review_required"
            recommendation_status = "provisional"
        else:
            decision_mode = "human_decision_support"
            recommendation_status = "ranked_search_candidate"
        item["platform_calibration"] = {
            "method": "within_platform_empirical_percentile",
            "scope": "eligible_query_candidates",
            "values": platform_values,
            "quality_score": quality_score,
            "missing_quality_signals": quality_missing,
        }
        item["robust_rank_score"] = robust_score
        item["robust_rank_method"] = ROBUST_RANK_VERSION
        item["ranking_claim_status"] = "descriptive_only"
        item["ranking_confidence"] = {
            "score": confidence_score,
            "level": confidence_level,
            "decision_mode": decision_mode,
            "recommendation_status": recommendation_status,
            "missing_signals": missing_signals,
            "signal_coverage": signal_coverage,
            "note": "排序证据置信度，不是预测准确率或业务结果。",
        }
        relevance_adjust = _number(item.get("display_relevance_adjust")) or 0.0
        item["display_rank_score"] = (
            round(float(robust_score) + relevance_adjust, 6) if robust_score is not None else None
        )
    return {
        "version": ROBUST_RANK_VERSION,
        "confidence_counts": confidence_counts,
        "abstain_count": abstain_count,
        "platform_count": len(by_platform),
        "missing_value_policy": "omit_and_renormalize_never_zero_impute",
        "followers_policy": "reach_gate_and_tie_break_only",
        "evidence_influence_cap": 0.10,
        "claim_status": "descriptive_only",
    }


def ranking_key(item: dict[str, Any]) -> tuple[float, float, float, int]:
    tier_order = {"strict": 2.0, "relaxed": 1.0, "backfill": 0.0}
    robust = _number(item.get("display_rank_score"))
    confidence = _number((item.get("ranking_confidence") or {}).get("score"))
    followers = int(_number(item.get("followers"), minimum=0.0) or 0)
    return (
        tier_order.get(str(item.get("match_tier") or "backfill"), 0.0),
        robust if robust is not None else -1.0,
        confidence if confidence is not None else 0.0,
        followers,
    )


def select_with_business_lane_quotas(
    items: list[dict[str, Any]],
    *,
    limit: int,
    bucket_policy: dict[str, int],
    creator_quota: int,
    reviewer_quota: int,
    allow_backfill: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Make business-lane targets real while profile type remains a soft balance."""

    eligible = [item for item in items if allow_backfill or item.get("match_tier") != "backfill"]
    eligible.sort(key=ranking_key, reverse=True)
    lane_order = ("core_vertical", "expansion", "exploration")
    selected: list[dict[str, Any]] = []
    selected_ids: set[int] = set()
    profile_counts = {"creator": 0, "reviewer": 0, "unknown": 0}

    def _take(pool: list[dict[str, Any]], count: int) -> None:
        for _index in range(max(0, count)):
            remaining = [row for row in pool if int(row.get("kol_pool_id") or 0) not in selected_ids]
            if not remaining or len(selected) >= limit:
                return
            creator_need = max(0, creator_quota - profile_counts["creator"])
            reviewer_need = max(0, reviewer_quota - profile_counts["reviewer"])
            preferred = "creator" if creator_need > reviewer_need else "reviewer" if reviewer_need > 0 else ""
            candidates = [row for row in remaining if row.get("bucket") == preferred] if preferred else []
            chosen = max(candidates or remaining, key=ranking_key)
            selected.append(chosen)
            selected_ids.add(int(chosen.get("kol_pool_id") or 0))
            lane = str(chosen.get("bucket") or "unknown")
            profile_counts[lane if lane in profile_counts else "unknown"] += 1

    lane_pools = {
        lane: [item for item in eligible if item.get("candidate_bucket") == lane]
        for lane in lane_order
    }
    lane_available = {lane: len(pool) for lane, pool in lane_pools.items()}

    # Core and expansion are minimum diversity targets. Exploration is a
    # maximum backfill budget, never a mandatory quota: do not inject broad
    # candidates when enough stricter core/adjacent evidence already exists.
    _take(lane_pools["core_vertical"], int(bucket_policy.get("core_vertical") or 0))
    _take(lane_pools["expansion"], int(bucket_policy.get("expansion") or 0))
    non_exploration = lane_pools["core_vertical"] + lane_pools["expansion"]
    _take(non_exploration, max(0, limit - len(selected)))
    _take(
        lane_pools["exploration"],
        min(
            max(0, limit - len(selected)),
            int(bucket_policy.get("exploration") or 0),
        ),
    )
    # If evidence supply is below the policy minima, still preserve the 30-person
    # count contract via explicit overflow. Diagnostics keep that lane violation
    # visible; no item is relabelled as core or expansion.
    _take(eligible, max(0, limit - len(selected)))
    selected.sort(key=ranking_key, reverse=True)
    lane_selected = {
        lane: sum(1 for item in selected if item.get("candidate_bucket") == lane)
        for lane in lane_order
    }
    lane_targets = {lane: int(bucket_policy.get(lane) or 0) for lane in lane_order}
    lane_shortfalls = {
        "core_vertical": max(0, lane_targets["core_vertical"] - lane_selected["core_vertical"]),
        "expansion": max(0, lane_targets["expansion"] - lane_selected["expansion"]),
        "exploration": 0,
    }
    exploration_overflow = max(
        0,
        lane_selected["exploration"] - lane_targets["exploration"],
    )
    lane_refills = {
        lane: max(0, lane_selected[lane] - lane_targets[lane])
        for lane in lane_order
    }
    return selected, {
        "lane_targets": lane_targets,
        "lane_available": lane_available,
        "lane_selected": lane_selected,
        "lane_shortfalls": lane_shortfalls,
        "lane_refills": lane_refills,
        "lane_contract_satisfied": (
            all(value == 0 for value in lane_shortfalls.values())
            and exploration_overflow == 0
            and len(selected) == limit
        ),
        "exploration_overflow": exploration_overflow,
        "lane_policy": {
            "core_vertical": "minimum",
            "expansion": "minimum",
            "exploration": "maximum_backfill_only",
        },
        "profile_counts": profile_counts,
        "profile_balance_policy": "soft_secondary_after_business_lane_targets",
    }
