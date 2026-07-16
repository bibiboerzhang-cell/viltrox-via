#!/usr/bin/env python3
"""Release-bound, resumable 72-hour V-KPI staging soak orchestrator.

Formal success is intentionally impossible before 72 natural hours have
elapsed.  Short runs are useful diagnostics, but their receipt remains
``pending``.  Samples are append-only, hash chained, private, and contain no
environment values, connection strings, journal messages, or response bodies.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import stat
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

if __package__:
    from .staging_soak_collectors import CollectorConfig, CollectionError, HostCollector
else:
    from staging_soak_collectors import CollectorConfig, CollectionError, HostCollector


SCHEMA_VERSION = 1
FORMAL_DURATION_SECONDS = 72 * 60 * 60
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
MIGRATION_RE = re.compile(r"^[0-9]{3}_[A-Za-z0-9_.-]+\.sql$")
UNIT_RE = re.compile(r"^[A-Za-z0-9@_.-]+\.service$")
DEFAULT_UNITS = (
    "viltrox-2.0-test.service",
    "vkpi-worker-interactive.service",
    *(f"vkpi-worker-bulk@{index}.service" for index in range(1, 7)),
    "vkpi-redis-worker.service",
)


class SoakError(RuntimeError):
    """Fail-closed state/evidence error."""


class SampleCollector(Protocol):
    def collect(self, *, journal_cursor: str | None) -> tuple[dict[str, Any], str]: ...


@dataclass(frozen=True)
class SoakConfig:
    health_url: str
    env_file: str
    root: str
    expected_head: str
    expected_migration: str
    expected_apify_workers: int
    expected_redis_workers: int
    systemd_units: tuple[str, ...]
    diagnostic_target_seconds: float
    interval_seconds: float
    max_sample_latency_ms: float
    max_p95_latency_ms: float
    max_queue_age_seconds: float
    max_lock_waits: int
    min_disk_free_bytes: int

    def validate(self, *, allow_short_diagnostic: bool) -> None:
        if SHA40_RE.fullmatch(self.expected_head) is None:
            raise SoakError("expected HEAD must be an exact lowercase 40-character SHA")
        if MIGRATION_RE.fullmatch(self.expected_migration) is None:
            raise SoakError("expected migration must be an exact migration filename")
        if not (1 <= self.expected_apify_workers <= 64):
            raise SoakError("expected Apify worker count is outside reviewed bounds")
        if not (1 <= self.expected_redis_workers <= 4):
            raise SoakError("expected Redis worker count is outside reviewed bounds")
        if (
            not self.systemd_units
            or len(set(self.systemd_units)) != len(self.systemd_units)
            or any(UNIT_RE.fullmatch(unit) is None for unit in self.systemd_units)
        ):
            raise SoakError("systemd unit set is invalid")
        if not (0.1 <= self.interval_seconds <= 300.0):
            raise SoakError("sample interval must stay within [0.1, 300] seconds")
        if not (0.1 <= self.diagnostic_target_seconds <= FORMAL_DURATION_SECONDS):
            raise SoakError("diagnostic duration is outside reviewed bounds")
        if self.diagnostic_target_seconds < FORMAL_DURATION_SECONDS and not allow_short_diagnostic:
            raise SoakError("short runs require --allow-short-diagnostic")
        if self.diagnostic_target_seconds >= FORMAL_DURATION_SECONDS and self.interval_seconds > 60.0:
            raise SoakError("formal 72-hour evidence requires sampling at least once per minute")
        if not (100.0 <= self.max_sample_latency_ms <= 120_000.0):
            raise SoakError("sample latency limit is outside reviewed bounds")
        if not (10.0 <= self.max_p95_latency_ms <= self.max_sample_latency_ms):
            raise SoakError("p95 latency limit is outside reviewed bounds")
        if not (0.0 <= self.max_queue_age_seconds <= 7 * 24 * 3600):
            raise SoakError("queue age limit is outside reviewed bounds")
        if not (0 <= self.max_lock_waits <= 1000):
            raise SoakError("lock wait limit is outside reviewed bounds")
        if self.min_disk_free_bytes < 1024**3:
            raise SoakError("minimum free disk gate must be at least 1 GiB")

    def public_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["systemd_units"] = list(self.systemd_units)
        value["env_file_path_sha256"] = hashlib.sha256(self.env_file.encode()).hexdigest()
        value["root_path_sha256"] = hashlib.sha256(self.root.encode()).hexdigest()
        value.pop("env_file")
        value.pop("root")
        return value


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _utc(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _parse_utc(value: str) -> float:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise SoakError("sample timestamp is invalid") from None
    if parsed.tzinfo is None:
        raise SoakError("sample timestamp is invalid")
    return parsed.timestamp()


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * percentile / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 3)
    weight = position - lower
    return round(ordered[lower] * (1.0 - weight) + ordered[upper] * weight, 3)


def _private_directory(path: Path) -> Path:
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_dir():
            raise SoakError("state path must be a real directory")
    else:
        path.mkdir(parents=True, mode=0o700)
    info = path.stat()
    if info.st_uid != os.geteuid() or info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise SoakError("state directory must be private and owned by the executor")
    return path.resolve()


def _assert_private_file(path: Path, *, label: str) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError:
        raise SoakError(f"{label} is unavailable") from None
    if (
        path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != os.geteuid()
        or info.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
    ):
        raise SoakError(f"{label} must be a private executor-owned regular file")
    return info


def _write_private_json(path: Path, payload: Mapping[str, Any], *, overwrite: bool) -> None:
    if path.is_symlink() or (path.exists() and not overwrite):
        raise SoakError("refusing to overwrite evidence path")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    parent_info = path.parent.lstat()
    if (
        path.parent.is_symlink()
        or not stat.S_ISDIR(parent_info.st_mode)
        or parent_info.st_uid != os.geteuid()
        or parent_info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    ):
        raise SoakError("evidence parent is unsafe")
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(json.dumps(payload, sort_keys=True, indent=2).encode("utf-8"))
            handle.write(b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class StateStore:
    """Private append-only hash chain plus an atomic resume checkpoint."""

    def __init__(self, state_dir: Path, config: SoakConfig) -> None:
        self.root = _private_directory(state_dir)
        self.manifest_path = self.root / "manifest.json"
        self.samples_path = self.root / "samples.jsonl"
        self.checkpoint_path = self.root / "checkpoint.json"
        self.lock_path = self.root / ".lock"
        self.config = config
        self.binding = {
            "expected_head": config.expected_head,
            "expected_migration": config.expected_migration,
            "expected_apify_workers": config.expected_apify_workers,
            "expected_redis_workers": config.expected_redis_workers,
            "systemd_units": list(config.systemd_units),
            "formal_duration_seconds": FORMAL_DURATION_SECONDS,
            "config_sha256": _sha256(config.public_dict()),
        }
        self._lock_handle = None

    def __enter__(self) -> "StateStore":
        if self.lock_path.exists() or self.lock_path.is_symlink():
            _assert_private_file(self.lock_path, label="state lock")
        try:
            flags = os.O_RDWR | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self.lock_path, flags, 0o600)
            self._lock_handle = os.fdopen(descriptor, "a+", encoding="utf-8")
        except OSError:
            raise SoakError("state lock is unavailable") from None
        try:
            fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SoakError("another soak process owns the state directory") from None
        self._ensure_manifest()
        return self

    def __exit__(self, *_args: Any) -> None:
        if self._lock_handle is not None:
            fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_UN)
            self._lock_handle.close()
            self._lock_handle = None

    def _ensure_manifest(self) -> None:
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "evidence_type": "vkpi_staging_soak_manifest",
            "binding": self.binding,
            "config": self.config.public_dict(),
            "secrets_included": False,
        }
        if not self.manifest_path.exists():
            _write_private_json(self.manifest_path, manifest, overwrite=False)
            descriptor = os.open(self.samples_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            return
        _assert_private_file(self.manifest_path, label="manifest")
        try:
            existing = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise SoakError("manifest is unreadable") from None
        if existing != manifest:
            raise SoakError("resume configuration does not match the sealed manifest")
        if not self.samples_path.is_file() or self.samples_path.is_symlink():
            raise SoakError("sample chain is missing or unsafe")
        _assert_private_file(self.samples_path, label="sample chain")

    def records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        previous = "0" * 64
        try:
            lines = self.samples_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            raise SoakError("sample chain is unreadable") from None
        for expected_seq, line in enumerate(lines, 1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                raise SoakError("sample chain contains invalid JSON") from None
            if not isinstance(record, dict):
                raise SoakError("sample chain contains invalid record")
            digest = str(record.pop("record_sha256", ""))
            valid = (
                record.get("seq") == expected_seq
                and record.get("previous_sha256") == previous
                and _sha256(record) == digest
            )
            record["record_sha256"] = digest
            if not valid:
                raise SoakError("sample hash chain verification failed")
            records.append(record)
            previous = digest
        if self.checkpoint_path.exists():
            _assert_private_file(self.checkpoint_path, label="checkpoint")
            try:
                checkpoint = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raise SoakError("checkpoint is unreadable") from None
            expected_head = records[-1]["record_sha256"] if records else "0" * 64
            if checkpoint.get("sample_count") != len(records) or checkpoint.get("chain_head") != expected_head:
                raise SoakError("checkpoint does not match the sample chain")
        return records

    def append(self, payload: dict[str, Any]) -> dict[str, Any]:
        records = self.records()
        previous = records[-1]["record_sha256"] if records else "0" * 64
        record = {
            "seq": len(records) + 1,
            "previous_sha256": previous,
            **payload,
        }
        record["record_sha256"] = _sha256(record)
        encoded = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        with self.samples_path.open("a", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(self.samples_path, 0o600)
        checkpoint = {
            "schema_version": SCHEMA_VERSION,
            "sample_count": record["seq"],
            "chain_head": record["record_sha256"],
            "last_captured_at": record["captured_at"],
            "journal_cursor": record["journal_cursor"],
        }
        _write_private_json(self.checkpoint_path, checkpoint, overwrite=True)
        return record


def _systemd_projection(sample: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    systemd = sample.get("systemd") if isinstance(sample.get("systemd"), Mapping) else {}
    result: dict[str, dict[str, int]] = {}
    for row in systemd.get("units") or []:
        if isinstance(row, Mapping):
            result[str(row.get("unit") or "")] = {
                "n_restarts": int(row.get("n_restarts") or 0),
                "main_pid": int(row.get("main_pid") or 0),
            }
    return result


def _baseline(sample: Mapping[str, Any]) -> dict[str, Any]:
    environment = (
        sample.get("environment") if isinstance(sample.get("environment"), Mapping) else {}
    )
    health = sample.get("health") if isinstance(sample.get("health"), Mapping) else {}
    apify = health.get("apify") if isinstance(health.get("apify"), Mapping) else {}
    redis_worker = (
        health.get("redis_worker") if isinstance(health.get("redis_worker"), Mapping) else {}
    )
    redis_state = sample.get("redis") if isinstance(sample.get("redis"), Mapping) else {}
    database = sample.get("database") if isinstance(sample.get("database"), Mapping) else {}
    return {
        "environment_content_sha256": str(environment.get("content_sha256") or ""),
        "database_name_sha256": str(database.get("database_name_sha256") or ""),
        "apify_boot_nonce_sha256_set": list(apify.get("boot_nonce_sha256_set") or []),
        "redis_worker_boot_nonce_sha256_set": list(
            redis_worker.get("boot_nonce_sha256_set") or []
        ),
        "redis_run_id_sha256": str(redis_state.get("run_id_sha256") or ""),
        "systemd": _systemd_projection(sample),
    }


def evaluate_sample(
    sample: Mapping[str, Any], config: SoakConfig, *, baseline: Mapping[str, Any] | None
) -> list[str]:
    violations: list[str] = []
    environment = (
        sample.get("environment") if isinstance(sample.get("environment"), Mapping) else {}
    )
    environment_sha = str(environment.get("content_sha256") or "")
    if len(environment_sha) != 64 or any(
        character not in "0123456789abcdef" for character in environment_sha
    ):
        violations.append("environment_fingerprint")
    health = sample.get("health") if isinstance(sample.get("health"), Mapping) else {}
    if health.get("status") != "ok":
        violations.append("health_status")
    if health.get("server_git_sha") != config.expected_head:
        violations.append("server_sha")
    if health.get("client_git_sha") != config.expected_head or health.get("sha_aligned") is not True:
        violations.append("client_sha")
    if health.get("migration_max") != config.expected_migration:
        violations.append("health_migration")
    if float(health.get("latency_ms") or 0.0) > config.max_sample_latency_ms:
        violations.append("health_latency")

    apify = health.get("apify") if isinstance(health.get("apify"), Mapping) else {}
    apify_nonces = list(apify.get("boot_nonce_sha256_set") or [])
    if (
        apify.get("online_count") != config.expected_apify_workers
        or len(apify_nonces) != config.expected_apify_workers
        or apify.get("unique_names") is not True
        or apify.get("unique_pids") is not True
        or apify.get("all_worker_sha_aligned") is not True
        or apify.get("all_heartbeats_fresh") is not True
        or not {"interactive", "batch"}.issubset(set(apify.get("lane_coverage") or []))
    ):
        violations.append("apify_fleet")
    redis_worker = (
        health.get("redis_worker") if isinstance(health.get("redis_worker"), Mapping) else {}
    )
    redis_nonces = list(redis_worker.get("boot_nonce_sha256_set") or [])
    if (
        redis_worker.get("online_count") != config.expected_redis_workers
        or len(redis_nonces) != config.expected_redis_workers
        or redis_worker.get("all_worker_sha_aligned") is not True
        or redis_worker.get("all_heartbeats_fresh") is not True
        or redis_worker.get("all_redis_ready") is not True
    ):
        violations.append("redis_worker_fleet")

    database = sample.get("database") if isinstance(sample.get("database"), Mapping) else {}
    if database.get("migration_max") != config.expected_migration:
        violations.append("database_migration")
    if database.get("transaction_mode") != "read_only":
        violations.append("database_transaction_mode")
    if int(database.get("idle_in_transaction_over_30s") or 0) != 0:
        violations.append("idle_in_transaction")
    if int(database.get("lock_waits") or 0) > config.max_lock_waits:
        violations.append("lock_waits")
    queue = database.get("queue") if isinstance(database.get("queue"), Mapping) else {}
    if queue.get("present") is not True:
        violations.append("queue_missing")
    oldest = queue.get("oldest_queued_age_seconds")
    if oldest is not None and float(oldest) > config.max_queue_age_seconds:
        violations.append("queue_age")

    redis_state = sample.get("redis") if isinstance(sample.get("redis"), Mapping) else {}
    if (
        redis_state.get("aof_enabled") is not True
        or redis_state.get("aof_last_write_status") != "ok"
        or redis_state.get("rdb_last_bgsave_status") != "ok"
    ):
        violations.append("redis_persistence")
    disk = sample.get("disk") if isinstance(sample.get("disk"), Mapping) else {}
    if int(disk.get("free_bytes") or 0) < config.min_disk_free_bytes:
        violations.append("disk_free")

    units = sample.get("systemd") if isinstance(sample.get("systemd"), Mapping) else {}
    unit_rows = {
        str(row.get("unit") or ""): row
        for row in (units.get("units") or [])
        if isinstance(row, Mapping)
    }
    if set(unit_rows) != set(config.systemd_units):
        violations.append("systemd_unit_set")
    elif any(
        row.get("load_state") != "loaded"
        or row.get("active_state") != "active"
        or int(row.get("main_pid") or 0) <= 0
        for row in unit_rows.values()
    ):
        violations.append("systemd_unit_state")
    journal = sample.get("journal") if isinstance(sample.get("journal"), Mapping) else {}
    if int(journal.get("priority_error_entries") or 0) != 0:
        violations.append("journal_priority_error")
    if journal.get("raw_messages_persisted") is not False:
        violations.append("journal_evidence_contract")

    if baseline is not None:
        if environment_sha != baseline.get("environment_content_sha256"):
            violations.append("environment_changed")
        if database.get("database_name_sha256") != baseline.get("database_name_sha256"):
            violations.append("database_target_changed")
        if apify_nonces != baseline.get("apify_boot_nonce_sha256_set"):
            violations.append("apify_restart")
        if redis_nonces != baseline.get("redis_worker_boot_nonce_sha256_set"):
            violations.append("redis_worker_restart")
        if redis_state.get("run_id_sha256") != baseline.get("redis_run_id_sha256"):
            violations.append("redis_server_restart")
        if _systemd_projection(sample) != baseline.get("systemd"):
            violations.append("systemd_restart_or_pid_change")
    return sorted(set(violations))


def aggregate(records: Sequence[Mapping[str, Any]], config: SoakConfig) -> dict[str, Any]:
    if not records:
        return {
            "status": "pending",
            "formal_gate_pass": False,
            "blockers": ["no_samples"],
            "natural_elapsed_seconds": 0.0,
            "sample_count": 0,
        }
    epochs = [_parse_utc(str(record.get("captured_at") or "")) for record in records]
    elapsed = max(0.0, epochs[-1] - epochs[0])
    gaps = [later - earlier for earlier, later in zip(epochs, epochs[1:])]
    max_gap = max(gaps, default=0.0)
    latencies = [float((record.get("sample") or {}).get("health", {}).get("latency_ms") or 0.0) for record in records]
    violation_rows = [
        {"seq": int(record.get("seq") or 0), "codes": list(record.get("violations") or [])}
        for record in records
        if record.get("violations")
    ]
    expected_samples = math.floor(FORMAL_DURATION_SECONDS / config.interval_seconds) + 1
    coverage_ratio = min(1.0, len(records) / expected_samples)
    max_allowed_gap = max(config.interval_seconds * 2.5, config.interval_seconds + 30.0)
    blockers: list[str] = []
    timestamp_integrity_failed = any(gap <= 0 for gap in gaps)
    if timestamp_integrity_failed:
        blockers.append("sample_timestamps_not_strictly_increasing")
    if elapsed < FORMAL_DURATION_SECONDS:
        blockers.append("natural_72h_not_elapsed")
    if coverage_ratio < 0.98:
        blockers.append("sample_coverage_below_98_percent")
    if max_gap > max_allowed_gap:
        blockers.append("sample_gap_exceeded")
    if violation_rows:
        blockers.append("sample_violations_present")
    p95 = _percentile(latencies, 95)
    if p95 is None or p95 > config.max_p95_latency_ms:
        blockers.append("health_p95_exceeded")
    formal_time_elapsed = elapsed >= FORMAL_DURATION_SECONDS
    status = (
        "passed"
        if not blockers
        else (
            "failed"
            if formal_time_elapsed and (violation_rows or timestamp_integrity_failed)
            else "pending"
        )
    )
    databases = [(record.get("sample") or {}).get("database", {}) for record in records]
    queues = [row.get("queue", {}) for row in databases]
    redis_rows = [(record.get("sample") or {}).get("redis", {}) for record in records]
    disks = [(record.get("sample") or {}).get("disk", {}) for record in records]
    journals = [(record.get("sample") or {}).get("journal", {}) for record in records]
    queue_ages = [float(row["oldest_queued_age_seconds"]) for row in queues if row.get("oldest_queued_age_seconds") is not None]
    return {
        "status": status,
        "formal_gate_pass": status == "passed",
        "blockers": sorted(set(blockers)),
        "started_at": records[0]["captured_at"],
        "finished_at": records[-1]["captured_at"],
        "natural_elapsed_seconds": round(elapsed, 3),
        "formal_required_seconds": FORMAL_DURATION_SECONDS,
        "diagnostic_target_seconds": config.diagnostic_target_seconds,
        "diagnostic_target_reached": elapsed >= config.diagnostic_target_seconds,
        "sample_count": len(records),
        "expected_formal_sample_count": expected_samples,
        "sample_coverage_ratio": round(coverage_ratio, 6),
        "max_sample_gap_seconds": round(max_gap, 3),
        "max_allowed_gap_seconds": round(max_allowed_gap, 3),
        "health_latency_ms": {
            "p50": _percentile(latencies, 50),
            "p95": p95,
            "p99": _percentile(latencies, 99),
            "max": round(max(latencies), 3),
        },
        "database": {
            "max_active_connections": max(int(row.get("connections", {}).get("active") or 0) for row in databases),
            "max_idle_connections": max(int(row.get("connections", {}).get("idle") or 0) for row in databases),
            "max_idle_in_transaction_over_30s": max(int(row.get("idle_in_transaction_over_30s") or 0) for row in databases),
            "max_lock_waits": max(int(row.get("lock_waits") or 0) for row in databases),
        },
        "queue": {
            "max_queued": max(int(row.get("queued") or 0) for row in queues),
            "max_oldest_queued_age_seconds": round(max(queue_ages), 3) if queue_ages else None,
            "max_failed_or_triage": max(int(row.get("failed_or_triage") or 0) for row in queues),
        },
        "redis": {
            "min_uptime_in_seconds": min(int(row.get("uptime_in_seconds") or 0) for row in redis_rows),
            "max_used_memory": max(int(row.get("used_memory") or 0) for row in redis_rows),
        },
        "disk": {"min_free_bytes": min(int(row.get("free_bytes") or 0) for row in disks)},
        "journal": {
            "entries": sum(int(row.get("entries") or 0) for row in journals),
            "priority_error_entries": sum(int(row.get("priority_error_entries") or 0) for row in journals),
            "raw_messages_persisted": False,
        },
        "violation_sample_count": len(violation_rows),
        "violation_codes": sorted({code for row in violation_rows for code in row["codes"]}),
        "would_fail_if_finalized": bool(violation_rows or timestamp_integrity_failed),
    }


class SoakOrchestrator:
    def __init__(
        self,
        *,
        store: StateStore,
        collector: SampleCollector,
        now: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.store = store
        self.collector = collector
        self.now = now
        self.sleep = sleep

    def take_sample(self) -> dict[str, Any]:
        records = self.store.records()
        cursor = str(records[-1].get("journal_cursor") or "") if records else None
        captured = self.now()
        try:
            sample, next_cursor = self.collector.collect(journal_cursor=cursor)
            base = _baseline(records[0]["sample"]) if records else None
            violations = evaluate_sample(sample, self.store.config, baseline=base)
            collection_error = None
        except CollectionError as exc:
            sample = {}
            next_cursor = cursor or ""
            violations = [f"collection:{exc.category}"]
            collection_error = exc.category
        return self.store.append(
            {
                "captured_at": _utc(captured),
                "sample": sample,
                "violations": violations,
                "collection_error": collection_error,
                "journal_cursor": next_cursor,
                "secrets_included": False,
            }
        )

    def run(self, *, max_samples: int = 0) -> dict[str, Any]:
        taken = 0
        while True:
            records = self.store.records()
            if records:
                elapsed = self.now() - _parse_utc(records[0]["captured_at"])
                if elapsed >= self.store.config.diagnostic_target_seconds:
                    captured_elapsed = _parse_utc(records[-1]["captured_at"]) - _parse_utc(
                        records[0]["captured_at"]
                    )
                    if captured_elapsed < self.store.config.diagnostic_target_seconds:
                        self.take_sample()
                    break
            self.take_sample()
            taken += 1
            if max_samples and taken >= max_samples:
                break
            records = self.store.records()
            elapsed = self.now() - _parse_utc(records[0]["captured_at"])
            if elapsed >= self.store.config.diagnostic_target_seconds:
                break
            self.sleep(min(self.store.config.interval_seconds, max(0.0, self.store.config.diagnostic_target_seconds - elapsed)))
        return aggregate(self.store.records(), self.store.config)


def build_receipt(records: Sequence[Mapping[str, Any]], config: SoakConfig) -> dict[str, Any]:
    summary = aggregate(records, config)
    chain_head = records[-1]["record_sha256"] if records else "0" * 64
    receipt: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "evidence_type": "vkpi_staging_72h_soak_receipt",
        "generated_at": _utc(time.time()),
        "binding": {
            "expected_head": config.expected_head,
            "expected_migration": config.expected_migration,
            "expected_apify_workers": config.expected_apify_workers,
            "expected_redis_workers": config.expected_redis_workers,
            "systemd_units": list(config.systemd_units),
        },
        "summary": summary,
        "sample_chain": {
            "sample_count": len(records),
            "chain_head_sha256": chain_head,
            "hash_chained": True,
            "raw_journal_messages_included": False,
        },
        "release_gate_eligible": summary.get("formal_gate_pass") is True,
        "signature": {"kind": "sha256_content_seal", "external_signature_present": False},
        "secrets_included": False,
    }
    receipt["receipt_sha256"] = _sha256(receipt)
    return receipt


def _config_from_args(args: argparse.Namespace) -> SoakConfig:
    units = tuple(args.unit or DEFAULT_UNITS)
    return SoakConfig(
        health_url=args.health_url,
        env_file=str(Path(args.env_file).resolve()),
        root=str(Path(args.root).resolve()),
        expected_head=args.expected_head,
        expected_migration=args.expected_migration,
        expected_apify_workers=args.expected_apify_workers,
        expected_redis_workers=args.expected_redis_workers,
        systemd_units=units,
        diagnostic_target_seconds=args.duration_seconds,
        interval_seconds=args.interval_seconds,
        max_sample_latency_ms=args.max_sample_latency_ms,
        max_p95_latency_ms=args.max_p95_latency_ms,
        max_queue_age_seconds=args.max_queue_age_seconds,
        max_lock_waits=args.max_lock_waits,
        min_disk_free_bytes=args.min_disk_free_bytes,
    )


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--health-url", default="http://127.0.0.1:8001/health")
    parser.add_argument("--env-file", default="/opt/viltrox-2.0/.env")
    parser.add_argument("--root", default="/opt/viltrox-2.0")
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--expected-migration", required=True)
    parser.add_argument("--expected-apify-workers", type=int, default=7)
    parser.add_argument("--expected-redis-workers", type=int, default=1)
    parser.add_argument("--unit", action="append", default=[])
    parser.add_argument("--duration-seconds", type=float, default=FORMAL_DURATION_SECONDS)
    parser.add_argument("--interval-seconds", type=float, default=60.0)
    parser.add_argument("--max-sample-latency-ms", type=float, default=5000.0)
    parser.add_argument("--max-p95-latency-ms", type=float, default=1000.0)
    parser.add_argument("--max-queue-age-seconds", type=float, default=21600.0)
    parser.add_argument("--max-lock-waits", type=int, default=0)
    parser.add_argument("--min-disk-free-bytes", type=int, default=5 * 1024**3)
    parser.add_argument("--allow-short-diagnostic", action="store_true")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Resumable, release-bound V-KPI staging soak")
    sub = result.add_subparsers(dest="command", required=True)
    sample = sub.add_parser("sample")
    _common(sample)
    run = sub.add_parser("run")
    _common(run)
    run.add_argument("--max-samples", type=int, default=0)
    status = sub.add_parser("status")
    _common(status)
    finalize = sub.add_parser("finalize")
    _common(finalize)
    finalize.add_argument("--receipt", required=True)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        config = _config_from_args(args)
        config.validate(allow_short_diagnostic=args.allow_short_diagnostic)
        with StateStore(Path(args.state_dir), config) as store:
            if args.command in {"sample", "run"}:
                collector = HostCollector(
                    CollectorConfig(
                        health_url=config.health_url,
                        env_file=Path(config.env_file),
                        root=Path(config.root),
                        systemd_units=config.systemd_units,
                    )
                )
                orchestrator = SoakOrchestrator(store=store, collector=collector)
                if args.command == "sample":
                    orchestrator.take_sample()
                    summary = aggregate(store.records(), config)
                else:
                    if args.max_samples < 0 or args.max_samples > 10_000:
                        raise SoakError("max samples is outside reviewed bounds")
                    summary = orchestrator.run(max_samples=args.max_samples)
            else:
                records = store.records()
                summary = aggregate(records, config)
                if args.command == "finalize":
                    _write_private_json(
                        Path(args.receipt),
                        build_receipt(records, config),
                        overwrite=False,
                    )
        sys.stdout.write(json.dumps(summary, sort_keys=True) + "\n")
        if summary.get("status") == "passed":
            return 0
        if summary.get("status") == "failed":
            return 1
        return 3
    except (SoakError, OSError, ValueError, KeyError) as exc:
        sys.stderr.write(f"staging soak failed: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
