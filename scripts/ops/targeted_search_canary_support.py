"""Pure safety, planning, DB, and redaction helpers for the targeted canary.

This module intentionally imports no V-KPI application or provider module.
The CLI can therefore clear forbidden credentials before any provider-ready
application code is loaded.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any, Callable
from urllib.parse import parse_qsl, urlsplit


CANARY_SCHEMA = "vkpi_targeted_search_canary_v1"
CLAIM_STATUS = "descriptive_only"
DEFAULT_QUERY = "Z1 Pro 找赛车和餐饮场景中会使用机顶闪光灯的创作者"
DEFAULT_LOCAL_DATABASE_URL = "postgresql://postgres@127.0.0.1:54329/viltrox2"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
MAX_QUERY_CELLS = 2
RAW_LIMIT_PER_CELL = 10
FOLLOWER_BAND = (50_000, 500_000)
KEY_TABLES = (
    "vkpi_kol_pool",
    "vkpi_kol_search_sessions",
    "vkpi_kol_search_session_items",
    "vkpi_kol_video_evidence",
    "apify_jobs",
    "vkpi_ai_cost_ledger",
)
DISABLED_CREDENTIALS = (
    "APIFY_TOKEN",
    "APIFY_API_TOKEN",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "CLAUDE_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_GENAI_API_KEY",
    "GOOGLE_GENERATIVE_AI_API_KEY",
    "XAI_API_KEY",
    "MISTRAL_API_KEY",
    "DEEPSEEK_API_KEY",
    "COHERE_API_KEY",
)
YOUTUBE_PUBLIC_HOSTS = frozenset({
    "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be",
})
SAFE_DATABASE_QUERY_KEYS = frozenset({
    "application_name", "connect_timeout", "sslmode",
})


Planner = Callable[..., dict[str, Any]]


class CanarySafetyError(RuntimeError):
    """Fail-closed canary error whose code contains no provider secret."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def text(value: Any, *, limit: int = 500) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def positive_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def validate_loopback_database_url(value: str) -> str:
    raw = str(value or "").strip()
    parsed = urlsplit(raw)
    try:
        query_keys = {
            key.casefold()
            for key, _value in parse_qsl(
                parsed.query,
                keep_blank_values=True,
                strict_parsing=True,
                max_num_fields=16,
            )
        }
    except ValueError as exc:
        raise ValueError("loopback_postgresql_url_required") from exc
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or parsed.hostname not in LOOPBACK_HOSTS
        or not (parsed.path or "").strip("/")
        or not query_keys.issubset(SAFE_DATABASE_QUERY_KEYS)
    ):
        raise ValueError("loopback_postgresql_url_required")
    return raw


def database_label(database_url: str) -> dict[str, Any]:
    parsed = urlsplit(database_url)
    return {
        "backend": "postgresql",
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "database": (parsed.path or "").strip("/"),
        "credentials_exposed": False,
        "loopback_only": True,
    }


def configure_runtime(database_url: str) -> None:
    """Clear forbidden providers before app import and force local read-only PG."""

    validated = validate_loopback_database_url(database_url)
    for key in DISABLED_CREDENTIALS:
        os.environ[key] = ""
    os.environ.update({
        "DATABASE_URL": validated,
        "DATABASE_POOL_URL": "",
        "DB_USE_PGBOUNCER": "0",
        "DB_RUNTIME_BACKEND": "postgres",
        "DB_TARGET_BACKEND": "postgres",
        "ENVIRONMENT": "local",
        "V2_PRODUCTION_MODE": "0",
        "VKPI_SKIP_DOTENV": "1",
        "ENABLE_SCHEDULER": "0",
        "ENABLE_LOCAL_ORCHESTRATOR": "0",
        "RECALL_LLM_RERANK_ENABLED": "0",
        "VKPI_APIFY_ENRICH_ENABLED": "0",
        "POSTGRES_POOL_MIN_SIZE": "1",
        "POSTGRES_POOL_MAX_SIZE": "1",
        "POSTGRES_POOL_TIMEOUT_SEC": "20",
        "PGOPTIONS": "-c default_transaction_read_only=on -c statement_timeout=120000",
    })


def _safe_string_list(value: Any, *, limit: int = 12) -> list[str]:
    raw = value if isinstance(value, (list, tuple, set)) else []
    output: list[str] = []
    seen: set[str] = set()
    for item in raw:
        item_text = text(item, limit=160)
        key = item_text.casefold()
        if item_text and key not in seen:
            seen.add(key)
            output.append(item_text)
        if len(output) >= limit:
            break
    return output


