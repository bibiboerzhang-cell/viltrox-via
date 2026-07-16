from __future__ import annotations

import json
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import local_release_acceptance as acceptance  # noqa: E402


NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=timezone.utc)
HEAD = "a" * 40
MIGRATION = "239_vkpi_kol_search_history_archive.sql"
TOKEN = "fixture-token-must-never-be-emitted"


class FixtureTransport:
    def __init__(self, responses: dict[str, tuple[int, Any, dict[str, str] | None, float]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str | None, float]] = []

    def get(self, path: str, *, token: str | None, timeout_seconds: float) -> acceptance.HttpResponse:
        self.calls.append((path, token, timeout_seconds))
        if path not in self.responses:
            raise AssertionError(f"unexpected offline HTTP request: {path}")
        status, payload, headers, latency = self.responses[path]
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        return acceptance.HttpResponse(
            status=status,
            body=body,
            headers=headers or {"content-type": "application/json"},
            latency_ms=latency,
        )


@pytest.fixture
def health_payload() -> dict[str, Any]:
    return {
        "status": "ok",
        "build": {
            "git_sha": HEAD,
            "client_build": HEAD,
            "client_matches_server": True,
        },
        "trust": {
            "db_migration_max": MIGRATION,
            "worker_heartbeat": "2026-07-13T11:59:30Z",
            "worker_online": True,
            "scheduler_status": {"total": 2, "enabled": 1},
            "worker_sha": HEAD,
            "server_git_sha": HEAD,
            "client_git_sha": HEAD,
            "sha_aligned": True,
        },
    }


@pytest.fixture
def scheduler_payload() -> dict[str, Any]:
    return {
        "tasks": [
            {
                "task_key": "daily_sync",
                "enabled": True,
                "last_run_at": "2026-07-13T11:00:00Z",
                "last_success_at": "2026-07-13T11:00:00Z",
                "last_error": "",
            },
            {
                "task_key": "weekly_review",
                "enabled": False,
                "last_run_at": None,
                "last_success_at": None,
                "last_error": "",
            },
        ],
        "status": {
            "available": True,
            "total": 2,
            "enabled": 1,
            "by_risk": {"low": 1, "medium": 1, "high": 0},
        },
    }


def _runner(
    manifest: dict[str, Any],
    transport: FixtureTransport,
    *,
    head: str = HEAD,
    migration: str = MIGRATION,
) -> acceptance.AcceptanceRunner:
    return acceptance.AcceptanceRunner(
        base_url="http://127.0.0.1:8102",
        manifest=manifest,
        auth=acceptance.AuthContext(token=TOKEN, role="admin", expires_in_seconds=300),
        transport=transport,
        local_head=head,
        latest_migration=migration,
        now_fn=lambda: NOW,
    )


def test_git_head_uses_sealed_build_identity_without_a_git_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = "b" * 40
    (tmp_path / "BUILD_GIT_SHA").write_text(expected + "\n", encoding="utf-8")
    monkeypatch.setattr(acceptance, "ROOT", tmp_path)

    assert acceptance._git_head() == expected


def test_default_manifest_covers_every_cockpit_family_and_only_safe_gets() -> None:
    manifest = acceptance.load_manifest()
    families = {item["family"] for item in manifest["endpoints"]}

    assert set(acceptance.COCKPIT_BOARD_FAMILIES) <= families
    assert manifest["board_families"] == acceptance.COCKPIT_BOARD_FAMILIES
    assert all(item["method"] == "GET" for item in manifest["endpoints"])
    assert all(item["read_only_ack"] is True for item in manifest["endpoints"])

    market_history = next(item for item in manifest["endpoints"] if item["id"] == "marketTrends.history")
    assert "history=true" in market_history["path"]
    endpoint_ids = {item["id"] for item in manifest["endpoints"]}
    assert {
        "intelligent.advisor-readiness",
        "intelligent.advisor-memory",
    } <= endpoint_ids
    assert len(manifest["endpoints"]) >= 41
    assert all("access_token=" not in str(item.get("path") or item.get("path_template")) for item in manifest["endpoints"])


