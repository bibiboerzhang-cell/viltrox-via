#!/usr/bin/env python3
"""Non-destructive, manifest-driven acceptance for the running local V-KPI stack.

Stdout is machine-readable JSON; stderr is one concise PASS/FAIL line. The
runner permits loopback GET requests plus explicitly declared read-only JSON
POST probes and never emits its short-lived JWT or request bodies.
"""
from __future__ import annotations

import argparse
import copy
import ipaddress
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import quote, urlparse

from local_release_acceptance_contracts import (
    _nonempty_text,
    _strict_utc_iso,
    _validate_ask_evidence,
    _validate_ask_fact,
    _validate_ask_find_v2,
)
from local_release_acceptance_deadline import (
    DEFAULT_OVERALL_TIMEOUT_SECONDS,
    DEFAULT_TOKEN_TTL_SECONDS,
    MAX_TOKEN_TTL_SECONDS,
    OverallDeadline,
    TOKEN_EXPIRY_SAFETY_SECONDS,
    validate_acceptance_timing,
    validate_token_ttl,
)
from local_release_acceptance_transport import (
    IsolatedRequestFailure,
    run_isolated_http_request,
)
from release_auth_contract import (
    PG_READ_ONLY_OPTIONS,
    select_acceptance_pg_admin as _select_pg_admin,
    select_acceptance_sqlite_admin as _select_sqlite_admin,
    validated_local_database as _validated_local_database,
)
from stdout_utils import out as stdout_out

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR, BACKEND_DIR = ROOT / "scripts", ROOT / "backend"
DEFAULT_BASE_URL = "http://127.0.0.1:8102"
DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_MAX_REQUEST_BYTES, DEFAULT_MAX_RESPONSE_BYTES = 64 * 1024, 5 * 1024 * 1024
DEFAULT_MAX_WORKER_AGE_SECONDS = 180
REVIEWED_READ_ONLY_POST_PATHS = {"/api/admin/vkpi/intelligent/query"}
DEFAULT_MAX_SCHEDULER_EVIDENCE_AGE_SECONDS = 8 * 24 * 60 * 60
COCKPIT_BOARD_FAMILIES = [
    "dashboard", "kol-pool", "my-kol", "projects", "events", "shopify", "dealers", "triage",
    "dataQuery", "marketTrends", "skillStudio", "intelligent", "replyQueue", "sku360", "kolProfile",
    "launchpad", "autonomy", "marketVoice", "creativeLibrary", "strategyBoard", "gtmCommand",
]
REAL_STATES = {"active", "complete", "completed", "connected", "ok", "ready", "success"}
EMPTY_STATES = {"awaiting_data", "empty", "no_data", "no-data", "no_data_in_window", "no_results", "not_found"}
PENDING_STATES = {"collecting", "module_pending", "not_ready", "pending", "planned", "processing", "queued", "running"}
DEGRADED_STATES = {"blocked", "degraded", "error", "fail", "failed", "not_configured", "partial", "unavailable"}
DEFAULT_ALLOWED_STATES = ["real", "empty", "pending"]
_MISSING = object()
_CONTROL_KEYS = {"archived", "basis", "count", "filters", "generated_at", "limit", "method", "note", "offset", "reason", "scope", "status", "total", "window"}


def _ep(endpoint_id: str, family: str, path: str, **options: Any) -> dict[str, Any]:
    aliases = {
        "c": "contract", "d": "data_paths", "s": "state_paths", "t": "timeout_seconds",
        "req": "required_paths", "lists": "list_paths", "ints": "integer_paths", "bools": "boolean_paths",
        "counts": "count_matches", "statuses": "expected_statuses", "allowed": "allowed_states", "scan": "redaction_scan",
    }
    opts = {aliases.get(key, key): value for key, value in options.items()}
    bind = opts.get("bind")
    method = str(opts.pop("method", "GET")).upper()
    item = {
        "id": endpoint_id, "family": family, "method": method, "contract": opts.pop("contract", "object"),
        "data_paths": list(opts.pop("data_paths", ["$"])), "state_paths": list(opts.pop("state_paths", ["status", "data_status"])),
        "allowed_states": list(opts.pop("allowed_states", DEFAULT_ALLOWED_STATES)), "required": True,
        "auth": opts.pop("auth", True), "read_only_ack": True,
    }
    item["path_template" if bind else "path"] = path
    for key in ("required_paths", "list_paths", "integer_paths", "boolean_paths", "expected_statuses"):
        if key in opts:
            opts[key] = list(opts[key])
    item.update(opts)
    return item


