"""Read-only closure status for the MY KOL employee workflow.

This projection deliberately separates four different facts that used to be
easy to conflate in the UI:

* a KOL/video is visible in the employee collection;
* the employee explicitly configured monitoring/tracking;
* the scheduler is enabled;
* a successful observation or analysis result actually landed.

The endpoint never selects targets, enables schedulers, queues jobs or calls a
provider.  Counts are descriptive evidence only; user/manager decisions stay
explicit.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


CONTRACT = "my_kol_closure_readiness_v1"
TASK_KEYS = (
    "vkpi_kol_content_monitoring",
    "vkpi_kol_video_metric_refresh",
    "vkpi_tracking_auto_enroll",
)

_COLLECTION_CTE = """
WITH collection AS (
    SELECT kp.id
    FROM vkpi_kol_pool kp
    WHERE kp.duplicate_of_id IS NULL
      AND (
        EXISTS (
            SELECT 1 FROM vkpi_kol_pool_favorites f
            WHERE f.kol_pool_id=kp.id AND (?=0 OR f.staff_id=?)
        )
        OR EXISTS (
            SELECT 1 FROM vkpi_kol_pool_members sm
            WHERE sm.kol_pool_id=kp.id AND (?=0 OR sm.staff_id=?)
        )
      )
), writable_collection AS (
    SELECT c.id
    FROM collection c
    WHERE ?=0 OR EXISTS (
        SELECT 1 FROM vkpi_kol_pool_favorites f
        WHERE f.kol_pool_id=c.id AND f.staff_id=?
    )
)
"""


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def _scope_params(staff_scope_id: int) -> tuple[int, int, int, int, int, int]:
    sid = max(0, _int(staff_scope_id))
    return sid, sid, sid, sid, sid, sid


def _row(conn: Any, sql: str, params: tuple[Any, ...]) -> dict[str, Any]:
    result = conn.execute(sql, params).fetchone()
    return dict(result) if result else {}


def _collection_counts(conn: Any, sid: int) -> dict[str, int]:
    row = _row(
        conn,
        _COLLECTION_CTE
        + """
        SELECT
          (SELECT COUNT(*) FROM collection) AS kol_count,
          (SELECT COUNT(*) FROM writable_collection) AS writable_kol_count,
          (SELECT COUNT(*)
             FROM vkpi_kol_content_monitoring_subscriptions s
             JOIN writable_collection c ON c.id=s.kol_pool_id
            WHERE s.status='active' AND (?=0 OR s.staff_id=?)) AS monitoring_active_kols,
          (SELECT COUNT(*)
             FROM vkpi_kol_content_monitoring_subscriptions s
             JOIN writable_collection c ON c.id=s.kol_pool_id
            WHERE s.status='active' AND s.last_success_at IS NOT NULL
              AND (?=0 OR s.staff_id=?)) AS monitoring_succeeded_kols,
          (SELECT COUNT(*)
             FROM vkpi_kol_pool_members sm
             JOIN collection c ON c.id=sm.kol_pool_id
            WHERE (?=0 OR sm.staff_id=?)) AS share_grants
        """,
        (*_scope_params(sid), sid, sid, sid, sid, sid, sid),
    )
    return {key: _int(value) for key, value in row.items()}


def _video_counts(conn: Any, sid: int) -> dict[str, int]:
    row = _row(
        conn,
        _COLLECTION_CTE
        + """
        , video_base AS (
            SELECT e.id
            FROM vkpi_kol_video_evidence e
            JOIN collection c ON c.id=e.kol_pool_id
            WHERE COALESCE(e.evidence_type, 'video')='video'
              AND e.is_active IS NOT FALSE
        ), writable_video_base AS (
            SELECT e.id
            FROM vkpi_kol_video_evidence e
            JOIN writable_collection c ON c.id=e.kol_pool_id
            WHERE COALESCE(e.evidence_type, 'video')='video'
              AND e.is_active IS NOT FALSE
        ), tracked AS (
            SELECT t.evidence_id
            FROM vkpi_kol_video_metric_tracking t
            JOIN writable_video_base v ON v.id=t.evidence_id
            WHERE t.status='active'
        )
        SELECT
          (SELECT COUNT(*) FROM video_base) AS candidate_videos,
          (SELECT COUNT(*) FROM writable_video_base) AS trackable_videos,
          (SELECT COUNT(*) FROM tracked) AS tracked_videos,
          (SELECT COUNT(DISTINCT s.evidence_id)
             FROM vkpi_content_metric_snapshots s
             JOIN tracked t ON t.evidence_id=s.evidence_id
            WHERE s.status='success') AS measured_tracked_videos,
          (SELECT COUNT(DISTINCT s.evidence_id)
             FROM vkpi_content_metric_snapshots s
             JOIN tracked t ON t.evidence_id=s.evidence_id
            WHERE s.status='legacy_current_only') AS legacy_only_tracked_videos,
          (SELECT COUNT(DISTINCT l.evidence_id)
             FROM vkpi_kol_video_product_links l
             JOIN tracked t ON t.evidence_id=l.evidence_id) AS sku_linked_tracked_videos,
          (SELECT COUNT(DISTINCT l.evidence_id)
             FROM vkpi_kol_video_product_links l
             JOIN tracked t ON t.evidence_id=l.evidence_id
            WHERE l.relation_type='manual') AS sku_manual_videos,
          (SELECT COUNT(DISTINCT l.evidence_id)
             FROM vkpi_kol_video_product_links l
             JOIN tracked t ON t.evidence_id=l.evidence_id
            WHERE l.relation_type='detected') AS sku_detected_videos,
          (SELECT COUNT(DISTINCT l.evidence_id)
             FROM vkpi_kol_video_product_links l
             JOIN tracked t ON t.evidence_id=l.evidence_id
            WHERE l.relation_type='detected'
              AND NOT EXISTS (
                  SELECT 1 FROM vkpi_kol_video_product_links c
                  WHERE c.evidence_id=l.evidence_id
                    AND c.product_sku=l.product_sku
                    AND c.relation_type='confirmed'
              )) AS sku_detected_pending_videos,
          (SELECT COUNT(DISTINCT l.evidence_id)
             FROM vkpi_kol_video_product_links l
             JOIN tracked t ON t.evidence_id=l.evidence_id
            WHERE l.relation_type='confirmed') AS sku_confirmed_videos,
          (SELECT COUNT(DISTINCT d.source_evidence_id)
             FROM vkpi_kol_llm_deep_analysis_results d
             JOIN video_base v ON v.id=d.source_evidence_id
            WHERE d.analysis_kind='video_final_v1' AND d.status='ready') AS final_v1_ready_videos,
          (SELECT COUNT(DISTINCT s.evidence_id)
             FROM vkpi_kol_lens_evidence_scan s
             JOIN video_base v ON v.id=s.evidence_id
            WHERE s.scan_status IN ('scanned', 'no_evidence', 'empty_result')) AS lens_scanned_videos,
          (SELECT COUNT(DISTINCT l.evidence_id)
             FROM vkpi_kol_lens_evidence l
             JOIN video_base v ON v.id=l.evidence_id) AS lens_mention_videos
        """,
        _scope_params(sid),
    )
    return {key: _int(value) for key, value in row.items()}


def _scheduler_states(conn: Any) -> dict[str, dict[str, Any]]:
    states = {
        key: {
            "task_key": key,
            "registered": False,
            "enabled": None,
            "last_run_at": None,
            "last_success_at": None,
        }
        for key in TASK_KEYS
    }
    try:
        placeholders = ",".join("?" for _ in TASK_KEYS)
        rows = conn.execute(
            f"""
            SELECT task_key, enabled, last_run_at, last_success_at
            FROM scheduler_tasks
            WHERE task_key IN ({placeholders})
            """,
            TASK_KEYS,
        ).fetchall()
    except Exception:
        return states
    for raw in rows:
        row = dict(raw)
        key = str(row.get("task_key") or "")
        if key not in states:
            continue
        states[key] = {
            "task_key": key,
            "registered": True,
            "enabled": _truthy(row.get("enabled")),
            "last_run_at": str(row.get("last_run_at") or "") or None,
            "last_success_at": str(row.get("last_success_at") or "") or None,
        }
    return states


def _state_no_target(total: int, configured: int, *, scheduler_enabled: bool | None) -> str:
    if total <= 0:
        return "no_targets"
    if configured <= 0:
        return "needs_employee_selection"
    if scheduler_enabled is not True:
        return "configured_scheduler_disabled"
    return "configured"


def build_closure_readiness(
    conn: Any,
    *,
    staff_scope_id: int | None,
) -> dict[str, Any]:
    """Build the scoped closure projection using SELECT statements only."""

    sid = max(0, _int(staff_scope_id))
    counts = {**_collection_counts(conn, sid), **_video_counts(conn, sid)}
    schedulers = _scheduler_states(conn)

    monitoring_scheduler = schedulers["vkpi_kol_content_monitoring"]["enabled"]
    metric_scheduler = schedulers["vkpi_kol_video_metric_refresh"]["enabled"]
    auto_enroll_scheduler = schedulers["vkpi_tracking_auto_enroll"]["enabled"]

    monitoring_state = _state_no_target(
        counts["writable_kol_count"],
        counts["monitoring_active_kols"],
        scheduler_enabled=monitoring_scheduler,
    )
    if monitoring_state == "configured":
        monitoring_state = (
            "operational"
            if counts["monitoring_succeeded_kols"] > 0
            else "configured_waiting_first_success"
        )

    tracking_state = _state_no_target(
        counts["trackable_videos"],
        counts["tracked_videos"],
        scheduler_enabled=metric_scheduler,
    )
    if tracking_state == "configured":
        if counts["measured_tracked_videos"] <= 0:
            tracking_state = "configured_waiting_first_measurement"
        elif counts["measured_tracked_videos"] < counts["tracked_videos"]:
            tracking_state = "partially_measured"
        else:
            tracking_state = "operational"

    if counts["tracked_videos"] <= 0:
        sku_state = "no_tracked_videos"
    elif counts["sku_linked_tracked_videos"] <= 0:
        sku_state = "needs_product_link"
    elif counts["sku_detected_pending_videos"] > 0:
        sku_state = "detected_pending_human_confirmation"
    elif counts["sku_linked_tracked_videos"] < counts["tracked_videos"]:
        sku_state = "partial"
    else:
        sku_state = "configured"

    if counts["candidate_videos"] <= 0:
        analysis_state = "no_targets"
    elif counts["final_v1_ready_videos"] <= 0:
        analysis_state = "no_final_v1_results"
    elif counts["lens_scanned_videos"] < counts["final_v1_ready_videos"]:
        analysis_state = "lens_extraction_pending"
    else:
        analysis_state = "ready_with_evidence"

    blockers: list[dict[str, Any]] = []

    def add_blocker(code: str, count: int, owner: str, *, approval_required: bool) -> None:
        if count <= 0:
            return
        blockers.append(
            {
                "code": code,
                "count": int(count),
                "owner": owner,
                "approval_required": bool(approval_required),
            }
        )

    add_blocker(
        "content_monitoring_not_configured",
        max(0, counts["writable_kol_count"] - counts["monitoring_active_kols"]),
        "employee",
        approval_required=True,
    )
    if counts["monitoring_active_kols"] > 0 and monitoring_scheduler is not True:
        add_blocker(
            "content_monitoring_scheduler_disabled",
            counts["monitoring_active_kols"],
            "manager",
            approval_required=True,
        )
    add_blocker(
        "videos_not_tracked",
        max(0, counts["trackable_videos"] - counts["tracked_videos"]),
        "employee",
        approval_required=True,
    )
    if counts["tracked_videos"] > 0 and metric_scheduler is not True:
        add_blocker(
            "video_metric_scheduler_disabled",
            counts["tracked_videos"],
            "manager",
            approval_required=True,
        )
    add_blocker(
        "tracked_without_success_snapshot",
        max(0, counts["tracked_videos"] - counts["measured_tracked_videos"]),
        "system",
        approval_required=False,
    )
    add_blocker(
        "tracked_without_sku",
        max(0, counts["tracked_videos"] - counts["sku_linked_tracked_videos"]),
        "employee",
        approval_required=True,
    )
    add_blocker(
        "detected_sku_pending_confirmation",
        counts["sku_detected_pending_videos"],
        "employee",
        approval_required=True,
    )
    add_blocker(
        "final_v1_missing",
        max(0, counts["candidate_videos"] - counts["final_v1_ready_videos"]),
        "manager",
        approval_required=True,
    )
    add_blocker(
        "lens_extraction_pending",
        max(0, counts["final_v1_ready_videos"] - counts["lens_scanned_videos"]),
        "system",
        approval_required=False,
    )

    configured_actions = (
        counts["monitoring_active_kols"]
        + counts["share_grants"]
        + counts["tracked_videos"]
        + counts["sku_linked_tracked_videos"]
    )
    return {
        "contract": CONTRACT,
        "status": "empty" if counts["kol_count"] <= 0 else ("attention" if blockers else "ready"),
        "scope": {
            "staff_scope_id": sid or None,
            "mode": "team" if sid == 0 else "own",
        },
        "counts": counts,
        "flows": {
            "content_monitoring": {
                "state": monitoring_state,
                "requires_employee_choice": True,
                "scheduler": schedulers["vkpi_kol_content_monitoring"],
            },
            "sharing": {
                "state": "configured" if counts["share_grants"] > 0 else "needs_employee_choice",
                "requires_employee_choice": True,
            },
            "video_tracking": {
                "state": tracking_state,
                "requires_employee_choice": True,
                "scheduler": schedulers["vkpi_kol_video_metric_refresh"],
                "auto_enroll_scheduler": {
                    **schedulers["vkpi_tracking_auto_enroll"],
                    "enabled": auto_enroll_scheduler,
                },
            },
            "sku_linking": {
                "state": sku_state,
                "requires_human_confirmation_for_detected": True,
            },
            "gemini_analysis": {
                "state": analysis_state,
                "provider_calls_performed": False,
            },
        },
        "blockers": blockers,
        "summary": {
            "configured_actions": configured_actions,
            "blocker_kinds": len(blockers),
            "automatic_changes_performed": 0,
        },
        "claim_status": "descriptive_only",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
