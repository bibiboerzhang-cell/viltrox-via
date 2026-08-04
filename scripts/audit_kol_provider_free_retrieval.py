#!/usr/bin/env python3
"""Read-only, provider-free KOL retrieval evidence-support audit.

This script intentionally does not import the application, load ``.env`` files,
call a Provider/LLM, mutate PostgreSQL, or expose candidate identities.  It runs
three fixed benchmark intents against a loopback PostgreSQL snapshot and emits
aggregate JSON only.

The reported relevance numbers are *evidence-support proxies*, not accuracy,
precision, recall, business outcome, or human relevance labels.  They are useful
for detecting phrase-match backfill, sparse evidence, and derived-index leakage;
they cannot establish production search quality without blinded human judgments.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Iterable
from urllib.parse import urlsplit


DEFAULT_LOCAL_DATABASE_URL = "postgresql://postgres@127.0.0.1:54329/viltrox2"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
TOP10 = 10
TARGET30 = 30

# Relevance weights deliberately exclude follower count. Followers are a hard
# eligibility floor and deterministic tie-break only.
WEIGHTS = {
    "factual_profile_idf": 0.40,
    "observed_evidence_and_final_idf": 0.30,
    "derived_index_idf_cap": 0.05,
    "persona_expansion_idf": 0.05,
    "creator_reviewer_type": 0.10,
    "evidence_confidence": 0.10,
}


@dataclass(frozen=True)
class Benchmark:
    query: str
    platform: str | None
    groups: dict[str, tuple[str, ...]]
    product_groups: tuple[str, ...]
    context_groups: tuple[str, ...]
    required_groups: tuple[str, ...]
    preferred_type: str
    persona_expansions: tuple[tuple[float, tuple[str, ...]], ...]


BENCHMARKS = (
    Benchmark(
        query="camera reviewer",
        platform=None,
        groups={
            "camera": ("camera", "相机"),
            "reviewer": ("reviewer", "review", "reviews", "评测", "测评"),
        },
        product_groups=(),
        context_groups=(),
        required_groups=("camera", "reviewer"),
        preferred_type="reviewer",
        persona_expansions=(
            (0.80, ("camera reviewer", "相机评测")),
            (0.80, ("lens reviewer", "镜头评测")),
            (0.25, ("photographer", "摄影师")),
            (0.25, ("camera gear", "摄影器材")),
        ),
    ),
    Benchmark(
        query="35mm 低光人像 YouTube 摄影师",
        platform="youtube",
        groups={
            "35mm": ("35mm", "35 mm"),
            "low_light": ("low light", "low-light", "lowlight", "低光", "弱光", "夜景", "夜拍"),
            "portrait": ("portrait", "portraits", "人像"),
            "photographer": ("photographer", "photography", "摄影师", "摄影"),
        },
        product_groups=("35mm",),
        context_groups=("low_light", "portrait"),
        required_groups=(),
        preferred_type="creator",
        persona_expansions=(
            (0.80, ("35mm portrait", "35mm 人像")),
            (0.80, ("low light portrait", "低光人像")),
            (0.25, ("videographer", "视频创作者")),
            (0.25, ("camera gear", "摄影器材")),
        ),
    ),
    Benchmark(
        query="26mm EVO 街头摄影",
        platform=None,
        groups={
            "26mm": ("26mm", "26 mm", "af26mm", "af 26mm", "af-26mm"),
            "evo": ("evo",),
            "street": ("street", "street photography", "街头", "街拍"),
            "photography": ("photographer", "photography", "摄影师", "摄影"),
        },
        # 26mm is the honest product anchor. EVO strengthens the score but does
        # not let a generic EVO-only match pass the strict product gate.
        product_groups=("26mm",),
        context_groups=("street",),
        required_groups=(),
        preferred_type="creator",
        persona_expansions=(
            (0.80, ("26mm evo", "af26mm evo", "26mm EVO")),
            (0.80, ("street photography", "街头摄影", "街拍")),
            (0.25, ("photographer", "摄影师")),
            (0.25, ("filmmaker", "视频创作者")),
        ),
    ),
)


def _normalise(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).casefold()


@lru_cache(maxsize=512)
def _alias_pattern(alias: str) -> re.Pattern[str] | None:
    alias = _normalise(alias).strip()
    if not alias:
        return None
    if not re.search(r"[a-z0-9]", alias):
        return None
    pieces = [re.escape(part) for part in re.split(r"[\s_-]+", alias) if part]
    body = r"[\s_-]*".join(pieces)
    return re.compile(rf"(?<![a-z0-9]){body}(?![a-z0-9])", re.IGNORECASE)


def _contains_alias(text: str, alias: str) -> bool:
    alias = _normalise(alias).strip()
    if not alias:
        return False
    pattern = _alias_pattern(alias)
    return bool(pattern.search(text)) if pattern else alias in text


def _matches(text: str, aliases: Iterable[str]) -> bool:
    return any(_contains_alias(text, alias) for alias in aliases)


def _json_text(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, str):
        return _normalise(value)
    return _normalise(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _prepare_row(raw: dict[str, Any]) -> dict[str, Any]:
    current_fields = tuple(
        _normalise(raw.get(key))
        for key in ("handle", "display_name", "bio", "primary_topic", "content_style")
    )
    factual_text = _normalise(
        " ".join(
            str(raw.get(key) or "")
            for key in (
                "handle",
                "display_name",
                "bio",
                "primary_topic",
                "secondary_topics_json",
                "content_style",
                "production_quality",
            )
        )
    )
    evidence_title_text = _normalise(raw.get("evidence_title_text"))
    final_text = _json_text(raw.get("final_results"))
    observed_text = _normalise(f"{evidence_title_text} {final_text}")
    derived_text = _normalise(raw.get("derived_index_text"))
    combined_text = _normalise(f"{factual_text} {observed_text} {derived_text}")
    followers = max(0, int(raw.get("followers") or 0))
    concerns = _normalise(raw.get("potential_concerns_json"))
    eligible = followers >= 1_000 and "low_reach" not in concerns
    audience = _json_text(raw.get("audience_estimated_json"))
    return {
        "id": int(raw["id"]),
        "platform": _normalise(raw.get("platform")),
        "source_type": _normalise(raw.get("source_type")) or "unknown",
        "followers": followers,
        "eligible": eligible,
        "current_fields": current_fields,
        "factual_text": factual_text,
        "evidence_title_text": evidence_title_text,
        "observed_text": observed_text,
        "derived_text": derived_text,
        "combined_text": combined_text,
        "creator_type": min(1.0, max(0.0, _safe_float(raw.get("creator_type_score")) / 100.0)),
        "reviewer_type": min(1.0, max(0.0, _safe_float(raw.get("reviewer_type_score")) / 100.0)),
        "positive_view_videos": max(0, int(raw.get("positive_view_videos") or 0)),
        "has_final": bool(raw.get("has_final")),
        "has_comments": bool(raw.get("has_comments")),
        "has_audience": "ensemble_v1" in audience,
    }


def _load_rows(conn: Any) -> list[dict[str, Any]]:
    sql = """
        WITH evidence AS (
            SELECT
                e.kol_pool_id,
                string_agg(concat_ws(' ', e.video_title, e.title), ' ') AS evidence_title_text,
                count(*) FILTER (WHERE COALESCE(e.view_count, 0) > 0) AS positive_view_videos
            FROM vkpi_kol_video_evidence e
            WHERE e.is_active IS DISTINCT FROM FALSE
            GROUP BY e.kol_pool_id
        ),
        finals AS (
            SELECT
                e.kol_pool_id,
                jsonb_agg(a.result) AS final_results,
                TRUE AS has_final
            FROM vkpi_kol_video_evidence e
            JOIN vkpi_analysis_cache a
              ON a.target_type = 'video'
             AND a.target_id = e.id::text
             AND a.derive_method = 'video_analysis_final_v1'
             AND a.status = 'ready'
            WHERE e.is_active IS DISTINCT FROM FALSE
            GROUP BY e.kol_pool_id
        ),
        profile_index AS (
            SELECT
                i.kol_pool_id,
                string_agg(i.profile_text, ' ') AS derived_index_text,
                max(i.creator_type_score) AS creator_type_score,
                max(i.reviewer_type_score) AS reviewer_type_score
            FROM vkpi_kol_profile_index_entries i
            WHERE i.status = 'ready'
            GROUP BY i.kol_pool_id
        ),
        comment_kols AS (
            SELECT DISTINCT c.account_id AS kol_pool_id
            FROM vkpi_comments c
            JOIN vkpi_kol_pool p ON p.id = c.account_id
            WHERE c.post_table IN ('evidence', 'vkpi_kol_video_evidence')
            UNION
            SELECT DISTINCT e.kol_pool_id
            FROM vkpi_comments c
            JOIN vkpi_kol_video_evidence e
              ON c.post_table IN ('evidence', 'vkpi_kol_video_evidence')
             AND c.post_id = e.id
            UNION
            SELECT DISTINCT p.id
            FROM vkpi_kol_pool p
            JOIN kol_comments c ON c.kol_id = p.linked_main_kol_id
        )
        SELECT
            p.id,
            p.platform,
            p.source_type,
            p.handle,
            p.display_name,
            p.bio,
            p.primary_topic,
            p.secondary_topics_json,
            p.content_style,
            p.production_quality,
            p.followers,
            p.potential_concerns_json,
            p.audience_estimated_json,
            e.evidence_title_text,
            e.positive_view_videos,
            f.final_results,
            COALESCE(f.has_final, FALSE) AS has_final,
            i.derived_index_text,
            i.creator_type_score,
            i.reviewer_type_score,
            (c.kol_pool_id IS NOT NULL) AS has_comments
        FROM vkpi_kol_pool p
        LEFT JOIN evidence e ON e.kol_pool_id = p.id
        LEFT JOIN finals f ON f.kol_pool_id = p.id
        LEFT JOIN profile_index i ON i.kol_pool_id = p.id
        LEFT JOIN comment_kols c ON c.kol_pool_id = p.id
        ORDER BY p.id
    """
    return [_prepare_row(dict(row)) for row in conn.execute(sql).fetchall()]


def _case_rows(rows: list[dict[str, Any]], case: Benchmark) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if row["eligible"] and (case.platform is None or row["platform"] == case.platform)
    ]


def _group_hits(row: dict[str, Any], case: Benchmark, field: str) -> dict[str, bool]:
    text = row[field]
    return {name: _matches(text, aliases) for name, aliases in case.groups.items()}


def _idf(total: int, document_frequency: int) -> float:
    return math.log(1.0 + (total - document_frequency + 0.5) / (document_frequency + 0.5))


def _field_idfs(rows: list[dict[str, Any]], case: Benchmark, field: str) -> dict[str, float]:
    return {
        name: _idf(len(rows), sum(_matches(row[field], aliases) for row in rows))
        for name, aliases in case.groups.items()
    }


def _normalised_idf_score(hits: dict[str, bool], idfs: dict[str, float]) -> float:
    denominator = sum(idfs.values())
    if denominator <= 0:
        return 0.0
    return sum(idfs[name] for name, matched in hits.items() if matched) / denominator


def _expansion_idfs(
    rows: list[dict[str, Any]], case: Benchmark
) -> tuple[tuple[float, tuple[str, ...]], ...]:
    return tuple(
        (
            provenance_weight * _idf(
                len(rows), sum(_matches(row["combined_text"], aliases) for row in rows)
            ),
            aliases,
        )
        for provenance_weight, aliases in case.persona_expansions
    )


def _expansion_score(
    row: dict[str, Any], expansion_idfs: tuple[tuple[float, tuple[str, ...]], ...]
) -> float:
    weighted_hits = 0.0
    weighted_total = 0.0
    for value, aliases in expansion_idfs:
        weighted_total += value
        if _matches(row["combined_text"], aliases):
            weighted_hits += value
    return weighted_hits / weighted_total if weighted_total else 0.0


def _evidence_confidence(row: dict[str, Any]) -> float:
    return (
        0.45 * min(row["positive_view_videos"] / 3.0, 1.0)
        + 0.25 * float(row["has_final"])
        + 0.15 * float(row["has_comments"])
        + 0.15 * float(row["has_audience"])
    )


def _assign_tier(
    case: Benchmark,
    factual_hits: dict[str, bool],
    observed_hits: dict[str, bool],
    derived_hits: dict[str, bool],
    preferred_type: float,
) -> str:
    authoritative = {name: factual_hits[name] or observed_hits[name] for name in case.groups}
    if case.product_groups:
        product_anchor = all(authoritative[name] for name in case.product_groups)
        context_anchor = any(authoritative[name] for name in case.context_groups)
        derived_product = all(derived_hits[name] for name in case.product_groups)
        if product_anchor and context_anchor:
            return "strict"
        if product_anchor or (derived_product and context_anchor):
            return "relaxed"
        if any(authoritative.values()) or any(derived_hits.values()):
            return "weak"
        return "backfill"
    if all(authoritative[name] for name in case.required_groups):
        return "strict"
    if any(authoritative[name] for name in case.required_groups) and preferred_type >= 0.50:
        return "relaxed"
    if any(authoritative.values()) or any(derived_hits.values()):
        return "weak"
    return "backfill"


def _rank_hybrid(rows: list[dict[str, Any]], case: Benchmark) -> list[dict[str, Any]]:
    field_idfs = {
        field: _field_idfs(rows, case, field)
        for field in ("factual_text", "observed_text", "derived_text")
    }
    expansion_idfs = _expansion_idfs(rows, case)
    ranked: list[dict[str, Any]] = []
    for row in rows:
        factual_hits = _group_hits(row, case, "factual_text")
        observed_hits = _group_hits(row, case, "observed_text")
        derived_hits = _group_hits(row, case, "derived_text")
        preferred_type = row[f"{case.preferred_type}_type"]
        tier = _assign_tier(case, factual_hits, observed_hits, derived_hits, preferred_type)
        score = (
            WEIGHTS["factual_profile_idf"]
            * _normalised_idf_score(factual_hits, field_idfs["factual_text"])
            + WEIGHTS["observed_evidence_and_final_idf"]
            * _normalised_idf_score(observed_hits, field_idfs["observed_text"])
            + WEIGHTS["derived_index_idf_cap"]
            * _normalised_idf_score(derived_hits, field_idfs["derived_text"])
            + WEIGHTS["persona_expansion_idf"] * _expansion_score(row, expansion_idfs)
            + WEIGHTS["creator_reviewer_type"] * preferred_type
            + WEIGHTS["evidence_confidence"] * _evidence_confidence(row)
        )
        ranked.append(
            {
                **row,
                "tier": tier,
                "proxy_score": score,
                "factual_hits": factual_hits,
                "observed_hits": observed_hits,
                "derived_hits": derived_hits,
            }
        )
    order = {"strict": 0, "relaxed": 1, "weak": 2, "backfill": 3}
    ranked.sort(
        key=lambda row: (
            order[row["tier"]],
            -row["proxy_score"],
            -row["followers"],
            -row["id"],
        )
    )
    return ranked


def _current_phrase_backfill_proxy(rows: list[dict[str, Any]], case: Benchmark) -> tuple[list[dict[str, Any]], int]:
    phrase = _normalise(case.query).strip()
    strict = [row for row in rows if any(phrase in field for field in row["current_fields"])]
    strict.sort(key=lambda row: (-row["followers"], -row["id"]))
    strict_ids = {row["id"] for row in strict}
    broad = sorted(
        (row for row in rows if row["id"] not in strict_ids),
        key=lambda row: (-row["followers"], -row["id"]),
    )
    return (strict + broad)[:TOP10], len(strict)


def _source_type_counts(selection: list[dict[str, Any]]) -> dict[str, int]:
    return dict(sorted(Counter(row["source_type"] for row in selection).items()))


def _selection_proxy(selection: list[dict[str, Any]], case: Benchmark) -> dict[str, Any]:
    tier_counts = Counter(row.get("tier", "broad_backfill") for row in selection)
    any_original = 0
    observed_term = 0
    factual_product = 0
    strict_support = 0
    evidence_supported = 0
    for row in selection:
        factual = row.get("factual_hits") or _group_hits(row, case, "factual_text")
        observed = row.get("observed_hits") or _group_hits(row, case, "observed_text")
        derived = row.get("derived_hits") or _group_hits(row, case, "derived_text")
        preferred = row[f"{case.preferred_type}_type"]
        tier = _assign_tier(case, factual, observed, derived, preferred)
        any_original += int(any(factual.values()) or any(observed.values()))
        observed_term += int(any(observed.values()))
        factual_product += int(
            bool(case.product_groups) and all(factual[name] for name in case.product_groups)
        )
        strict_support += int(tier == "strict")
        evidence_supported += int(_evidence_confidence(row) >= 0.50)
    result: dict[str, Any] = {
        "returned": len(selection),
        "tier_counts": dict(sorted(tier_counts.items())),
        "strict_support_proxy": strict_support,
        "any_authoritative_query_support_proxy": any_original,
        "observed_evidence_term_support_proxy": observed_term,
        "evidence_sufficiency_ge_0_50_proxy": evidence_supported,
        "source_type_counts": _source_type_counts(selection),
    }
    if case.product_groups:
        result["factual_product_support_proxy"] = factual_product
    return result


def _leakage_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    terms = {
        "35mm": ("35mm", "35 mm"),
        "26mm": ("26mm", "26 mm", "af26mm", "af 26mm", "af-26mm"),
        "evo": ("evo",),
        "street": ("street", "street photography", "街头", "街拍"),
    }

    def scope_counts(scope: list[dict[str, Any]], aliases: tuple[str, ...]) -> dict[str, int]:
        factual = {_row["id"] for _row in scope if _matches(_row["factual_text"], aliases)}
        titles = {_row["id"] for _row in scope if _matches(_row["evidence_title_text"], aliases)}
        observed = {_row["id"] for _row in scope if _matches(_row["observed_text"], aliases)}
        derived = {_row["id"] for _row in scope if _matches(_row["derived_text"], aliases)}
        authoritative = factual | observed
        return {
            "eligible_docs": len(scope),
            "factual_profile_docs": len(factual),
            "observed_video_title_docs": len(titles),
            "observed_evidence_or_final_docs": len(observed),
            "derived_index_docs": len(derived),
            "derived_index_only_docs": len(derived - authoritative),
        }

    eligible = [row for row in rows if row["eligible"]]
    youtube = [row for row in eligible if row["platform"] == "youtube"]
    return {
        term: {
            "all_eligible": scope_counts(eligible, aliases),
            "youtube_eligible": scope_counts(youtube, aliases),
        }
        for term, aliases in terms.items()
    }


def _benchmark_result(rows: list[dict[str, Any]], case: Benchmark) -> dict[str, Any]:
    eligible = _case_rows(rows, case)
    current, exact_phrase_count = _current_phrase_backfill_proxy(eligible, case)
    hybrid = _rank_hybrid(eligible, case)
    tiers = Counter(row["tier"] for row in hybrid)
    strict_available = tiers.get("strict", 0)
    return {
        "query": case.query,
        "platform_filter": case.platform,
        "eligible_candidates": len(eligible),
        "current_phrase_plus_broad_backfill_proxy": {
            "exact_phrase_candidates": exact_phrase_count,
            "strict_phrase_in_top10": min(exact_phrase_count, TOP10),
            "broad_backfill_in_top10": max(0, len(current) - min(exact_phrase_count, TOP10)),
            "top10": _selection_proxy(current, case),
        },
        "fielded_hybrid_proxy": {
            "candidate_tier_counts": dict(sorted(tiers.items())),
            "honest_strict_available": strict_available,
            "strict_shortfall_to_30": max(0, TARGET30 - strict_available),
            "top10": _selection_proxy(hybrid[:TOP10], case),
            "top30": _selection_proxy(hybrid[:TARGET30], case),
        },
    }


def _validate_loopback_database_url(database_url: str) -> None:
    parsed = urlsplit(database_url)
    if parsed.scheme not in {"postgres", "postgresql"} or parsed.hostname not in LOOPBACK_HOSTS:
        raise ValueError("loopback_postgresql_url_required")


def _emit(payload: dict[str, Any], *, pretty: bool) -> None:
    sys.stdout.write(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2 if pretty else None,
            separators=None if pretty else (",", ":"),
        )
        + "\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.getenv("KOL_AUDIT_DATABASE_URL", DEFAULT_LOCAL_DATABASE_URL),
        help="Loopback PostgreSQL URL. Defaults to KOL_AUDIT_DATABASE_URL or the isolated local snapshot.",
    )
    parser.add_argument("--pretty", action="store_true", help="Indent JSON for review.")
    args = parser.parse_args(argv)

    base: dict[str, Any] = {
        "claim_status": "evidence_support_proxy_not_accuracy",
        "read_only": True,
        "provider_calls": False,
    }
    try:
        _validate_loopback_database_url(args.database_url)
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(
            args.database_url,
            row_factory=dict_row,
            options="-c default_transaction_read_only=on -c statement_timeout=120000",
        ) as conn:
            with conn.transaction():
                conn.execute("SET TRANSACTION READ ONLY")
                rows = _load_rows(conn)
        payload = {
            **base,
            "database_scope": "loopback_snapshot",
            "pool_rows": len(rows),
            "weights": WEIGHTS,
            "proxy_definitions": {
                "authoritative": "pool factual fields plus observed video titles/final_v1; excludes derived profile index",
                "strict_product_gate": "product anchor must occur in authoritative text and pair with a requested scenario",
                "derived_index_only": "derived index term absent from factual profile and observed evidence/final_v1",
                "followers": "minimum 1000 and tie-break only; never a relevance score",
                "accuracy_boundary": "no human labels or finalized outcomes are used, so accuracy/precision/recall are not reported",
            },
            "source_leakage": _leakage_counts(rows),
            "benchmarks": [_benchmark_result(rows, case) for case in BENCHMARKS],
        }
        _emit(payload, pretty=args.pretty)
        return 0
    except ValueError as exc:
        _emit({**base, "error": str(exc)}, pretty=args.pretty)
        return 2
    except Exception:
        # Keep connection strings, credentials, candidate data, and SQL details
        # out of stdout/stderr. The non-zero exit code is the diagnostic signal.
        _emit({**base, "error": "read_only_audit_failed"}, pretty=args.pretty)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