E = _ep
SKU = {"sku": {"endpoint": "sku360.list", "paths": ["items.0.sku", "items.0.product_sku"]}}
KOL = {"kol_id": {"endpoint": "kol-pool.list", "paths": ["items.0.id", "items.0.kol_pool_id"]}}
DEFAULT_MANIFEST: dict[str, Any] = {
    "name": "vkpi-local-release-acceptance", "version": 2, "board_families": list(COCKPIT_BOARD_FAMILIES),
    "endpoints": [
        E("runtime.health", "runtime", "/health", c="health", d=["build", "trust"], allowed=["real"]),
        E("runtime.scheduler-registry", "runtime", "/api/admin/vkpi/settings/scheduler-tasks", c="scheduler_registry", d=["tasks"], req=["tasks", "status"], lists=["tasks"], allowed=["real"], scan=True),
        E("dashboard.summary", "dashboard", "/api/admin/vkpi/dashboard?window_days=30", d=["summary"], req=["summary"], t=20),
        E("kol-pool.list", "kol-pool", "/api/admin/vkpi/kol-pool?limit=25&offset=0&refresh_if_stale=false", c="list_response", d=["items"], lists=["items"]),
        E("my-kol.board-ext", "my-kol", "/api/admin/vkpi/my-kol/board-ext?days=30",
          d=["kpi_series.series", "funnel.items", "platform_dist.items", "fit_dist.items", "views_top.items", "recent_videos.items", "v_content.items"],
          s=["status", "kpi_series.status", "funnel.status", "platform_dist.status", "fit_dist.status", "contact_coverage.status", "views_top.status", "recent_videos.status", "v_content.status"], t=20),
        E("projects.list", "projects", "/api/admin/vkpi/projects?limit=25", c="list_response", d=["projects"], lists=["projects"]),
        E("projects.stages", "projects", "/api/admin/vkpi/projects/deliverable-stages-summary", c="list_response", d=["stages"], lists=["stages"]),
        E("events.list", "events", "/api/admin/vkpi/events?limit=25", c="list_response", d=["items"], lists=["items"]),
        E("events.inventory", "events", "/api/admin/vkpi/inventory", c="list_response", d=["items"], lists=["items"]),
        E("events.radar", "events", "/api/admin/vkpi/event-radar?limit=25&offset=0&time_window=upcoming",
          c="list_response", d=["items"], req=["items", "count", "page", "coverage_claim", "global_complete"],
          lists=["items"], ints=["count", "page.limit", "page.offset", "page.returned"], bools=["page.has_more"]),
        E("events.us-source-registry", "events", "/api/admin/vkpi/event-radar/us-source-registry",
          d=["event_sources", "source_jurisdiction_matrix.event_sources"],
          req=["ok", "event_sources", "source_jurisdiction_matrix", "import_gate", "claim_boundaries"], lists=["event_sources"]),
        E("events.candidate-staging", "events", "/api/admin/vkpi/event-radar/candidate-staging",
          d=["review_status", "promotion_gate_status"],
          req=["status", "candidate_type", "total", "review_status", "promotion_gate_status", "automatic_promotion"],
          ints=["total", "linked_field_evidence"], bools=["automatic_promotion"]),
        E("shopify.gmv", "shopify", "/api/admin/vkpi/shopify/gmv", d=["gmv_cents", "order_count", "attribution_gmv_cents", "order_ledger_gmv_cents"]),
        E("dealers.list", "dealers", "/api/admin/vkpi/dealers?limit=25", c="list_response", d=["dealers"], lists=["dealers"]),
        E("dealers.locations", "dealers", "/api/admin/vkpi/dealers/locations", c="list_response", d=["pins"], lists=["pins"]),
        E("dealers.coverage", "dealers", "/api/admin/vkpi/dealers/coverage",
          d=["contacts", "freshness", "identity", "passports", "us_jurisdiction_matrix"],
          req=["status", "total", "located", "coverage_claim", "global_complete", "claim_boundaries"],
          ints=["total", "located", "states", "countries"]),
        E("dealers.us-source-registry", "dealers", "/api/admin/vkpi/dealers/us-source-registry",
          d=["dealer_discovery_sources", "source_jurisdiction_matrix.dealer_discovery_sources"],
          req=["ok", "dealer_discovery_sources", "source_jurisdiction_matrix", "import_gate", "claim_boundaries"],
          lists=["dealer_discovery_sources"]),
        E("dealers.candidate-staging", "dealers", "/api/admin/vkpi/dealers/candidate-staging",
          d=["review_status", "promotion_gate_status"],
          req=["status", "candidate_type", "total", "review_status", "promotion_gate_status", "automatic_promotion"],
          ints=["total", "linked_field_evidence"], bools=["automatic_promotion"]),
        E("triage.data-quality", "triage", "/api/marketing/data-quality?limit=25", c="list_response", d=["issues", "summary"], req=["status", "issues", "count", "total_count"], lists=["issues"], ints=["count", "total_count"]),
        E("triage.jobs", "triage", "/api/admin/vkpi/triage/jobs?limit=25", c="list_response", d=["items"], lists=["items"], ints=["count"]),
        E("dataQuery.intents", "dataQuery", "/api/admin/vkpi/analytics/intents", c="list_response", d=["intents"], lists=["intents"]),
        E("dataQuery.canned-queries", "dataQuery", "/api/admin/vkpi/canned-queries", c="list_response", d=["questions"], lists=["questions"]),
        E("marketTrends.history", "marketTrends", "/api/intelligence/market/trends?history=true&limit=100", c="list_response", d=["observations"], lists=["observations"], ints=["count"], counts={"count": "observations"}),
        E("skillStudio.skills", "skillStudio", "/api/admin/vkpi/skills", c="object_or_array", d=["$", "skills", "items"]),
        E("intelligent.stats", "intelligent", "/api/admin/vkpi/intelligent/stats", d=["by_day", "total"]),
        E("intelligent.suggestions", "intelligent", "/api/admin/vkpi/intelligent/suggestions", c="list_response", d=["suggestions"], lists=["suggestions"]),
        E("intelligent.advisor-readiness", "intelligent", "/api/admin/vkpi/marketing-advisor/readiness", c="advisor_readiness",
          d=["persistence_ready", "action_mode"], req=["status", "provider_ready", "provider_called", "persistence_ready", "action_mode"],
          bools=["provider_ready", "provider_called", "persistence_ready"], allowed=["real", "degraded"]),
        E("intelligent.advisor-memory", "intelligent", "/api/admin/vkpi/marketing-advisor/memory?limit=25",
          d=["settings", "candidates", "facts"], req=["status", "settings", "candidates", "facts"],
          lists=["candidates", "facts"]),
        E("intelligent.ask-kol-overview", "intelligent", "/api/admin/vkpi/intelligent/query",
          method="POST", read_only_post=True, c="ask_find_v2", expected_intent="kol.pool.overview",
          json_body={"query": "目前 KOL 数量是多少？", "locale": "zh-CN", "thread_id": "local-release-acceptance",
                     "scope": "auto", "filters": {"intent": "kol.pool.overview", "limit": 20},
                     "mode": "deterministic", "client_request_id": "acceptance-kol-overview"},
          d=["facts", "evidence"], s=["status"], allowed=["real", "empty", "pending"], t=30, scan=True),
        E("intelligent.ask-video-26mm-evo", "intelligent", "/api/admin/vkpi/intelligent/query",
          method="POST", read_only_post=True, c="ask_find_v2", expected_intent="kol.video_topic.count",
          json_body={"query": "多少 KOL 做过 26mm EVO 视频？", "locale": "zh-CN", "thread_id": "local-release-acceptance",
                     "scope": "auto", "filters": {"intent": "kol.video_topic.count", "topic": "26mm EVO", "limit": 30},
                     "mode": "deterministic", "client_request_id": "acceptance-video-26mm-evo"},
          d=["facts", "evidence"], s=["status"], allowed=["real", "empty", "pending"], t=30, scan=True),
        E("intelligent.ask-project-search", "intelligent", "/api/admin/vkpi/intelligent/query",
          method="POST", read_only_post=True, c="ask_find_v2", expected_intent="project.search",
          json_body={"query": "搜索 Viltrox 项目", "locale": "zh-CN", "thread_id": "local-release-acceptance",
                     "scope": "auto", "filters": {"intent": "project.search", "keyword": "Viltrox", "limit": 20},
                     "mode": "deterministic", "client_request_id": "acceptance-project-search"},
          d=["facts", "evidence"], s=["status"], allowed=["real", "empty", "pending"], t=30, scan=True),
        E("intelligent.ask-weekly-market", "intelligent", "/api/admin/vkpi/intelligent/query",
          method="POST", read_only_post=True, c="ask_find_v2", expected_intent="market.viltrox.weekly_voice",
          json_body={"query": "总结本周市场对 Viltrox 的评价", "locale": "zh-CN", "thread_id": "local-release-acceptance",
                     "scope": "auto", "time_range": "7d",
                     "filters": {"intent": "market.viltrox.weekly_voice", "limit": 20},
                     "mode": "deterministic", "client_request_id": "acceptance-weekly-market"},
          d=["facts", "evidence"], s=["status"], allowed=["real", "empty", "pending"], t=40, scan=True),
        E("intelligent.global-search", "intelligent", "/api/admin/vkpi/global-search?q=Viltrox&limit=5",
          c="global_search", d=["kols", "projects", "events"], s=[],
          req=["q", "kols", "projects", "events", "source_status"],
          lists=["kols", "projects", "events"], allowed=["real", "empty"], t=20, scan=True),
        E("replyQueue.list", "replyQueue", "/api/admin/vkpi/reply-queue?limit=25&offset=0", c="list_response", d=["items"], req=["items", "count", "total", "offset", "limit"], lists=["items"], ints=["count", "total", "offset", "limit"]),
        E("sku360.list", "sku360", "/api/admin/vkpi/sku/list?query=&limit=10", c="list_response", d=["items"], lists=["items"]),
        E("sku360.profile", "sku360", "/api/admin/vkpi/sku/{sku}/profile", d=["product", "content", "comments", "bh_reviews"], bind=SKU, t=30, allowed=["real"]),
        E("kolProfile.detail-bundle", "kolProfile", "/api/admin/vkpi/kol-pool/{kol_id}/detail-bundle?video_limit=12&llm_limit=10", d=["item", "videos", "evidence", "llm_deep_analysis"], bind=KOL, t=30, allowed=["real"]),
        E("launchpad.assemble", "launchpad", "/api/admin/vkpi/launch/assemble?sku={sku}&max_roster=4", d=["product", "roster.members", "budgets.items", "schedule.items", "playbooks.items"], s=["status", "roster.status", "budgets.status", "schedule.status", "playbooks.status", "forecast.status"], bind=SKU, t=60),
        E("launchpad.publish-approvals", "launchpad", "/api/admin/vkpi/publish/pending?status=all&limit=25", c="list_response", d=["items"], lists=["items"]),
        E("autonomy.licenses", "autonomy", "/api/admin/vkpi/autonomy/licenses", c="list_response", d=["items"], lists=["items"]),
        E("marketVoice.report-ext", "marketVoice", "/api/admin/vkpi/market/voice-report-ext",
          d=["kpi_series.series", "sentiment_summary.series", "platform_dist.items", "line_voice.items", "topics.items", "language_dist.items", "competitor_voice.items", "prd_referrals.count"],
          s=["status", "kpi_series.status", "sentiment_summary.status", "alerts_state.status", "platform_dist.status", "line_voice.status", "topics.status", "language_dist.status", "competitor_voice.status", "prd_referrals.status"], t=40),
        E("creativeLibrary.search", "creativeLibrary", "/api/admin/vkpi/creative-segments/search?query=&style=&focal=&limit=50", c="list_response", d=["items"], lists=["items"], t=50),
        E("strategyBoard.benchmark", "strategyBoard", "/api/admin/vkpi/strategy/industry-benchmark?window_days=90", d=["brands", "ranking", "head_to_head", "viltrox"], t=25),
        E("strategyBoard.tracks", "strategyBoard", "/api/admin/vkpi/strategy/category-tracks", d=["category_tracks", "focal_tracks", "opportunities", "no_go", "mount_signals"], t=25),
        E("strategyBoard.performance", "strategyBoard", "/api/admin/vkpi/strategy/performance", d=["predictions", "bets", "fulfillment", "lessons"], t=25),
        E("gtmCommand.summary", "gtmCommand", "/api/admin/vkpi/market-brain/summary", d=["weekly_signals.items", "product_opportunities.items", "recommended_actions.items", "strategy_defaults", "learning_digest"], s=["weekly_signals.status", "product_opportunities.status", "recommended_actions.status", "strategy_defaults.status"], t=20),
        E("gtmCommand.preview", "gtmCommand", "/api/admin/vkpi/market-brain/gtm-plan/preview?sku={sku}&budget_usd=3000&window_days=30", d=["plan", "bets", "public_plan", "gtm_plan_id"], bind=SKU, t=60),
        E("reports.history", "reports", "/api/admin/vkpi/reports?archived=false&limit=25", c="report_history_list", d=["reports"], req=["reports", "count", "archived"], lists=["reports"], ints=["count"], bools=["archived"], counts={"count": "reports"}),
        E("reports.weekly-list", "reports", "/api/admin/vkpi/weekly-reports/list?limit=25", c="weekly_report_list", d=["reports"], req=["reports", "count"], lists=["reports"], ints=["count"], counts={"count": "reports"}, allowed=["real", "empty"]),
        E("reports.weekly-read", "reports", "/api/admin/vkpi/weekly-reports/{report_id}", c="weekly_report_read", d=["body_md", "title"], bind={"report_id": {"endpoint": "reports.weekly-list", "paths": ["reports.0.id"], "fallback": 0}}, allowed=["real", "empty"]),
        # Bind the read-contract probe to a terminal-success session. The newest
        # history entry may legitimately be partial (for example one profile
        # failed while 19 candidates remain usable); using items.0 without a
        # status filter made release acceptance depend on ordinary user data.
        E("search-history.list", "search-history", "/api/admin/vkpi/kol-search-history?limit=12&item_limit=5&archived=false&status=ready", c="search_history_list", d=["items"], req=["status", "count", "items", "filters"], lists=["items"], ints=["count"], counts={"count": "items"}, allowed=["real"], scan=True),
        E("search-history.read", "search-history", "/api/admin/vkpi/kol-search-sessions/{session_id}", c="search_session_read", bind={"session_id": {"endpoint": "search-history.list", "paths": ["items.0.id"]}}, allowed=["real", "pending"], scan=True),
        E("security.error-redaction", "security", "/api/admin/vkpi/reports?limit=not-an-integer", c="expected_error", statuses=[422], allowed=["real"], scan=True),
    ],
}


