"""Read-only KOL data-completion priority for search results or the full pool.

The score produced here answers one operational question only: *which stored
candidate should be enriched first to unblock a human decision?*  It is not a
search score, model confidence, precision, recall, fit, or business outcome.

The loader uses two set-based reads regardless of candidate count: one coverage
aggregate and, when product anchors are supplied, one anchor-evidence probe.
It never writes ``vkpi_kol_pool`` or schedules provider/LLM/Apify work.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from app.domains.kol.data_completion_priority_item import (
    anchor_coverage,
    comment_evidence_status,
    priority_actions,
    priority_band,
)

from app.db.connection import get_conn, is_postgres_runtime


VERSION = "kol_data_completion_priority_v1"
CLAIM_STATUS = "descriptive_only"
READINESS_VIDEO_TARGET = 5
READINESS_VIEW_RATIO = 0.8
READINESS_FINAL_V1_TARGET = 3
UNKNOWN_TEXT = frozenset(
    {"", "0", "-", "--", "[]", "{}", "unknown", "n/a", "na", "null", "none", "未知", "未提供"}
)
KNOWN_PLATFORMS = frozenset(
    {
        "bilibili",
        "douyin",
        "facebook",
        "instagram",
        "linkedin",
        "media",
        "pinterest",
        "reddit",
        "threads",
        "tiktok",
        "twitch",
        "twitter",
        "x",
        "xiaohongshu",
        "youtube",
    }
)
COUNT_FIELDS = frozenset(
    {
        "evidence_count",
        "view_count_known_count",
        "comment_metric_known_count",
        "final_v1_count",
        "stored_comment_count",
        "direct_account_comment_count",
        "evidence_bridge_comment_count",
    }
)


@dataclass(frozen=True)
class AnchorSpec:
    key: str
    aliases: tuple[str, ...]


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _normalized_text(value: Any) -> str:
    return _text(value).casefold()


def _known_text(value: Any) -> bool:
    return _normalized_text(value) not in UNKNOWN_TEXT


def _platform_status(value: Any) -> str:
    normalized = _normalized_text(value)
    if normalized in UNKNOWN_TEXT:
        return "missing"
    return "known" if normalized in KNOWN_PLATFORMS else "invalid"


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _nonnegative_int(value: Any) -> int:
    parsed = _int_or_none(value)
    return max(0, parsed or 0)


def _ratio(numerator: int | float, denominator: int | float) -> float | None:
    return round(float(numerator) / float(denominator), 4) if denominator > 0 else None


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def normalize_anchor_specs(
    anchors: Mapping[str, Sequence[str]] | Sequence[str] | None,
) -> tuple[AnchorSpec, ...]:
    """Normalize independent product anchors without silently joining them."""

    if not anchors:
        return ()
    raw_items: Iterable[tuple[Any, Any]]
    if isinstance(anchors, Mapping):
        raw_items = anchors.items()
    else:
        raw_items = ((_text(item), (item,)) for item in anchors)

    output: list[AnchorSpec] = []
    seen: set[str] = set()
    for raw_key, raw_aliases in raw_items:
        key = _normalized_text(raw_key)
        if not key or key in seen:
            continue
        if isinstance(raw_aliases, str):
            alias_values = (raw_aliases,)
        else:
            alias_values = tuple(raw_aliases or ())
        aliases = tuple(
            dict.fromkeys(
                alias
                for raw in alias_values
                if (alias := _normalized_text(raw))
            )
        )
        if not aliases:
            aliases = (key,)
        output.append(AnchorSpec(key=key, aliases=aliases[:8]))
        seen.add(key)
        if len(output) >= 12:
            break
    return tuple(output)


def _scope(ids: Sequence[int] | None) -> tuple[str, tuple[int, ...]]:
    if ids is None:
        return "", ()
    parsed: list[int] = []
    for value in ids:
        item_id = _int_or_none(value)
        if item_id is not None and item_id > 0:
            parsed.append(item_id)
    normalized = tuple(dict.fromkeys(parsed))
    if not normalized:
        return " WHERE 1=0", ()
    placeholders = ",".join(["?"] * len(normalized))
    return f" WHERE p.id IN ({placeholders})", normalized


def load_completion_rows(
    kol_pool_ids: Sequence[int] | None = None,
    *,
    conn: Any | None = None,
) -> list[dict[str, Any]]:
    """Load one coverage row per KOL with a single aggregate SELECT."""

    db = conn or get_conn()
    scope_sql, params = _scope(kol_pool_ids)
    active = "e.is_active IS DISTINCT FROM FALSE" if is_postgres_runtime() else "COALESCE(e.is_active, 1) != 0"
    rows = db.execute(
        f"""
        WITH scoped AS (
            SELECT p.id, p.platform, p.country, p.language, p.followers,
                   p.source_type, p.audience_estimated_json
            FROM vkpi_kol_pool p
            {scope_sql}
        ),
        evidence AS (
            SELECT e.kol_pool_id,
                   COUNT(DISTINCT e.id) AS evidence_count,
                   COUNT(DISTINCT CASE WHEN e.view_count IS NOT NULL THEN e.id END)
                       AS view_count_known_count,
                   COUNT(DISTINCT CASE WHEN e.comment_count IS NOT NULL THEN e.id END)
                       AS comment_metric_known_count
            FROM vkpi_kol_video_evidence e
            JOIN scoped s ON s.id=e.kol_pool_id
            WHERE {active}
            GROUP BY e.kol_pool_id
        ),
        finals AS (
            SELECT e.kol_pool_id, COUNT(DISTINCT e.id) AS final_v1_count
            FROM vkpi_kol_video_evidence e
            JOIN scoped s ON s.id=e.kol_pool_id
            JOIN vkpi_analysis_cache c
              ON c.target_type='video'
             AND c.target_id=CAST(e.id AS TEXT)
             AND c.derive_method='video_analysis_final_v1'
             AND c.status='ready'
            WHERE {active}
            GROUP BY e.kol_pool_id
        ),
        comment_links AS (
            SELECT s.id AS kol_pool_id, c.id AS comment_id, 'account' AS bridge
            FROM scoped s
            JOIN vkpi_comments c ON c.account_id=s.id
            UNION
            SELECT e.kol_pool_id, c.id AS comment_id, 'evidence' AS bridge
            FROM vkpi_kol_video_evidence e
            JOIN scoped s ON s.id=e.kol_pool_id
            JOIN vkpi_comments c
              ON c.post_id=e.id
             AND c.post_table IN ('evidence', 'vkpi_kol_video_evidence')
            WHERE {active}
        ),
        comments AS (
            SELECT kol_pool_id,
                   COUNT(DISTINCT comment_id) AS stored_comment_count,
                   COUNT(DISTINCT CASE WHEN bridge='account' THEN comment_id END)
                       AS direct_account_comment_count,
                   COUNT(DISTINCT CASE WHEN bridge='evidence' THEN comment_id END)
                       AS evidence_bridge_comment_count
            FROM comment_links
            GROUP BY kol_pool_id
        )
        SELECT s.id AS kol_pool_id, s.platform, s.country, s.language,
               s.followers, s.source_type, s.audience_estimated_json,
               COALESCE(e.evidence_count, 0) AS evidence_count,
               COALESCE(e.view_count_known_count, 0) AS view_count_known_count,
               COALESCE(e.comment_metric_known_count, 0) AS comment_metric_known_count,
               COALESCE(f.final_v1_count, 0) AS final_v1_count,
               COALESCE(c.stored_comment_count, 0) AS stored_comment_count,
               COALESCE(c.direct_account_comment_count, 0) AS direct_account_comment_count,
               COALESCE(c.evidence_bridge_comment_count, 0) AS evidence_bridge_comment_count
        FROM scoped s
        LEFT JOIN evidence e ON e.kol_pool_id=s.id
        LEFT JOIN finals f ON f.kol_pool_id=s.id
        LEFT JOIN comments c ON c.kol_pool_id=s.id
        ORDER BY s.id
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def _postgres_anchor_pattern(alias: str) -> str:
    pieces = [re.escape(piece) for piece in re.split(r"[\s_-]+", alias) if piece]
    body = "[[:space:]_-]*".join(pieces)
    if not re.search(r"[a-z0-9]", alias):
        return re.escape(alias)
    return rf"(^|[^[:alnum:]]){body}([^[:alnum:]]|$)"


