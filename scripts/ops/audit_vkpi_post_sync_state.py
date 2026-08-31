#!/usr/bin/env python3
"""Read-only, evidence-bound post-sync acceptance gate for V-KPI."""
from __future__ import annotations
import sys as _stdout_sys
from pathlib import Path as _StdoutPath

_stdout_sys.dont_write_bytecode = True
_STDOUT_UTILS_DIR = _StdoutPath(__file__).resolve().parents[1]
if str(_STDOUT_UTILS_DIR) not in _stdout_sys.path:
    _stdout_sys.path.insert(1, str(_STDOUT_UTILS_DIR))
from stdout_utils import out as stdout_out  # noqa: E402

import argparse
import hashlib
import json
import re
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASELINE = ROOT / "scripts" / "ops" / "baselines" / "vkpi-post-sync-baseline.json"
BASELINE_SCHEMA = "vkpi-post-sync-baseline/v1"
REQUIRED_CHECKS = (
    "service_inactive_proven",
    "runtime_invocation_stable",
    "latest_invocation_complete",
    "official_18_active",
    "official_all_synced",
    "legacy_baseline_met",
    "finished_receipt_terminal",
    "batch_bound_to_log",
    "parent_terminal",
    "parent_summary_terminal",
    "child_ledger_terminal",
    "child_ledger_successful",
    "provider_claims_reconciled",
    "official_synced_in_batch",
    "official_provider_provenance",
    "official_execution_provenance",
    "maintenance_completed",
)


