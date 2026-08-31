"""共享层 顶层(千行卫兵拆分)。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

import pytest


# ════════════════════════════════════════════════════════════════
# 1) llm_gateway_ledger.record_call(台账写入,逐字节)
# ════════════════════════════════════════════════════════════════

from app.platform import llm_gateway  # noqa: E402
from app.platform import llm_gateway_ledger as ledger  # noqa: E402


class _OneRow:
    def __init__(self, row: Any):
        self._row = row

    def fetchone(self) -> Any:
        return self._row


class _LedgerConn:
    def __init__(self, select_row: Any = None):
        self.calls: list[tuple[str, tuple]] = []
        self.commits = 0
        self.select_row = select_row

    def execute(self, sql: str, params: tuple = ()) -> _OneRow:
        self.calls.append((" ".join(sql.split()), tuple(params)))
        if sql.strip().upper().startswith("SELECT"):
            return _OneRow(self.select_row)
        return _OneRow(None)

    def commit(self) -> None:
        self.commits += 1


class _BudgetGuard:
    def __init__(self, result: Any = None, exc: BaseException | None = None):
        self.result = result
        self.exc = exc
        self.calls: list[dict[str, Any]] = []

    def record_cost(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc
        return self.result


def _patch_gateway(
    monkeypatch: pytest.MonkeyPatch,
    conn: _LedgerConn,
    *,
    budget: _BudgetGuard | None = None,
) -> None:
    monkeypatch.setattr(llm_gateway, "ensure_vkpi_product_industry_schema", lambda: None)
    monkeypatch.setattr(llm_gateway, "get_conn", lambda: conn)
    monkeypatch.setattr(
        llm_gateway,
        "resolve_staff_id",
        lambda staff: staff.get("staff_id") if isinstance(staff, dict) else None,
    )
    monkeypatch.setattr(llm_gateway, "_existing_staff_id", lambda c, sid: sid)
    monkeypatch.setattr(llm_gateway, "_utcnow", lambda: "2026-08-30T00:00:00Z")
    monkeypatch.setattr(llm_gateway, "_provider_budget_scope", lambda p: f"prov:{p}")
    if budget is not None:
        monkeypatch.setattr(llm_gateway, "_budget_guard", lambda: budget)


# ════════════════════════════════════════════════════════════════
# 2) projects.outreach.generate_outreach
# ════════════════════════════════════════════════════════════════

from app.core.config import OPENAI_MODEL  # noqa: E402
from app.domains.projects import outreach  # noqa: E402
from app.platform import llm_production  # noqa: E402

_SOW_PLACEHOLDER = "TODO — 待人工按预算与谈判确定(本草案不承诺价格)"


def _creators() -> list[dict[str, Any]]:
    return [
        {
            "id": 1,
            "platform": "youtube",
            "handle": "alpha",
            "display_name": "Alpha",
            "primary_topic": "lenses",
            "followers": 1000,
            "email": "",
        },
        {
            "id": 2,
            "platform": "instagram",
            "handle": "beta",
            "display_name": "",
            "primary_topic": "",
            "followers": None,
            "email": "",
        },
    ]


def _llm_payload() -> dict[str, Any]:
    return {
        "messages": [
            {"kol_pool_id": 1, "subject": "S1", "body": "B1"},
            {"kol_pool_id": 2, "subject": "S2", "body": "B2"},
        ],
        "sow_draft": {
            "scope": "Scope-X",
            "deliverables": ["d1", "d2"],
            "timeline": "2 weeks",
            "usage_rights": "3 months",
            "compensation": "$500",
        },
    }


# ════════════════════════════════════════════════════════════════
# 3) intelligent_query.handlers.kol_pool_overview
# ════════════════════════════════════════════════════════════════

from app.domains.intelligent_query import handlers  # noqa: E402
from app.domains.intelligent_query.contracts import (  # noqa: E402
    NormalizedRequest,
    QueryScope,
    QueryWindow,
)
from app.domains.intelligent_query.repository import freshness_status  # noqa: E402


class _RouteConn:
    def __init__(self, routes: dict[str, dict[str, Any] | None]):
        self.routes = routes
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql: str, params: tuple = ()) -> _OneRow:
        self.calls.append((" ".join(sql.split()), tuple(params)))
        for key, row in self.routes.items():
            if key in sql:
                return _OneRow(row)
        return _OneRow(None)


def _request(locale: str = "zh-CN", filters: dict[str, Any] | None = None) -> NormalizedRequest:
    start = datetime(2026, 8, 23, tzinfo=timezone.utc)
    end = datetime(2026, 8, 30, tzinfo=timezone.utc)
    return NormalizedRequest(
        query="kol pool overview",
        locale=locale,
        thread_id="t1",
        scope=QueryScope(mode="auto", requested_staff_id=None),
        window=QueryWindow(start=start, end=end, preset="7d"),
        filters=filters or {},
        mode="auto",
        client_request_id="c1",
        request_id="r1",
    )


_NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


def _patch_pool_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    pool_columns: set[str],
    tables_present: set[str],
    predicates: tuple[list[str], list[Any], list[dict[str, Any]]] | None = None,
    evidence_columns: set[str] | None = None,
) -> None:
    monkeypatch.setattr(
        handlers, "actual_scope_context", lambda request, staff: {"applied_mode": "auto"}
    )

    def fake_table_columns(conn: Any, table: str) -> set[str]:
        if table == "vkpi_kol_pool":
            return set(pool_columns)
        if table == "vkpi_kol_video_evidence":
            return set(evidence_columns or set())
        return set()

    monkeypatch.setattr(handlers, "table_columns", fake_table_columns)
    monkeypatch.setattr(
        handlers, "table_present", lambda conn, table: table in tables_present
    )
    monkeypatch.setattr(
        handlers,
        "pool_predicates",
        lambda conn, request, staff, alias: predicates or ([], [], []),
    )


def _full_routes() -> dict[str, dict[str, Any]]:
    return {
        "FROM vkpi_kol_pool p": {
            "total_kols": 120,
            "duplicate_rows": 7,
            "data_updated_at": "2026-08-28T00:00:00Z",
        },
        "FROM vkpi_kol_video_evidence e": {
            "video_kols": 45,
            "video_rows": 300,
            "data_updated_at": "2026-08-29T00:00:00Z",
        },
        "FROM vkpi_kol_llm_deep_analysis_results d": {"deep_kols": 12},
    }


# ════════════════════════════════════════════════════════════════
# 4) reports.model_policy.evaluate_report_model_policy
# ════════════════════════════════════════════════════════════════

from app.domains.reports import model_policy  # noqa: E402
from app.domains.reports.model_policy import (  # noqa: E402
    ADVANCED_MODEL_MODE,
    DETERMINISTIC_DESCRIPTIVE_MODE,
    REPORT_CHALLENGER_MODEL,
    REPORT_PRIMARY_MODEL,
)


class _Resolved:
    def __init__(self, binding: str, *, blocker: str = "", availability: str = "verified"):
        self._binding = binding
        self._blocker = blocker
        self.runtime_availability = availability

    def blocker(self, **kwargs: Any) -> str:
        assert kwargs == {
            "require_registered": True,
            "require_runtime_verified": False,
            "require_pricing": True,
        }
        return self._blocker

    def to_dict(self) -> dict[str, Any]:
        return {
            "binding": self._binding,
            "runtime_availability": self.runtime_availability,
            "runtime_evidence_source": "should-be-popped",
        }


def _model_readiness(*, production_ready: bool, failure_reasons: list[str] | None = None) -> dict[str, Any]:
    return {
        "production_ready": production_ready,
        "configured": True,
        "probed": production_ready,
        "evaluated": production_ready,
        "availability": "available" if production_ready else "unknown",
        "claim_status": "ok" if production_ready else "pending",
        "failure_reasons": list(failure_reasons or []),
    }


def _ready_payload() -> dict[str, Any]:
    return {
        "status": "ready",
        "ready": True,
        "claimable": True,
        "claim_level": "validated",
        "blockers": [],
    }


def _good_sources() -> list[dict[str, Any]]:
    return [
        {"key": "weekly_metrics", "observed": 12, "minimum": 10, "source_count": 3, "data_status": "real"},
        {"key": "kol_rows", "observed": 40, "minimum": 10, "source_count": 1, "data_status": "real"},
    ]


def _patch_policy_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    selectable: bool = True,
    static_blocker: str = "",
    production_ready: bool = True,
    failure_reasons: list[str] | None = None,
    availability: str = "verified",
) -> None:
    monkeypatch.setattr(model_policy, "is_selectable_model", lambda binding: selectable)
    monkeypatch.setattr(
        model_policy,
        "readiness_evidence_from_environment",
        lambda: ({}, {"source": "environment", "parsed": True}),
    )
    monkeypatch.setattr(
        model_policy, "configured_providers_from_environment", lambda: {"openai": True, "anthropic": True}
    )
    monkeypatch.setattr(
        model_policy,
        "resolve_model_binding",
        lambda provider, model_id, runtime_availability=None: _Resolved(
            f"{provider}/{model_id}", blocker=static_blocker, availability=availability
        ),
    )
    monkeypatch.setattr(
        model_policy,
        "assess_model_readiness",
        lambda resolved, configured, evidence, as_of: _model_readiness(
            production_ready=production_ready, failure_reasons=failure_reasons
        ),
    )


# ════════════════════════════════════════════════════════════════
# 5) media.cache.cache_video_for_item
# ════════════════════════════════════════════════════════════════

from pathlib import Path  # noqa: E402

from app.domains.media import cache  # noqa: E402
from app.domains.media.cache_core import VideoCacheCancelled  # noqa: E402


class _FakeResponse:
    def __init__(self, headers: dict[str, str], chunks: list[bytes]):
        self._headers = {k.lower(): v for k, v in headers.items()}
        self._chunks = list(chunks)

    @property
    def headers(self) -> "_FakeResponse":
        return self

    def get(self, key: str, default: Any = None) -> Any:
        return self._headers.get(str(key).lower(), default)

    def read(self, _n: int) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: Any) -> None:
        return None


def _patch_video_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    existing: str | None = None,
    state: dict[str, Any] | None = None,
    normalized: tuple[str, str] | None = ("https://cdn.example.com/v.mp4", "cdn.example.com"),
    page_url: str = "",
    head: tuple[int, str] = (0, "video/mp4"),
    gc: dict[str, Any] | None = None,
    max_bytes: int = 1000,
    r2: dict[str, Any] | None = None,
) -> dict[str, Any]:
    digest = "f" * 64
    cache_path = tmp_path / "videos" / digest
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    content_type_path = tmp_path / "videos" / f"{digest}.type"
    sidecar_path = tmp_path / "sidecars" / "item.json"
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    captured: dict[str, Any] = {
        "digest": digest,
        "cache_path": cache_path,
        "content_type_path": content_type_path,
        "sidecar_path": sidecar_path,
        "failures": [],
        "assets": [],
        "r2_calls": [],
    }
    monkeypatch.setattr(cache, "cached_video_url_for_item", lambda p, v: existing)
    monkeypatch.setattr(cache, "_video_item_sidecar_path", lambda p, v: sidecar_path)
    monkeypatch.setattr(cache, "video_cache_item_state", lambda p, v: dict(state or {}))
    monkeypatch.setattr(cache, "_normalize_video_url", lambda url: normalized)
    monkeypatch.setattr(cache, "_public_video_page_url", lambda url, p: page_url)
    monkeypatch.setattr(
        cache, "_video_cache_paths", lambda url: (digest, cache_path, content_type_path)
    )
    monkeypatch.setattr(cache, "_video_max_file_bytes", lambda: max_bytes)
    monkeypatch.setattr(cache, "_head_content_length", lambda url, host, timeout: head)
    monkeypatch.setattr(
        cache, "run_video_cache_gc", lambda target_free_bytes: dict(gc or {"free_bytes": 10_000_000})
    )

    def fake_r2(**kwargs: Any) -> dict[str, Any]:
        captured["r2_calls"].append(kwargs)
        return dict(r2 or {"storage_backend": "local", "r2_status": "disabled"})

    monkeypatch.setattr(cache, "_upload_to_r2_if_enabled", fake_r2)
    monkeypatch.setattr(
        cache, "_record_media_cache_asset", lambda payload: captured["assets"].append(payload)
    )

    def fake_failure(**kwargs: Any) -> None:
        captured["failures"].append(kwargs)

    monkeypatch.setattr(cache, "_video_item_failure_sidecar", fake_failure)
    monkeypatch.setattr(cache, "_sha256_file", lambda path: "checksum-1")
    monkeypatch.setattr(cache, "_utcnow", lambda: "2026-08-30T00:00:00Z")
    return captured