def _anchor_match_sql(text_sql: str, aliases: Sequence[str], *, postgres: bool) -> tuple[str, list[str]]:
    clauses: list[str] = []
    params: list[str] = []
    for alias in aliases:
        if postgres:
            clauses.append(f"LOWER({text_sql}) ~ ?")
            params.append(_postgres_anchor_pattern(alias))
        else:
            normalized_sql = f"LOWER({text_sql})"
            for separator in ("-", "_", "/", ".", ",", ":", ";", "(", ")", "[", "]", "{", "}"):
                normalized_sql = f"REPLACE({normalized_sql}, '{separator}', ' ')"
            pieces = [piece for piece in re.split(r"[\s_-]+", alias) if piece]
            escaped = [
                piece.replace("!", "!!").replace("%", "!%").replace("_", "!_")
                for piece in pieces
            ]
            clauses.append(f"(' ' || {normalized_sql} || ' ') LIKE ? ESCAPE '!'")
            params.append(f"% {'%'.join(escaped)} %")
    return "(" + " OR ".join(clauses) + ")", params


def load_anchor_hits(
    kol_pool_ids: Sequence[int] | None,
    anchors: Mapping[str, Sequence[str]] | Sequence[str] | None,
    *,
    conn: Any | None = None,
) -> dict[int, dict[str, dict[str, bool]]]:
    """Probe factual profile, evidence titles, and ready final_v1 in one SELECT."""

    specs = normalize_anchor_specs(anchors)
    if not specs:
        return {}
    db = conn or get_conn()
    scope_sql, scope_params = _scope(kol_pool_ids)
    postgres = is_postgres_runtime()
    active = "e.is_active IS DISTINCT FROM FALSE" if postgres else "COALESCE(e.is_active, 1) != 0"
    factual_text = (
        "COALESCE(p.bio, '') || ' ' || COALESCE(p.primary_topic, '') || ' ' || "
        "COALESCE(p.secondary_topics_json, '') || ' ' || COALESCE(p.content_style, '')"
    )
    evidence_text = "COALESCE(e.video_title, '') || ' ' || COALESCE(e.title, '')"
    final_text = "CAST(c.result AS TEXT)"
    projections = ["p.id AS kol_pool_id"]
    select_params: list[Any] = []
    for index, spec in enumerate(specs):
        factual_sql, factual_params = _anchor_match_sql(factual_text, spec.aliases, postgres=postgres)
        evidence_sql, evidence_params = _anchor_match_sql(evidence_text, spec.aliases, postgres=postgres)
        final_sql, final_params = _anchor_match_sql(final_text, spec.aliases, postgres=postgres)
        projections.extend(
            [
                f"CASE WHEN {factual_sql} THEN 1 ELSE 0 END AS a{index}_factual",
                f"CASE WHEN EXISTS ("
                f"SELECT 1 FROM vkpi_kol_video_evidence e "
                f"WHERE e.kol_pool_id=p.id AND {active} AND {evidence_sql}"
                f") THEN 1 ELSE 0 END AS a{index}_evidence",
                f"CASE WHEN EXISTS ("
                f"SELECT 1 FROM vkpi_kol_video_evidence e "
                f"JOIN vkpi_analysis_cache c ON c.target_type='video' "
                f"AND c.target_id=CAST(e.id AS TEXT) "
                f"AND c.derive_method='video_analysis_final_v1' AND c.status='ready' "
                f"WHERE e.kol_pool_id=p.id AND {active} AND {final_sql}"
                f") THEN 1 ELSE 0 END AS a{index}_final_v1",
            ]
        )
        select_params.extend(factual_params)
        select_params.extend(evidence_params)
        select_params.extend(final_params)
    rows = db.execute(
        f"SELECT {', '.join(projections)} FROM vkpi_kol_pool p {scope_sql} ORDER BY p.id",
        tuple(select_params) + scope_params,
    ).fetchall()
    output: dict[int, dict[str, dict[str, bool]]] = {}
    for raw in rows:
        row = dict(raw)
        kol_id = int(row["kol_pool_id"])
        output[kol_id] = {
            spec.key: {
                "factual_profile": bool(row.get(f"a{index}_factual")),
                "video_evidence": bool(row.get(f"a{index}_evidence")),
                "final_v1": bool(row.get(f"a{index}_final_v1")),
            }
            for index, spec in enumerate(specs)
        }
    return output