@dataclass(frozen=True)
class AuthContext:
    token: str
    role: str
    expires_in_seconds: int
    source: str = "local_db_security"

    def public_dict(self) -> dict[str, Any]:
        return {"source": self.source, "role": self.role, "expires_in_seconds": self.expires_in_seconds, "token_emitted": False}


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes
    headers: Mapping[str, str]
    latency_ms: float


class Transport(Protocol):
    def get(self, path: str, *, token: str | None, timeout_seconds: float) -> HttpResponse: ...

    def post(
        self,
        path: str,
        *,
        json_body: Mapping[str, Any],
        token: str | None,
        timeout_seconds: float,
    ) -> HttpResponse: ...


class TransportFailure(RuntimeError):
    def __init__(self, kind: str, latency_ms: float) -> None:
        super().__init__(kind)
        self.kind, self.latency_ms = kind, latency_ms


class UrlLibTransport:
    def __init__(self, base_url: str, *, max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES) -> None:
        self.base_url = validate_loopback_base_url(base_url)
        self.max_response_bytes = max(1024, int(max_response_bytes))

    def get(self, path: str, *, token: str | None, timeout_seconds: float) -> HttpResponse:
        return self._request("GET", path, token=token, timeout_seconds=timeout_seconds)

    def post(
        self,
        path: str,
        *,
        json_body: Mapping[str, Any],
        token: str | None,
        timeout_seconds: float,
    ) -> HttpResponse:
        return self._request(
            "POST",
            path,
            json_body=json_body,
            token=token,
            timeout_seconds=timeout_seconds,
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        token: str | None,
        timeout_seconds: float,
        json_body: Mapping[str, Any] | None = None,
    ) -> HttpResponse:
        try:
            response = run_isolated_http_request(
                base_url=self.base_url,
                method=method,
                path=path,
                token=token,
                timeout_seconds=timeout_seconds,
                max_response_bytes=self.max_response_bytes,
                json_body=json_body,
            )
        except IsolatedRequestFailure as exc:
            raise TransportFailure(exc.kind, exc.latency_ms) from None
        return HttpResponse(response.status, response.body, response.headers, response.latency_ms)


