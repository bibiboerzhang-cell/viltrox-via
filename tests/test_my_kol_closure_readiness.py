from __future__ import annotations

import sqlite3

from app.api.routers import vkpi_my_kol
from app.core import release_validation
from app.domains.kol import my_kol_closure_readiness


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE vkpi_kol_pool (
          id INTEGER PRIMARY KEY,
          duplicate_of_id INTEGER
        );
        CREATE TABLE vkpi_kol_pool_favorites (
          id INTEGER PRIMARY KEY,
          kol_pool_id INTEGER NOT NULL,
          staff_id INTEGER NOT NULL
        );
        CREATE TABLE vkpi_kol_pool_members (
          id INTEGER PRIMARY KEY,
          kol_pool_id INTEGER NOT NULL,
          staff_id INTEGER NOT NULL,
          shared_by INTEGER
        );
        CREATE TABLE vkpi_kol_content_monitoring_subscriptions (
          id INTEGER PRIMARY KEY,
          staff_id INTEGER NOT NULL,
          kol_pool_id INTEGER NOT NULL,
          status TEXT NOT NULL,
          last_success_at TEXT
        );
        CREATE TABLE vkpi_kol_video_evidence (
          id INTEGER PRIMARY KEY,
          kol_pool_id INTEGER NOT NULL,
          evidence_type TEXT,
          is_active BOOLEAN
        );
        CREATE TABLE vkpi_kol_video_metric_tracking (
          evidence_id INTEGER PRIMARY KEY,
          status TEXT NOT NULL
        );
        CREATE TABLE vkpi_content_metric_snapshots (
          id INTEGER PRIMARY KEY,
          evidence_id INTEGER NOT NULL,
          status TEXT NOT NULL
        );
        CREATE TABLE vkpi_kol_video_product_links (
          id INTEGER PRIMARY KEY,
          evidence_id INTEGER NOT NULL,
          product_sku TEXT NOT NULL DEFAULT 'SKU-A',
          relation_type TEXT NOT NULL
        );
        CREATE TABLE vkpi_kol_llm_deep_analysis_results (
          id INTEGER PRIMARY KEY,
          source_evidence_id INTEGER,
          analysis_kind TEXT NOT NULL,
          status TEXT NOT NULL
        );
        CREATE TABLE vkpi_kol_lens_evidence_scan (
          cache_id INTEGER PRIMARY KEY,
          evidence_id INTEGER,
          scan_status TEXT NOT NULL
        );
        CREATE TABLE vkpi_kol_lens_evidence (
          id INTEGER PRIMARY KEY,
          evidence_id INTEGER
        );
        CREATE TABLE scheduler_tasks (
          task_key TEXT PRIMARY KEY,
          enabled BOOLEAN,
          last_run_at TEXT,
          last_success_at TEXT
        );

        INSERT INTO vkpi_kol_pool VALUES (1, NULL), (2, NULL), (3, NULL), (4, 1);
        INSERT INTO vkpi_kol_pool_favorites VALUES (1, 1, 7), (2, 2, 8), (3, 3, 8), (4, 4, 7);
        INSERT INTO vkpi_kol_pool_members VALUES (1, 2, 7, 8);
        INSERT INTO vkpi_kol_content_monitoring_subscriptions
          VALUES (1, 7, 1, 'active', NULL), (2, 8, 2, 'active', '2026-08-24T00:00:00Z');
        INSERT INTO vkpi_kol_video_evidence
          VALUES (11, 1, 'video', 1), (12, 2, 'video', 1), (13, 3, 'video', 1), (16, 1, 'video', 1),
                 (14, 1, 'carousel', 1), (15, 1, 'video', 0);
        INSERT INTO vkpi_kol_video_metric_tracking VALUES (11, 'active'), (12, 'paused');
        INSERT INTO vkpi_content_metric_snapshots
          VALUES (1, 11, 'legacy_current_only'), (2, 11, 'success');
        INSERT INTO vkpi_kol_video_product_links VALUES (1, 11, 'SKU-A', 'detected');
        INSERT INTO vkpi_kol_llm_deep_analysis_results
          VALUES (1, 11, 'video_final_v1', 'ready');
        INSERT INTO scheduler_tasks VALUES
          ('vkpi_kol_content_monitoring', 0, NULL, NULL),
          ('vkpi_kol_video_metric_refresh', 0, NULL, NULL),
          ('vkpi_tracking_auto_enroll', 0, NULL, NULL);
        """
    )
    return conn


def test_closure_readiness_separates_configuration_scheduler_and_results() -> None:
    conn = _conn()
    statements: list[str] = []
    conn.set_trace_callback(statements.append)

    result = my_kol_closure_readiness.build_closure_readiness(conn, staff_scope_id=7)

    assert result["contract"] == "my_kol_closure_readiness_v1"
    assert result["claim_status"] == "descriptive_only"
    assert result["status"] == "attention"
    assert result["counts"] == {
        "kol_count": 2,
        "writable_kol_count": 1,
        "monitoring_active_kols": 1,
        "monitoring_succeeded_kols": 0,
        "share_grants": 1,
        "candidate_videos": 3,
        "trackable_videos": 2,
        "tracked_videos": 1,
        "measured_tracked_videos": 1,
        "legacy_only_tracked_videos": 1,
        "sku_linked_tracked_videos": 1,
        "sku_manual_videos": 0,
        "sku_detected_videos": 1,
        "sku_detected_pending_videos": 1,
        "sku_confirmed_videos": 0,
        "final_v1_ready_videos": 1,
        "lens_scanned_videos": 0,
        "lens_mention_videos": 0,
    }
    assert result["flows"]["content_monitoring"]["state"] == "configured_scheduler_disabled"
    assert result["flows"]["video_tracking"]["state"] == "configured_scheduler_disabled"
    assert result["flows"]["sku_linking"]["state"] == "detected_pending_human_confirmation"
    assert result["flows"]["gemini_analysis"]["state"] == "lens_extraction_pending"
    codes = {item["code"] for item in result["blockers"]}
    assert {
            "content_monitoring_scheduler_disabled",
        "videos_not_tracked",
        "video_metric_scheduler_disabled",
        "detected_sku_pending_confirmation",
        "final_v1_missing",
        "lens_extraction_pending",
    } <= codes
    assert result["summary"]["automatic_changes_performed"] == 0
    assert statements
    assert all(statement.lstrip().upper().startswith(("SELECT", "WITH")) for statement in statements)


def test_closure_readiness_team_scope_includes_all_non_duplicate_collections() -> None:
    result = my_kol_closure_readiness.build_closure_readiness(_conn(), staff_scope_id=None)

    assert result["scope"] == {"staff_scope_id": None, "mode": "team"}
    assert result["counts"]["kol_count"] == 3
    assert result["counts"]["writable_kol_count"] == 3
    assert result["counts"]["monitoring_active_kols"] == 2
    assert result["counts"]["monitoring_succeeded_kols"] == 1
    assert result["counts"]["candidate_videos"] == 4
    assert result["counts"]["trackable_videos"] == 4


def test_closure_readiness_router_enforces_employee_own_scope(monkeypatch) -> None:
    conn = _conn()
    monkeypatch.setattr(vkpi_my_kol, "get_conn", lambda: conn)

    result = vkpi_my_kol.my_kol_closure_readiness_endpoint(
        staff_id=8,
        staff={"id": 7, "role": "employee"},
    )

    assert result["scope"]["staff_scope_id"] == 7
    assert result["scope_context"]["scope_mode"] == "own"
    assert result["scope_context"]["requested_staff_id"] == 8


def test_closure_readiness_is_allowed_during_read_only_release_validation() -> None:
    path = "/api/admin/vkpi/my-kol/closure-readiness"

    assert release_validation.release_validation_request_allowed("GET", path)
    assert release_validation.release_validation_request_allowed("HEAD", path)
    assert not release_validation.release_validation_request_allowed("POST", path)