def test_default_strategy_and_gtm_data_paths_do_not_mislabel_populated_payloads() -> None:
    manifest = acceptance.load_manifest()
    specs = {item["id"]: item for item in manifest["endpoints"]}

    strategy = {
        "status": "ready",
        "category_tracks": [{"name": "lens"}],
        "focal_tracks": [],
        "opportunities": [],
        "no_go": [],
        "mount_signals": [],
    }
    gtm = {
        "weekly_signals": {"status": "ready", "items": [{"id": 1}]},
        "product_opportunities": {"status": "empty", "items": []},
        "recommended_actions": {"status": "ready", "items": []},
        "strategy_defaults": {"status": "ready", "simulate_entry": "sku360"},
        "learning_digest": {"validated": 1},
    }

    assert acceptance.classify_data_state(strategy, specs["strategyBoard.tracks"], acceptance.Validation()) == "real"
    assert acceptance.classify_data_state(gtm, specs["gtmCommand.summary"], acceptance.Validation()) == "real"


def test_staged_search_partial_without_failures_is_pending_not_degraded() -> None:
    spec = acceptance._ep(
        "search-history.read",
        "search-history",
        "/sessions/1",
        contract="search_session_read",
        allowed_states=["real", "pending"],
    )
    payload = {
        "id": 1,
        "query_text": "creator",
        "query_type": "keyword",
        "source": "profile_pipeline",
        "status": "partial",
        "items": [{"id": 11, "status": "partial", "stage": "profile"}],
        "count": 1,
        "counts": {"by_status": {"partial": 1}},
    }

    validation = acceptance._validate_contract(payload, spec, None)

    assert validation.errors == []
    assert validation.state_override == "pending"
    assert acceptance.classify_data_state(payload, spec, validation) == "pending"


def test_staged_search_partial_with_failed_item_stays_degraded() -> None:
    spec = acceptance._ep(
        "search-history.read",
        "search-history",
        "/sessions/1",
        contract="search_session_read",
        allowed_states=["real", "pending"],
    )
    payload = {
        "id": 1,
        "query_text": "creator",
        "query_type": "keyword",
        "source": "profile_pipeline",
        "status": "partial",
        "items": [{"id": 11, "status": "failed", "stage": "profile"}],
        "count": 1,
        "counts": {"by_status": {"failed": 1}},
    }

    validation = acceptance._validate_contract(payload, spec, None)

    assert validation.state_override is None
    assert acceptance.classify_data_state(payload, spec, validation) == "degraded"