@dataclass
class Validation:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    state_override: str | None = None
    observed: dict[str, Any] = field(default_factory=dict)


def _is_loopback(host: str | None) -> bool:
    clean = str(host or "").strip().lower()
    if clean == "localhost":
        return True
    try:
        return ipaddress.ip_address(clean).is_loopback
    except ValueError:
        return False


def validate_loopback_base_url(base_url: str) -> str:
    clean, parsed = str(base_url or "").strip().rstrip("/"), urlparse(str(base_url or "").strip().rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base URL must be absolute HTTP(S)")
    if parsed.username or parsed.password:
        raise ValueError("base URL credentials are forbidden")
    if not _is_loopback(parsed.hostname):
        raise ValueError("base URL must use a loopback host")
    if parsed.query or parsed.fragment:
        raise ValueError("base URL must not contain query or fragment")
    return clean


def create_local_auth_context(ttl_seconds: int = DEFAULT_TOKEN_TTL_SECONDS) -> AuthContext:
    ttl = validate_token_ttl(ttl_seconds)
    os.environ.setdefault("LOG_LEVEL", "WARNING")
    for path in (SCRIPTS_DIR, BACKEND_DIR):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    from runtime_env import apply_runtime_env
    apply_runtime_env()
    from app.core.config import DB_PATH, DB_RUNTIME_BACKEND, DB_RUNTIME_URL, JWT_SECRET
    from app.core.security import JWT_AUDIENCE, JWT_ISSUER
    import jwt
    database_url = str(DB_RUNTIME_URL or "")
    expected_database = _validated_local_database(database_url, Path(DB_PATH), ROOT)
    if str(DB_RUNTIME_BACKEND).lower() == "postgres":
        import psycopg
        if not expected_database:
            raise RuntimeError("configured PostgreSQL database identity is missing")
        with psycopg.connect(database_url, options=PG_READ_ONLY_OPTIONS) as conn:
            user_id, role = _select_pg_admin(
                conn,
                expected_database=expected_database,
            )
            conn.rollback()
    else:
        with sqlite3.connect(f"file:{Path(DB_PATH).resolve()}?mode=ro", uri=True) as conn:
            user_id, role = _select_sqlite_admin(conn)
    now = int(time.time())
    token = jwt.encode({"uid": user_id, "role": role, "iss": JWT_ISSUER, "aud": JWT_AUDIENCE, "iat": now, "exp": now + ttl}, JWT_SECRET, algorithm="HS256")
    if not token:
        raise RuntimeError("local security module did not create a token")
    return AuthContext(str(token), role, ttl)


def _git_head() -> str:
    build_file = ROOT / "BUILD_GIT_SHA"
    if build_file.is_file() and not build_file.is_symlink():
        value = build_file.read_text(encoding="utf-8").strip().lower()
        if re.fullmatch(r"[0-9a-f]{40}", value):
            return value
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=5, check=True).stdout.strip()


def _latest_migration() -> str:
    names = sorted(p.name for p in (ROOT / "migrations").glob("*.sql") if not p.name.endswith("_down.sql"))
    if not names:
        raise RuntimeError("no local migrations found")
    return names[-1]


def _get_path(payload: Any, path: str) -> Any:
    if path in {"", "$"}:
        return payload
    current = payload
    for part in str(path).split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return _MISSING
    return current


def _extract_first(payload: Any, paths: list[str]) -> Any:
    for path in paths:
        value = _get_path(payload, path)
        if value is not _MISSING and value not in (None, ""):
            return value
    return _MISSING


def _meaningful(value: Any) -> bool:
    if value is _MISSING or value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return math.isfinite(float(value)) and float(value) != 0
    if isinstance(value, str):
        clean = value.strip().lower()
        return bool(clean) and clean not in REAL_STATES | EMPTY_STATES | PENDING_STATES | DEGRADED_STATES
    if isinstance(value, list):
        return bool(value)
    if isinstance(value, Mapping):
        return any(_meaningful(item) for key, item in value.items() if str(key) not in _CONTROL_KEYS)
    return True


def classify_data_state(payload: Any, spec: Mapping[str, Any], validation: Validation) -> str:
    if validation.state_override:
        return validation.state_override
    if spec.get("contract") == "expected_error":
        return "real"
    statuses = [str(value).strip().lower() for path in spec.get("state_paths", []) if (value := _get_path(payload, path)) not in (_MISSING, None)]
    if any(value in DEGRADED_STATES for value in statuses):
        return "degraded"
    if any(_get_path(payload, path) is False for path in spec.get("availability_paths", [])):
        return "degraded"
    if any(value in PENDING_STATES for value in statuses):
        return "pending"
    return "real" if any(_meaningful(_get_path(payload, path)) for path in spec.get("data_paths", ["$"])) else "empty"


