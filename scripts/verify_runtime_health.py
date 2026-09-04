#!/usr/bin/env python3
"""Validate the bounded /health trust contract without performing network I/O.

The caller supplies the response body on stdin.  This keeps the validator
deterministic and testable while ``verify.sh`` remains responsible for the
loopback-only HTTP request.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from stdout_utils import out as stdout_out  # noqa: E402


MAX_INPUT_BYTES = 1024 * 1024
DEFAULT_MAX_WORKER_AGE_SECONDS = 180
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class DuplicateKeyError(ValueError):
    """Raised when an object contains a repeated JSON key."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise DuplicateKeyError(f"duplicate JSON key: {key}")
        output[key] = value
    return output


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def strict_json_loads(raw: bytes) -> Any:
    if not raw:
        raise ValueError("health response is empty")
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("health response exceeds the 1 MiB limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("health response is not UTF-8") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_nonfinite,
        )
    except (json.JSONDecodeError, DuplicateKeyError, ValueError) as exc:
        raise ValueError("health response is not strict JSON") from exc


def _parse_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        check=True,
        text=True,
        timeout=5,
    ).stdout.strip().lower()


def _latest_migration() -> str:
    names = sorted(
        path.name
        for path in (ROOT / "migrations").glob("*.sql")
        if not path.name.endswith("_down.sql")
    )
    if not names:
        raise RuntimeError("no migration manifest found")
    return names[-1]