def test_runner_resolves_dependencies_and_preserves_real_empty_pending_states(
    health_payload: dict[str, Any],
    scheduler_payload: dict[str, Any],
) -> None:
    manifest = {
        "name": "offline-fixture",
        "version": 1,
        "board_families": ["alpha", "empty", "pending"],
        "endpoints": [
            acceptance._ep(
                "runtime.health",
                "runtime",
                "/health",
                contract="health",
                data_paths=["build", "trust"],
                allowed_states=["real"],
                auth=False,
            ),
            acceptance._ep(
                "runtime.scheduler-registry",
                "runtime",
                "/scheduler",
                contract="scheduler_registry",
                data_paths=["tasks"],
                required_paths=["tasks", "status"],
                list_paths=["tasks"],
                allowed_states=["real"],
            ),
            acceptance._ep(
                "alpha.list",
                "alpha",
                "/items",
                contract="list_response",
                data_paths=["items"],
                list_paths=["items"],
            ),
            acceptance._ep(
                "alpha.read",
                "alpha",
                "/items/{item_id}",
                data_paths=["item"],
                bind={"item_id": {"endpoint": "alpha.list", "paths": ["items.0.id"]}},
                allowed_states=["real"],
            ),
            acceptance._ep(
                "empty.list",
                "empty",
                "/empty",
                contract="list_response",
                data_paths=["items"],
                list_paths=["items"],
            ),
            acceptance._ep(
                "pending.list",
                "pending",
                "/pending",
                contract="list_response",
                data_paths=["items"],
                state_paths=["status"],
                list_paths=["items"],
            ),
            acceptance._ep(
                "security.error",
                "security",
                "/bad-limit",
                contract="expected_error",
                expected_statuses=[422],
                data_paths=["$"],
                allowed_states=["real"],
                redaction_scan=True,
            ),
        ],
    }
    transport = FixtureTransport(
        {
            "/health": (200, health_payload, None, 2.0),
            "/scheduler": (200, scheduler_payload, None, 3.0),
            "/items": (200, {"status": "ready", "items": [{"id": "A/B"}]}, None, 4.0),
            "/items/A%2FB": (200, {"status": "ready", "item": {"id": "A/B"}}, None, 5.0),
            "/empty": (200, {"status": "ready", "items": []}, None, 6.0),
            "/pending": (200, {"status": "pending", "items": []}, None, 7.0),
            "/bad-limit": (422, {"detail": [{"type": "int_parsing"}]}, None, 8.0),
        }
    )

    report = _runner(manifest, transport).run()
    by_id = {item["id"]: item for item in report["endpoints"]}

    assert report["overall"]["pass"] is True
    assert by_id["alpha.read"]["data_state"] == "real"
    assert by_id["empty.list"]["data_state"] == "empty"
    assert by_id["pending.list"]["data_state"] == "pending"
    assert any(path == "/items/A%2FB" for path, _, _ in transport.calls)
    assert TOKEN not in json.dumps(report)
    assert all(TOKEN not in path for path, _, _ in transport.calls)
    assert next(token for path, token, _ in transport.calls if path == "/health") is None


def test_redaction_failure_reports_labels_without_echoing_sensitive_body() -> None:
    manifest = {
        "name": "redaction",
        "version": 1,
        "board_families": [],
        "endpoints": [
            acceptance._ep(
                "security.error",
                "security",
                "/error",
                contract="expected_error",
                expected_statuses=[422],
                allowed_states=["real"],
                redaction_scan=True,
            )
        ],
    }
    leaked = "Traceback (most recent call last): password=supersecretvalue"
    transport = FixtureTransport({"/error": (422, {"detail": leaked}, None, 1.0)})

    report = _runner(manifest, transport).run()
    row = report["endpoints"][0]
    serialized = json.dumps(report)

    assert report["overall"]["pass"] is False
    assert row["redaction_findings"] == ["credential_assignment", "stack_trace"]
    assert "supersecretvalue" not in serialized
    assert "Traceback" not in serialized


def test_health_trust_mismatches_and_stale_worker_fail(health_payload: dict[str, Any]) -> None:
    payload = deepcopy(health_payload)
    payload["build"]["git_sha"] = "b" * 40
    payload["build"]["client_matches_server"] = False
    payload["trust"]["server_git_sha"] = "b" * 40
    payload["trust"]["client_git_sha"] = "c" * 40
    payload["trust"]["worker_sha"] = "d" * 40
    payload["trust"]["sha_aligned"] = False
    payload["trust"]["db_migration_max"] = "238_old.sql"
    payload["trust"]["worker_heartbeat"] = "2026-07-13T10:00:00Z"

    manifest = {
        "name": "health-only",
        "version": 1,
        "board_families": [],
        "endpoints": [
            acceptance._ep(
                "runtime.health",
                "runtime",
                "/health",
                contract="health",
                data_paths=["build", "trust"],
                allowed_states=["real"],
                auth=False,
            )
        ],
    }
    transport = FixtureTransport({"/health": (200, payload, None, 2.0)})

    report = _runner(manifest, transport).run()
    errors = report["endpoints"][0]["errors"]

    assert report["overall"]["pass"] is False
    assert "server SHA does not match local HEAD" in errors
    assert "frontend build does not match server" in errors
    assert "applied migration max does not match local manifest" in errors
    assert "worker heartbeat is stale" in errors