def _safe_locked_term_groups(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    groups: list[dict[str, Any]] = []
    for raw in value.get("groups") or []:
        if not isinstance(raw, dict):
            continue
        canonical = text(raw.get("canonical_term"), limit=120)
        kind = text(raw.get("kind") or raw.get("key"), limit=80).lower()
        if not canonical or kind not in {"product", "scene"}:
            continue
        groups.append({
            "kind": kind,
            "key": kind,
            "evidence_group": (
                "product_use_fit" if kind == "product" else "segment_use_case"
            ),
            "canonical_term": canonical,
            "aliases": _safe_string_list(raw.get("aliases"), limit=24),
            "use_suitability_terms": _safe_string_list(
                raw.get("use_suitability_terms"), limit=24
            ),
        })
        if len(groups) >= 8:
            break
    if not groups:
        return None
    return {
        "schema": text(value.get("schema"), limit=80),
        "version": positive_int(value.get("version")),
        "source": text(value.get("source"), limit=80),
        "groups": groups,
    }


def _safe_query_cells(
    value: Any,
    *,
    cell_count: int,
) -> tuple[list[dict[str, Any]], int]:
    requested = max(1, min(MAX_QUERY_CELLS, int(cell_count or MAX_QUERY_CELLS)))
    raw_cells = value if isinstance(value, list) else []
    cells: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_queries: set[str] = set()
    executable_count = 0
    for raw in raw_cells:
        if not isinstance(raw, dict) or positive_int(raw.get("round") or 1) != 1:
            continue
        cell_id = text(raw.get("query_cell_id"), limit=120)
        primary_query = text(raw.get("primary_query"), limit=500)
        key = primary_query.casefold()
        if not cell_id or not primary_query or cell_id in seen_ids or key in seen_queries:
            continue
        seen_ids.add(cell_id)
        seen_queries.add(key)
        executable_count += 1
        if len(cells) >= requested:
            continue
        cell = {
            "query_cell_id": cell_id,
            "objective": "prospective_growth",
            "segment": text(raw.get("segment"), limit=120),
            "segment_label": text(raw.get("segment_label"), limit=240),
            "primary_query": primary_query,
            "platforms": ["youtube"],
            "round": 1,
            "raw_limit": RAW_LIMIT_PER_CELL,
            "required_evidence_groups": _safe_string_list(
                raw.get("required_evidence_groups"), limit=8
            ),
            "brand_or_model_required": False,
            "brand_or_model_ranking_weight": 0,
        }
        locked_groups = _safe_locked_term_groups(raw.get("locked_term_groups"))
        if locked_groups:
            cell["locked_term_groups"] = locked_groups
        cells.append(cell)
    return cells, max(0, executable_count - len(cells))


def _resolved_product(value: Any) -> dict[str, str]:
    raw = value if isinstance(value, dict) else {}
    return {
        "sku": text(raw.get("sku"), limit=160),
        "model_name": text(raw.get("model_name"), limit=240),
        "marketing_name": text(raw.get("marketing_name"), limit=300),
        "category_main": text(raw.get("category_main"), limit=120),
    }


def _authorization_payload(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        key: plan[key]
        for key in (
            "schema", "query", "market", "objective", "resolved_product",
            "platforms", "query_cells", "search_inputs", "provider_policy",
            "qualification_profiles", "execution_limits",
        )
    }


def _plan_hash(plan: dict[str, Any]) -> str:
    encoded = json.dumps(
        _authorization_payload(plan),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_canary_plan(
    *,
    query: str,
    market: str,
    product_sku: str,
    planner: Planner,
    cell_count: int = MAX_QUERY_CELLS,
) -> dict[str, Any]:
    query_text = text(query, limit=500)
    if not query_text:
        raise CanarySafetyError("query_required")
    body: dict[str, Any] = {
        "objective": "prospective_growth",
        "platforms": ["youtube"],
        "market": text(market, limit=20).upper() or "US",
        "first_round_raw_limit": RAW_LIMIT_PER_CELL,
    }
    if text(product_sku, limit=160):
        body["product_sku"] = text(product_sku, limit=160)
    raw_plan = planner(query_text, body=body)
    if not isinstance(raw_plan, dict):
        raise CanarySafetyError("provider_free_plan_invalid")
    if text(raw_plan.get("status"), limit=80) == "needs_clarification":
        raise CanarySafetyError(
            text(raw_plan.get("reason"), limit=120) or "planner_needs_clarification"
        )
    objective = text(raw_plan.get("objective"), limit=80) or "prospective_growth"
    if objective != "prospective_growth":
        raise CanarySafetyError("prospective_growth_objective_required")
    query_cells, omitted = _safe_query_cells(
        raw_plan.get("query_cells"), cell_count=cell_count
    )
    if not query_cells:
        raise CanarySafetyError("no_executable_first_round_query_cells")
    search_inputs = {
        "product_focus": _safe_string_list(raw_plan.get("product_focus"), limit=16),
        "ideal_creator_types": _safe_string_list(
            raw_plan.get("ideal_creator_types"), limit=16
        ),
        "verticals": _safe_string_list(raw_plan.get("verticals"), limit=16),
        "avoid_types": _safe_string_list(raw_plan.get("avoid_types"), limit=16),
        "target_persona": text(raw_plan.get("target_persona"), limit=1000),
    }
    raw_brief = raw_plan.get("search_brief")
    brief = dict(raw_brief) if isinstance(raw_brief, dict) else {}
    search_brief = {
        "search_spec_version": text(
            brief.get("search_spec_version"), limit=80
        ) or "targeted_search_v2",
        "objective": "prospective_growth",
        "claim_status": CLAIM_STATUS,
        "authoritative_query_field": "query_cells",
        "query_cells": query_cells,
    }
    plan: dict[str, Any] = {
        "schema": CANARY_SCHEMA,
        "claim_status": CLAIM_STATUS,
        "mode": "plan_only",
        "query": query_text,
        "market": body["market"],
        "objective": "prospective_growth",
        "resolved_product": _resolved_product(raw_plan.get("resolved_product")),
        "platforms": ["youtube"],
        "query_cells": query_cells,
        "query_cells_requested_for_canary": len(query_cells),
        "query_cells_omitted": omitted,
        "execution_limits": {
            "query_cells": len(query_cells),
            "raw_rows_per_cell": RAW_LIMIT_PER_CELL,
            "max_discovery_legs": len(query_cells),
            "max_youtube_search_calls": len(query_cells),
            "max_youtube_combined_quota_units": 2 * len(query_cells),
            "max_youtube_api_calls": 3 * len(query_cells),
        },
        "search_inputs": search_inputs,
        "search_brief": search_brief,
        "provider_policy": {
            "allowed": ["youtube_data_api"],
            "apify": "disabled",
            "llm": "disabled",
            "gemini": "disabled",
            "fallback_queries": "disabled",
            "auto_enroll": False,
        },
        "qualification_profiles": [
            {"name": "followers_unlimited", "followers_min": None, "followers_max": None},
            {
                "name": "followers_50k_500k",
                "followers_min": FOLLOWER_BAND[0],
                "followers_max": FOLLOWER_BAND[1],
            },
        ],
    }
    plan["plan_hash"] = _plan_hash(plan)
    plan["authorization"] = {
        "required_for_live": True,
        "value": plan["plan_hash"],
        "bound_to_exact_plan": True,
    }
    return plan


def _row_scalar(row: Any) -> Any:
    if row is None:
        return None
    for key in (0, "row_count"):
        try:
            return row[key]
        except (KeyError, IndexError, TypeError):
            continue
    return None


def start_read_only(conn: Any) -> None:
    """Start, then verify, an explicit PostgreSQL read-only transaction."""

    conn.execute("BEGIN TRANSACTION READ ONLY")
    row = conn.execute("SHOW transaction_read_only").fetchone()
    if str(_row_scalar(row) or "").strip().lower() != "on":
        raise CanarySafetyError("database_read_only_guard_failed")


def table_counts(conn: Any) -> dict[str, int]:
    return {
        table: positive_int(_row_scalar(conn.execute(
            f"SELECT COUNT(*) AS row_count FROM {table}"
        ).fetchone()))
        for table in KEY_TABLES
    }


def count_delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    return {
        table: int(after.get(table, 0)) - int(before.get(table, 0))
        for table in KEY_TABLES
    }


def provider_readiness() -> dict[str, bool]:
    return {
        "youtube_data_api_configured": bool(
            str(os.environ.get("YOUTUBE_API_KEY") or "").strip()
            or str(os.environ.get("GOOGLE_YOUTUBE_API_KEY") or "").strip()
        ),
        "apify_disabled": not any(
            str(os.environ.get(key) or "").strip()
            for key in ("APIFY_TOKEN", "APIFY_API_TOKEN")
        ),
        "llm_disabled": not any(
            str(os.environ.get(key) or "").strip()
            for key in (
                "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CLAUDE_API_KEY",
                "XAI_API_KEY", "MISTRAL_API_KEY", "DEEPSEEK_API_KEY",
                "COHERE_API_KEY",
            )
        ),
        "gemini_disabled": not any(
            str(os.environ.get(key) or "").strip()
            for key in (
                "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_API_KEY",
                "GOOGLE_GENERATIVE_AI_API_KEY",
            )
        ),
    }


def authorization_reason(args: Any, plan_hash: str) -> str:
    if not (bool(args.execute) and bool(args.allow_provider_calls)):
        return "provider_calls_not_authorized"
    authorization = str(args.authorization or "").strip()
    if not authorization:
        return "authorization_missing"
    if not hmac.compare_digest(authorization, plan_hash):
        return "authorization_plan_hash_mismatch"
    return ""


def _safe_public_url(value: Any) -> str:
    raw = text(value, limit=1000)
    if not raw:
        return ""
    parsed = urlsplit(raw)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in YOUTUBE_PUBLIC_HOSTS
        or parsed.username
        or parsed.password
    ):
        return ""
    return raw


def safe_candidate(raw: Any) -> dict[str, Any]:
    item = raw if isinstance(raw, dict) else {}
    matches: list[dict[str, str]] = []
    for match in item.get("matched_query_cells") or []:
        if isinstance(match, dict):
            matches.append({
                "query_cell_id": text(match.get("query_cell_id"), limit=120),
                "segment": text(match.get("segment"), limit=120),
            })
        if len(matches) >= MAX_QUERY_CELLS:
            break
    return {
        "platform": "youtube" if text(item.get("platform"), limit=20).lower() == "youtube" else "",
        "handle": text(item.get("handle") or item.get("channel_handle"), limit=160),
        "display_name": text(
            item.get("display_name") or item.get("channel_name") or item.get("name"),
            limit=240,
        ),
        "followers": positive_int(item.get("followers") or item.get("follower_count")),
        "profile_url": _safe_public_url(item.get("profile_url") or item.get("channel_url")),
        "sample_title": text(
            item.get("sample_title") or item.get("latest_video_title"), limit=300
        ),
        "representative_video_views": positive_int(item.get("representative_video_views")),
        "representative_video_likes": positive_int(item.get("representative_video_likes")),
        "representative_video_comments": positive_int(item.get("representative_video_comments")),
        "activation_sample_count": positive_int(item.get("activation_sample_count")),
        "activation_metrics_scope": text(item.get("activation_metrics_scope"), limit=80),
        "activation_evidence_status": text(item.get("activation_evidence_status"), limit=80),
        "matched_query_cells": matches,
    }


def _safe_int_dict(value: Any, *, limit: int = 40) -> dict[str, int]:
    raw = value if isinstance(value, dict) else {}
    output: dict[str, int] = {}
    for key, count in raw.items():
        safe_key = text(key, limit=100)
        if safe_key:
            output[safe_key] = positive_int(count)
        if len(output) >= limit:
            break
    return output


def qualification_summary(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    accepted = [
        safe_candidate(item)
        for item in (raw.get("accepted") or [])[:30]
        if isinstance(item, dict)
    ]
    return {
        "schema": text(raw.get("schema"), limit=100),
        "accepted_count": len(accepted),
        "counts": _safe_int_dict(raw.get("counts")),
        "rejected_by_reason": _safe_int_dict(raw.get("rejected_by_reason")),
        "qualification_stats": _safe_int_dict(raw.get("qualification_stats")),
        "public_candidates": accepted,
    }


def safe_cell_runs(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in value if isinstance(value, list) else []:
        if not isinstance(raw, dict):
            continue
        rows.append({
            "query_cell_id": text(raw.get("query_cell_id"), limit=120),
            "segment": text(raw.get("segment"), limit=120),
            "primary_query": text(raw.get("primary_query"), limit=500),
            "raw_limit": positive_int(raw.get("raw_limit")),
            "status": text(raw.get("status"), limit=80),
            "returned": positive_int(raw.get("returned")),
            "provider_calls": positive_int(raw.get("provider_calls")),
            "platforms": ["youtube"] if "youtube" in (raw.get("platforms") or []) else [],
            "query_mode": text(raw.get("query_mode"), limit=80),
        })
        if len(rows) >= MAX_QUERY_CELLS:
            break
    return rows


def youtube_usage(batch: dict[str, Any]) -> dict[str, int]:
    usage = {
        "youtube_search_calls": 0,
        "youtube_combined_quota_units": 0,
        "youtube_api_calls": 0,
    }
    for raw in batch.get("platform_results") or []:
        if not isinstance(raw, dict) or text(raw.get("platform"), limit=20).lower() != "youtube":
            continue
        metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        searches = positive_int(metadata.get("youtube_search_calls"))
        combined = positive_int(metadata.get("youtube_combined_quota_units"))
        if metadata.get("youtube_combined_quota_units") is None:
            legacy = positive_int(metadata.get("quota_units"))
            if metadata.get("quota_units_deprecated"):
                combined = legacy
            else:
                searches = searches or max(0, (legacy - 1) // 100)
                combined = max(0, legacy - 100 * searches)
        api_calls = positive_int(metadata.get("youtube_api_calls"))
        if metadata.get("youtube_api_calls") is None:
            api_calls = searches + combined
        usage["youtube_search_calls"] += searches
        usage["youtube_combined_quota_units"] += combined
        usage["youtube_api_calls"] += api_calls
    return usage


def youtube_quota_units(batch: dict[str, Any]) -> int:
    """Deprecated alias for the combined quota bucket only."""

    return youtube_usage(batch)["youtube_combined_quota_units"]