def _parse_ts(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _validate_generic(payload: Any, spec: Mapping[str, Any], kind: str = "object") -> Validation:
    result = Validation()
    if not (isinstance(payload, Mapping) if kind == "object" else isinstance(payload, (Mapping, list))):
        result.errors.append(f"response JSON must be {kind}")
        return result
    for path in spec.get("required_paths", []):
        if _get_path(payload, path) is _MISSING:
            result.errors.append(f"missing contract path: {path}")
    typed = (("list_paths", list, "list"), ("integer_paths", int, "integer"), ("boolean_paths", bool, "boolean"))
    for key, cls, label in typed:
        for path in spec.get(key, []):
            value = _get_path(payload, path)
            bad_int = key == "integer_paths" and isinstance(value, bool)
            if value is not _MISSING and (bad_int or not isinstance(value, cls)):
                result.errors.append(f"contract path is not a {label}: {path}")
    for count_path, list_path in spec.get("count_matches", {}).items():
        count, items = _get_path(payload, count_path), _get_path(payload, list_path)
        if isinstance(count, int) and not isinstance(count, bool) and isinstance(items, list) and count != len(items):
            result.errors.append(f"count contract mismatch: {count_path} != len({list_path})")
    return result


def _validate_health(payload: Any, spec: Mapping[str, Any], runner: "AcceptanceRunner") -> Validation:
    result = _validate_generic(payload, spec)
    if result.errors or not isinstance(payload, Mapping):
        return result
    build = payload.get("build") if isinstance(payload.get("build"), Mapping) else {}
    trust = payload.get("trust") if isinstance(payload.get("trust"), Mapping) else {}
    sha = str(build.get("git_sha") or "")
    if payload.get("status") != "ok": result.errors.append("health status is not ok")
    if not sha or sha == "unknown": result.errors.append("health build SHA is missing")
    if runner.local_head and sha != runner.local_head: result.errors.append("server SHA does not match local HEAD")
    if build.get("client_matches_server") is not True: result.errors.append("frontend build does not match server")
    if trust.get("sha_aligned") is not True: result.errors.append("health trust SHA alignment is not true")
    for key in ("server_git_sha", "client_git_sha", "worker_sha"):
        if not trust.get(key): result.errors.append(f"health trust field is missing: {key}")
        elif sha and str(trust.get(key)) != sha: result.errors.append(f"health trust SHA mismatch: {key}")
    migration = str(trust.get("db_migration_max") or "")
    if not migration: result.errors.append("applied migration max is missing")
    elif runner.latest_migration and migration != runner.latest_migration: result.errors.append("applied migration max does not match local manifest")
    if trust.get("worker_online") is not True: result.errors.append("worker is not online")
    heartbeat = _parse_ts(trust.get("worker_heartbeat"))
    age = None if heartbeat is None else max(0.0, (runner.now_fn() - heartbeat).total_seconds())
    if heartbeat is None: result.errors.append("worker heartbeat is missing or invalid")
    elif age > runner.max_worker_age_seconds: result.errors.append("worker heartbeat is stale")
    scheduler = trust.get("scheduler_status")
    total = int(scheduler.get("total") or 0) if isinstance(scheduler, Mapping) else 0
    enabled = int(scheduler.get("enabled") or 0) if isinstance(scheduler, Mapping) else 0
    if not isinstance(scheduler, Mapping): result.errors.append("scheduler registration trust is unavailable")
    elif total <= 0 or enabled <= 0 or enabled > total: result.errors.append("scheduler registration counts are not trustworthy")
    result.observed = {"git_sha": sha, "migration": migration, "worker_heartbeat": str(trust.get("worker_heartbeat") or ""), "worker_heartbeat_age_seconds": round(age, 1) if age is not None else None, "scheduler_total": total, "scheduler_enabled": enabled}
    return result


def _validate_scheduler(payload: Any, spec: Mapping[str, Any], runner: "AcceptanceRunner") -> Validation:
    result = _validate_generic(payload, spec)
    if result.errors or not isinstance(payload, Mapping):
        return result
    tasks, status = payload.get("tasks"), payload.get("status")
    if not isinstance(tasks, list) or not isinstance(status, Mapping):
        return result
    total, enabled = int(status.get("total") or 0), int(status.get("enabled") or 0)
    enabled_tasks = [task for task in tasks if isinstance(task, Mapping) and task.get("enabled") is True]
    if status.get("available") is not True: result.errors.append("scheduler registry is unavailable")
    if total != len(tasks): result.errors.append("scheduler total does not match task rows")
    if enabled != len(enabled_tasks): result.errors.append("scheduler enabled count does not match task rows")
    if total <= 0 or enabled <= 0: result.errors.append("scheduler registry has no enabled tasks")
    health_counts = _get_path(runner.payloads.get("runtime.health"), "trust.scheduler_status")
    if isinstance(health_counts, Mapping) and (int(health_counts.get("total") or 0), int(health_counts.get("enabled") or 0)) != (total, enabled):
        result.errors.append("scheduler registry counts disagree with health trust")
    evidence = [parsed for task in enabled_tasks for key in ("last_success_at", "last_run_at") if (parsed := _parse_ts(task.get(key))) is not None]
    if enabled_tasks and not evidence:
        result.warnings.append("enabled scheduler tasks have no execution evidence")
        result.state_override = "degraded"
    elif evidence:
        latest, age = max(evidence), max(0.0, (runner.now_fn() - max(evidence)).total_seconds())
        if age > runner.max_scheduler_evidence_age_seconds:
            result.warnings.append("scheduler execution evidence is stale")
            result.state_override = "degraded"
        result.observed.update({"latest_execution_evidence": latest.isoformat().replace("+00:00", "Z"), "latest_execution_age_seconds": round(age, 1)})
    result.observed.update({"total": total, "enabled": enabled})
    return result


def _item_errors(payload: Mapping[str, Any], path: str, fields: list[str]) -> list[str]:
    items, errors = _get_path(payload, path), []
    if not isinstance(items, list):
        return errors
    for index, item in enumerate(items):
        if not isinstance(item, Mapping): errors.append(f"{path}[{index}] is not an object")
        elif missing := [field for field in fields if field not in item]: errors.append(f"{path}[{index}] missing fields: {','.join(missing)}")
    return errors



def _validate_global_search(payload: Mapping[str, Any], result: Validation) -> None:
    degraded_sources: list[str] = []
    source_status = payload.get("source_status")
    if not isinstance(source_status, Mapping):
        result.errors.append("global search source_status is not an object")
        result.state_override = "degraded"
        return
    for source in ("kols", "projects", "events"):
        items, status_item = payload.get(source), source_status.get(source)
        if not isinstance(items, list):
            result.errors.append(f"global search {source} is not a list")
            degraded_sources.append(source)
        if not isinstance(status_item, Mapping):
            result.errors.append(f"global search source_status.{source} is missing or invalid")
            degraded_sources.append(source)
            continue
        status = str(status_item.get("status") or "").strip().lower()
        if status not in {"ready", "degraded", "error", "blocked"}:
            result.errors.append(f"global search source_status.{source}.status is invalid")
            degraded_sources.append(source)
        elif status != "ready":
            degraded_sources.append(source)
        count = status_item.get("result_count")
        if isinstance(count, bool) or not isinstance(count, int):
            result.errors.append(f"global search source_status.{source}.result_count is not an integer")
        elif isinstance(items, list) and count != len(items):
            result.errors.append(f"global search source_status.{source}.result_count does not match results")
    if degraded_sources:
        result.state_override = "degraded"
        result.warnings.append("global search has unavailable sources: " + ",".join(sorted(set(degraded_sources))))


def _validate_contract(payload: Any, spec: Mapping[str, Any], runner: "AcceptanceRunner") -> Validation:
    name = str(spec.get("contract") or "object")
    if name == "health": return _validate_health(payload, spec, runner)
    if name == "scheduler_registry": return _validate_scheduler(payload, spec, runner)
    result = _validate_generic(payload, spec, "object_or_array" if name == "object_or_array" else "object")
    if result.errors or not isinstance(payload, Mapping): return result
    if name == "ask_find_v2":
        _validate_ask_find_v2(payload, spec, result)
    elif name == "global_search":
        _validate_global_search(payload, result)
    elif name == "report_history_list":
        result.errors += _item_errors(payload, "reports", ["id", "report_uid", "report_type", "period_start", "period_end", "status", "schema_version", "data_status"])
    elif name == "advisor_readiness":
        # External AI is an explicitly optional first-release scope.  It is
        # release-safe only when the provider path is demonstrably fail-closed:
        # persistence is healthy, no provider was called, the operator switch
        # is off and the UI remains draft-only.  Any other degraded reason is a
        # real failure and must not be washed green by the allowed state.
        if payload.get("persistence_ready") is not True:
            result.errors.append("advisor persistence is not ready")
        if payload.get("action_mode") != "draft_only":
            result.errors.append("advisor action mode is not draft_only")
        if payload.get("provider_ready") is not True:
            if payload.get("provider_called") is not False:
                result.errors.append("disabled advisor must prove provider_called=false")
            if payload.get("operator_enabled") is not False:
                result.errors.append("degraded advisor is not explicitly operator-disabled")
            if payload.get("reason") != "advisor_external_ai_operator_disabled":
                result.errors.append("degraded advisor reason is not the reviewed first-release exclusion")
            if not result.errors:
                result.warnings.append("external advisor AI is explicitly disabled for this release scope")
    elif name == "weekly_report_list":
        result.errors += _item_errors(payload, "reports", ["id", "staff_id", "template_key", "title", "status", "generated_at"])
    elif name == "weekly_report_read":
        typed_empty_markers = (
            payload.get("status") == "not_found",
            payload.get("data_status") == "no_data",
            payload.get("reason") == "report_not_found",
        )
        if any(typed_empty_markers):
            expected_keys = {"status", "data_status", "report_id", "reason"}
            if set(payload) != expected_keys:
                result.errors.append("weekly report typed empty fields are not exact")
            if payload.get("status") != "not_found":
                result.errors.append("weekly report typed empty status is invalid")
            if payload.get("data_status") != "no_data":
                result.errors.append("weekly report typed empty data_status is invalid")
            if payload.get("reason") != "report_not_found":
                result.errors.append("weekly report typed empty reason is invalid")
            report_id = payload.get("report_id")
            if isinstance(report_id, bool) or not isinstance(report_id, int):
                result.errors.append("weekly report typed empty report_id is not an integer")
            if not result.errors:
                result.state_override = "empty"
                result.warnings.append("weekly report does not exist")
            return result
        invalidated = payload.get("truth_invalidated") is True
        required = ("id", "staff_id", "template_key", "title", "status", "generated_at")
        for path in required:
            if _get_path(payload, path) is _MISSING: result.errors.append(f"missing weekly report read field: {path}")
        if invalidated:
            if payload.get("data_status") != "unavailable":
                result.errors.append("truth-invalidated weekly report must be unavailable")
            if _get_path(payload, "body_md") is not _MISSING:
                result.errors.append("truth-invalidated weekly report leaked withdrawn body_md")
            if not str(payload.get("truth_invalidation_reason") or "").strip():
                result.errors.append("truth-invalidated weekly report is missing its reason")
            result.state_override = "empty"
            result.warnings.append("weekly report body was intentionally withdrawn by truth invalidation")
        elif _get_path(payload, "body_md") is _MISSING:
            result.errors.append("missing weekly report read field: body_md")
    elif name == "search_history_list":
        result.errors += _item_errors(payload, "items", ["id", "query_text", "query_type", "source", "status", "created_at", "updated_at"])
    elif name == "search_session_read":
        for path in ("id", "query_text", "query_type", "source", "status", "items", "count", "counts"):
            if _get_path(payload, path) is _MISSING: result.errors.append(f"missing search session read field: {path}")
        items = _get_path(payload, "items")
        if items is not _MISSING and not isinstance(items, list):
            result.errors.append("search session items is not a list")
        # Progressive KOL analysis deliberately returns a visible profile stage
        # while audience/video/comment work continues. Treat that honest partial
        # state as pending only when the session contains data and no failed item;
        # a partial session with failures remains degraded and blocks release.
        if str(payload.get("status") or "").strip().lower() == "partial" and isinstance(items, list) and items:
            failed_states = {"blocked", "crawl_failed", "error", "failed", "profile_crawl_failed"}
            item_states = {str(item.get("status") or "").strip().lower() for item in items if isinstance(item, Mapping)}
            if not item_states.intersection(failed_states):
                result.state_override = "pending"
                result.warnings.append("search session is partially materialized with no failed items")
    elif name == "expected_error" and _get_path(payload, "detail") is _MISSING:
        result.errors.append("expected JSON error detail is missing")
    return result


_REDACTION_PATTERNS = [
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("database_dsn", re.compile(r"(?i)(?:postgres(?:ql)?|mysql|redis)://[^\s/:@]+:[^\s@]+@")),
    ("bearer_token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}")),
    ("provider_key", re.compile(r"\b(?:sk-[A-Za-z0-9_-]{12,}|AIza[A-Za-z0-9_-]{20,})\b")),
    ("credential_assignment", re.compile(r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token|authorization)\s*[:=]\s*[\"']?[^\s\"']{8,}")),
    ("stack_trace", re.compile(r"(?i)(?:Traceback \(most recent call last\)|\bFile \"/|psycopg\.errors|sqlalchemy\.exc)")),
    ("local_path", re.compile(r"(?:/Users/[^\s\"']+|/opt/viltrox[^\s\"']*)")),
]