def test_report_and_search_history_list_read_contracts_are_exercised() -> None:
    manifest = {
        "name": "read-contracts",
        "version": 1,
        "board_families": [],
        "endpoints": [
            acceptance._ep(
                "reports.weekly-list",
                "reports",
                "/weekly/list",
                contract="weekly_report_list",
                data_paths=["reports"],
                required_paths=["reports", "count"],
                list_paths=["reports"],
                integer_paths=["count"],
                count_matches={"count": "reports"},
                allowed_states=["real"],
            ),
            acceptance._ep(
                "reports.weekly-read",
                "reports",
                "/weekly/{report_id}",
                contract="weekly_report_read",
                data_paths=["body_md", "title"],
                bind={"report_id": {"endpoint": "reports.weekly-list", "paths": ["reports.0.id"]}},
                allowed_states=["real"],
            ),
            acceptance._ep(
                "search-history.list",
                "search-history",
                "/history",
                contract="search_history_list",
                data_paths=["items"],
                required_paths=["status", "count", "items", "filters"],
                list_paths=["items"],
                integer_paths=["count"],
                count_matches={"count": "items"},
                allowed_states=["real"],
            ),
            acceptance._ep(
                "search-history.read",
                "search-history",
                "/sessions/{session_id}",
                contract="search_session_read",
                bind={"session_id": {"endpoint": "search-history.list", "paths": ["items.0.id"]}},
                allowed_states=["real"],
            ),
        ],
    }
    weekly_row = {
        "id": 9,
        "staff_id": 1,
        "template_key": "staff_weekly",
        "title": "Weekly",
        "status": "ready",
        "generated_at": "2026-07-13T10:00:00Z",
    }
    history_row = {
        "id": 12,
        "query_text": "creator",
        "query_type": "keyword",
        "source": "smart_kol_input",
        "status": "complete",
        "created_at": "2026-07-13T10:00:00Z",
        "updated_at": "2026-07-13T10:01:00Z",
    }
    transport = FixtureTransport(
        {
            "/weekly/list": (200, {"count": 1, "reports": [weekly_row]}, None, 1.0),
            "/weekly/9": (200, {**weekly_row, "body_md": "# Weekly"}, None, 1.0),
            "/history": (
                200,
                {"status": "ready", "count": 1, "items": [history_row], "filters": {"limit": 12}},
                None,
                1.0,
            ),
            "/sessions/12": (
                200,
                {**history_row, "items": [], "count": 0, "counts": {}},
                None,
                1.0,
            ),
        }
    )

    report = _runner(manifest, transport).run()

    assert report["overall"]["pass"] is True
    assert [path for path, _, _ in transport.calls] == ["/weekly/list", "/weekly/9", "/history", "/sessions/12"]


def test_reviewed_no_ai_scope_and_truth_invalidated_weekly_report_are_fail_closed() -> None:
    manifest = {
        "name": "reviewed-first-release-exclusions",
        "version": 1,
        "board_families": [],
        "endpoints": [
            acceptance._ep(
                "intelligent.advisor-readiness",
                "intelligent",
                "/advisor/readiness",
                contract="advisor_readiness",
                required_paths=["status", "provider_ready", "provider_called", "persistence_ready", "action_mode"],
                boolean_paths=["provider_ready", "provider_called", "persistence_ready"],
                allowed_states=["real", "degraded"],
            ),
            acceptance._ep(
                "reports.weekly-read",
                "reports",
                "/weekly/9",
                contract="weekly_report_read",
                data_paths=["body_md", "title"],
                allowed_states=["real", "empty"],
            ),
        ],
    }
    transport = FixtureTransport(
        {
            "/advisor/readiness": (
                200,
                {
                    "status": "degraded",
                    "provider_ready": False,
                    "provider_called": False,
                    "persistence_ready": True,
                    "operator_enabled": False,
                    "reason": "advisor_external_ai_operator_disabled",
                    "action_mode": "draft_only",
                },
                None,
                1.0,
            ),
            "/weekly/9": (
                200,
                {
                    "id": 9,
                    "staff_id": 1,
                    "template_key": "staff_weekly",
                    "title": "Withdrawn weekly report",
                    "status": "invalidated",
                    "generated_at": "2026-07-13T10:00:00Z",
                    "truth_invalidated": True,
                    "truth_invalidation_reason": "financial evidence invalidated by migration 256",
                    "data_status": "unavailable",
                },
                None,
                1.0,
            ),
        }
    )

    report = _runner(manifest, transport).run()
    by_id = {item["id"]: item for item in report["endpoints"]}

    assert report["overall"]["pass"] is True
    assert by_id["intelligent.advisor-readiness"]["data_state"] == "degraded"
    assert by_id["reports.weekly-read"]["data_state"] == "empty"


