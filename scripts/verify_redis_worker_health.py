#!/usr/bin/env python3
"""Fail-closed release gate for the dedicated Redis worker health block."""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from verify_runtime_health import strict_json_loads  # noqa: E402


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _utc(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def validate_redis_worker_health(
    payload: Any,
    *,
    expected_head: str,
    expected_count: int = 1,
    max_age_seconds: int = 180,
    expected_main_pid: int | None = None,
    min_ready_sequence: int = 3,
    worker_not_before: datetime | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    expected = str(expected_head or "").strip().lower()
    if not SHA_RE.fullmatch(expected):
        raise ValueError("expected HEAD must be a 40-character lowercase SHA")
    if isinstance(expected_count, bool) or int(expected_count) <= 0:
        raise ValueError("expected count must be positive")
    if isinstance(max_age_seconds, bool) or int(max_age_seconds) <= 0:
        raise ValueError("max age must be positive")
    if expected_main_pid is not None and (
        isinstance(expected_main_pid, bool) or int(expected_main_pid) <= 0
    ):
        raise ValueError("expected main PID must be positive")
    if isinstance(min_ready_sequence, bool) or int(min_ready_sequence) < 3:
        raise ValueError("minimum ready sequence must be at least 3")
    if not isinstance(payload, Mapping):
        return {"pass": False, "errors": ["health root must be an object"], "observed": {}}
    trust = payload.get("trust") if isinstance(payload.get("trust"), Mapping) else {}
    fleet = trust.get("redis_worker_fleet")
    if not isinstance(fleet, Mapping):
        return {"pass": False, "errors": ["redis worker fleet trust is unavailable"], "observed": {}}
    if fleet.get("online") is not True:
        errors.append("redis worker fleet is not release-ready")
    if fleet.get("online_count") != int(expected_count):
        errors.append("redis worker online count does not match expectation")
    if fleet.get("expected_count") != int(expected_count):
        errors.append("redis worker health expectation differs from release gate")
    if fleet.get("unique_names") is not True or fleet.get("unique_pids") is not True:
        errors.append("redis worker identities are not unique")
    if fleet.get("all_worker_sha_aligned") is not True:
        errors.append("redis worker fleet SHA alignment is not true")
    if fleet.get("all_redis_ready") is not True:
        errors.append("redis worker fleet has not proved Redis readiness")
    workers = fleet.get("workers")
    if not isinstance(workers, list):
        errors.append("redis worker instance list is unavailable")
        workers = []
    online = [item for item in workers if isinstance(item, Mapping) and item.get("online") is True]
    if len(online) != int(expected_count):
        errors.append("redis worker online instance list does not match expectation")
    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    for item in online:
        name = str(item.get("worker_name") or "")
        if not name.startswith("redis-worker-"):
            errors.append("redis worker name is outside its dedicated namespace")
        pid = item.get("pid")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            errors.append("redis worker PID is invalid")
        elif expected_main_pid is not None and pid != int(expected_main_pid):
            errors.append("redis worker PID does not match systemd MainPID")
        if str(item.get("worker_sha") or "").strip().lower() != expected:
            errors.append("redis worker SHA does not match release HEAD")
        if not SHA256_RE.fullmatch(str(item.get("boot_nonce_sha256") or "").strip().lower()):
            errors.append("redis worker boot nonce is invalid")
        heartbeat = _utc(item.get("heartbeat"))
        started = _utc(item.get("started_at"))
        readiness_at = _utc(item.get("redis_readiness_at"))
        ready_sequence = item.get("redis_ready_sequence")
        interval = item.get("redis_heartbeat_interval_seconds")
        if item.get("redis_ready") is not True:
            errors.append("redis worker Redis readiness is not true")
        if not str(item.get("redis_stream_key") or "").strip():
            errors.append("redis worker stream identity is missing")
        if not str(item.get("redis_group_name") or "").strip():
            errors.append("redis worker group identity is missing")
        if isinstance(item.get("redis_consumer_count"), bool) or int(item.get("redis_consumer_count") or 0) <= 0:
            errors.append("redis worker consumer registration count is invalid")
        if isinstance(ready_sequence, bool) or not isinstance(ready_sequence, int) or ready_sequence < int(min_ready_sequence):
            errors.append("redis worker has not sustained readiness for two heartbeat cycles")
        if isinstance(interval, bool) or not isinstance(interval, int) or interval < 5 or interval > 60:
            errors.append("redis worker heartbeat interval is invalid")
        if heartbeat is None:
            errors.append("redis worker heartbeat is invalid")
        else:
            age = (reference - heartbeat).total_seconds()
            if not math.isfinite(age) or age < -30 or age > int(max_age_seconds):
                errors.append("redis worker heartbeat is stale")
        if started is None:
            errors.append("redis worker started_at is invalid")
        elif worker_not_before is not None and started < worker_not_before.astimezone(timezone.utc):
            errors.append("redis worker predates this release restart")
        elif heartbeat is not None and heartbeat < started:
            errors.append("redis worker heartbeat predates worker start")
        elif heartbeat is not None and isinstance(interval, int):
            if (heartbeat - started).total_seconds() < 2 * interval:
                errors.append("redis worker boot has not survived two heartbeat cycles")
        if readiness_at is None:
            errors.append("redis worker readiness timestamp is invalid")
        else:
            readiness_age = (reference - readiness_at).total_seconds()
            if not math.isfinite(readiness_age) or readiness_age < -30 or readiness_age > int(max_age_seconds):
                errors.append("redis worker readiness proof is stale")
            if started is not None and readiness_at < started:
                errors.append("redis worker readiness predates worker start")

    # A Redis heartbeat must never inflate the Apify fleet count/gate.
    apify_fleet = trust.get("worker_fleet")
    if isinstance(apify_fleet, Mapping):
        apify_workers = apify_fleet.get("workers")
        if isinstance(apify_workers, list) and any(
            str(item.get("worker_name") or "").startswith("redis-worker-")
            for item in apify_workers
            if isinstance(item, Mapping)
        ):
            errors.append("redis worker identity polluted the Apify worker fleet")
    return {
        "pass": not errors,
        "errors": errors,
        "observed": {
            "online_count": fleet.get("online_count"),
            "expected_count": fleet.get("expected_count"),
            "worker_names": [str(item.get("worker_name") or "") for item in online],
            "main_pid": expected_main_pid,
            "min_ready_sequence": int(min_ready_sequence),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--expected-count", type=int, default=1)
    parser.add_argument("--max-age-seconds", type=int, default=180)
    parser.add_argument("--expected-main-pid", type=int)
    parser.add_argument("--min-ready-sequence", type=int, default=3)
    parser.add_argument("--worker-not-before")
    parser.add_argument("--now")
    args = parser.parse_args(argv)
    try:
        payload = strict_json_loads(sys.stdin.buffer.read(1024 * 1024 + 1))
        not_before = _utc(args.worker_not_before) if args.worker_not_before else None
        now = _utc(args.now) if args.now else None
        if args.worker_not_before and not_before is None:
            raise ValueError("worker-not-before must be timezone-aware")
        if args.now and now is None:
            raise ValueError("now must be timezone-aware")
        report = validate_redis_worker_health(
            payload,
            expected_head=args.expected_head,
            expected_count=args.expected_count,
            max_age_seconds=args.max_age_seconds,
            expected_main_pid=args.expected_main_pid,
            min_ready_sequence=args.min_ready_sequence,
            worker_not_before=not_before,
            now=now,
        )
        code = 0 if report["pass"] else 1
    except (OSError, ValueError) as exc:
        report = {"pass": False, "errors": [f"redis worker gate setup failed ({type(exc).__name__})"], "observed": {}}
        code = 2
    sys.stdout.write(json.dumps(report, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