def scan_redaction(body: bytes, headers: Mapping[str, str]) -> list[str]:
    combined = body.decode("utf-8", errors="replace") + "\n" + "\n".join(f"{k}: {v}" for k, v in headers.items())
    return sorted({label for label, pattern in _REDACTION_PATTERNS if pattern.search(combined)})


def _safe_json(body: bytes) -> tuple[Any, str | None]:
    if not body: return None, "response body is empty"
    try: return json.loads(body.decode("utf-8")), None
    except (UnicodeDecodeError, json.JSONDecodeError): return None, "response is not valid UTF-8 JSON"


def _render_path(spec: Mapping[str, Any], payloads: Mapping[str, Any]) -> tuple[str | None, str | None]:
    path = str(spec.get("path") or spec.get("path_template") or "")
    for name, binding in spec.get("bind", {}).items():
        source_id, paths = str(binding.get("endpoint") or ""), [str(item) for item in binding.get("paths", [])]
        if source_id not in payloads: return None, f"dependency response unavailable: {source_id}"
        value = _extract_first(payloads[source_id], paths)
        if value is _MISSING:
            if "fallback" not in binding:
                return None, f"dependency produced no value: {source_id}"
            value = binding["fallback"]
        path = path.replace("{" + str(name) + "}", quote(str(value), safe=""))
    return (None, "unresolved path template") if "{" in path or "}" in path else (path, None)