REMOTE_AUDIT = r'''
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get("VKPI_CODE_ROOT") or Path.cwd())
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.db.connection import close_db_runtime, get_conn  # noqa: E402

SUCCESS = {"done", "completed", "succeeded", "success"}
TERMINAL = SUCCESS | {
    "partial", "partial_done", "skipped", "skipped_known_reason", "failed", "error",
    "prefilter_rejected", "cancelled", "canceled", "timeout", "timed_out", "dead_letter",
}
MAX_LOG_BYTES = 8 * 1024 * 1024
EXECUTION_PROVENANCE_SCHEMA = "vkpi-sync-execution-provenance/v1"

def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def rows(sql, params=()):
    try:
        return [dict(row) for row in get_conn().execute(sql, params).fetchall()]
    except Exception as exc:
        return [{"error": f"{type(exc).__name__}: {exc}", "sql": sql.strip()[:120]}]

def one(sql, params=()):
    try:
        row = get_conn().execute(sql, params).fetchone()
        return dict(row) if row else {}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "sql": sql.strip()[:120]}

def count_table(table):
    result = one(f"SELECT COUNT(*) AS n FROM {table}")
    if result.get("error"):
        return {"exists": False, "count": 0, "error": result["error"]}
    return {"exists": True, "count": int(result.get("n") or 0)}

def json_object(value):
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}

def timestamp(value):
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

def log_events(path_value):
    path = Path(path_value) if path_value else None
    evidence = {
        "path": str(path or ""), "exists": False, "regular_file": False,
        "bounded_bytes": 0, "events_parsed": 0, "parse_errors": 0,
    }
    if path is None:
        evidence["error"] = "sync_log_path_missing"
        return evidence, []
    try:
        if not path.exists() or not path.is_file():
            evidence["error"] = "sync_log_unavailable"
            return evidence, []
        evidence["exists"] = True
        evidence["regular_file"] = True
        size = path.stat().st_size
        start = max(0, size - MAX_LOG_BYTES)
        events = []
        with path.open("rb") as handle:
            handle.seek(start)
            if start:
                handle.readline()
            for raw in handle:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("{"):
                    continue
                try:
                    event = json.loads(line)
                except (TypeError, ValueError):
                    evidence["parse_errors"] += 1
                    continue
                if isinstance(event, dict) and event.get("event"):
                    events.append(event)
        evidence["bounded_bytes"] = min(size, MAX_LOG_BYTES)
        evidence["events_parsed"] = len(events)
        return evidence, events
    except Exception as exc:
        evidence["error"] = f"{type(exc).__name__}: {exc}"
        return evidence, []

def sync_log_evidence(path_value, expected_invocation):
    evidence, events = log_events(path_value)
    started_indexes = [index for index, item in enumerate(events) if item.get("event") == "cron_daily_sync_started"]
    if not started_indexes:
        evidence.update({
            "started_present": False, "finished_present": False, "latest_invocation_complete": False,
            "batch_id": "", "maintenance_completed": False,
        })
        return evidence
    started_index = started_indexes[-1]
    started = events[started_index]
    started_invocation = str(started.get("invocation_id") or "").lower()
    after_started = list(enumerate(events[started_index + 1:], start=started_index + 1))
    finished_matches = [
        (index, item) for index, item in after_started
        if item.get("event") == "cron_daily_sync_finished"
        and str(item.get("invocation_id") or "").lower() == expected_invocation
    ]
    finished_index, finished = finished_matches[0] if len(finished_matches) == 1 else (-1, {})
    summary = finished.get("summary") if isinstance(finished.get("summary"), dict) else {}
    batch_id = str(summary.get("batch_id") or "")
    after_finished = events[finished_index + 1:] if finished_index >= 0 else []
    failure_events = {
        "cron_daily_sync_interrupted", "cron_daily_sync_blocked", "cron_daily_sync_failed",
    }
    attempt_events = failure_events | {"cron_daily_sync_started", "cron_daily_sync_finished"}
    failed_after_started = [
        str(item.get("event") or "") for _, item in after_started
        if str(item.get("event") or "") in failure_events
    ]
    foreign_attempt_events = [
        str(item.get("event") or "") for _, item in after_started
        if str(item.get("event") or "") in attempt_events
        and str(item.get("invocation_id") or "").lower() != expected_invocation
    ]
    terminal_after_finished = [
        str(item.get("event") or "") for item in after_finished
        if str(item.get("event") or "") in attempt_events
    ]
    invocation_matches = bool(expected_invocation) and started_invocation == expected_invocation
    latest_complete = bool(
        invocation_matches and len(finished_matches) == 1
        and not failed_after_started and not foreign_attempt_events and not terminal_after_finished
    )
    maintenance_events = {
        "cron_daily_sync_gapfill", "cron_daily_sync_gapfill_failed",
        "cron_daily_sync_index_maint_done", "cron_daily_sync_index_maint_failed",
        "cron_daily_sync_post_maintenance_skipped",
    }
    matched = [
        item for item in after_finished
        if str(item.get("event") or "") in maintenance_events
        and batch_id
        and str(item.get("batch_id") or "") == batch_id
        and str(item.get("invocation_id") or "").lower() == expected_invocation
    ]
    names = [str(item.get("event") or "") for item in matched]
    unmatched = [
        str(item.get("event") or "") for _, item in after_started
        if str(item.get("event") or "") in maintenance_events and item not in matched
    ]
    failed = sorted({name for name in names if name in {
        "cron_daily_sync_gapfill_failed", "cron_daily_sync_index_maint_failed",
    }})
    skipped = "cron_daily_sync_post_maintenance_skipped" in names
    gapfill_done = "cron_daily_sync_gapfill" in names
    index_done = "cron_daily_sync_index_maint_done" in names
    evidence.update({
        "started_present": True,
        "latest_started_at": started.get("at"),
        "expected_invocation_id": expected_invocation,
        "invocation_id": started_invocation,
        "invocation_matches": invocation_matches,
        "finished_present": len(finished_matches) == 1,
        "latest_invocation_complete": latest_complete,
        "attempt_failure_events": failed_after_started[:20],
        "foreign_attempt_events": foreign_attempt_events[:20],
        "terminal_events_after_finished": terminal_after_finished[:20],
        "finished_at": finished.get("at"),
        "finished_summary": summary,
        "batch_id": batch_id,
        "gapfill_done": gapfill_done,
        "index_maintenance_done": index_done,
        "maintenance_failed_events": failed,
        "maintenance_skipped": skipped,
        "unmatched_maintenance_events": unmatched[:20],
        "maintenance_completed": (
            latest_complete and gapfill_done and index_done
            and not failed and not skipped and not unmatched
        ),
    })
    return evidence

def linked_rows(table, task_ids, columns):
    if not task_ids:
        return []
    placeholders = ",".join(["?"] * len(task_ids))
    return rows(f"SELECT {columns} FROM {table} WHERE task_id IN ({placeholders}) ORDER BY task_id", tuple(task_ids))

def official_task_map(task_links):
    expected, errors = {}, []
    for index, item in enumerate(task_links):
        if not isinstance(item, dict) or str(item.get("lane") or "").lower() != "official":
            continue
        task_id = str(item.get("task_id") or "").strip()
        try:
            channel_id = int(item.get("channel_id") or 0)
        except (TypeError, ValueError):
            channel_id = 0
        if channel_id <= 0 or not task_id or channel_id in expected:
            errors.append({"index": index, "channel_id": channel_id, "reason": "invalid_or_duplicate_official_link"})
            continue
        expected[channel_id] = task_id
    return expected, errors

def official_run_evidence(parent_started_at, parent_finished_at, batch_id, expected_tasks, task_link_errors):
    source_rows = rows("""
        SELECT c.id, c.platform, c.account_handle, c.last_sync_status, c.last_sync_at,
               m.captured_at AS metric_captured_at, m.raw_payload_json
        FROM vkpi_employee_channels c
        LEFT JOIN vkpi_channel_metrics m ON m.id = (
          SELECT mm.id FROM vkpi_channel_metrics mm
          WHERE mm.channel_id=c.id
          ORDER BY mm.snapshot_date DESC, mm.captured_at DESC, mm.id DESC LIMIT 1
        )
        WHERE c.deleted_at IS NULL AND c.status='active'
        ORDER BY c.id
    """)
    if any(item.get("error") for item in source_rows):
        return {"error": (source_rows[0] or {}).get("error"), "active": 0}
    anchor = timestamp(parent_started_at)
    ceiling = timestamp(parent_finished_at)
    synced_ids, metric_ids, provenance_ids, exact_ids = [], [], [], []
    missing_provider, mismatched_execution = [], []
    providers = {}
    for item in source_rows:
        channel_id = int(item.get("id") or 0)
        sync_at = timestamp(item.get("last_sync_at"))
        metric_at = timestamp(item.get("metric_captured_at"))
        raw = json_object(item.get("raw_payload_json"))
        provider = str(raw.get("provider") or "").strip()
        execution = raw.get("execution_provenance") if isinstance(raw.get("execution_provenance"), dict) else {}
        expected_task_id = str(expected_tasks.get(channel_id) or "")
        if anchor and ceiling and sync_at and anchor <= sync_at <= ceiling and str(item.get("last_sync_status") or "").lower() == "synced":
            synced_ids.append(channel_id)
        if anchor and ceiling and metric_at and anchor <= metric_at <= ceiling:
            metric_ids.append(channel_id)
            if provider:
                provenance_ids.append(channel_id)
                providers[provider] = int(providers.get(provider) or 0) + 1
            else:
                missing_provider.append(channel_id)
            if (
                expected_task_id
                and str(execution.get("schema_version") or "") == EXECUTION_PROVENANCE_SCHEMA
                and str(execution.get("task_id") or "") == expected_task_id
                and str(execution.get("orchestration_batch_id") or "") == batch_id
                and str(execution.get("orchestration_lane") or "").lower() == "official"
            ):
                exact_ids.append(channel_id)
            else:
                mismatched_execution.append(channel_id)
    return {
        "active": len(source_rows),
        "expected_official_tasks": len(expected_tasks),
        "task_link_errors": task_link_errors[:20],
        "synced_since_parent": len(set(synced_ids)),
        "metrics_since_parent": len(set(metric_ids)),
        "provider_provenance_since_parent": len(set(provenance_ids)),
        "exact_execution_provenance_since_parent": len(set(exact_ids)),
        "missing_provider_channel_ids": sorted(set(missing_provider))[:20],
        "missing_or_mismatched_execution_channel_ids": sorted(set(mismatched_execution))[:20],
        "providers": dict(sorted(providers.items())),
        "parent_started_at": parent_started_at,
        "parent_finished_at": parent_finished_at,
        "orchestration_batch_id": batch_id,
    }

sync_log = sync_log_evidence(
    os.environ.get("VKPI_SYNC_LOG_PATH", ""),
    str(os.environ.get("VKPI_EXPECTED_INVOCATION_ID") or "").lower(),
)
batch_id = str(sync_log.get("batch_id") or "")
parent = one("""
    SELECT run_id, job_name, stage, started_at, finished_at, status, reason, summary_json
    FROM vkpi_sync_runs
    WHERE run_id=? AND job_name='daily_incremental_sync' AND stage='durable_batch'
    LIMIT 1
""", (batch_id,)) if batch_id else {}
parent_summary = json_object(parent.get("summary_json"))
batch = parent_summary.get("batch") if isinstance(parent_summary.get("batch"), dict) else {}
parent_task_ids = list(dict.fromkeys(str(item).strip() for item in (batch.get("task_ids") or []) if str(item).strip()))
task_links = batch.get("task_links") if isinstance(batch.get("task_links"), list) else []
link_task_ids = [str(item.get("task_id") or "").strip() for item in task_links if isinstance(item, dict)]
official_tasks, official_task_link_errors = official_task_map(task_links)
finished_summary = sync_log.get("finished_summary") if isinstance(sync_log.get("finished_summary"), dict) else {}
log_task_ids = list(dict.fromkeys(str(item).strip() for item in (finished_summary.get("task_ids") or []) if str(item).strip()))
ledger_rows = linked_rows(
    "job_execution_ledger", parent_task_ids,
    "task_id, job_type, status, started_at, finished_at, result_json",
)
claim_rows = linked_rows(
    "vkpi_provider_execution_claims", parent_task_ids,
    "task_id, job_type, state, provider_run_id, completed_at",
)
ledger_error = next((item.get("error") for item in ledger_rows if item.get("error")), "")
claim_error = next((item.get("error") for item in claim_rows if item.get("error")), "")
ledger_statuses = {str(item.get("task_id") or ""): str(item.get("status") or "").lower() for item in ledger_rows if not item.get("error")}
claim_states = {str(item.get("task_id") or ""): str(item.get("state") or "").lower() for item in claim_rows if not item.get("error")}

payload = {
    "checked_at": utcnow(),
    "sync_log": sync_log,
    "daily_batch": {
        "batch_id": batch_id,
        "parent": {key: value for key, value in parent.items() if key != "summary_json"},
        "parent_summary": parent_summary,
        "parent_task_ids": parent_task_ids,
        "log_task_ids": log_task_ids,
        "task_links_match": bool(
            batch_id
            and parent.get("run_id") == batch_id
            and batch.get("batch_id") == batch_id
            and parent_task_ids
            and parent_task_ids == log_task_ids
            and parent_task_ids == link_task_ids
        ),
        "ledger": {
            "error": ledger_error,
            "expected": len(parent_task_ids),
            "found": len(ledger_statuses),
            "status_counts": {status: list(ledger_statuses.values()).count(status) for status in sorted(set(ledger_statuses.values()))},
            "missing_task_ids": sorted(set(parent_task_ids) - set(ledger_statuses))[:20],
            "all_terminal": bool(parent_task_ids) and len(ledger_statuses) == len(parent_task_ids) and all(value in TERMINAL for value in ledger_statuses.values()),
            "all_successful": bool(parent_task_ids) and len(ledger_statuses) == len(parent_task_ids) and all(value in SUCCESS for value in ledger_statuses.values()),
        },
        "provider_claims": {
            "error": claim_error,
            "expected": len(parent_task_ids),
            "found": len(claim_states),
            "state_counts": {state: list(claim_states.values()).count(state) for state in sorted(set(claim_states.values()))},
            "missing_task_ids": sorted(set(parent_task_ids) - set(claim_states))[:20],
            "all_reconciled": bool(parent_task_ids) and len(claim_states) == len(parent_task_ids) and all(value == "completed" for value in claim_states.values()),
            "provider_run_ids": sum(1 for item in claim_rows if item.get("provider_run_id")),
        },
    },
    "official_run_evidence": official_run_evidence(
        parent.get("started_at"), parent.get("finished_at"), batch_id,
        official_tasks, official_task_link_errors,
    ),
    "official_channels": one("""
        SELECT COUNT(*) AS total_channels,
          SUM(CASE WHEN deleted_at IS NULL AND status='active' THEN 1 ELSE 0 END) AS active_channels,
          SUM(CASE WHEN deleted_at IS NULL AND status='active' AND last_sync_status='synced' THEN 1 ELSE 0 END) AS synced_channels,
          MAX(last_sync_at) AS latest_channel_sync_at
        FROM vkpi_employee_channels
    """),
    "platforms": rows("""
        SELECT c.platform, COUNT(*) AS channel_count,
          SUM(CASE WHEN c.last_sync_status='synced' THEN 1 ELSE 0 END) AS synced_count,
          COALESCE(SUM(m.followers), 0) AS followers,
          COALESCE(SUM(m.posts_count), 0) AS posts_count,
          COALESCE(SUM(m.total_views), 0) AS total_views,
          COALESCE(SUM(m.followers_delta), 0) AS followers_delta,
          COALESCE(SUM(m.posts_delta), 0) AS posts_delta,
          COALESCE(SUM(m.views_delta_24h), 0) AS views_delta
        FROM vkpi_employee_channels c
        LEFT JOIN vkpi_channel_metrics m ON m.id = (
          SELECT mm.id FROM vkpi_channel_metrics mm WHERE mm.channel_id=c.id
          ORDER BY mm.snapshot_date DESC, mm.captured_at DESC, mm.id DESC LIMIT 1
        )
        WHERE c.deleted_at IS NULL AND c.status='active'
        GROUP BY c.platform ORDER BY c.platform
    """),
    "today_metric_delta": one("""
        SELECT COUNT(*) AS metric_rows, COALESCE(SUM(followers_delta), 0) AS followers_delta,
          COALESCE(SUM(posts_delta), 0) AS posts_delta, COALESCE(SUM(views_delta_24h), 0) AS views_delta
        FROM vkpi_channel_metrics WHERE snapshot_date >= CURRENT_DATE
    """),
    "reddit": rows("""
        SELECT c.id, c.account_handle, c.last_sync_at, c.last_sync_status,
          COALESCE(m.followers, 0) AS followers, COALESCE(m.posts_count, 0) AS posts_count,
          COALESCE(m.total_views, 0) AS total_views, COALESCE(m.total_likes, 0) AS total_likes,
          COALESCE(m.total_comments, 0) AS total_comments
        FROM vkpi_employee_channels c
        LEFT JOIN vkpi_channel_metrics m ON m.id = (
          SELECT mm.id FROM vkpi_channel_metrics mm WHERE mm.channel_id=c.id
          ORDER BY mm.snapshot_date DESC, mm.captured_at DESC, mm.id DESC LIMIT 1
        )
        WHERE c.deleted_at IS NULL AND c.platform='reddit' ORDER BY c.id
    """),
    "kol_pool": one("""
        SELECT COUNT(*) AS total,
          SUM(CASE WHEN source_type='legacy_excel_p2d' THEN 1 ELSE 0 END) AS legacy_excel_p2d,
          SUM(CASE WHEN source_type='legacy_excel_p2d' AND updated_at >= CURRENT_DATE THEN 1 ELSE 0 END) AS legacy_updated_today,
          SUM(CASE WHEN raw_platform_data IS NOT NULL AND raw_platform_data NOT IN ('', '{}', '[]') THEN 1 ELSE 0 END) AS with_raw_platform_data
        FROM vkpi_kol_pool
    """),
    "kol_pool_by_platform": rows("""
        SELECT platform, COUNT(*) AS count FROM vkpi_kol_pool
        GROUP BY platform ORDER BY count DESC, platform ASC LIMIT 20
    """),
    "brand_signal": count_table("vkpi_brand_signal"),
    "brand_signal_summary": one("""
        SELECT COUNT(*) AS total, SUM(CASE WHEN is_new THEN 1 ELSE 0 END) AS new_count,
          SUM(CASE WHEN brand_role='competitor' THEN 1 ELSE 0 END) AS competitor_count,
          SUM(CASE WHEN analysis_scope='current_year' THEN 1 ELSE 0 END) AS current_year_count
        FROM vkpi_brand_signal
    """),
    "competitor_relation": count_table("vkpi_competitor_relation"),
    "competitor_relation_summary": one("""
        SELECT COUNT(*) AS total, COUNT(DISTINCT kol_pool_id) AS kol_count,
          SUM(CASE WHEN risk_tier='avoid' THEN 1 ELSE 0 END) AS avoid_count,
          SUM(CASE WHEN risk_tier='caution' THEN 1 ELSE 0 END) AS caution_count,
          SUM(CASE WHEN risk_tier='safe' THEN 1 ELSE 0 END) AS safe_count,
          SUM(CASE WHEN risk_tier='opportunity' THEN 1 ELSE 0 END) AS opportunity_count,
          MAX(computed_at) AS latest_computed_at
        FROM vkpi_competitor_relation
    """),
    "media_cache_assets": rows("""
        SELECT storage_backend, COUNT(*) AS count, COALESCE(SUM(size_bytes), 0) AS size_bytes
        FROM vkpi_media_cache_assets GROUP BY storage_backend ORDER BY storage_backend
    """),
}
print(json.dumps(payload, ensure_ascii=False, default=str, indent=2, sort_keys=True))

try:
    close_db_runtime()
except Exception:
    pass
'''


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(command: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, text=True, capture_output=True, timeout=timeout)


