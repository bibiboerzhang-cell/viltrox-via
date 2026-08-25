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
          media_kind TEXT,
          is_active BOOLEAN
        );
        CREATE TABLE apify_jobs (
          id INTEGER PRIMARY KEY,
          job_type TEXT,
          payload TEXT,
          status TEXT
        );
        CREATE TABLE vkpi_kol_video_metric_tracking (
          evidence_id INTEGER PRIMARY KEY,
          status TEXT NOT NULL,
          source TEXT NOT NULL,
          tracked_by_staff_id INTEGER
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
          source_cache_id INTEGER,
          analysis_kind TEXT NOT NULL,
          status TEXT NOT NULL,
          created_at TEXT
        );
        CREATE TABLE vkpi_analysis_cache (
          id INTEGER PRIMARY KEY,
          target_type TEXT NOT NULL,
          target_id TEXT NOT NULL,
          derive_method TEXT NOT NULL,
          status TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE vkpi_kol_lens_evidence_scan (
          cache_id INTEGER PRIMARY KEY,
          evidence_id INTEGER,
          cache_updated_at TEXT,
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
          VALUES (11, 1, 'video', 'video', 1), (12, 2, 'video', 'video', 1),
                 (13, 3, 'video', 'video', 1), (16, 1, 'video', 'video', 1),
                 (14, 1, 'video', 'carousel', 1), (15, 1, 'video', 'video', 0);
        INSERT INTO apify_jobs VALUES
          (1, 'video', '{"target_id":"11","derive_method":"video_analysis_final_v1"}', 'done'),
          (2, 'video', '{"target_id":"16","derive_method":"video_analysis_final_v1"}', 'done');
        INSERT INTO vkpi_kol_video_metric_tracking VALUES
          (11, 'active', 'my_kol_video_tracking', 7),
          (12, 'paused', 'my_kol_video_tracking', 7),
          (16, 'active', 'enroll_metric_tracking', 7);
        INSERT INTO vkpi_content_metric_snapshots
          VALUES (1, 11, 'legacy_current_only'), (2, 11, 'success'),
                 (3, 16, 'legacy_current_only');
        INSERT INTO vkpi_kol_video_product_links VALUES (1, 11, 'SKU-A', 'detected');
        INSERT INTO vkpi_kol_llm_deep_analysis_results
          VALUES (1, 11, 101, 'video_final_v1', 'ready', '2026-08-24T00:01:00Z'),
                 (2, 16, 102, 'video_final_v1', 'ready', '2026-08-24T00:01:00Z');
        INSERT INTO vkpi_analysis_cache VALUES
          (101, 'video', '11', 'video_analysis_final_v1', 'ready', '2026-08-24T00:00:00Z'),
          (102, 'video', '16', 'video_analysis_final_v1', 'ready', '2026-08-24T00:00:00Z'),
          (103, 'video', '16', 'video_analysis_final_v1', 'ready', '2026-08-24T00:00:00Z');
        INSERT INTO vkpi_kol_lens_evidence_scan
          VALUES (101, 11, '2026-08-24T00:00:00Z', 'scanned'),
                 (103, 16, '2026-08-24T00:00:00Z', 'empty_result');
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
        "outbound_share_grants": 0,
        "received_share_grants": 1,
        "unattributed_received_share_grants": 0,
        "share_grants": 1,
        "content_items": 4,
        "writable_content_items": 3,
        "analysis_eligible_videos": 3,
        "non_video_content_items": 1,
        "final_v1_requested_videos": 2,
        "final_v1_completed_videos": 2,
        "final_v1_projected_videos": 2,
        "final_v1_not_requested_videos": 1,
        "final_v1_requested_not_completed_videos": 0,
        "final_v1_projection_pending_videos": 0,
        "candidate_videos": 3,
        "trackable_videos": 2,
        "tracked_videos": 2,
        "employee_explicit_tracked_videos": 1,
        "other_employee_explicit_tracked_videos": 0,
        "system_seeded_tracked_videos": 1,
        "unclassified_tracked_videos": 0,
        "measured_tracked_videos": 1,
        "legacy_only_tracked_videos": 1,
        "sku_linked_tracked_videos": 1,
        "sku_manual_videos": 0,
        "sku_detected_videos": 1,
        "sku_detected_pending_videos": 1,
        "sku_confirmed_videos": 0,
        "final_v1_ready_videos": 2,
        "final_v1_source_linked_videos": 2,
        "final_v1_current_source_videos": 2,
        "lens_scanned_videos": 2,
        "lens_source_linked_videos": 2,
        "final_v1_lens_scanned_videos": 1,
        "lens_mention_videos": 0,
        "employee_explicit_tracking_gap_videos": 1,
    }
    assert result["flows"]["content_monitoring"]["state"] == "configured_scheduler_disabled"
    assert result["flows"]["video_tracking"]["state"] == "configured_scheduler_disabled"
    assert result["flows"]["sku_linking"]["state"] == "detected_pending_human_confirmation"
    assert result["flows"]["gemini_analysis"]["state"] == "partially_requested"
    assert result["flows"]["sharing"]["state"] == "received_only"
    assert result["flows"]["video_tracking"]["employee_explicit_state"] == "needs_employee_selection"
    assert result["summary"]["configured_actions"] == 3
    codes = {item["code"] for item in result["blockers"]}
    assert {
        "content_monitoring_scheduler_disabled",
        "video_metric_scheduler_disabled",
        "employee_explicit_tracking_not_selected",
        "detected_sku_pending_confirmation",
        "final_v1_not_requested",
        "lens_extraction_pending",
    } <= codes
    assert result["summary"]["automatic_changes_performed"] == 0
    assert statements
    assert all(statement.lstrip().upper().startswith(("SELECT", "WITH", "PRAGMA")) for statement in statements)


def test_confirmed_row_clears_detected_pending_without_erasing_detection() -> None:
    conn = _conn()
    conn.execute(
        "INSERT INTO vkpi_kol_video_product_links VALUES (2, 11, 'SKU-A', 'confirmed')"
    )

    result = my_kol_closure_readiness.build_closure_readiness(conn, staff_scope_id=7)

    assert result["counts"]["sku_detected_videos"] == 1
    assert result["counts"]["sku_confirmed_videos"] == 1
    assert result["counts"]["sku_detected_pending_videos"] == 0
    assert result["flows"]["sku_linking"]["state"] == "partial"
    assert "detected_sku_pending_confirmation" not in {
        item["code"] for item in result["blockers"]
    }


def test_closure_readiness_unknown_tracking_source_is_not_employee_choice() -> None:
    conn = _conn()
    conn.execute(
        "UPDATE vkpi_kol_video_metric_tracking SET source='future_unknown' WHERE evidence_id=16"
    )

    result = my_kol_closure_readiness.build_closure_readiness(conn, staff_scope_id=7)

    assert result["counts"]["tracked_videos"] == 2
    assert result["counts"]["employee_explicit_tracked_videos"] == 1
    assert result["counts"]["system_seeded_tracked_videos"] == 0
    assert result["counts"]["unclassified_tracked_videos"] == 1
    assert result["counts"]["employee_explicit_tracking_gap_videos"] == 1
    assert result["summary"]["configured_actions"] == 3


def test_other_staff_tracking_does_not_become_scoped_employee_choice() -> None:
    conn = _conn()
    conn.execute(
        "UPDATE vkpi_kol_video_metric_tracking SET tracked_by_staff_id=8 WHERE evidence_id=11"
    )

    result = my_kol_closure_readiness.build_closure_readiness(conn, staff_scope_id=7)

    assert result["counts"]["tracked_videos"] == 2
    assert result["counts"]["employee_explicit_tracked_videos"] == 0
    assert result["counts"]["other_employee_explicit_tracked_videos"] == 1
    assert result["counts"]["employee_explicit_tracking_gap_videos"] == 2
    assert result["flows"]["video_tracking"]["state"] == "configured_scheduler_disabled"


def test_received_share_is_not_configured_action_but_outbound_share_is() -> None:
    conn = _conn()
    received_only = my_kol_closure_readiness.build_closure_readiness(conn, staff_scope_id=7)

    assert received_only["counts"]["received_share_grants"] == 1
    assert received_only["counts"]["outbound_share_grants"] == 0
    assert received_only["summary"]["configured_actions"] == 3

    conn.execute("INSERT INTO vkpi_kol_pool_members VALUES (2, 1, 8, 7)")
    with_outbound = my_kol_closure_readiness.build_closure_readiness(conn, staff_scope_id=7)

    assert with_outbound["counts"]["received_share_grants"] == 1
    assert with_outbound["counts"]["outbound_share_grants"] == 1
    assert with_outbound["summary"]["configured_actions"] == 4
    assert with_outbound["flows"]["sharing"]["state"] == "outbound_configured"


def test_gemini_bundle_requires_same_fresh_source_cache() -> None:
    conn = _conn()
    result = my_kol_closure_readiness.build_closure_readiness(conn, staff_scope_id=7)

    # Evidence 16 has both final_v1 and a lens scan, but their cache ids differ.
    assert result["counts"]["final_v1_ready_videos"] == 2
    assert result["counts"]["lens_scanned_videos"] == 2
    assert result["counts"]["final_v1_lens_scanned_videos"] == 1

    conn.execute(
        "UPDATE vkpi_analysis_cache SET updated_at='2026-08-25T00:00:00Z' WHERE id=101"
    )
    conn.execute(
        "UPDATE vkpi_kol_lens_evidence_scan "
        "SET cache_updated_at='2026-08-25T00:00:00Z' WHERE cache_id=101"
    )
    stale = my_kol_closure_readiness.build_closure_readiness(conn, staff_scope_id=7)

    assert stale["counts"]["lens_source_linked_videos"] == 2
    assert stale["counts"]["final_v1_current_source_videos"] == 1
    assert stale["counts"]["final_v1_lens_scanned_videos"] == 0
    assert stale["flows"]["gemini_analysis"]["state"] == "partially_requested"


def test_non_video_content_is_visible_but_never_in_final_v1_denominator() -> None:
    conn = _conn()

    result = my_kol_closure_readiness.build_closure_readiness(conn, staff_scope_id=7)

    assert result["counts"]["content_items"] == 4
    assert result["counts"]["analysis_eligible_videos"] == 3
    assert result["counts"]["non_video_content_items"] == 1
    assert result["counts"]["candidate_videos"] == 3
    final_blockers = {
        item["code"]: item["count"]
        for item in result["blockers"]
        if str(item["code"]).startswith("final_v1_")
    }
    assert final_blockers == {"final_v1_not_requested": 1}


def test_final_v1_requested_completed_and_projected_are_independent() -> None:
    conn = _conn()
    conn.execute(
        "INSERT INTO apify_jobs VALUES "
        "(3, 'video', '{\"target_id\":\"12\",\"derive_method\":\"video_analysis_final_v1\"}', 'queued')"
    )

    requested = my_kol_closure_readiness.build_closure_readiness(conn, staff_scope_id=7)

    assert requested["counts"]["analysis_eligible_videos"] == 3
    assert requested["counts"]["final_v1_requested_videos"] == 3
    assert requested["counts"]["final_v1_completed_videos"] == 2
    assert requested["counts"]["final_v1_requested_not_completed_videos"] == 1
    assert requested["counts"]["final_v1_not_requested_videos"] == 0
    assert requested["flows"]["gemini_analysis"]["state"] == "requested_not_completed"
    assert any(
        item["code"] == "final_v1_requested_not_completed" and item["count"] == 1
        for item in requested["blockers"]
    )

    conn.execute("UPDATE vkpi_kol_video_evidence SET is_active=0 WHERE id=12")
    conn.execute("DELETE FROM vkpi_kol_llm_deep_analysis_results WHERE source_evidence_id=16")
    projection = my_kol_closure_readiness.build_closure_readiness(conn, staff_scope_id=7)

    assert projection["counts"]["analysis_eligible_videos"] == 2
    assert projection["counts"]["final_v1_requested_videos"] == 2
    assert projection["counts"]["final_v1_completed_videos"] == 2
    assert projection["counts"]["final_v1_projected_videos"] == 1
    assert projection["counts"]["final_v1_projection_pending_videos"] == 1
    assert projection["flows"]["gemini_analysis"]["state"] == "projection_pending"


def test_closure_readiness_team_scope_includes_all_non_duplicate_collections() -> None:
    result = my_kol_closure_readiness.build_closure_readiness(_conn(), staff_scope_id=None)

    assert result["scope"] == {"staff_scope_id": None, "mode": "team"}
    assert result["counts"]["kol_count"] == 3
    assert result["counts"]["writable_kol_count"] == 3
    assert result["counts"]["monitoring_active_kols"] == 2
    assert result["counts"]["monitoring_succeeded_kols"] == 1
    assert result["counts"]["content_items"] == 5
    assert result["counts"]["analysis_eligible_videos"] == 4
    assert result["counts"]["non_video_content_items"] == 1
    assert result["counts"]["candidate_videos"] == 4
    assert result["counts"]["trackable_videos"] == 4
    assert result["counts"]["outbound_share_grants"] == 1
    assert result["counts"]["received_share_grants"] == 1
    assert result["counts"]["unattributed_received_share_grants"] == 0


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
