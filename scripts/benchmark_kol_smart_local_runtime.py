#!/usr/bin/env python3
"""Reproducible Smart-local runtime benchmark against a disposable loopback DB.

Unlike the SQL supply benchmark, this executes the current ``kol-smart-search``
route and its provider-free recall/qualification engine.  Fixture construction
happens in a disposable PostgreSQL database; every measured route call runs in
one READ ONLY transaction with provider and session-write tripwires enabled.

The JSON report contains aggregate counts, hard-gate outcomes, timings and
shortfalls only.  It never serializes creator identities or contact data.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time
from typing import Any, Iterator, Sequence

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from app.api.routers import vkpi_kol_pool_search  # noqa: E402
from app.db.connection import PostgresCompatConnection  # noqa: E402
from app.domains.costs import product_catalog  # noqa: E402
from app.domains.kol import profile_recall, search_sessions  # noqa: E402
from app.platform import llm_gateway  # noqa: E402
from scripts.stdout_utils import out_json  # noqa: E402


SCHEMA_VERSION = "vkpi_kol_smart_local_runtime_v1"
DEFAULT_GOLDEN = ROOT / "scripts" / "kol_search_60_golden_queries.json"
DEFAULT_OUTPUT = Path("/private/tmp/vkpi-kol-smart-local-runtime.json")
FORBIDDEN_REPORT_KEYS = {
    "handle", "display_name", "email", "profile_url", "content_url",
    "contact", "contacts", "contact_channels", "other_contacts_json",
    "kol_pool_id", "canonical_key", "gate_evidence", "items", "buckets",
}
_READ_PREFIXES = ("SELECT", "WITH", "SHOW", "EXPLAIN")


def _percentile(values: Sequence[float], q: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    index = max(0, min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1))
    return round(ordered[index], 3)


def _stats(values: Sequence[float]) -> dict[str, float | int | None]:
    return {
        "n": len(values),
        "min": round(min(values), 3) if values else None,
        "p50": _percentile(values, 0.50),
        "p95": _percentile(values, 0.95),
        "max": round(max(values), 3) if values else None,
    }


def _count_stats(values: Sequence[int]) -> dict[str, int | bool | None]:
    return {
        "n": len(values),
        "min": min(values) if values else None,
        "max": max(values) if values else None,
        "stable": bool(values) and min(values) == max(values),
    }


def load_golden(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("queries") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or len(rows) != 5:
        raise ValueError("golden_must_have_exactly_5_queries")
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in rows:
        query_id = str(raw.get("id") or "").strip() if isinstance(raw, dict) else ""
        query = str(raw.get("query") or "").strip() if isinstance(raw, dict) else ""
        market = str(raw.get("market") or "").strip().upper() if isinstance(raw, dict) else ""
        platforms = [str(value).strip().lower() for value in raw.get("platforms") or []]
        terms = [str(value).strip().lower() for value in raw.get("evidence_terms") or []]
        if not query_id or query_id in seen or not query or not market or not platforms or not terms:
            raise ValueError(f"invalid_golden:{query_id or 'missing'}")
        if any(platform not in {"youtube", "instagram", "tiktok"} for platform in platforms):
            raise ValueError(f"unsupported_platform:{query_id}")
        seen.add(query_id)
        output.append({
            "id": query_id,
            "query": query,
            "market": market,
            "platforms": list(dict.fromkeys(platforms)),
            "terms": list(dict.fromkeys(terms)),
        })
    return output


def validate_rounds(value: int) -> int:
    rounds = int(value)
    if rounds < 3 or rounds > 5:
        raise ValueError("rounds_must_be_between_3_and_5")
    return rounds


def _assert_loopback(raw_conn: psycopg.Connection[Any]) -> None:
    row = raw_conn.execute(
        "SELECT COALESCE(inet_server_addr()::text, 'local_socket'), current_database()"
    ).fetchone()
    address = str(row[0] if row else "")
    if address != "local_socket":
        try:
            if not ipaddress.ip_interface(address).ip.is_loopback:
                raise RuntimeError("postgres_server_is_not_loopback")
        except ValueError as exc:
            raise RuntimeError("postgres_server_address_invalid") from exc


def _database_dsn(admin_dsn: str, database: str) -> str:
    params = conninfo_to_dict(admin_dsn)
    params["dbname"] = database
    return psycopg.conninfo.make_conninfo(**params)


def _fixture_topic(query_id: str) -> tuple[str, str]:
    mapping = {
        "us_youtube_lens_review": ("lens review optics tests", "lens review"),
        "gb_instagram_portrait": ("portrait bokeh fashion photography", "portrait"),
        "us_tiktok_video_creator": (
            "cinematic cinematography filmmaker videography",
            "cinematic cinematography",
        ),
        "ca_youtube_travel_landscape": ("travel landscape photography", "travel landscape"),
        "us_cross_platform_macro_product": ("macro product photography", "macro product photography"),
    }
    if query_id not in mapping:
        raise ValueError(f"unsupported_fixture_query:{query_id}")
    return mapping[query_id]


def _create_schema(conn: psycopg.Connection[Any]) -> None:
    conn.execute(
        """
        CREATE TABLE vkpi_kol_pool (
            id BIGINT PRIMARY KEY, platform TEXT, handle TEXT, display_name TEXT,
            profile_url TEXT, avatar_url TEXT, followers BIGINT, avg_views BIGINT,
            avg_comments BIGINT, engagement_rate NUMERIC, bio TEXT, country TEXT,
            primary_topic TEXT, content_style TEXT, language TEXT,
            secondary_topics_json TEXT DEFAULT '[]', brand_collaborations_json TEXT DEFAULT '[]',
            email TEXT, other_contacts_json TEXT DEFAULT '[]', raw_platform_data TEXT DEFAULT '{}',
            duplicate_of_id BIGINT
        );
        CREATE TABLE vkpi_kol_profile_index_entries (
            kol_pool_id BIGINT, collection_name TEXT, method TEXT, status TEXT,
            profile_type TEXT, creator_type_score NUMERIC, reviewer_type_score NUMERIC,
            type_reason TEXT, type_method TEXT, sufficiency TEXT, profile_text TEXT
        );
        CREATE TABLE vkpi_kol_video_evidence (
            id BIGINT PRIMARY KEY, kol_pool_id BIGINT, title TEXT, video_title TEXT,
            content_url TEXT, thumbnail_url TEXT, view_count BIGINT, like_count BIGINT,
            posted_at TIMESTAMPTZ, evidence_type TEXT, is_active BOOLEAN
        );
        CREATE TABLE vkpi_analysis_cache (
            target_type TEXT, target_id TEXT, derive_method TEXT, status TEXT, result JSONB
        );
        CREATE TABLE vkpi_kol_pool_favorites (kol_pool_id BIGINT);
        CREATE TABLE vkpi_project_kol_assignments (kol_pool_id BIGINT);
        CREATE TABLE vkpi_products (
            sku TEXT PRIMARY KEY, category_main TEXT, category_detail TEXT, model_name TEXT,
            marketing_name TEXT, price_usd NUMERIC, status TEXT, description TEXT,
            source_file TEXT, series TEXT, mount TEXT, product_url TEXT, specs_json TEXT,
            fit_tags_json TEXT, source_url TEXT, source_checked_at TIMESTAMPTZ,
            source_confidence NUMERIC, imported_at TIMESTAMPTZ, updated_at TIMESTAMPTZ
        );
        """
    )


def _seed_fixture(conn: psycopg.Connection[Any], golden: list[dict[str, Any]]) -> dict[str, int]:
    now = datetime.now(timezone.utc)
    pool_rows: list[tuple[Any, ...]] = []
    index_rows: list[tuple[Any, ...]] = []
    evidence_rows: list[tuple[Any, ...]] = []
    next_id = 1
    for golden_index, query in enumerate(golden):
        bio, topic = _fixture_topic(query["id"])
        platforms = list(query["platforms"])
        first_valid_handle = ""
        specifications = [
            *("valid" for _ in range(34)),
            "followers_low", "video_stale", "market_unknown", "canonical_duplicate",
        ]
        for offset, specification in enumerate(specifications):
            platform = platforms[offset % len(platforms)]
            handle = f"fixture_{golden_index}_{offset}"
            if specification == "valid" and not first_valid_handle:
                first_valid_handle = handle
            if specification == "canonical_duplicate":
                handle = first_valid_handle
            followers = 2_999 if specification == "followers_low" else 5_000 + offset
            country = "" if specification == "market_unknown" else query["market"]
            posted_at = now - timedelta(days=50 if specification == "video_stale" else 7)
            profile_type = "creator" if offset % 2 == 0 else "reviewer"
            pool_rows.append((
                next_id, platform, handle, f"Fixture {next_id}",
                f"https://fixture.invalid/{next_id}", "", followers, 10_000, 30, 0.04,
                bio, country, topic, "independent review", "en", "[]", "[]", "", "[]", "{}", None,
            ))
            index_rows.append((
                next_id, "vkpi_kol_profile_index_v1", "vector_recall", "ready",
                profile_type, 80 if profile_type == "creator" else 20,
                80 if profile_type == "reviewer" else 20,
                "fixture", "fixture_v1", "sufficient", bio,
            ))
            evidence_rows.append((
                next_id, next_id, f"{topic} field test", "",
                f"https://fixture.invalid/video/{next_id}", "", 20_000, 500,
                posted_at, "video", True,
            ))
            next_id += 1
    with conn.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO vkpi_kol_pool (
                id, platform, handle, display_name, profile_url, avatar_url, followers,
                avg_views, avg_comments, engagement_rate, bio, country, primary_topic,
                content_style, language, secondary_topics_json, brand_collaborations_json,
                email, other_contacts_json, raw_platform_data, duplicate_of_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            pool_rows,
        )
        cursor.executemany(
            """
            INSERT INTO vkpi_kol_profile_index_entries (
                kol_pool_id, collection_name, method, status, profile_type,
                creator_type_score, reviewer_type_score, type_reason, type_method,
                sufficiency, profile_text
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            index_rows,
        )
        cursor.executemany(
            """
            INSERT INTO vkpi_kol_video_evidence (
                id, kol_pool_id, title, video_title, content_url, thumbnail_url,
                view_count, like_count, posted_at, evidence_type, is_active
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            evidence_rows,
        )
    return {
        "fixture_candidate_rows": len(pool_rows),
        "fixture_video_rows": len(evidence_rows),
    }


class ReadOnlyAuditConnection:
    """Compat connection that rejects any application SQL outside read verbs."""

    def __init__(self, delegate: PostgresCompatConnection) -> None:
        self._delegate = delegate
        self.read_statement_count = 0
        self.write_statement_count = 0

    def execute(self, statement: str, params: Sequence[Any] | None = None):
        normalized = re.sub(r"^(?:\s|--[^\n]*\n|/\*.*?\*/)*", "", statement, flags=re.DOTALL).upper()
        if not normalized.startswith(_READ_PREFIXES):
            self.write_statement_count += 1
            raise RuntimeError("benchmark_application_write_sql_forbidden")
        self.read_statement_count += 1
        return self._delegate.execute(statement, params)


def _tripwire(label: str):
    def blocked(*_args: Any, **_kwargs: Any):
        raise RuntimeError(f"benchmark_forbidden_call:{label}")

    return blocked


@contextmanager
def _runtime_barriers(conn: ReadOnlyAuditConnection) -> Iterator[None]:
    patches = [
        (profile_recall, "get_conn", lambda: conn),
        (product_catalog, "get_conn", lambda: conn),
        (profile_recall, "_embed_query", _tripwire("embedding_provider")),
        (profile_recall, "_search_qdrant", _tripwire("vector_provider")),
        (profile_recall, "_llm_rerank_buckets", _tripwire("llm_rerank")),
        (llm_gateway, "invoke", _tripwire("llm_gateway")),
        (search_sessions, "create_session", _tripwire("session_create")),
        (search_sessions, "attach_recall_result", _tripwire("session_attach")),
    ]
    previous = [(module, name, getattr(module, name)) for module, name, _value in patches]
    old_rerank = os.environ.get("RECALL_LLM_RERANK_ENABLED")
    os.environ["RECALL_LLM_RERANK_ENABLED"] = "0"
    try:
        for module, name, value in patches:
            setattr(module, name, value)
        yield
    finally:
        for module, name, value in previous:
            setattr(module, name, value)
        if old_rerank is None:
            os.environ.pop("RECALL_LLM_RERANK_ENABLED", None)
        else:
            os.environ["RECALL_LLM_RERANK_ENABLED"] = old_rerank


async def _run_once(query: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    response = await vkpi_kol_pool_search.smart_kol_search(
        {
            "input": query["query"],
            "market": query["market"],
            "platforms": query["platforms"],
            "create_session": False,
            "include_new_discovery": False,
            "execute_new_discovery": False,
            # Deliberately adversarial old client limits: the server contract
            # must still own candidate_limit=500 and target=30.
            "candidate_limit": 1,
            "limit": 1,
        },
        staff={"id": 0, "role": "benchmark"},
    )
    route_ms = round((time.perf_counter() - started) * 1000.0, 3)
    result = response.get("result") if isinstance(response.get("result"), dict) else {}
    contract = result.get("local_qualification") if isinstance(result.get("local_qualification"), dict) else {}
    diagnostics = result.get("diagnostics") if isinstance(result.get("diagnostics"), dict) else {}
    items = result.get("items") if isinstance(result.get("items"), list) else []
    violations: Counter[str] = Counter()
    for item in items:
        gate = item.get("qualification_evidence") if isinstance(item, dict) else None
        if not isinstance(gate, dict) or not gate.get("passed"):
            violations["qualification_evidence"] += 1
            continue
        for gate_name in ("followers", "activity", "market", "platform", "relevance"):
            evidence = gate.get(gate_name) if isinstance(gate.get(gate_name), dict) else {}
            if not evidence.get("passed"):
                violations[gate_name] += 1
    if response.get("provider_calls") is not False:
        violations["provider_calls"] += 1
    if result.get("search_session") is not None or response.get("search_session") is not None:
        violations["session_created"] += 1
    policy = contract.get("policy") if isinstance(contract.get("policy"), dict) else {}
    if policy.get("target_count") != 30 or policy.get("min_followers") != 3_000:
        violations["server_policy"] += 1
    return {
        "returned_count": len(items),
        "qualified_count": int(contract.get("qualified_count") or 0),
        "shortfall": int(contract.get("shortfall") or 0),
        "retrieved_candidate_count": int(diagnostics.get("candidate_count") or 0),
        "prequalification_no_match_evidence": int(
            diagnostics.get("filtered_no_match_evidence") or 0
        ),
        "route_ms": route_ms,
        "engine_total_ms": float(contract.get("total_ms") or 0.0),
        "hard_gate_violations": dict(sorted(violations.items())),
        "rejected_by_reason": {
            str(key): int(value)
            for key, value in sorted((contract.get("rejected_by_reason") or {}).items())
        },
    }


def _summarize_query(query_id: str, runs: list[dict[str, Any]]) -> dict[str, Any]:
    rejection_totals: Counter[str] = Counter()
    violation_totals: Counter[str] = Counter()
    for run in runs:
        rejection_totals.update(run["rejected_by_reason"])
        violation_totals.update(run["hard_gate_violations"])
    returned = [int(run["returned_count"]) for run in runs]
    qualified = [int(run["qualified_count"]) for run in runs]
    shortfall = [int(run["shortfall"]) for run in runs]
    retrieved = [int(run["retrieved_candidate_count"]) for run in runs]
    prequalification_filtered = [
        int(run["prequalification_no_match_evidence"]) for run in runs
    ]
    return {
        "query_id": query_id,
        "rounds": len(runs),
        "returned_count": _count_stats(returned),
        "qualified_count": _count_stats(qualified),
        "shortfall": _count_stats(shortfall),
        "retrieved_candidate_count": _count_stats(retrieved),
        "prequalification_no_match_evidence": _count_stats(prequalification_filtered),
        "target_met": bool(returned) and min(returned) >= 30 and max(shortfall) == 0,
        "hard_gate_violations": dict(sorted(violation_totals.items())),
        "hard_gates_passed": not violation_totals,
        "rejected_by_reason_total": dict(sorted(rejection_totals.items())),
        "route_timing_ms": _stats([float(run["route_ms"]) for run in runs]),
        "engine_timing_ms": _stats([float(run["engine_total_ms"]) for run in runs]),
    }


def _assert_report_private(report: Any, *, path: str = "$") -> None:
    if isinstance(report, dict):
        for key, value in report.items():
            if str(key).lower() in FORBIDDEN_REPORT_KEYS:
                raise ValueError(f"identity_or_contact_key_forbidden:{path}.{key}")
            _assert_report_private(value, path=f"{path}.{key}")
    elif isinstance(report, list):
        for index, value in enumerate(report):
            _assert_report_private(value, path=f"{path}[{index}]")


def assert_hermetic_fixture_target(queries: list[dict[str, Any]]) -> None:
    """A controlled valid fixture must not manufacture an algorithm shortfall."""
    for query in queries:
        returned = query.get("returned_count") if isinstance(query.get("returned_count"), dict) else {}
        shortfall = query.get("shortfall") if isinstance(query.get("shortfall"), dict) else {}
        if (
            query.get("target_met") is not True
            or int(returned.get("min") or 0) < 30
            or int(shortfall.get("max") or 0) != 0
        ):
            raise RuntimeError(f"hermetic_fixture_query_failed_target:{query.get('query_id')}")


def validate_output_path(path: Path) -> Path:
    """Keep the operator path identity; resolving would hide a final symlink."""
    absolute = Path(os.path.abspath(os.fspath(path)))
    try:
        mode = os.lstat(absolute).st_mode
    except FileNotFoundError:
        return absolute
    if stat.S_ISLNK(mode):
        raise ValueError("output_symlink_forbidden")
    if not stat.S_ISREG(mode):
        raise ValueError("output_must_be_regular_file")
    return absolute


def write_report(path: Path, report: dict[str, Any]) -> None:
    _assert_report_private(report)
    path = validate_output_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    finally:
        if temporary.exists():
            temporary.unlink()


async def _benchmark_read_only(
    database_dsn: str,
    golden: list[dict[str, Any]],
    rounds: int,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    raw = psycopg.connect(database_dsn, autocommit=True)
    _assert_loopback(raw)
    raw.execute("BEGIN TRANSACTION READ ONLY")
    transaction_read_only = str(raw.execute("SHOW transaction_read_only").fetchone()[0]).lower() == "on"
    compat = PostgresCompatConnection(raw)
    audited = ReadOnlyAuditConnection(compat)
    try:
        summaries: list[dict[str, Any]] = []
        all_runs: list[dict[str, Any]] = []
        with _runtime_barriers(audited):
            for query in golden:
                runs = [await _run_once(query) for _round in range(rounds)]
                all_runs.extend(runs)
                summaries.append(_summarize_query(query["id"], runs))
        xid_assigned = bool(raw.execute("SELECT txid_current_if_assigned()").fetchone()[0])
        return (
            summaries,
            {
                "transaction_read_only": transaction_read_only,
                "application_read_statement_count": audited.read_statement_count,
                "application_write_statement_count": audited.write_statement_count,
                "transaction_id_assigned": xid_assigned,
            },
            {
                "route_timing_ms": _stats([float(run["route_ms"]) for run in all_runs]),
                "engine_timing_ms": _stats([
                    float(run["engine_total_ms"]) for run in all_runs
                ]),
            },
        )
    finally:
        raw.execute("ROLLBACK")
        raw.close()


def run_benchmark(*, admin_dsn: str, golden_path: Path, rounds: int) -> dict[str, Any]:
    golden = load_golden(golden_path)
    rounds = validate_rounds(rounds)
    database_name = f"vkpi_smart_runtime_{os.getpid()}_{secrets.token_hex(4)}"
    admin = psycopg.connect(admin_dsn, autocommit=True)
    _assert_loopback(admin)
    fixture_counts: dict[str, int] = {}
    dropped = False
    started = time.perf_counter()
    try:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
        database_dsn = _database_dsn(admin_dsn, database_name)
        fixture = psycopg.connect(database_dsn)
        try:
            _create_schema(fixture)
            fixture_counts = _seed_fixture(fixture, golden)
            fixture.commit()
        finally:
            fixture.close()
        queries, read_only, runtime_timing = asyncio.run(
            _benchmark_read_only(database_dsn, golden, rounds)
        )
        assert_hermetic_fixture_target(queries)
    finally:
        try:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database_name)))
            dropped = True
        finally:
            admin.close()
    total_shortfall = sum(int(summary["shortfall"]["max"] or 0) for summary in queries)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "claim_status": "runtime_algorithm_only",
        "scope": {
            "current_smart_route_and_engine_executed": True,
            "provider_free": True,
            "isolated_loopback_fixture_database": True,
            "measured_transaction_read_only": bool(read_only["transaction_read_only"]),
            "create_session": False,
            "production_http_tested": False,
            "production_ui_tested": False,
            "production_database_tested": False,
            "session_attach_tested": False,
            "deep_analysis_tested": False,
            "fixture_setup_writes_outside_measured_transaction": True,
        },
        "configuration": {
            "golden_query_count": len(golden),
            "rounds_per_query": rounds,
            "total_route_calls": len(golden) * rounds,
            "server_target_count": 30,
            "minimum_followers": 3_000,
            "maximum_video_age_days": 45,
            **fixture_counts,
        },
        "read_only_receipt": {
            **read_only,
            "disposable_database_dropped": dropped,
        },
        "queries": queries,
        "aggregate": {
            "query_count": len(queries),
            "hard_gate_pass_query_count": sum(bool(item["hard_gates_passed"]) for item in queries),
            "target_met_query_count": sum(bool(item["target_met"]) for item in queries),
            "zero_shortfall_query_count": sum(int(item["shortfall"]["max"] or 0) == 0 for item in queries),
            "total_max_shortfall": total_shortfall,
            **runtime_timing,
            "wall_clock_ms": round((time.perf_counter() - started) * 1000.0, 3),
        },
        "required_caveats": [
            "Synthetic disposable fixture; not production data quality or production KOL availability.",
            "Direct Python route invocation; production HTTP middleware and UI rendering were not tested.",
            "create_session=false; session persistence/attach behavior was not tested.",
            "Deep analysis, contact enrichment, online discovery and provider execution were not tested.",
            "Counts and timings validate only the current provider-free Smart-local runtime contract.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-dsn", default="postgresql://127.0.0.1/postgres")
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    # Preflight before disposable DB setup: an unsafe destination must not
    # cause fixture creation or any route execution.
    output_path = validate_output_path(args.output)
    report = run_benchmark(
        admin_dsn=str(args.admin_dsn),
        golden_path=args.golden.resolve(),
        rounds=validate_rounds(args.rounds),
    )
    write_report(output_path, report)
    out_json({
        "status": "ok",
        "output": str(output_path),
        "query_count": report["aggregate"]["query_count"],
        "total_route_calls": report["configuration"]["total_route_calls"],
        "hard_gate_pass_query_count": report["aggregate"]["hard_gate_pass_query_count"],
        "zero_shortfall_query_count": report["aggregate"]["zero_shortfall_query_count"],
    }, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