def validate_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(manifest, Mapping) or not isinstance(manifest.get("endpoints"), list):
        raise ValueError("manifest must contain an endpoints list")
    normalized, seen = copy.deepcopy(dict(manifest)), set()
    for item in normalized["endpoints"]:
        if not isinstance(item, dict): raise ValueError("manifest endpoints must be objects")
        endpoint_id, method = str(item.get("id") or "").strip(), str(item.get("method") or "GET").upper()
        if not endpoint_id or endpoint_id in seen: raise ValueError("manifest endpoint IDs must be unique and non-empty")
        seen.add(endpoint_id)
        if method not in {"GET", "POST"}: raise ValueError(f"non-destructive manifest forbids method: {method}")
        if item.get("read_only_ack") is not True: raise ValueError(f"endpoint lacks read_only_ack: {endpoint_id}")
        path = str(item.get("path") or item.get("path_template") or "")
        if not path.startswith("/") or path.startswith("//") or urlparse(path).scheme: raise ValueError(f"endpoint path must be local and relative: {endpoint_id}")
        if method == "POST":
            if item.get("read_only_post") is not True:
                raise ValueError(f"POST endpoint lacks explicit read_only_post declaration: {endpoint_id}")
            body = item.get("json_body")
            if not isinstance(body, Mapping):
                raise ValueError(f"read-only POST requires a JSON object body: {endpoint_id}")
            try:
                serialized = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            except (TypeError, ValueError) as exc:
                raise ValueError(f"read-only POST body is not valid JSON: {endpoint_id}") from exc
            if len(serialized) > DEFAULT_MAX_REQUEST_BYTES:
                raise ValueError(f"read-only POST body exceeds size limit: {endpoint_id}")
            if path not in REVIEWED_READ_ONLY_POST_PATHS or item.get("contract") != "ask_find_v2":
                raise ValueError(f"POST endpoint is not on the reviewed read-only allowlist: {endpoint_id}")
            if item.get("auth", True) is not True:
                raise ValueError(f"read-only POST must require authentication: {endpoint_id}")
        elif "json_body" in item or item.get("read_only_post") is True:
            raise ValueError(f"GET endpoint must not declare a request body: {endpoint_id}")
        item.update({"method": method, "required": item.get("required", True), "auth": item.get("auth", True)})
        item.setdefault("expected_statuses", [200]); item.setdefault("allowed_states", list(DEFAULT_ALLOWED_STATES))
        item.setdefault("data_paths", ["$"]); item.setdefault("state_paths", ["status", "data_status"])
    families = normalized.get("board_families", [])
    if not isinstance(families, list) or any(not isinstance(item, str) for item in families): raise ValueError("board_families must be a string list")
    return normalized


def load_manifest(path: Path | None = None) -> dict[str, Any]:
    if path is None: return validate_manifest(DEFAULT_MANIFEST)
    try: payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc: raise ValueError("manifest is not readable JSON") from exc
    return validate_manifest(payload)


class AcceptanceRunner:
    def __init__(self, *, base_url: str, manifest: Mapping[str, Any], auth: AuthContext, transport: Transport,
                 local_head: str, latest_migration: str, default_timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
                 overall_timeout_seconds: float = DEFAULT_OVERALL_TIMEOUT_SECONDS,
                 max_worker_age_seconds: int = DEFAULT_MAX_WORKER_AGE_SECONDS,
                 max_scheduler_evidence_age_seconds: int = DEFAULT_MAX_SCHEDULER_EVIDENCE_AGE_SECONDS,
                 now_fn: Callable[[], datetime] | None = None,
                 monotonic_fn: Callable[[], float] = time.perf_counter) -> None:
        self.base_url, self.manifest, self.auth, self.transport = validate_loopback_base_url(base_url), validate_manifest(manifest), auth, transport
        self.local_head, self.latest_migration = str(local_head or ""), str(latest_migration or "")
        _, self.overall_timeout_seconds = validate_acceptance_timing(
            self.auth.expires_in_seconds, overall_timeout_seconds
        )
        self.default_timeout_seconds, self.max_worker_age_seconds = max(.1, float(default_timeout_seconds)), max(1, int(max_worker_age_seconds))
        self.max_scheduler_evidence_age_seconds = max(1, int(max_scheduler_evidence_age_seconds))
        self.now_fn, self.payloads, self.observed = now_fn or (lambda: datetime.now(timezone.utc)), {}, {}
        self.deadline = OverallDeadline(self.overall_timeout_seconds, monotonic_fn=monotonic_fn)
        self.deadline_exhausted = False

    def _run_endpoint(self, spec: Mapping[str, Any]) -> dict[str, Any]:
        endpoint_id, path_error = str(spec["id"]), None
        path, path_error = _render_path(spec, self.payloads)
        method = str(spec.get("method") or "GET").upper()
        base = {"id": endpoint_id, "family": str(spec.get("family") or "unclassified"), "method": method,
                "path": path or str(spec.get("path_template") or spec.get("path") or ""), "required": bool(spec.get("required", True)),
                "contract": str(spec.get("contract") or "object")}
        requested_timeout = float(spec.get("timeout_seconds") or self.default_timeout_seconds)
        bounded_timeout = self.deadline.bounded_timeout(requested_timeout)
        if bounded_timeout is None:
            self.deadline_exhausted = True
            return {**base, "pass": False, "http_status": None, "latency_ms": 0., "response_bytes": 0,
                    "data_state": "degraded", "errors": ["acceptance overall deadline exhausted before request"],
                    "warnings": [], "redaction_findings": []}
        if path_error:
            return {**base, "pass": False, "http_status": None, "latency_ms": 0., "response_bytes": 0, "data_state": "empty",
                    "errors": [path_error], "warnings": [], "redaction_findings": []}
        try:
            request_options = {
                "token": self.auth.token if spec.get("auth", True) else None,
                "timeout_seconds": bounded_timeout,
            }
            if method == "POST":
                response = self.transport.post(str(path), json_body=spec["json_body"], **request_options)
            else:
                response = self.transport.get(str(path), **request_options)
        except TransportFailure as exc:
            if self.deadline.exhausted():
                self.deadline_exhausted = True
                error = "acceptance overall deadline exhausted during request"
            else:
                error = f"HTTP transport failure: {exc.kind}"
            return {**base, "pass": False, "http_status": None, "latency_ms": round(exc.latency_ms, 1), "response_bytes": 0,
                    "data_state": "degraded", "errors": [error], "warnings": [], "redaction_findings": []}
        payload, json_error = _safe_json(response.body)
        expected = [int(item) for item in spec.get("expected_statuses", [200])]
        errors = [] if response.status in expected else [f"unexpected HTTP status: {response.status}"]
        if self.deadline.exhausted():
            self.deadline_exhausted = True
            errors.append("acceptance overall deadline exhausted during request")
        content_type = next((str(value).lower() for key, value in response.headers.items() if str(key).lower() == "content-type"), "")
        if method == "POST" and "application/json" not in content_type:
            errors.append("read-only POST response content-type is not JSON")
        if json_error:
            errors.append(json_error); validation, state = Validation(), "degraded"
        else:
            validation = _validate_contract(payload, spec, self); errors += validation.errors
            state = classify_data_state(payload, spec, validation); self.payloads[endpoint_id] = payload
            if validation.observed: self.observed[endpoint_id] = validation.observed
        findings = scan_redaction(response.body, response.headers) if spec.get("redaction_scan") or response.status >= 400 else []
        if findings: errors.append("response failed error-redaction scan")
        if state not in {str(item) for item in spec.get("allowed_states", DEFAULT_ALLOWED_STATES)}:
            errors.append(f"data state is not release-acceptable: {state}")
        return {**base, "pass": not errors, "http_status": response.status, "latency_ms": round(response.latency_ms, 1),
                "response_bytes": len(response.body), "data_state": state, "errors": errors, "warnings": validation.warnings,
                "redaction_findings": findings}

    def run(self) -> dict[str, Any]:
        started, perf = self.now_fn(), self.deadline.start()
        self.deadline_exhausted = False
        results = [self._run_endpoint(spec) for spec in self.manifest["endpoints"]]
        finished = self.now_fn()
        duration = (self.deadline.monotonic_fn() - perf) * 1000
        required = [item for item in results if item["required"]]; failed = [item for item in required if not item["pass"]]
        states = {state: sum(item["data_state"] == state for item in results) for state in ("real", "empty", "pending", "degraded")}
        wanted = [str(item) for item in self.manifest.get("board_families", [])]; represented = {str(item["family"]) for item in results}
        missing = sorted(set(wanted) - represented); families = {}
        for family in sorted(represented):
            rows = [item for item in results if item["family"] == family and item["required"]]
            families[family] = {"pass": bool(rows) and all(item["pass"] for item in rows), "endpoints": len(rows), "failed": [item["id"] for item in rows if not item["pass"]]}
        latencies = sorted(float(item["latency_ms"]) for item in results if item["http_status"] is not None)
        p95 = latencies[max(0, math.ceil(len(latencies) * .95) - 1)] if latencies else 0.
        http_methods = sorted({str(item.get("method") or "GET").upper() for item in self.manifest["endpoints"]})
        return {
            "schema_version": "vkpi.local-release-acceptance.v1", "run_id": str(uuid.uuid4()),
            "started_at": started.isoformat().replace("+00:00", "Z"), "finished_at": finished.isoformat().replace("+00:00", "Z"),
            "duration_ms": round(duration, 1), "base_url": self.base_url,
            "manifest": {"name": self.manifest.get("name"), "version": self.manifest.get("version"), "endpoint_count": len(results)},
            "repo": {"head": self.local_head, "latest_migration": self.latest_migration}, "auth": self.auth.public_dict(),
            "safety": {"http_methods": http_methods, "loopback_only": True, "json_only": True,
                       "read_only_post_requests": sum(item["method"] == "POST" for item in self.manifest["endpoints"]),
                       "paid_provider_calls": False, "business_record_mutations": False,
                       "token_emitted": False, "request_bodies_emitted": False,
                       "overall_deadline_seconds": round(self.overall_timeout_seconds, 3),
                       "token_expiry_safety_seconds": TOKEN_EXPIRY_SAFETY_SECONDS,
                       "deadline_exhausted": self.deadline_exhausted},
            "overall": {"pass": not failed and not missing, "required_passed": len(required) - len(failed), "required_total": len(required),
                        "failed_endpoint_ids": [item["id"] for item in failed], "data_states": states, "latency_p95_ms": round(p95, 1),
                        "deadline_exhausted": self.deadline_exhausted},
            "coverage": {"required_board_families": wanted, "represented_board_families": sorted(set(wanted) & represented), "missing_board_families": missing},
            "families": families, "trust_observed": self.observed, "endpoints": results, "strict_runtime_binding": {"nonce": os.getenv("VKPI_STRICT_RUN_NONCE", ""), "ports": os.getenv("VKPI_STRICT_RUNTIME_PORTS", ""), "candidate_sha256": os.getenv("VKPI_STRICT_CANDIDATE_SHA256", "")},
        }