def parse_json_blob(output: str) -> dict[str, Any]:
    text = output.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no json object in output")
    payload = json.loads(text[start:end + 1])
    return payload if isinstance(payload, dict) else {"value": payload}


def load_baseline(path: Path) -> dict[str, Any]:
    path = path.expanduser()
    if path.is_symlink() or not path.is_file():
        raise ValueError("baseline must be a regular non-symlink file")
    raw = path.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("baseline is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != BASELINE_SCHEMA:
        raise ValueError(f"baseline schema must be {BASELINE_SCHEMA}")
    minimum = payload.get("legacy_excel_p2d_minimum")
    if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 0:
        raise ValueError("baseline legacy_excel_p2d_minimum must be a non-negative integer")
    evidence = dict(payload.get("evidence")) if isinstance(payload.get("evidence"), dict) else {}
    if not str(payload.get("observed_at") or "").strip() or not str(evidence.get("path") or "").strip():
        raise ValueError("baseline requires observed_at and evidence.path")
    evidence_sha = str(evidence.get("sha256") or "").lower()
    if len(evidence_sha) != 64 or any(char not in "0123456789abcdef" for char in evidence_sha):
        raise ValueError("baseline evidence.sha256 must be a SHA-256 hex digest")
    evidence_path = Path(str(evidence["path"])).expanduser()
    if not evidence_path.is_absolute():
        evidence_path = path.parent / evidence_path
    if evidence_path.is_symlink() or not evidence_path.is_file():
        raise ValueError("baseline evidence must be a regular non-symlink file")
    evidence_raw = evidence_path.read_bytes()
    if hashlib.sha256(evidence_raw).hexdigest() != evidence_sha:
        raise ValueError("baseline evidence SHA-256 mismatch")
    try:
        observation: Any = json.loads(evidence_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("baseline evidence is not valid UTF-8 JSON") from exc
    field = str(evidence.get("field") or "").strip()
    for part in field.split(".") if field else ():
        if not isinstance(observation, dict) or part not in observation:
            raise ValueError("baseline evidence field is missing")
        observation = observation[part]
    if isinstance(observation, bool) or not isinstance(observation, int) or observation != minimum:
        raise ValueError("baseline minimum does not match its evidence observation")
    evidence.update({
        "verified": True,
        "verified_path": str(evidence_path.resolve()),
        "observed_value": observation,
    })
    return {
        "schema_version": BASELINE_SCHEMA,
        "legacy_excel_p2d_minimum": minimum,
        "observed_at": str(payload["observed_at"]),
        "evidence": evidence,
        "policy_file": str(path.resolve()),
        "policy_sha256": hashlib.sha256(raw).hexdigest(),
    }


def _mapping(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    return value if isinstance(value, dict) else {}


def evaluate_acceptance(payload: dict[str, Any], baseline: dict[str, Any]) -> dict[str, bool]:
    official = _mapping(payload, "official_channels")
    kol_pool = _mapping(payload, "kol_pool")
    sync_log = _mapping(payload, "sync_log")
    daily = _mapping(payload, "daily_batch")
    parent = _mapping(daily, "parent")
    parent_summary = _mapping(daily, "parent_summary")
    completion = _mapping(parent_summary, "completion")
    ledger = _mapping(daily, "ledger")
    claims = _mapping(daily, "provider_claims")
    official_run = _mapping(payload, "official_run_evidence")
    runtime = _mapping(payload, "runtime_guard")
    active = int(official.get("active_channels") or 0)
    synced = int(official.get("synced_channels") or 0)
    legacy_actual = int(kol_pool.get("legacy_excel_p2d") or 0)
    finished = _mapping(sync_log, "finished_summary")
    return {
        "service_inactive_proven": bool(runtime.get("terminal_success")),
        "runtime_invocation_stable": bool(runtime.get("verified")),
        "latest_invocation_complete": bool(sync_log.get("latest_invocation_complete")),
        "official_18_active": active >= 18,
        "official_all_synced": synced >= active >= 18,
        "legacy_baseline_met": legacy_actual >= int(baseline["legacy_excel_p2d_minimum"]),
        "finished_receipt_terminal": (
            str(finished.get("status") or "").lower() == "completed"
            and str(finished.get("completion_scope") or "").lower() == "provider_terminal"
            and str(finished.get("provider_completion") or "").lower() == "completed"
            and int(finished.get("tasks_pending") or 0) == 0
        ),
        "batch_bound_to_log": bool(daily.get("task_links_match")),
        "parent_terminal": str(parent.get("status") or "").lower() == "completed" and bool(parent.get("finished_at")),
        "parent_summary_terminal": (
            str(parent_summary.get("status") or "").lower() == "completed"
            and str(parent_summary.get("completion_scope") or completion.get("completion_scope") or "").lower() == "provider_terminal"
            and str(parent_summary.get("provider_completion") or completion.get("provider_completion") or "").lower() == "completed"
            and bool(completion.get("complete"))
            and int(completion.get("tasks_pending") or 0) == 0
        ),
        "child_ledger_terminal": bool(ledger.get("all_terminal")) and not ledger.get("error"),
        "child_ledger_successful": bool(ledger.get("all_successful")) and not ledger.get("error"),
        "provider_claims_reconciled": bool(claims.get("all_reconciled")) and not claims.get("error"),
        "official_synced_in_batch": active >= 18 and int(official_run.get("synced_since_parent") or 0) >= active,
        "official_provider_provenance": (
            active >= 18
            and int(official_run.get("metrics_since_parent") or 0) == active
            and int(official_run.get("provider_provenance_since_parent") or 0) == active
        ),
        "official_execution_provenance": (
            active >= 18
            and int(official_run.get("expected_official_tasks") or 0) == active
            and int(official_run.get("exact_execution_provenance_since_parent") or 0) == active
            and not official_run.get("task_link_errors")
        ),
        "maintenance_completed": bool(sync_log.get("maintenance_completed")),
    }


_SYSTEMD_PROPERTIES = (
    "ActiveState", "SubState", "Result", "InvocationID", "ExecMainCode", "ExecMainStatus",
)


def _systemd_snapshot(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    values = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in _SYSTEMD_PROPERTIES:
            values[key] = value.strip()
    return {
        "probe_ok": result.returncode == 0,
        "active_state": values.get("ActiveState", "unknown").lower(),
        "sub_state": values.get("SubState", "unknown").lower(),
        "result": values.get("Result", "unknown").lower(),
        "invocation_id": values.get("InvocationID", "").lower(),
        "exec_main_code": values.get("ExecMainCode", ""),
        "exec_main_status": values.get("ExecMainStatus", ""),
    }


def _systemd_command(service: str) -> list[str]:
    return [
        "systemctl", "show", service, "--no-pager",
        *[f"--property={name}" for name in _SYSTEMD_PROPERTIES],
    ]


def remote_systemd_snapshot(target: str, service: str) -> dict[str, Any]:
    command = " ".join(shlex.quote(item) for item in _systemd_command(service))
    return _systemd_snapshot(run(["ssh", target, command], timeout=20))


def local_systemd_snapshot(service: str) -> dict[str, Any]:
    return _systemd_snapshot(run(_systemd_command(service), timeout=20))


def runtime_guard(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "probe_ok", "active_state", "sub_state", "result", "invocation_id",
        "exec_main_code", "exec_main_status",
    )
    stable = all(before.get(key) == after.get(key) for key in fields)
    invocation_id = str(before.get("invocation_id") or "")
    terminal_success = bool(
        before.get("probe_ok")
        and before.get("active_state") == "inactive"
        and before.get("sub_state") == "dead"
        and before.get("result") == "success"
        and str(before.get("exec_main_code") or "") == "1"
        and str(before.get("exec_main_status") or "") == "0"
        and re.fullmatch(r"[0-9a-f]{32}", invocation_id)
    )
    return {
        "before": before, "after": after, "stable": stable,
        "terminal_success": terminal_success,
        "verified": stable and terminal_success,
        "invocation_id": invocation_id,
    }


def _audit_command(root: str, sync_log_path: str, expected_invocation: str) -> str:
    root_q = shlex.quote(root)
    log_q = shlex.quote(sync_log_path)
    invocation_q = shlex.quote(expected_invocation)
    return (
        f"root={root_q}; code_root=\"$root/current\"; "
        "[ -d \"$code_root/backend\" ] || code_root=\"$root\"; "
        "cd \"$root\" && "
        f"export VKPI_CODE_ROOT=\"$code_root\" PYTHONPATH=\"$code_root/backend\" "
        f"VKPI_SYNC_LOG_PATH={log_q} VKPI_EXPECTED_INVOCATION_ID={invocation_q} && "
        f"env PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B - <<'PY'\n{REMOTE_AUDIT}\nPY"
    )


def audit_remote(target: str, root: str, sync_log_path: str, expected_invocation: str) -> dict[str, Any]:
    result = run(["ssh", target, _audit_command(root, sync_log_path, expected_invocation)], timeout=120)
    if result.returncode != 0:
        return {
            "checked_at": utcnow(), "target": target, "remote_root": root,
            "error": result.stderr.strip() or result.stdout.strip() or f"ssh exited {result.returncode}",
        }
    payload = parse_json_blob(result.stdout)
    payload.update({"target": target, "remote_root": root})
    return payload


def audit_local(root: str, sync_log_path: str, expected_invocation: str) -> dict[str, Any]:
    result = run(["bash", "-lc", _audit_command(root, sync_log_path, expected_invocation)], timeout=120)
    if result.returncode != 0:
        return {
            "checked_at": utcnow(), "target": "local", "remote_root": root,
            "error": result.stderr.strip() or result.stdout.strip() or f"local audit exited {result.returncode}",
        }
    payload = parse_json_blob(result.stdout)
    payload.update({"target": "local", "remote_root": root})
    return payload


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only post-sync V-KPI acceptance gate")
    parser.add_argument("--remote", default="viltrox", help="SSH target")
    parser.add_argument("--remote-root", default="/opt/viltrox-2.0", help="Remote deployment root")
    parser.add_argument("--service", default="vkpi-sync-daily.service", help="Sync systemd service name")
    parser.add_argument("--allow-during-sync", action="store_true", help="Inspect while active; acceptance still fails closed")
    parser.add_argument("--local", action="store_true", help="Audit the current machine directly instead of SSH")
    parser.add_argument("--baseline-file", type=Path, default=DEFAULT_BASELINE, help="Versioned acceptance baseline JSON")
    parser.add_argument(
        "--sync-log-path",
        default=f"/var/log/vkpi/sync_daily_{datetime.now(timezone.utc).strftime('%Y%m%d')}.log",
        help="Append-only JSON event log for the run being accepted",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        baseline = load_baseline(args.baseline_file)
    except (OSError, ValueError) as exc:
        stdout_out(json.dumps({
            "checked_at": utcnow(), "acceptance_ready": False,
            "configuration_error": f"{type(exc).__name__}: {exc}",
            "baseline_file": str(args.baseline_file),
        }, ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    snapshot = local_systemd_snapshot if args.local else lambda service: remote_systemd_snapshot(args.remote, service)
    before = snapshot(args.service)
    service_state = str(before.get("active_state") or "unknown")
    if service_state in {"active", "activating"} and not args.allow_during_sync:
        stdout_out(json.dumps({
            "checked_at": utcnow(), "acceptance_ready": False, "blocked": True,
            "reason": f"{args.service} is {service_state}; post-sync acceptance requires a terminal service",
            "service": args.service, "service_state": service_state,
            "runtime_before": before,
            "target": "local" if args.local else args.remote,
            "remote_root": args.remote_root, "acceptance_baseline": baseline,
        }, ensure_ascii=False, indent=2, sort_keys=True))
        return 3
    expected_invocation = str(before.get("invocation_id") or "")
    try:
        payload = (
            audit_local(args.remote_root, args.sync_log_path, expected_invocation)
            if args.local else
            audit_remote(args.remote, args.remote_root, args.sync_log_path, expected_invocation)
        )
    except Exception as exc:
        payload = {"checked_at": utcnow(), "error": f"{type(exc).__name__}: {exc}"}
    after = snapshot(args.service)
    guard = runtime_guard(before, after)
    payload.update({
        "service": args.service, "service_state": service_state,
        "runtime_guard": guard,
        "acceptance_baseline": baseline, "required_checks": list(REQUIRED_CHECKS),
    })
    if payload.get("error"):
        payload.update({"acceptance_ready": False, "failed_checks": list(REQUIRED_CHECKS)})
        stdout_out(json.dumps(payload, ensure_ascii=False, default=str, indent=2, sort_keys=True))
        return 2
    acceptance = evaluate_acceptance(payload, baseline)
    failed = [key for key in REQUIRED_CHECKS if not acceptance.get(key)]
    payload.update({"acceptance": acceptance, "failed_checks": failed, "acceptance_ready": not failed})
    stdout_out(json.dumps(payload, ensure_ascii=False, default=str, indent=2, sort_keys=True))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