def validate_health(
    payload: Any,
    *,
    expected_head: str,
    expected_migration: str | None = None,
    require_migration_set_complete: bool = False,
    require_worker: bool = False,
    max_worker_age_seconds: int = DEFAULT_MAX_WORKER_AGE_SECONDS,
    expected_worker_boot_nonce_sha256: str | None = None,
    worker_not_before: datetime | None = None,
    expected_worker_count: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    observed: dict[str, Any] = {}
    expected = str(expected_head or "").strip().lower()
    if not SHA_RE.fullmatch(expected):
        raise ValueError("expected HEAD must be a 40-character lowercase SHA")
    if not isinstance(payload, Mapping):
        return {"pass": False, "errors": ["health root must be an object"], "observed": {}}

    build = payload.get("build") if isinstance(payload.get("build"), Mapping) else {}
    trust = payload.get("trust") if isinstance(payload.get("trust"), Mapping) else {}
    server_sha = str(build.get("git_sha") or "").strip().lower()
    trust_server = str(trust.get("server_git_sha") or "").strip().lower()
    trust_client = str(trust.get("client_git_sha") or "").strip().lower()
    worker_sha = str(trust.get("worker_sha") or "").strip().lower()
    worker_sha_source = str(trust.get("worker_sha_source") or "").strip()
    worker_heartbeat_source = str(trust.get("worker_heartbeat_source") or "").strip()
    worker_boot_nonce_sha256 = str(trust.get("worker_boot_nonce_sha256") or "").strip().lower()
    migration = str(trust.get("db_migration_max") or "").strip()
    migration_source = str(trust.get("db_migration_source") or "").strip()

    if payload.get("status") != "ok":
        errors.append("health status is not ok")
    if server_sha != expected:
        errors.append("server build SHA does not match local HEAD")
    if build.get("client_matches_server") is not True:
        errors.append("frontend build does not match server")
    if trust.get("sha_aligned") is not True:
        errors.append("health trust SHA alignment is not true")
    if trust_server != expected:
        errors.append("trusted server SHA does not match local HEAD")
    if trust_client != expected:
        errors.append("trusted client SHA does not match local HEAD")

    if expected_migration is not None:
        expected_migration = str(expected_migration).strip()
        if not expected_migration:
            raise ValueError("expected migration must not be empty")
        if migration != expected_migration:
            errors.append("applied migration max does not match local manifest")
        if migration_source != "schema_migrations":
            errors.append("migration truth is not sourced from schema_migrations")
        db_startup = trust.get("db_startup")
        if not isinstance(db_startup, Mapping):
            errors.append("database startup trust is unavailable")
        else:
            if str(db_startup.get("backend") or "") != "postgres":
                errors.append("database startup backend is not postgres")
            if str(db_startup.get("state") or "") != "completed":
                errors.append("database startup did not complete")
            if str(db_startup.get("schema_migrations") or "") != "completed":
                errors.append("database migration startup stage did not complete")

    # Complete-set proof is an independent contract.  Keeping it outside the
    # expected-max branch prevents callers from accidentally turning the flag
    # into a no-op by omitting ``expected_migration``.
    if require_migration_set_complete:
        if trust.get("db_migration_complete") is not True:
            errors.append("applied migration set is incomplete")
        if trust.get("db_migration_exact") is not True:
            errors.append("applied migration set is not exact")
        for field in ("db_migration_missing_count", "db_migration_unexpected_count"):
            value = trust.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value != 0:
                errors.append(f"{field} is not zero")

    worker_age: float | None = None
    if require_worker:
        if worker_sha != expected:
            errors.append("worker SHA does not match local HEAD")
        if trust.get("worker_online") is not True:
            errors.append("worker is not online")
        if worker_sha_source != "db_heartbeat":
            errors.append("worker SHA is not sourced from its database heartbeat")
        if worker_heartbeat_source != "db_heartbeat":
            errors.append("worker liveness is not sourced from its database heartbeat")
        worker_pid = trust.get("worker_pid")
        if isinstance(worker_pid, bool) or not isinstance(worker_pid, int) or worker_pid <= 0:
            errors.append("worker PID is missing or invalid")
        if not SHA256_RE.fullmatch(worker_boot_nonce_sha256):
            errors.append("worker boot nonce binding is missing or invalid")
        if expected_worker_boot_nonce_sha256 is not None:
            expected_boot = str(expected_worker_boot_nonce_sha256).strip().lower()
            if not SHA256_RE.fullmatch(expected_boot):
                raise ValueError("expected worker boot nonce must be a lowercase SHA-256")
            if worker_boot_nonce_sha256 != expected_boot:
                errors.append("worker boot nonce does not match this deployment")
        heartbeat = _parse_utc(trust.get("worker_heartbeat"))
        worker_started_at = _parse_utc(trust.get("worker_started_at"))
        reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if heartbeat is None:
            errors.append("worker heartbeat is missing or invalid")
        else:
            worker_age = (reference - heartbeat).total_seconds()
            if not math.isfinite(worker_age) or worker_age < -30:
                errors.append("worker heartbeat is implausibly in the future")
            elif worker_age > max(1, int(max_worker_age_seconds)):
                errors.append("worker heartbeat is stale")
        if worker_started_at is None:
            errors.append("worker started_at is missing or invalid")
        else:
            if worker_not_before is not None:
                threshold = worker_not_before.astimezone(timezone.utc)
                if worker_started_at < threshold:
                    errors.append("worker started before this deployment restart")
            if heartbeat is not None and heartbeat < worker_started_at:
                errors.append("worker heartbeat predates worker start")

        scheduler = trust.get("scheduler_status")
        if not isinstance(scheduler, Mapping):
            errors.append("scheduler trust is unavailable")
        else:
            total, enabled = scheduler.get("total"), scheduler.get("enabled")
            invalid_counts = (
                isinstance(total, bool)
                or isinstance(enabled, bool)
                or not isinstance(total, int)
                or not isinstance(enabled, int)
                or total <= 0
                or enabled <= 0
                or enabled > total
            )
            if invalid_counts:
                errors.append("scheduler registration counts are not trustworthy")

        if expected_worker_count is not None:
            if isinstance(expected_worker_count, bool) or int(expected_worker_count) <= 0:
                raise ValueError("expected worker count must be a positive integer")
            fleet = trust.get("worker_fleet")
            if not isinstance(fleet, Mapping):
                errors.append("worker fleet trust is unavailable")
            else:
                online_count = fleet.get("online_count")
                if (
                    isinstance(online_count, bool)
                    or not isinstance(online_count, int)
                    or online_count != int(expected_worker_count)
                ):
                    errors.append("worker fleet online count does not match expectation")
                if fleet.get("unique_names") is not True:
                    errors.append("worker fleet names are not unique")
                if fleet.get("unique_pids") is not True:
                    errors.append("worker fleet PIDs are not unique")
                if fleet.get("all_worker_sha_aligned") is not True:
                    errors.append("worker fleet SHA alignment is not true")
                lane_coverage = fleet.get("lane_coverage")
                if int(expected_worker_count) > 1 and not (
                    isinstance(lane_coverage, list)
                    and {"interactive", "batch"}.issubset({str(item) for item in lane_coverage})
                ):
                    errors.append("worker fleet does not cover interactive and batch lanes")
                workers = fleet.get("workers")
                if not isinstance(workers, list):
                    errors.append("worker fleet instance list is unavailable")
                else:
                    online_instances = [
                        item for item in workers if isinstance(item, Mapping) and item.get("online") is True
                    ]
                    if len(online_instances) != int(expected_worker_count):
                        errors.append("worker fleet instance list does not match expectation")
                    instance_names: list[str] = []
                    instance_pids: list[int] = []
                    instance_nonces: list[str] = []
                    for item in online_instances:
                        name = str(item.get("worker_name") or "").strip()
                        pid = item.get("pid")
                        sha = str(item.get("worker_sha") or "").strip().lower()
                        nonce = str(item.get("boot_nonce_sha256") or "").strip().lower()
                        heartbeat_at = _parse_utc(item.get("heartbeat"))
                        started = _parse_utc(item.get("started_at"))
                        if not name:
                            errors.append("worker fleet instance name is missing")
                        else:
                            instance_names.append(name)
                        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
                            errors.append("worker fleet instance PID is invalid")
                        else:
                            instance_pids.append(pid)
                        if sha != expected:
                            errors.append("worker fleet instance SHA does not match local HEAD")
                        if not SHA256_RE.fullmatch(nonce):
                            errors.append("worker fleet instance boot nonce is invalid")
                        else:
                            instance_nonces.append(nonce)
                        if heartbeat_at is None:
                            errors.append("worker fleet instance heartbeat is invalid")
                        else:
                            instance_age = (reference - heartbeat_at).total_seconds()
                            if instance_age < -30 or instance_age > max(1, int(max_worker_age_seconds)):
                                errors.append("worker fleet instance heartbeat is stale")
                        if started is None:
                            errors.append("worker fleet instance started_at is invalid")
                        elif worker_not_before is not None and started < worker_not_before.astimezone(
                            timezone.utc
                        ):
                            errors.append("worker fleet instance started before this deployment restart")
                        elif heartbeat_at is not None and heartbeat_at < started:
                            errors.append("worker fleet instance heartbeat predates start")
                    if len(instance_names) != len(set(instance_names)):
                        errors.append("worker fleet instance names are duplicated")
                    if len(instance_pids) != len(set(instance_pids)):
                        errors.append("worker fleet instance PIDs are duplicated")
                    if len(instance_nonces) != len(set(instance_nonces)):
                        errors.append("worker fleet instance boot nonces are duplicated")

    observed.update(
        {
            "server_git_sha": server_sha[:8] if server_sha else None,
            "client_git_sha": trust_client[:8] if trust_client else None,
            "worker_git_sha": worker_sha[:8] if worker_sha else None,
            "worker_sha_source": worker_sha_source or None,
            "worker_heartbeat_source": worker_heartbeat_source or None,
            "worker_pid": trust.get("worker_pid"),
            "worker_started_at": str(trust.get("worker_started_at") or "") or None,
            "worker_boot_nonce_sha256": (
                worker_boot_nonce_sha256[:12] if worker_boot_nonce_sha256 else None
            ),
            "migration": migration or None,
            "migration_source": migration_source or None,
            "migration_set_complete": trust.get("db_migration_complete"),
            "migration_set_exact": trust.get("db_migration_exact"),
            "migration_missing_count": trust.get("db_migration_missing_count"),
            "migration_unexpected_count": trust.get("db_migration_unexpected_count"),
            "worker_heartbeat_age_seconds": (
                round(worker_age, 1) if worker_age is not None and math.isfinite(worker_age) else None
            ),
            "worker_fleet_online_count": (
                (trust.get("worker_fleet") or {}).get("online_count")
                if isinstance(trust.get("worker_fleet"), Mapping)
                else None
            ),
        }
    )
    return {"pass": not errors, "errors": errors, "observed": observed}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate one V-KPI /health JSON response from stdin.")
    parser.add_argument("--expected-head", default="")
    parser.add_argument("--expected-migration")
    parser.add_argument("--require-migration-set-complete", action="store_true")
    parser.add_argument("--require-worker", action="store_true")
    parser.add_argument("--max-worker-age-seconds", type=int)
    parser.add_argument("--expected-worker-boot-nonce-sha256")
    parser.add_argument("--worker-not-before")
    parser.add_argument("--expected-worker-count", type=int)
    parser.add_argument(
        "--strict-deploy",
        action="store_true",
        help="Require every deployment-acceptance expectation to be supplied explicitly.",
    )
    parser.add_argument("--now", help="Optional timezone-aware ISO timestamp for deterministic verification.")
    return parser.parse_args(argv)


def _resolve_cli_contract(
    args: argparse.Namespace,
) -> tuple[str, str | None, bool, int, str | None, datetime | None]:
    """Resolve CLI expectations while making deployment acceptance fail closed."""
    if args.strict_deploy:
        missing: list[str] = []
        if not str(args.expected_head or "").strip():
            missing.append("--expected-head")
        if not str(args.expected_migration or "").strip():
            missing.append("--expected-migration")
        if args.require_worker is not True:
            missing.append("--require-worker")
        if args.max_worker_age_seconds is None:
            missing.append("--max-worker-age-seconds")
        if (
            args.expected_worker_count is None
            and not str(args.expected_worker_boot_nonce_sha256 or "").strip()
        ):
            missing.append("--expected-worker-boot-nonce-sha256")
        if args.expected_worker_count is not None and int(args.expected_worker_count) <= 0:
            missing.append("--expected-worker-count=<positive integer>")
        if not str(args.worker_not_before or "").strip():
            missing.append("--worker-not-before")
        if missing:
            raise ValueError(f"strict deploy validation requires: {', '.join(missing)}")

    max_worker_age_seconds = (
        DEFAULT_MAX_WORKER_AGE_SECONDS
        if args.max_worker_age_seconds is None
        else args.max_worker_age_seconds
    )
    if isinstance(max_worker_age_seconds, bool) or max_worker_age_seconds <= 0:
        raise ValueError("max worker age must be a positive integer")

    expected_head = str(args.expected_head or "").strip() or _git_head()
    expected_migration = args.expected_migration
    require_worker = bool(args.require_worker)
    expected_worker_boot_nonce_sha256 = (
        str(args.expected_worker_boot_nonce_sha256 or "").strip().lower() or None
    )
    if expected_worker_boot_nonce_sha256 is not None and not SHA256_RE.fullmatch(
        expected_worker_boot_nonce_sha256
    ):
        raise ValueError("expected worker boot nonce must be a lowercase SHA-256")
    worker_not_before = _parse_utc(args.worker_not_before) if args.worker_not_before else None
    if args.worker_not_before and worker_not_before is None:
        raise ValueError("--worker-not-before must be a timezone-aware ISO timestamp")
    return (
        expected_head,
        expected_migration,
        require_worker,
        max_worker_age_seconds,
        expected_worker_boot_nonce_sha256,
        worker_not_before,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        (
            expected_head,
            expected_migration,
            require_worker,
            max_worker_age_seconds,
            expected_worker_boot_nonce_sha256,
            worker_not_before,
        ) = (
            _resolve_cli_contract(args)
        )
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        payload = strict_json_loads(raw)
        now = _parse_utc(args.now) if args.now else None
        if args.now and now is None:
            raise ValueError("--now must be a timezone-aware ISO timestamp")
        report = validate_health(
            payload,
            expected_head=expected_head,
            expected_migration=expected_migration,
            require_migration_set_complete=(
                args.require_migration_set_complete or args.strict_deploy
            ),
            require_worker=require_worker,
            max_worker_age_seconds=max_worker_age_seconds,
            expected_worker_boot_nonce_sha256=expected_worker_boot_nonce_sha256,
            worker_not_before=worker_not_before,
            expected_worker_count=args.expected_worker_count,
            now=now,
        )
        exit_code = 0 if report["pass"] else 1
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
        report = {
            "pass": False,
            "errors": [f"runtime health validation setup failed ({exc.__class__.__name__})"],
            "observed": {},
        }
        exit_code = 2
    stdout_out(json.dumps(report, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    summary = "PASS" if report["pass"] else "FAIL"
    detail = "; ".join(str(item) for item in report["errors"][:4])
    stdout_out(f"[verify-runtime] {summary}" + (f": {detail}" if detail else ""), file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