def concise_summary(report: Mapping[str, Any]) -> str:
    overall, states = report.get("overall", {}), report.get("overall", {}).get("data_states", {})
    text = (f"{'PASS' if overall.get('pass') is True else 'FAIL'} local release acceptance: "
            f"{overall.get('required_passed', 0)}/{overall.get('required_total', 0)} required endpoints; "
            f"states real={states.get('real', 0)} empty={states.get('empty', 0)} pending={states.get('pending', 0)} degraded={states.get('degraded', 0)}; "
            f"p95={overall.get('latency_p95_ms', 0)}ms")
    failed = overall.get("failed_endpoint_ids", [])
    if failed: text += "; failed=" + ",".join(str(item) for item in failed[:8]) + (f",+{len(failed)-8}" if len(failed) > 8 else "")
    # p95 预算闸(2026-07-18,先警告不硬拦):基线实测 暖 251-258ms / 冷 ~1237ms /
    # freeze 带载 1141-1262ms。默认阈值 1500ms 高于带载最差,只抓真回归。
    warn_ms = float(os.getenv("VKPI_P95_WARN_MS", "1500") or 1500)
    p95_val = float(overall.get("latency_p95_ms", 0) or 0)
    if p95_val > warn_ms:
        text += f"; WARNING p95 {p95_val:.0f}ms exceeds budget {warn_ms:.0f}ms (baseline warm 255ms)"
    return text


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run read-only real-HTTP acceptance against the local V-KPI stack.")
    parser.add_argument("--base-url", default=os.getenv("VKPI_LOCAL_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--manifest", type=Path, help="Optional JSON manifest override (GET or declared read-only JSON POST only).")
    parser.add_argument("--json-out", type=Path, help="Also write the JSON report to this path.")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--token-ttl", type=int, default=DEFAULT_TOKEN_TTL_SECONDS)
    parser.add_argument("--overall-timeout", type=float, default=DEFAULT_OVERALL_TIMEOUT_SECONDS)
    parser.add_argument("--max-worker-age", type=int, default=DEFAULT_MAX_WORKER_AGE_SECONDS)
    parser.add_argument("--max-scheduler-evidence-age", type=int, default=DEFAULT_MAX_SCHEDULER_EVIDENCE_AGE_SECONDS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        token_ttl, overall_timeout = validate_acceptance_timing(args.token_ttl, args.overall_timeout)
        base = validate_loopback_base_url(args.base_url); manifest = load_manifest(args.manifest)
        runner = AcceptanceRunner(base_url=base, manifest=manifest, auth=create_local_auth_context(token_ttl),
                                  transport=UrlLibTransport(base), local_head=_git_head(), latest_migration=_latest_migration(),
                                  default_timeout_seconds=args.timeout, overall_timeout_seconds=overall_timeout,
                                  max_worker_age_seconds=args.max_worker_age,
                                  max_scheduler_evidence_age_seconds=args.max_scheduler_evidence_age)
        report, exit_code = runner.run(), None
    except Exception as exc:
        label = exc.__class__.__name__ if isinstance(exc, (ValueError, RuntimeError, subprocess.SubprocessError)) else "SetupError"
        report = {"schema_version": "vkpi.local-release-acceptance.v1", "base_url": str(args.base_url),
                  "overall": {"pass": False, "required_passed": 0, "required_total": 0, "failed_endpoint_ids": ["setup"],
                              "data_states": {"real": 0, "empty": 0, "pending": 0, "degraded": 1}, "latency_p95_ms": 0},
                  "safety": {"token_emitted": False}, "setup_error": f"local acceptance setup failed ({label})", "endpoints": []}
        exit_code = 2
    output = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    stdout_out(output)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True); args.json_out.write_text(output + "\n", encoding="utf-8")
    stdout_out(concise_summary(report), file=sys.stderr)
    return exit_code if exit_code is not None else (0 if report["overall"]["pass"] else 1)


if __name__ == "__main__":
    raise SystemExit(main())
