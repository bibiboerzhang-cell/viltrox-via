"""Lexical recall orchestration and scoring extracted from the public facade."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Callable, Iterable, Mapping


@dataclass(frozen=True)
class LexicalRuntime:
    build_terms: Callable[[Any, Any], list[Any]]
    get_conn: Callable[[], Any]
    country_hard_filter: Callable[[dict[str, Any]], tuple[str, list[Any]]]
    language_hard_filter: Callable[..., tuple[str, list[Any]]]
    table_columns: Callable[..., Any]
    is_postgres_runtime: Callable[[], bool]
    text_expression: Callable[[Iterable[str]], str]
    collect_rows: Callable[..., None]
    query_anchor_groups: Callable[..., tuple[frozenset[str], ...]]
    term_in_blob: Callable[[str, str], bool]
    platform_predicate_prefix: str
    lexical_method: str
    max_query_rows: int
    source_weights: Mapping[str, float]
    factual_sources: frozenset[str]


def _terms_payload(terms: list[Any]) -> list[dict[str, Any]]:
    return [term.__dict__ for term in terms]


def _hard_filter_sql(
    filters: dict[str, Any],
    active_conn: Any,
    runtime: LexicalRuntime,
) -> tuple[str, tuple[Any, ...]]:
    clauses: list[str] = []
    params: list[Any] = []
    platforms = [
        str(value).strip().lower()
        for value in filters.get("platforms") or []
        if str(value).strip()
    ]
    if platforms:
        clauses.append(
            runtime.platform_predicate_prefix
            + ",".join("?" for _value in platforms)
            + ")"
        )
        params.extend(platforms)
    if filters.get("followers_min") not in (None, ""):
        clauses.append("p.followers IS NOT NULL AND p.followers >= ?")
        params.append(int(filters["followers_min"]))
    if filters.get("followers_max") not in (None, ""):
        clauses.append("p.followers IS NOT NULL AND p.followers <= ?")
        params.append(int(filters["followers_max"]))
    country_clause, country_params = runtime.country_hard_filter(filters)
    if country_clause:
        clauses.append(country_clause)
        params.extend(country_params)
    language_clause, language_params = runtime.language_hard_filter(
        filters,
        active_conn,
        runtime.table_columns,
    )
    if language_clause:
        clauses.append(language_clause)
        params.extend(language_params)
    return "".join(f" AND {clause}" for clause in clauses), tuple(params)


def _collect_base_sources(
    active_conn: Any,
    *,
    terms: list[Any],
    hard_sql: str,
    hard_params: tuple[Any, ...],
    row_limit: int,
    documents: dict[int, dict[str, list[str]]],
    runtime: LexicalRuntime,
) -> None:
    pool_expression = runtime.text_expression(
        (
            "p.platform",
            "p.handle",
            "p.display_name",
            "p.bio",
            "p.primary_topic",
            "p.content_style",
        )
    )
    runtime.collect_rows(
        active_conn,
        source="pool",
        sql_prefix=(
            f"SELECT p.id AS kol_pool_id, {pool_expression} AS search_text "
            f"FROM vkpi_kol_pool p WHERE p.duplicate_of_id IS NULL{hard_sql}"
        ),
        expression=pool_expression,
        sql_suffix="ORDER BY p.id DESC",
        prefix_params=hard_params,
        terms=terms,
        limit=row_limit,
        documents=documents,
    )
    profile_expression = runtime.text_expression(("e.profile_text", "e.type_reason"))
    runtime.collect_rows(
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
        prefix_params=(
            *hard_params,
            "vkpi_kol_profile_index_v1",
            "vector_recall",
        ),
        terms=terms,
        limit=row_limit,
        documents=documents,
    )
    evidence_expression = runtime.text_expression(
        ("e.title", "e.video_title", "e.content_url")
    )
    active_predicate = (
        "e.is_active IS NOT FALSE"
        if runtime.is_postgres_runtime()
        else "COALESCE(e.is_active, 1) != 0"
    )
    runtime.collect_rows(
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
        prefix_params=hard_params,
        terms=terms,
        limit=row_limit,
        documents=documents,
    )


def _analysis_expression(runtime: LexicalRuntime) -> str:
    if not runtime.is_postgres_runtime():
        return "CAST(c.result AS TEXT)"
    return runtime.text_expression(
        (
            "c.result #>> '{layer1_visual_content,content_summary}'",
            "c.result #>> '{layer1_visual_content,product_presence}'",
            "c.result #>> '{layer1_visual_content,brand_exposure}'",
            "c.result #>> '{raw_gemini_video,viltrox_products_all}'",
        )
    )


def _collect_analysis_source(
    active_conn: Any,
    *,
    terms: list[Any],
    hard_sql: str,
    hard_params: tuple[Any, ...],
    row_limit: int,
    documents: dict[int, dict[str, list[str]]],
    runtime: LexicalRuntime,
) -> bool:
    analysis_terms = [term for term in terms if term.category in {"anchor", "scene"}]
    if not analysis_terms:
        return False
    expression = _analysis_expression(runtime)
    target_id_expression = (
        "e.id::text "
        if runtime.is_postgres_runtime()
        else "CAST(e.id AS TEXT) "
    )
    runtime.collect_rows(
        active_conn,
        source="analysis",
        sql_prefix=(
            f"SELECT e.kol_pool_id, {expression} AS search_text "
            "FROM vkpi_analysis_cache c "
            "JOIN vkpi_kol_video_evidence e ON c.target_type='video' AND c.target_id="
            + target_id_expression
            + "JOIN vkpi_kol_pool p ON p.id=e.kol_pool_id "
            + f"WHERE p.duplicate_of_id IS NULL{hard_sql} "
            + "AND c.derive_method='video_analysis_final_v1' "
            + "AND c.status='ready'"
        ),
        expression=expression,
        sql_suffix="ORDER BY e.kol_pool_id DESC, e.id DESC",
        prefix_params=hard_params,
        terms=analysis_terms,
        limit=row_limit,
        documents=documents,
    )
    return True


def _document_tokens(
    documents: dict[int, dict[str, list[str]]],
    terms: list[Any],
    runtime: LexicalRuntime,
) -> tuple[dict[int, dict[str, set[str]]], dict[str, int]]:
    output: dict[int, dict[str, set[str]]] = {}
    frequency = {term.token: 0 for term in terms}
    for kol_pool_id, source_texts in documents.items():
        output[kol_pool_id] = {}
        all_matched: set[str] = set()
        for source, texts in source_texts.items():
            blob = " ".join(texts)
            matched = {
                term.token
                for term in terms
                if runtime.term_in_blob(term.token, blob)
            }
            output[kol_pool_id][source] = matched
            all_matched.update(matched)
        for token in all_matched:
            frequency[token] += 1
    return output, frequency


def _strict_decision(
    *,
    anchor_groups: tuple[frozenset[str], ...],
    scene_tokens: set[str],
    factual_matched: set[str],
    term_by_token: dict[str, Any],
) -> tuple[bool, str]:
    factual_scene = scene_tokens & factual_matched
    if anchor_groups:
        anchor_gate = all(group & factual_matched for group in anchor_groups)
        strict = bool(anchor_gate and (not scene_tokens or factual_scene))
        return strict, "" if strict else "missing_factual_anchor_or_scene"
    factual_non_platform = {
        token
        for token in factual_matched
        if term_by_token[token].category != "platform"
    }
    strict = bool(factual_scene and len(factual_non_platform) >= 2)
    return strict, "" if strict else "derived_or_single_factual_term_only"


def _score_document(
    kol_pool_id: int,
    source_matches: dict[str, set[str]],
    *,
    idf: dict[str, float],
    denominator: float,
    term_by_token: dict[str, Any],
    anchor_groups: tuple[frozenset[str], ...],
    anchor_tokens: set[str],
    scene_tokens: set[str],
    runtime: LexicalRuntime,
) -> dict[str, Any] | None:
    source_scores: dict[str, float] = {}
    factual_matched: set[str] = set()
    all_matched: set[str] = set()
    available_source_weight = 0.0
    weighted_score = 0.0
    for source, matched in source_matches.items():
        if not matched:
            continue
        coverage = sum(
            idf[token] * term_by_token[token].weight for token in matched
        ) / denominator
        source_score = min(1.0, coverage)
        source_scores[source] = round(source_score, 6)
        source_weight = runtime.source_weights[source]
        weighted_score += source_weight * source_score
        available_source_weight += source_weight
        all_matched.update(matched)
        if source in runtime.factual_sources:
            factual_matched.update(matched)
    if not available_source_weight:
        return None
    source_weight_total = sum(runtime.source_weights.values()) or 1.0
    lexical_score = weighted_score / source_weight_total
    strict, relaxed_reason = _strict_decision(
        anchor_groups=anchor_groups,
        scene_tokens=scene_tokens,
        factual_matched=factual_matched,
        term_by_token=term_by_token,
    )
    return {
        "kol_pool_id": kol_pool_id,
        "lexical_score": round(float(lexical_score), 6),
        "retrieval_method": runtime.lexical_method,
        "retrieval_tier": "strict" if strict else "relaxed",
        "relaxed_reason": relaxed_reason,
        "matched_terms": sorted(all_matched),
        "factual_matched_terms": sorted(factual_matched),
        "factual_anchor_terms": sorted(anchor_tokens & factual_matched),
        "required_factual_anchor_groups": [sorted(group) for group in anchor_groups],
        "factual_scene_terms": sorted(scene_tokens & factual_matched),
        "matched_term_sources": {
            token: list(term_by_token[token].sources)
            for token in sorted(all_matched)
        },
        "source_scores": source_scores,
        "derived_profile_strict_eligible": False,
    }


def _rank_documents(
    documents: dict[int, dict[str, list[str]]],
    terms: list[Any],
    *,
    effective_query: Any,
    operator_query: Any,
    runtime: LexicalRuntime,
) -> list[dict[str, Any]]:
    tokens, frequency = _document_tokens(documents, terms, runtime)
    population = len(documents)
    idf = {
        token: math.log((population + 1.0) / (count + 0.5)) + 1.0
        for token, count in frequency.items()
    }
    denominator = sum(
        idf[term.token] * term.weight
        for term in terms
        if term.category != "platform"
    ) or 1.0
    term_by_token = {term.token: term for term in terms}
    anchor_groups = runtime.query_anchor_groups(effective_query, operator_query)
    anchor_tokens = set().union(*anchor_groups) if anchor_groups else set()
    scene_tokens = {term.token for term in terms if term.category == "scene"}
    items = [
        item
        for kol_pool_id, source_matches in tokens.items()
        if (
            item := _score_document(
                kol_pool_id,
                source_matches,
                idf=idf,
                denominator=denominator,
                term_by_token=term_by_token,
                anchor_groups=anchor_groups,
                anchor_tokens=anchor_tokens,
                scene_tokens=scene_tokens,
                runtime=runtime,
            )
        )
        is not None
    ]
    items.sort(
        key=lambda item: (
            item["retrieval_tier"] == "strict",
            float(item["lexical_score"]),
            len(item["factual_matched_terms"]),
            -int(item["kol_pool_id"]),
        ),
        reverse=True,
    )
    return items


def lexical_recall_candidates(
    effective_query: Any,
    *,
    operator_query: Any,
    candidate_limit: int,
    conn: Any | None,
    hard_filters: dict[str, Any] | None,
    runtime: LexicalRuntime,
) -> dict[str, Any]:
    terms = runtime.build_terms(effective_query, operator_query)
    if not terms:
        return {
            "items": [],
            "terms": [],
            "method": runtime.lexical_method,
            "query_count": 0,
        }
    active_conn = conn or runtime.get_conn()
    row_limit = max(
        100,
        min(runtime.max_query_rows, int(candidate_limit or 100) * 12),
    )
    filters = hard_filters if isinstance(hard_filters, dict) else {}
    hard_sql, hard_params = _hard_filter_sql(filters, active_conn, runtime)
    documents: dict[int, dict[str, list[str]]] = {}
    _collect_base_sources(
        active_conn,
        terms=terms,
        hard_sql=hard_sql,
        hard_params=hard_params,
        row_limit=row_limit,
        documents=documents,
        runtime=runtime,
    )
    query_count = 3 + int(
        _collect_analysis_source(
            active_conn,
            terms=terms,
            hard_sql=hard_sql,
            hard_params=hard_params,
            row_limit=row_limit,
            documents=documents,
            runtime=runtime,
        )
    )
    if not documents:
        return {
            "items": [],
            "terms": _terms_payload(terms),
            "method": runtime.lexical_method,
            "query_count": query_count,
        }
    items = _rank_documents(
        documents,
        terms,
        effective_query=effective_query,
        operator_query=operator_query,
        runtime=runtime,
    )
    return {
        "items": items[: max(1, min(500, int(candidate_limit or 100)))],
        "terms": _terms_payload(terms),
        "method": runtime.lexical_method,
        "query_count": query_count,
        "idf_scope": "bounded_candidate_documents",
        "derived_profile_weight": runtime.source_weights["profile"],
        "strict_anchor_sources": sorted(runtime.factual_sources),
    }
