#!/usr/bin/env python3
"""Fail-closed verification for the local apify_jobs worker pool."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row


ROOT = Path(__file__).resolve().parents[1]


def _head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True, timeout=5
    ).stdout.strip().lower()


def _utc(value: Any) -> datetime | None:
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


def verify_pool(lanes: list[str], *, max_age_seconds: int = 30) -> dict[str, Any]:
    errors: list[str] = []
    host = socket.gethostname().split(".", 1)[0]
    expected_head = _head()
    expected: list[tuple[str, str, Path]] = []
    for lane in lanes:
        if lane != "interactive" and not (
            lane.startswith("bulk") and lane[4:].isdigit() and int(lane[4:]) >= 1
        ):
            raise ValueError(f"invalid lane: {lane}")
        expected.append(
            (lane, f"apify-worker-{lane}-{host}", ROOT / "runtime" / f"worker-{lane}.pid")
        )

    database_url = str(os.getenv("DATABASE_URL") or "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    names = [item[1] for item in expected]
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        rows = conn.execute(
            """
            SELECT worker_name, pid, last_heartbeat_at, worker_git_sha,
                   boot_nonce_sha256, started_at
            FROM vkpi_worker_heartbeat
            WHERE worker_name = ANY(%s)
            ORDER BY worker_name
            """,
            (names,),
        ).fetchall()
        running = conn.execute(
            "SELECT COUNT(*) AS n FROM apify_jobs WHERE status='running'"
        ).fetchone()

    by_name = {str(row["worker_name"]): dict(row) for row in rows}
    observed: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    seen_pids: set[int] = set()
    seen_nonces: set[str] = set()
    for lane, name, pidfile in expected:
        try:
            pid = int(pidfile.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pid = 0
            errors.append(f"{lane}: missing or invalid pidfile")
        row = by_name.get(name)
        heartbeat = _utc((row or {}).get("last_heartbeat_at"))
        age = (now - heartbeat).total_seconds() if heartbeat else None
        row_pid = int((row or {}).get("pid") or 0)
        sha = str((row or {}).get("worker_git_sha") or "").strip().lower()
        nonce = str((row or {}).get("boot_nonce_sha256") or "").strip().lower()
        started = _utc((row or {}).get("started_at"))
        if row is None:
            errors.append(f"{lane}: heartbeat row missing")
        if pid <= 1 or row_pid != pid:
            errors.append(f"{lane}: heartbeat PID does not match pidfile")
        if age is None or age < -30 or age > max(1, int(max_age_seconds)):
            errors.append(f"{lane}: heartbeat is stale")
        if sha != expected_head:
            errors.append(f"{lane}: worker SHA does not match HEAD")
        if len(nonce) != 64 or any(char not in "0123456789abcdef" for char in nonce):
            errors.append(f"{lane}: boot nonce binding is invalid")
        if heartbeat is not None and started is not None and heartbeat < started:
            errors.append(f"{lane}: heartbeat predates start")
        if row_pid in seen_pids:
            errors.append(f"{lane}: duplicate PID")
        if nonce and nonce in seen_nonces:
            errors.append(f"{lane}: duplicate boot nonce")
        seen_pids.add(row_pid)
        seen_nonces.add(nonce)
        observed.append(
            {
                "lane": lane,
                "worker_name": name,
                "pid": row_pid or None,
                "sha": sha[:8] if sha else None,
                "heartbeat_age_seconds": round(age, 1) if age is not None else None,
                "boot_nonce": hashlib.sha256(nonce.encode("utf-8")).hexdigest()[:12] if nonce else None,
            }
        )
    if len(rows) != len(expected):
        errors.append("heartbeat row count does not match expected pool size")
    return {
        "pass": not errors,
        "expected_count": len(expected),
        "online_count": len(rows),
        "running_jobs": int((running or {}).get("n") or 0),
        "errors": errors,
        "workers": observed,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lanes", nargs="+", default=["interactive", "bulk1", "bulk2"])
    parser.add_argument("--max-age-seconds", type=int, default=30)
    args = parser.parse_args()
    try:
        report = verify_pool(args.lanes, max_age_seconds=args.max_age_seconds)
        code = 0 if report["pass"] else 1
    except Exception as exc:
        report = {"pass": False, "errors": [f"verification setup failed: {type(exc).__name__}"]}
        code = 2
    sys.stdout.write(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
