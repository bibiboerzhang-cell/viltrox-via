#!/usr/bin/env python3
"""Provider-free, read-only evidence benchmark for the KOL local-30 contract.

This script deliberately does not import the application search router, start
workers, call providers, or write business tables.  It connects only to a
loopback PostgreSQL database whose name looks isolated, starts a READ ONLY
transaction, and evaluates five fixed golden queries against stored evidence.

The benchmark checks mechanical contract quality (count, hard gates, market
evidence, lexical query evidence, uniqueness, and contact-status coverage).
It never claims human relevance precision unless an explicit labels file is
provided.  Reports contain aggregates only; creator identities and contact
values are never emitted.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
import ipaddress
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterable, Mapping, Sequence

import psycopg
from psycopg.rows import dict_row
from stdout_utils import out_json


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUERIES = ROOT / "scripts" / "kol_search_60_golden_queries.json"
SCHEMA_VERSION = "vkpi_kol_search_60_local_benchmark_v1"
REQUIRED_TABLES = {"vkpi_kol_pool", "vkpi_kol_video_evidence"}
SAFE_DATABASE_NAME = re.compile(r"(?:audit|bench|snapshot|search60|test|tmp)", re.IGNORECASE)
SAFE_MARKETS = {
    "US", "GB", "CA", "DE", "FR", "IT", "ES", "NL", "BE", "JP", "KR",
    "CN", "HK", "TW", "AU", "NZ", "IN", "PH", "PL", "AE", "TH", "MX",
    "BR", "PT", "RU", "ID", "TR", "SG", "SA", "CZ",
}
COUNTRY_ALIASES = {
    "us": "US", "usa": "US", "u.s.": "US", "united states": "US", "america": "US", "美国": "US",
    "uk": "GB", "gb": "GB", "united kingdom": "GB", "great britain": "GB", "england": "GB", "英国": "GB",
    "canada": "CA", "加拿大": "CA", "germany": "DE", "德国": "DE", "france": "FR", "法国": "FR",
    "italy": "IT", "意大利": "IT", "spain": "ES", "西班牙": "ES", "netherlands": "NL", "荷兰": "NL",
    "belgium": "BE", "比利时": "BE", "japan": "JP", "日本": "JP", "south korea": "KR", "korea": "KR", "韩国": "KR",
    "china": "CN", "中国": "CN", "hong kong": "HK", "香港": "HK", "taiwan": "TW", "台湾": "TW",
    "australia": "AU", "澳大利亚": "AU", "new zealand": "NZ", "新西兰": "NZ", "india": "IN", "印度": "IN",
    "philippines": "PH", "菲律宾": "PH", "poland": "PL", "波兰": "PL", "united arab emirates": "AE", "uae": "AE", "阿联酋": "AE", "迪拜": "AE",
    "thailand": "TH", "泰国": "TH", "mexico": "MX", "墨西哥": "MX", "brazil": "BR", "巴西": "BR",
    "portugal": "PT", "葡萄牙": "PT", "russia": "RU", "俄罗斯": "RU", "indonesia": "ID", "印尼": "ID", "印度尼西亚": "ID",
    "turkey": "TR", "土耳其": "TR", "singapore": "SG", "新加坡": "SG", "saudi arabia": "SA", "沙特": "SA", "czech republic": "CZ", "捷克": "CZ",
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 4)


def _percentile(values: Sequence[float], quantile: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    index = max(0, min(len(ordered) - 1, math.ceil(float(quantile) * len(ordered)) - 1))
    return round(ordered[index], 4)


def _timing_summary(values: Sequence[float]) -> dict[str, float | None]:
    return {
        "n": len(values),
        "p50_ms": _percentile(values, 0.50),
        "p95_ms": _percentile(values, 0.95),
        "max_ms": round(max(values), 4) if values else None,
    }


def _normal_market(value: Any) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return ""
    normalized = COUNTRY_ALIASES.get(text.lower(), text.upper())
    return normalized if normalized in SAFE_MARKETS else ""


def _market_evidence(country: Any, inferred: Any) -> tuple[str, str]:
    exact = _normal_market(country)
    if exact:
        return "exact", exact
    inferred_code = _normal_market(inferred)
    if inferred_code:
        return "inferred", inferred_code
    return "unknown", ""


def _safe_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}:{str(exc).splitlines()[0][:180]}"


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label}_must_be_object")
    return payload


def load_golden_queries(path: Path) -> list[dict[str, Any]]:
    payload = _load_json(path, label="golden_queries")
    rows = payload.get("queries")
    if not isinstance(rows, list) or len(rows) != 5:
        raise ValueError("golden_queries_must_contain_exactly_5_queries")
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError("golden_query_must_be_object")
        query_id = str(raw.get("id") or "").strip()
        terms = [" ".join(str(item or "").lower().split()) for item in raw.get("evidence_terms") or []]
        terms = [item for item in terms if item]
        platforms = [str(item or "").strip().lower() for item in raw.get("platforms") or []]
        if not query_id or query_id in seen or not terms or not platforms:
            raise ValueError(f"invalid_golden_query:{query_id or 'missing_id'}")
        if any(platform not in {"youtube", "instagram", "tiktok"} for platform in platforms):
            raise ValueError(f"unsupported_platform:{query_id}")
        seen.add(query_id)
        output.append(
            {
                "id": query_id,
                "query": str(raw.get("query") or "").strip(),
                "platforms": platforms,
                "market": _normal_market(raw.get("market")),
                "evidence_terms": list(dict.fromkeys(terms))[:12],
            }
        )
    return output


def verify_snapshot(snapshot_dir: Path) -> dict[str, Any]:
    started = time.perf_counter()
    dump = snapshot_dir / "prod-db.dump"
    checksum_file = snapshot_dir / "prod-db.dump.sha256"
    readme = snapshot_dir / "README.txt"
    result: dict[str, Any] = {
        "snapshot_id": snapshot_dir.name,
        "path": str(snapshot_dir),
        "dump_present": dump.is_file(),
        "checksum_present": checksum_file.is_file(),
        "checksum_verified": False,
        "pg_restore_list_verified": False,
        "elapsed_ms": None,
        "errors": [],
    }
    if readme.is_file():
        for line in readme.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("snapshot="):
                result["declared_snapshot_id"] = line.split("=", 1)[1].strip()
            elif line.startswith("downloaded_at_utc="):
                result["downloaded_at_utc"] = line.split("=", 1)[1].strip()
    if not dump.is_file() or not checksum_file.is_file():
        result["errors"].append("snapshot_dump_or_checksum_missing")
        result["elapsed_ms"] = _elapsed_ms(started)
        return result
    try:
        expected = checksum_file.read_text(encoding="utf-8").split()[0].strip().lower()
        digest = hashlib.sha256()
        with dump.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        result["checksum_verified"] = bool(expected and digest.hexdigest() == expected)
        if not result["checksum_verified"]:
            result["errors"].append("snapshot_checksum_mismatch")
    except Exception as exc:  # noqa: BLE001 - recorded as bounded audit evidence
        result["errors"].append(_safe_error(exc))
    try:
        probe = subprocess.run(
            ["pg_restore", "--list", str(dump)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            check=False,
        )
        result["pg_restore_list_verified"] = probe.returncode == 0
        if probe.returncode != 0:
            result["errors"].append(f"pg_restore_list_failed:{probe.returncode}")
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(_safe_error(exc))
    result["elapsed_ms"] = _elapsed_ms(started)
    return result


def _database_preflight(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    started = time.perf_counter()
    with conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            "SELECT current_database() AS database_name, "
            "COALESCE(inet_server_addr()::text, 'local_socket') AS server_addr, "
            "current_setting('transaction_read_only') AS transaction_read_only"
        )
        identity = dict(cursor.fetchone() or {})
        database_name = str(identity.get("database_name") or "")
        server_addr = str(identity.get("server_addr") or "")
        if not SAFE_DATABASE_NAME.search(database_name):
            raise RuntimeError("database_name_does_not_look_isolated")
        if server_addr != "local_socket":
            try:
                if not ipaddress.ip_interface(server_addr).ip.is_loopback:
                    raise RuntimeError("database_server_is_not_loopback")
            except ValueError as exc:
                raise RuntimeError("database_server_address_invalid") from exc
        if str(identity.get("transaction_read_only") or "").lower() != "on":
            raise RuntimeError("transaction_is_not_read_only")
        cursor.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name = ANY(%s)",
            (list(REQUIRED_TABLES),),
        )
        tables = {str(row["table_name"]) for row in cursor.fetchall()}
        missing = sorted(REQUIRED_TABLES - tables)
        if missing:
            raise RuntimeError(f"required_tables_missing:{','.join(missing)}")
        cursor.execute("SELECT COUNT(*) AS n FROM public.vkpi_kol_pool")
        pool_count = int((cursor.fetchone() or {}).get("n") or 0)
        cursor.execute("SELECT COUNT(*) AS n FROM public.vkpi_kol_video_evidence")
        evidence_count = int((cursor.fetchone() or {}).get("n") or 0)
    return {
        "database_name": database_name,
        "server_addr": server_addr,
        "transaction_read_only": True,
        "required_tables_present": True,
        "pool_row_count": pool_count,
        "video_evidence_row_count": evidence_count,
        "binding_status": "operator_provided_isolated_restore_not_cryptographically_bound",
        "elapsed_ms": _elapsed_ms(started),
    }


def _match_expression(terms: Sequence[str]) -> tuple[str, list[str]]:
    fields = (
        "p.handle", "p.display_name", "p.bio", "p.primary_topic",
        "p.content_style", "p.secondary_topics_json",
    )
    term_clauses: list[str] = []
    params: list[str] = []
    for term in terms:
        field_clauses: list[str] = []
        for field in fields:
            field_clauses.append(f"LOWER(COALESCE({field}, '')) LIKE %s")
            params.append(f"%{term}%")
        term_clauses.append(f"CASE WHEN ({' OR '.join(field_clauses)}) THEN 1 ELSE 0 END")
    return " + ".join(term_clauses) if term_clauses else "0", params


def _fetch_candidates(
    conn: psycopg.Connection[Any],
    query: Mapping[str, Any],
    *,
    as_of: date,
    candidate_limit: int,
) -> list[dict[str, Any]]:
    expression, expression_params = _match_expression(list(query["evidence_terms"]))
    platforms = list(query["platforms"])
    sql = f"""
        WITH latest AS (
            SELECT kol_pool_id,
                   MAX(posted_at) AS latest_video_at
            FROM public.vkpi_kol_video_evidence
            WHERE is_active IS NOT FALSE
              AND LOWER(COALESCE(evidence_type, 'video')) = 'video'
              AND posted_at IS NOT NULL
              AND posted_at < %s::date + INTERVAL '1 day'
            GROUP BY kol_pool_id
        ), scored AS (
            SELECT p.id AS kol_pool_id,
                   p.platform,
                   p.followers,
                   latest.latest_video_at,
                   p.country,
                   NULL::text AS inferred_country,
                   CASE
                     WHEN LENGTH(TRIM(COALESCE(p.email, ''))) > 0 THEN TRUE
                     WHEN LOWER(TRIM(COALESCE(p.other_contacts_json, ''))) NOT IN ('', '[]', '{{}}', 'null') THEN TRUE
                     WHEN COALESCE(jsonb_array_length(CASE WHEN jsonb_typeof(p.contact_channels)='array' THEN p.contact_channels ELSE '[]'::jsonb END), 0) > 0 THEN TRUE
                     ELSE FALSE
                   END AS contact_available,
                   ({expression})::integer AS query_evidence_count
            FROM public.vkpi_kol_pool p
            JOIN latest ON latest.kol_pool_id = p.id
            WHERE COALESCE(p.dashboard_account_type, 'kol') = 'kol'
              AND p.duplicate_of_id IS NULL
              AND p.followers >= 3000
              AND latest.latest_video_at >= %s::date - INTERVAL '45 days'
              AND latest.latest_video_at < %s::date + INTERVAL '1 day'
              AND p.platform = ANY(%s)
        )
        SELECT kol_pool_id, platform, followers, latest_video_at, country,
               inferred_country, contact_available, query_evidence_count
        FROM scored
        WHERE query_evidence_count > 0
        ORDER BY query_evidence_count DESC, latest_video_at DESC, followers DESC, kol_pool_id
        LIMIT %s
    """
    params: list[Any] = [as_of, *expression_params, as_of, as_of, platforms, int(candidate_limit)]
    with conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]


def _rank_candidates(rows: Iterable[Mapping[str, Any]], query: Mapping[str, Any], *, as_of: date) -> list[dict[str, Any]]:
    target_market = str(query.get("market") or "")
    output: list[dict[str, Any]] = []
    seen: set[int] = set()
    for raw in rows:
        try:
            canonical_id = int(raw.get("kol_pool_id") or 0)
            followers = int(raw.get("followers") or 0)
        except (TypeError, ValueError):
            continue
        if canonical_id <= 0 or canonical_id in seen:
            continue
        latest_raw = raw.get("latest_video_at")
        latest_date = latest_raw.date() if isinstance(latest_raw, datetime) else latest_raw
        if not isinstance(latest_date, date):
            continue
        seen.add(canonical_id)
        market_status, market = _market_evidence(raw.get("country"), raw.get("inferred_country"))
        market_match = bool(target_market and market == target_market)
        age_days = (as_of - latest_date).days
        evidence_count = max(0, int(raw.get("query_evidence_count") or 0))
        rank_key = (
            1 if market_match else 0,
            evidence_count,
            1 if age_days <= 30 else 0,
            -max(0, age_days),
            followers,
            -canonical_id,
        )
        output.append(
            {
                "canonical_id": canonical_id,
                "followers": followers,
                "latest_video_age_days": age_days,
                "recent_30d": age_days <= 30,
                "recent_45d": age_days <= 45,
                "market_evidence": market_status,
                "market": market,
                "market_match": market_match,
                "query_evidence_count": evidence_count,
                "contact_available": bool(raw.get("contact_available")),
                "rank_key": rank_key,
            }
        )
    output.sort(key=lambda item: item["rank_key"], reverse=True)
    return output


def _human_precision(
    query_id: str,
    selected: Sequence[Mapping[str, Any]],
    labels: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if labels is None or query_id not in labels:
        return {"status": "not_evaluated", "precision_at_30": None, "reason": "human_labels_missing"}
    raw_ids = labels.get(query_id)
    if not isinstance(raw_ids, list):
        return {"status": "not_evaluated", "precision_at_30": None, "reason": "invalid_human_labels"}
    relevant = {int(value) for value in raw_ids if str(value).isdigit() and int(value) > 0}
    if not selected:
        return {"status": "evaluated", "precision_at_30": 0.0, "relevant_returned": 0, "denominator": 0}
    relevant_returned = sum(1 for item in selected[:30] if int(item["canonical_id"]) in relevant)
    denominator = min(30, len(selected))
    return {
        "status": "evaluated",
        "precision_at_30": round(relevant_returned / denominator, 4),
        "relevant_returned": relevant_returned,
        "denominator": denominator,
    }


def _query_metrics(
    query: Mapping[str, Any],
    ranked: Sequence[Mapping[str, Any]],
    *,
    limit: int,
    min_market_known_ratio: float,
    human_labels: Mapping[str, Any] | None,
) -> dict[str, Any]:
    target_market = str(query.get("market") or "")
    market_eligible = (
        [item for item in ranked if bool(item.get("market_match"))]
        if target_market
        else list(ranked)
    )
    selected = list(market_eligible[:limit])
    returned = len(selected)
    qualified = sum(
        1 for item in selected
        if int(item["followers"]) >= 3000
        and bool(item["recent_45d"])
        and int(item["query_evidence_count"]) > 0
        and (not target_market or bool(item["market_match"]))
    )
    unique = len({int(item["canonical_id"]) for item in selected})
    market_counts = {
        status: sum(1 for item in selected if item["market_evidence"] == status)
        for status in ("exact", "inferred", "unknown")
    }
    known_market = market_counts["exact"] + market_counts["inferred"]
    market_known_ratio = round(known_market / returned, 4) if returned else 0.0
    query_evidence_covered = sum(1 for item in selected if int(item["query_evidence_count"]) > 0)
    contact_available = sum(1 for item in selected if item["contact_available"])
    shortfalls: list[str] = []
    if returned < limit:
        shortfalls.append(
            "target_market_candidates_below_30"
            if target_market
            else "qualified_query_candidates_below_30"
        )
    if qualified != returned:
        shortfalls.append("hard_gate_failure_in_returned_set")
    if unique != returned:
        shortfalls.append("duplicate_returned")
    if query_evidence_covered != returned:
        shortfalls.append("query_evidence_missing")
    if market_known_ratio < min_market_known_ratio:
        shortfalls.append("market_evidence_ratio_below_contract")
    contract_pass = not shortfalls and returned >= limit
    return {
        "query_id": str(query["id"]),
        "query_text": str(query["query"]),
        "platforms": list(query["platforms"]),
        "target_market": str(query.get("market") or ""),
        "pre_market_candidate_count": len(ranked),
        "target_market_candidate_count": len(market_eligible),
        "market_gate_filtered_count": len(ranked) - len(market_eligible),
        "returned_count": returned,
        "qualified_count": qualified,
        "unique_count": unique,
        "followers_gte_3000_count": sum(1 for item in selected if int(item["followers"]) >= 3000),
        "latest_video_lte_30d_count": sum(1 for item in selected if bool(item["recent_30d"])),
        "latest_video_lte_45d_count": sum(1 for item in selected if bool(item["recent_45d"])),
        "market_evidence": market_counts,
        "market_known_ratio": market_known_ratio,
        "market_match_count": sum(1 for item in selected if bool(item["market_match"])),
        "query_evidence_covered_count": query_evidence_covered,
        "query_evidence_coverage_ratio": round(query_evidence_covered / returned, 4) if returned else 0.0,
        "contact_status": {
            "available_count": contact_available,
            "unavailable_count": returned - contact_available,
            "coverage_ratio": round(contact_available / returned, 4) if returned else 0.0,
            "contact_values_emitted": False,
        },
        "contract": {
            "status": "pass" if contract_pass else "fail",
            "pass": contract_pass,
            "minimum_returned": limit,
            "minimum_followers": 3000,
            "maximum_video_age_days": 45,
            "minimum_market_known_ratio": min_market_known_ratio,
            "shortfall_reasons": shortfalls,
        },
        "human_relevance": _human_precision(str(query["id"]), selected, human_labels),
    }


def run_benchmark(
    *,
    database_url: str,
    snapshot_dir: Path,
    queries: Sequence[Mapping[str, Any]],
    runs: int,
    as_of: date,
    candidate_limit: int,
    min_market_known_ratio: float,
    labels: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    benchmark_started = time.perf_counter()
    snapshot = verify_snapshot(snapshot_dir)
    errors: list[str] = list(snapshot.get("errors") or [])
    run_rows: list[dict[str, Any]] = []
    preflight: dict[str, Any] | None = None
    if not snapshot.get("checksum_verified") or not snapshot.get("pg_restore_list_verified"):
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": _now_utc().isoformat(),
            "provider_calls_performed": False,
            "business_writes_performed": False,
            "snapshot": snapshot,
            "database": None,
            "runs": [],
            "summary": {"status": "blocked", "contract_pass": False},
            "errors": errors or ["snapshot_verification_failed"],
            "total_elapsed_ms": _elapsed_ms(benchmark_started),
        }
    try:
        connect_started = time.perf_counter()
        with psycopg.connect(
            database_url,
            autocommit=False,
            options="-c default_transaction_read_only=on -c statement_timeout=15000 -c lock_timeout=2000",
            row_factory=dict_row,
        ) as conn:
            connect_ms = _elapsed_ms(connect_started)
            with conn.transaction():
                conn.execute("SET TRANSACTION READ ONLY")
                preflight = _database_preflight(conn)
                for run_number in range(1, runs + 1):
                    run_started = time.perf_counter()
                    query_rows: list[dict[str, Any]] = []
                    for query in queries:
                        query_started = time.perf_counter()
                        stages: dict[str, float] = {}
                        try:
                            stage_started = time.perf_counter()
                            candidates = _fetch_candidates(
                                conn, query, as_of=as_of, candidate_limit=candidate_limit
                            )
                            stages["candidate_fetch_ms"] = _elapsed_ms(stage_started)
                            stage_started = time.perf_counter()
                            ranked = _rank_candidates(candidates, query, as_of=as_of)
                            stages["qualification_and_rank_ms"] = _elapsed_ms(stage_started)
                            stage_started = time.perf_counter()
                            metrics = _query_metrics(
                                query,
                                ranked,
                                limit=30,
                                min_market_known_ratio=min_market_known_ratio,
                                human_labels=labels,
                            )
                            stages["metrics_ms"] = _elapsed_ms(stage_started)
                            metrics["candidate_count"] = len(candidates)
                            metrics["stage_timing_ms"] = stages
                            metrics["total_elapsed_ms"] = _elapsed_ms(query_started)
                            metrics["bugs_or_errors"] = []
                            query_rows.append(metrics)
                        except Exception as exc:  # noqa: BLE001 - preserve remaining golden queries
                            safe = _safe_error(exc)
                            errors.append(f"query:{query['id']}:{safe}")
                            query_rows.append(
                                {
                                    "query_id": str(query["id"]),
                                    "query_text": str(query["query"]),
                                    "platforms": list(query["platforms"]),
                                    "target_market": str(query.get("market") or ""),
                                    "returned_count": 0,
                                    "qualified_count": 0,
                                    "unique_count": 0,
                                    "contract": {
                                        "status": "error", "pass": False,
                                        "shortfall_reasons": ["benchmark_query_error"],
                                    },
                                    "human_relevance": {
                                        "status": "not_evaluated", "precision_at_30": None,
                                        "reason": "benchmark_query_error",
                                    },
                                    "stage_timing_ms": stages,
                                    "total_elapsed_ms": _elapsed_ms(query_started),
                                    "bugs_or_errors": [safe],
                                }
                            )
                    run_rows.append(
                        {
                            "run": run_number,
                            "connect_ms": connect_ms if run_number == 1 else 0.0,
                            "queries": query_rows,
                            "total_elapsed_ms": _elapsed_ms(run_started),
                        }
                    )
    except Exception as exc:  # noqa: BLE001
        errors.append(_safe_error(exc))

    query_summaries: list[dict[str, Any]] = []
    for query in queries:
        query_id = str(query["id"])
        observations = [
            item for run_row in run_rows for item in run_row["queries"]
            if item.get("query_id") == query_id
        ]
        query_summaries.append(
            {
                "query_id": query_id,
                "runs": len(observations),
                "latency": _timing_summary([float(item.get("total_elapsed_ms") or 0.0) for item in observations]),
                "returned": {
                    "min": min((int(item.get("returned_count") or 0) for item in observations), default=0),
                    "max": max((int(item.get("returned_count") or 0) for item in observations), default=0),
                },
                "contract_pass_runs": sum(1 for item in observations if item.get("contract", {}).get("pass") is True),
                "human_relevance_status": (
                    "evaluated" if observations and all(item.get("human_relevance", {}).get("status") == "evaluated" for item in observations)
                    else "not_evaluated"
                ),
            }
        )
    all_contract_pass = bool(run_rows) and all(
        item.get("contract", {}).get("pass") is True
        for run_row in run_rows for item in run_row["queries"]
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_utc().isoformat(),
        "scope": {
            "lane": "local_existing_pool_30",
            "benchmark_kind": "sql_supply_lower_bound_not_runtime_algorithm",
            "production_endpoint_measured": False,
            "planner_recall_session_attach_measured": False,
            "market_inference_policy": "explicit_country_only_lower_bound",
            "network_discovery_30": "not_evaluated_provider_free",
            "provider_calls_performed": False,
            "business_writes_performed": False,
            "identity_or_contact_values_emitted": False,
        },
        "contract": {
            "minimum_returned": 30,
            "minimum_followers": 3000,
            "maximum_video_age_days": 45,
            "prefer_video_age_days": 30,
            "minimum_market_known_ratio": min_market_known_ratio,
            "query_evidence_required": True,
            "uniqueness_required": True,
        },
        "as_of_date": as_of.isoformat(),
        "snapshot": snapshot,
        "database": preflight,
        "runs": run_rows,
        "summary": {
            "status": "complete" if run_rows else "blocked",
            "contract_pass": all_contract_pass,
            "total_run_latency": _timing_summary([float(row["total_elapsed_ms"]) for row in run_rows]),
            "queries": query_summaries,
            "human_relevance_precision_at_30": (
                "evaluated" if labels is not None else "not_evaluated"
            ),
            "human_relevance_note": (
                "precision@30 is separate from the mechanical contract and requires explicit human labels"
            ),
        },
        "errors": errors,
        "total_elapsed_ms": _elapsed_ms(benchmark_started),
    }


def _csv_text(report: Mapping[str, Any]) -> str:
    buffer = io.StringIO()
    fields = [
        "run", "query_id", "total_elapsed_ms", "candidate_count", "returned_count",
        "pre_market_candidate_count", "target_market_candidate_count", "market_gate_filtered_count",
        "qualified_count", "unique_count", "followers_gte_3000_count",
        "latest_video_lte_30d_count", "latest_video_lte_45d_count",
        "market_exact_count", "market_inferred_count", "market_unknown_count",
        "market_known_ratio", "market_match_count", "query_evidence_covered_count",
        "contact_available_count", "contract_status", "shortfall_reasons",
        "human_relevance_status", "precision_at_30", "bugs_or_errors",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    for run_row in report.get("runs") or []:
        for item in run_row.get("queries") or []:
            market = item.get("market_evidence") or {}
            contact = item.get("contact_status") or {}
            contract = item.get("contract") or {}
            relevance = item.get("human_relevance") or {}
            writer.writerow(
                {
                    "run": run_row.get("run"),
                    "query_id": item.get("query_id"),
                    "total_elapsed_ms": item.get("total_elapsed_ms"),
                    "candidate_count": item.get("candidate_count", 0),
                    "returned_count": item.get("returned_count", 0),
                    "pre_market_candidate_count": item.get("pre_market_candidate_count", 0),
                    "target_market_candidate_count": item.get("target_market_candidate_count", 0),
                    "market_gate_filtered_count": item.get("market_gate_filtered_count", 0),
                    "qualified_count": item.get("qualified_count", 0),
                    "unique_count": item.get("unique_count", 0),
                    "followers_gte_3000_count": item.get("followers_gte_3000_count", 0),
                    "latest_video_lte_30d_count": item.get("latest_video_lte_30d_count", 0),
                    "latest_video_lte_45d_count": item.get("latest_video_lte_45d_count", 0),
                    "market_exact_count": market.get("exact", 0),
                    "market_inferred_count": market.get("inferred", 0),
                    "market_unknown_count": market.get("unknown", 0),
                    "market_known_ratio": item.get("market_known_ratio", 0),
                    "market_match_count": item.get("market_match_count", 0),
                    "query_evidence_covered_count": item.get("query_evidence_covered_count", 0),
                    "contact_available_count": contact.get("available_count", 0),
                    "contract_status": contract.get("status"),
                    "shortfall_reasons": "|".join(contract.get("shortfall_reasons") or []),
                    "human_relevance_status": relevance.get("status"),
                    "precision_at_30": relevance.get("precision_at_30"),
                    "bugs_or_errors": "|".join(item.get("bugs_or_errors") or []),
                }
            )
    return buffer.getvalue()


def _write_private_text(path: Path, text: str) -> None:
    path = path.absolute()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise ValueError("output_path_must_be_regular_file")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temp_path = Path(temporary)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(temp_path, path)
        os.chmod(path, 0o600)
    finally:
        temp_path.unlink(missing_ok=True)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Provider-free read-only KOL local-30 benchmark")
    parser.add_argument("--database-url", default=os.getenv("KOL_SEARCH_BENCH_DATABASE_URL", ""))
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    parser.add_argument("--labels-json", type=Path)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--candidate-limit", type=int, default=500)
    parser.add_argument("--as-of", type=date.fromisoformat)
    parser.add_argument("--min-market-known-ratio", type=float, default=0.5)
    parser.add_argument("--json-out", type=Path, required=True)
    parser.add_argument("--csv-out", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("--database-url or KOL_SEARCH_BENCH_DATABASE_URL is required")
    if args.runs < 1 or args.runs > 50:
        parser.error("--runs must be between 1 and 50")
    if args.candidate_limit < 30 or args.candidate_limit > 5000:
        parser.error("--candidate-limit must be between 30 and 5000")
    if not 0.0 <= args.min_market_known_ratio <= 1.0:
        parser.error("--min-market-known-ratio must be between 0 and 1")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    queries = load_golden_queries(args.queries)
    labels = _load_json(args.labels_json, label="human_labels") if args.labels_json else None
    as_of = args.as_of or date.today()
    report = run_benchmark(
        database_url=args.database_url,
        snapshot_dir=args.snapshot_dir,
        queries=queries,
        runs=args.runs,
        as_of=as_of,
        candidate_limit=args.candidate_limit,
        min_market_known_ratio=args.min_market_known_ratio,
        labels=labels,
    )
    _write_private_text(args.json_out, json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    _write_private_text(args.csv_out, _csv_text(report))
    out_json({
        "status": report.get("summary", {}).get("status"),
        "contract_pass": report.get("summary", {}).get("contract_pass"),
        "json_out": str(args.json_out),
        "csv_out": str(args.csv_out),
        "provider_calls_performed": False,
        "business_writes_performed": False,
    }, ensure_ascii=False, sort_keys=True)
    return 0 if report.get("summary", {}).get("status") == "complete" else 2


if __name__ == "__main__":
    sys.exit(main())