def test_degraded_advisor_or_missing_live_weekly_body_cannot_be_washed_green() -> None:
    advisor_spec = acceptance._ep(
        "advisor",
        "intelligent",
        "/advisor",
        contract="advisor_readiness",
        allowed_states=["real", "degraded"],
    )
    weekly_spec = acceptance._ep(
        "weekly",
        "reports",
        "/weekly",
        contract="weekly_report_read",
        allowed_states=["real", "empty"],
    )
    manifest = {"name": "fail-closed", "version": 1, "board_families": [], "endpoints": [advisor_spec, weekly_spec]}
    transport = FixtureTransport(
        {
            "/advisor": (
                200,
                {
                    "status": "degraded",
                    "provider_ready": False,
                    "provider_called": False,
                    "persistence_ready": True,
                    "operator_enabled": True,
                    "reason": "advisor_exact_model_not_production_ready",
                    "action_mode": "draft_only",
                },
                None,
                1.0,
            ),
            "/weekly": (
                200,
                {
                    "id": 9,
                    "staff_id": 1,
                    "template_key": "staff_weekly",
                    "title": "Live report without body",
                    "status": "ready",
                    "generated_at": "2026-07-13T10:00:00Z",
                    "truth_invalidated": False,
                    "data_status": "real",
                },
                None,
                1.0,
            ),
        }
    )

    report = _runner(manifest, transport).run()
    by_id = {item["id"]: item for item in report["endpoints"]}

    assert report["overall"]["pass"] is False
    assert by_id["advisor"]["pass"] is False
    assert by_id["weekly"]["pass"] is False


def test_empty_dependency_fails_instead_of_claiming_read_contract_success() -> None:
    manifest = {
        "name": "empty-contract",
        "version": 1,
        "board_families": [],
        "endpoints": [
            acceptance._ep(
                "reports.weekly-list",
                "reports",
                "/weekly/list",
                contract="weekly_report_list",
                data_paths=["reports"],
                list_paths=["reports"],
                allowed_states=["real"],
            ),
            acceptance._ep(
                "reports.weekly-read",
                "reports",
                "/weekly/{report_id}",
                contract="weekly_report_read",
                bind={"report_id": {"endpoint": "reports.weekly-list", "paths": ["reports.0.id"]}},
                allowed_states=["real"],
            ),
        ],
    }
    transport = FixtureTransport({"/weekly/list": (200, {"count": 0, "reports": []}, None, 1.0)})

    report = _runner(manifest, transport).run()
    by_id = {item["id"]: item for item in report["endpoints"]}

    assert report["overall"]["pass"] is False
    assert by_id["reports.weekly-list"]["data_state"] == "empty"
    assert by_id["reports.weekly-read"]["pass"] is False
    assert by_id["reports.weekly-read"]["errors"] == ["dependency produced no value: reports.weekly-list"]


def test_manifest_and_base_url_guards_reject_mutating_or_remote_targets() -> None:
    with pytest.raises(ValueError, match="loopback"):
        acceptance.validate_loopback_base_url("https://example.com")

    bad_manifest = {
        "name": "bad",
        "version": 1,
        "board_families": [],
        "endpoints": [
            {
                "id": "mutate",
                "family": "bad",
                "method": "POST",
                "path": "/api/mutate",
                "read_only_ack": True,
            }
        ],
    }
    with pytest.raises(ValueError, match="forbids method"):
        acceptance.validate_manifest(bad_manifest)

    assert "token" not in acceptance.AuthContext(TOKEN, "admin", 300).public_dict()