def load_search_session_kol_ids(session_id: int, *, conn: Any | None = None) -> list[int]:
    """Load the de-duplicated candidate IDs for one persisted search session."""

    db = conn or get_conn()
    rows = db.execute(
        """
        SELECT kol_pool_id, MIN(COALESCE(rank, 2147483647)) AS first_rank
        FROM vkpi_kol_search_session_items
        WHERE session_id=? AND kol_pool_id IS NOT NULL
        GROUP BY kol_pool_id
        ORDER BY first_rank, kol_pool_id
        """,
        (int(session_id),),
    ).fetchall()
    return [int(dict(row)["kol_pool_id"]) for row in rows]


def _merge_anchor_hits(
    left: Mapping[str, Mapping[str, Any]] | None,
    right: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, dict[str, bool]]:
    output: dict[str, dict[str, bool]] = {}
    for source in (left or {}, right or {}):
        for anchor, states in source.items():
            merged = output.setdefault(str(anchor), {})
            for key, value in (states or {}).items():
                merged[str(key)] = bool(merged.get(str(key)) or value)
    return output


def _dedupe_rows(rows: Iterable[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    by_id: dict[int, dict[str, Any]] = {}
    input_count = 0
    for raw in rows:
        input_count += 1
        kol_id = _int_or_none(raw.get("kol_pool_id") or raw.get("id"))
        if not kol_id or kol_id <= 0:
            continue
        incoming = dict(raw)
        incoming["kol_pool_id"] = kol_id
        if kol_id not in by_id:
            by_id[kol_id] = incoming
            continue
        current = by_id[kol_id]
        for field in COUNT_FIELDS:
            current[field] = max(_nonnegative_int(current.get(field)), _nonnegative_int(incoming.get(field)))
        for field in ("platform", "country", "language", "source_type"):
            if not _known_text(current.get(field)) and _known_text(incoming.get(field)):
                current[field] = incoming[field]
        if _followers_status(current.get("followers")) != "known" and _followers_status(incoming.get("followers")) == "known":
            current["followers"] = incoming["followers"]
        if _audience_status(current.get("audience_estimated_json"))["status"] != "ready" and _audience_status(incoming.get("audience_estimated_json"))["status"] == "ready":
            current["audience_estimated_json"] = incoming["audience_estimated_json"]
        current["anchor_hits"] = _merge_anchor_hits(current.get("anchor_hits"), incoming.get("anchor_hits"))
    unique = [by_id[key] for key in sorted(by_id)]
    return unique, max(0, input_count - len(unique))


def _audience_status(value: Any) -> dict[str, Any]:
    parsed = _json_dict(value)
    method = _normalized_text(parsed.get("method"))
    sample_size = _nonnegative_int(parsed.get("sample_size"))
    if method == "ensemble_v1" and sample_size > 0:
        return {"status": "ready", "method": method, "sample_size": sample_size}
    if value not in (None, "", "{}", {}) and not parsed:
        return {"status": "invalid", "method": None, "sample_size": 0}
    if parsed:
        return {"status": "partial", "method": method or None, "sample_size": sample_size}
    return {"status": "missing", "method": None, "sample_size": 0}


def _followers_status(value: Any) -> str:
    parsed = _int_or_none(value)
    if parsed is None:
        return "missing"
    if parsed < 0:
        return "invalid"
    if parsed == 0:
        # Zero is a common ingestion sentinel here.  It is not converted to a
        # missing numeric value, but it also cannot prove hard-filter reach.
        return "zero_unverified"
    return "known"


def _action(
    code: str,
    contribution: float,
    action: str,
    reason: str,
    cost_tier: str,
    impact_level: str,
    decision: str,
    effect: str,
) -> dict[str, Any]:
    return {
        "code": code,
        "score_contribution": round(max(0.0, contribution), 2),
        "recommended_action": action,
        "reason": reason,
        "cost_tier": cost_tier,
        "expected_decision_impact": {
            "level": impact_level,
            "decision": decision,
            "effect": effect,
        },
    }


_IMPACT_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1}


def _priority_item(row: Mapping[str, Any], specs: Sequence[AnchorSpec]) -> dict[str, Any]:
    kol_id = int(row["kol_pool_id"])
    evidence_count = _nonnegative_int(row.get("evidence_count"))
    view_known = min(evidence_count, _nonnegative_int(row.get("view_count_known_count")))
    comment_metric_known = min(evidence_count, _nonnegative_int(row.get("comment_metric_known_count")))
    final_count = min(evidence_count, _nonnegative_int(row.get("final_v1_count")))
    stored_comments = _nonnegative_int(row.get("stored_comment_count"))
    direct_comments = _nonnegative_int(row.get("direct_account_comment_count"))
    evidence_bridge_comments = _nonnegative_int(row.get("evidence_bridge_comment_count"))
    view_ratio = _ratio(view_known, evidence_count)
    comment_metric_ratio = _ratio(comment_metric_known, evidence_count)
    audience = _audience_status(row.get("audience_estimated_json"))
    followers_status = _followers_status(row.get("followers"))
    field_status = {
        "platform": _platform_status(row.get("platform")),
        "country": "known" if _known_text(row.get("country")) else "missing",
        "language": "known" if _known_text(row.get("language")) else "missing",
        "followers": followers_status,
    }

    product_anchor_coverage, missing_anchors = anchor_coverage(specs, row.get("anchor_hits"))
    actions = priority_actions(
        field_status=field_status,
        followers_status=followers_status,
        specs=specs,
        missing_anchors=missing_anchors,
        evidence_count=evidence_count,
        view_known=view_known,
        view_ratio=view_ratio,
        evidence_bridge_comments=evidence_bridge_comments,
        direct_comments=direct_comments,
        stored_comments=stored_comments,
        comment_metric_known=comment_metric_known,
        audience=audience,
        final_count=final_count,
        video_target=READINESS_VIDEO_TARGET,
        required_view_ratio=READINESS_VIEW_RATIO,
        final_target=READINESS_FINAL_V1_TARGET,
        action=_action,
    )

    actions.sort(
        key=lambda item: (
            -_IMPACT_ORDER.get(item["expected_decision_impact"]["level"], 0),
            -float(item["score_contribution"]),
            item["code"],
        )
    )
    raw_priority_score = round(sum(float(item["score_contribution"]) for item in actions), 2)
    priority_score = round(min(100.0, raw_priority_score), 2)
    band = priority_band(priority_score)
    top_action = actions[0] if actions else _action(
        "no_material_gap", 0, "keep_monitoring", "当前存量数据未发现需要优先补全的门槛性缺口。", "low", "low", "monitoring", "保持现状并等待业务复核"
    )
    return {
        "kol_pool_id": kol_id,
        "source_type": _normalized_text(row.get("source_type")) or "unknown",
        "priority_score": priority_score,
        "raw_priority_score": raw_priority_score,
        "priority_band": band,
        "recommended_action": top_action["recommended_action"],
        "reason": top_action["reason"],
        "cost_tier": top_action["cost_tier"],
        "expected_decision_impact": top_action["expected_decision_impact"],
        "missing_signals": [item["code"] for item in actions],
        "action_plan": actions,
        "field_status": field_status,
        "evidence_status": {
            "video_evidence_count": evidence_count,
            "video_sample_target": READINESS_VIDEO_TARGET,
            "view_count_known_count": view_known,
            "view_count_coverage": view_ratio,
            "comment_metric_known_count": comment_metric_known,
            "comment_metric_coverage": comment_metric_ratio,
            "stored_comment_count": stored_comments,
            "direct_account_comment_count": direct_comments,
            "evidence_bridge_comment_count": evidence_bridge_comments,
            "comment_evidence_status": comment_evidence_status(
                evidence_bridge_comments,
                direct_comments,
                comment_metric_known,
            ),
            "final_v1_count": final_count,
            "final_v1_target": READINESS_FINAL_V1_TARGET,
            "audience": audience,
        },
        "required_product_anchors": [spec.key for spec in specs],
        "product_anchor_coverage": product_anchor_coverage,
        "claim_status": CLAIM_STATUS,
    }


def _presence(item: Mapping[str, Any], field: str) -> bool:
    fields = item["field_status"]
    evidence = item["evidence_status"]
    mapping = {
        "platform": fields["platform"] == "known",
        "country": fields["country"] == "known",
        "language": fields["language"] == "known",
        "followers": fields["followers"] == "known",
        "video_evidence": evidence["video_evidence_count"] > 0,
        "view_count_ready": (evidence["view_count_coverage"] or 0) >= READINESS_VIEW_RATIO,
        "comments": evidence["evidence_bridge_comment_count"] > 0,
        "comment_metric_ready": evidence["comment_metric_known_count"] > 0,
        "comment_text_ready": evidence["evidence_bridge_comment_count"] > 0,
        "audience": evidence["audience"]["status"] == "ready",
        "final_v1_ready": evidence["final_v1_count"] >= READINESS_FINAL_V1_TARGET,
        "product_anchors": all(value["observed"] for value in item["product_anchor_coverage"].values())
        if item["product_anchor_coverage"]
        else True,
    }
    return bool(mapping[field])


def _cramers_v(groups: Mapping[str, Sequence[bool]]) -> float | None:
    nonempty = {key: list(values) for key, values in groups.items() if values}
    n = sum(len(values) for values in nonempty.values())
    if n <= 0 or len(nonempty) <= 1:
        return None
    total_true = sum(sum(values) for values in nonempty.values())
    total_false = n - total_true
    if total_true == 0 or total_false == 0:
        return 0.0
    chi_square = 0.0
    for values in nonempty.values():
        row_n = len(values)
        observed_true = sum(values)
        observed_false = row_n - observed_true
        expected_true = row_n * total_true / n
        expected_false = row_n * total_false / n
        chi_square += (observed_true - expected_true) ** 2 / expected_true
        chi_square += (observed_false - expected_false) ** 2 / expected_false
    return round(math.sqrt(chi_square / n), 4)


def source_bias_diagnostics(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Diagnose whether signal availability is associated with ingestion source."""

    fields = (
        "platform",
        "country",
        "language",
        "followers",
        "video_evidence",
        "view_count_ready",
        "comments",
        "comment_metric_ready",
        "comment_text_ready",
        "audience",
        "final_v1_ready",
    )
    if any(item.get("product_anchor_coverage") for item in items):
        fields += ("product_anchors",)
    group_values: dict[str, dict[str, list[bool]]] = {
        field: defaultdict(list) for field in fields
    }
    for item in items:
        source = _text(item.get("source_type")) or "unknown"
        for field in fields:
            group_values[field][source].append(_presence(item, field))

    field_reports: dict[str, Any] = {}
    high_fields: list[str] = []
    for field in fields:
        groups = group_values[field]
        by_source = {
            source: {
                "n": len(values),
                "known_or_ready": int(sum(values)),
                "coverage": _ratio(sum(values), len(values)),
                "small_sample": len(values) < 5,
            }
            for source, values in sorted(groups.items())
        }
        rates = [record["coverage"] or 0.0 for record in by_source.values()]
        cramer = _cramers_v(groups)
        if cramer is None or cramer < 0.1:
            severity = "none"
        elif cramer < 0.3:
            severity = "low"
        elif cramer < 0.5:
            severity = "medium"
        else:
            severity = "high"
            high_fields.append(field)
        field_reports[field] = {
            "cramers_v_source_association": cramer,
            "coverage_range": round(max(rates) - min(rates), 4) if rates else None,
            "severity": severity,
            "by_source": by_source,
        }
    return {
        "method": "presence_by_source_cramers_v_v1",
        "candidate_count": len(items),
        "source_counts": dict(sorted(Counter(str(item.get("source_type") or "unknown") for item in items).items())),
        "fields": field_reports,
        "high_association_fields": high_fields,
        "ranking_caution": (
            "补全优先级明显受上游来源覆盖差异影响；应按来源修复采集链，不能把缺失解释为创作者质量差。"
            if high_fields
            else "当前样本未发现高强度来源关联；仍需结合来源样本量解释。"
        ),
        "priority_score_source_adjustment": False,
    }


def build_data_completion_priority(
    rows: Iterable[Mapping[str, Any]],
    *,
    required_product_anchors: Mapping[str, Sequence[str]] | Sequence[str] | None = None,
    output_limit: int | None = None,
) -> dict[str, Any]:
    """Build a deterministic, missingness-aware operational backlog."""

    specs = normalize_anchor_specs(required_product_anchors)
    unique_rows, duplicate_count = _dedupe_rows(rows)
    items = [_priority_item(row, specs) for row in unique_rows]
    cost_order = {"low": 1, "medium": 2, "high": 3}
    items.sort(
        key=lambda item: (
            -max(
                (_IMPACT_ORDER.get(action["expected_decision_impact"]["level"], 0) for action in item["action_plan"]),
                default=0,
            ),
            -float(item["raw_priority_score"]),
            min((cost_order.get(action["cost_tier"], 9) for action in item["action_plan"]), default=9),
            int(item["kol_pool_id"]),
        )
    )
    full_items = items
    if output_limit is not None:
        items = items[: max(0, int(output_limit))]

    signal_fields = (
        "platform",
        "country",
        "language",
        "followers",
        "video_evidence",
        "view_count_ready",
        "comments",
        "comment_metric_ready",
        "comment_text_ready",
        "audience",
        "final_v1_ready",
    )
    coverage = {
        field: {
            "known_or_ready": sum(_presence(item, field) for item in full_items),
            "total": len(full_items),
            "ratio": _ratio(sum(_presence(item, field) for item in full_items), len(full_items)),
        }
        for field in signal_fields
    }
    if specs:
        coverage["product_anchors"] = {
            "known_or_ready": sum(_presence(item, "product_anchors") for item in full_items),
            "total": len(full_items),
            "ratio": _ratio(sum(_presence(item, "product_anchors") for item in full_items), len(full_items)),
        }
    return {
        "version": VERSION,
        "claim_status": CLAIM_STATUS,
        "read_only": True,
        "writes_db": False,
        "provider_calls_made": False,
        "score_contract": {
            "name": "data_completion_operational_priority",
            "range": [0, 100],
            "higher_means": "more stored-data gaps are blocking a human decision",
            "not_measures": ["accuracy", "precision", "recall", "fit", "model_confidence", "business_outcome"],
            "missingness_rule": "unknown and unverified values create explicit actions; they are never converted into performance zeroes",
            "source_bias_rule": "source association is diagnosed but does not alter candidate priority_score",
            "scope_rule": "full-pool mode ranks data-gap density only; use search-result IDs when intent-scoped operational priority is required",
            "sort_rule": "decision blocker severity, uncapped gap score, cheapest available action, stable KOL id",
            "saturation_rule": "priority_score remains capped at 100 for display; raw_priority_score preserves queue ordering",
        },
        "scope": {
            "input_rows": len(unique_rows) + duplicate_count,
            "unique_candidates": len(unique_rows),
            "duplicate_rows_deduped": duplicate_count,
            "required_product_anchors": [spec.key for spec in specs],
            "returned_priorities": len(items),
        },
        "summary": {
            "priority_bands": dict(sorted(Counter(item["priority_band"] for item in full_items).items())),
            "recommended_actions": dict(sorted(Counter(item["recommended_action"] for item in full_items).items())),
            "coverage": coverage,
        },
        "source_bias": source_bias_diagnostics(full_items),
        "priorities": items,
    }


def generate_data_completion_priority(
    *,
    kol_pool_ids: Sequence[int] | None = None,
    required_product_anchors: Mapping[str, Sequence[str]] | Sequence[str] | None = None,
    output_limit: int | None = None,
    conn: Any | None = None,
) -> dict[str, Any]:
    """Run the bounded read path and return the operational priority report."""

    db = conn or get_conn()
    rows = load_completion_rows(kol_pool_ids, conn=db)
    hits = load_anchor_hits(kol_pool_ids, required_product_anchors, conn=db)
    for row in rows:
        row["anchor_hits"] = hits.get(int(row["kol_pool_id"]), {})
    report = build_data_completion_priority(
        rows,
        required_product_anchors=required_product_anchors,
        output_limit=output_limit,
    )
    report["scope"]["mode"] = "search_result" if kol_pool_ids is not None else "full_pool"
    report["scope"]["query_count"] = 1 + int(bool(normalize_anchor_specs(required_product_anchors)))
    anchor_count = len(normalize_anchor_specs(required_product_anchors))
    report["scope"]["operational_queue_valid"] = not (kol_pool_ids is None and anchor_count > 0)
    report["scope"]["scope_warning"] = (
        "全池产品锚点仅用于覆盖诊断；产品补全队列必须限定到一次搜索结果，避免把无关 KOL 排成高优先级。"
        if kol_pool_ids is None and anchor_count > 0
        else None
    )
    report["scope"]["pressure_warning"] = (
        "全池多锚点探针会放大相关子查询；超过两个锚点前先做 EXPLAIN ANALYZE。"
        if kol_pool_ids is None and anchor_count > 2
        else None
    )
    return report
