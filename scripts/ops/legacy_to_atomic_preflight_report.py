"""Local projection and reporting helpers for the read-only cloud preflight.

The remote collector intentionally remains self-contained in
``legacy_to_atomic_preflight.py`` because that source is streamed to the target
over SSH stdin.  This module is loaded only by the local CLI after collection;
it has no I/O beyond reading the local migration manifest.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence


MIGRATION_RE = re.compile(r"^[0-9]{3}_[A-Za-z0-9_.-]+\.sql$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.@-]+$")
SAFE_PATH_RE = re.compile(r"^/[A-Za-z0-9_./@-]+$")
DOMAIN_RE = re.compile(
    r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$"
)
DATABASE_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
SAFE_STATE_RE = re.compile(r"^[A-Za-z0-9_.@:-]+$")
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SECRET_KEY_RE = re.compile(
    r"(?i)(?:^|_)(?:authorization|password|passwd|secret|token|api_key|access_key|"
    r"secret_key|database_url|redis_url|cookie|credential)$"
)
URL_CREDENTIAL_RE = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/@:]+:[^\s/@]+@")
TOKEN_VALUE_RE = re.compile(
    r"(?i)(?:bearer\s+[A-Za-z0-9._~-]{8,}|sk-[A-Za-z0-9_-]{8,}|"
    r"AKIA[0-9A-Z]{16})"
)


def _safe_identifier(value: object) -> str | None:
    text = str(value or "").strip()
    return text if IDENTIFIER_RE.fullmatch(text) else None


def _safe_state(value: object) -> str | None:
    text = str(value or "").strip()
    return text if SAFE_STATE_RE.fullmatch(text) else None


def _safe_path(value: object) -> str | None:
    text = str(value or "").strip()
    return (
        text
        if SAFE_PATH_RE.fullmatch(text) and ".." not in PurePosixPath(text).parts
        else None
    )


def _safe_sha40(value: object) -> str | None:
    text = str(value or "").strip().lower()
    return text if SHA40_RE.fullmatch(text) else None


def _safe_sha256(value: object) -> str | None:
    text = str(value or "").strip().lower()
    return text if SHA256_RE.fullmatch(text) else None


def _safe_migration(value: object) -> str | None:
    text = str(value or "").strip()
    return text if MIGRATION_RE.fullmatch(text) else None


def _bool(value: object) -> bool:
    return value is True


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _float_or_none(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        return float(value)
    return None


def project_snapshot(
    snapshot: Mapping[str, Any], observed_units: Sequence[str]
) -> dict[str, Any]:
    """Copy only the exact public schema; unknown or secret-bearing fields vanish."""

    release = snapshot.get("release_layout") if isinstance(snapshot.get("release_layout"), dict) else {}
    environment = snapshot.get("environment") if isinstance(snapshot.get("environment"), dict) else {}
    database = snapshot.get("database") if isinstance(snapshot.get("database"), dict) else {}
    redis_state = snapshot.get("redis") if isinstance(snapshot.get("redis"), dict) else {}
    nginx = snapshot.get("nginx") if isinstance(snapshot.get("nginx"), dict) else {}
    health = snapshot.get("health") if isinstance(snapshot.get("health"), dict) else {}
    backup = snapshot.get("backup") if isinstance(snapshot.get("backup"), dict) else {}

    def pointer(name: str) -> dict[str, Any]:
        raw = release.get(name) if isinstance(release.get(name), dict) else {}
        return {
            "kind": _safe_state(raw.get("kind")),
            "safe_target": _bool(raw.get("safe_target")),
            "target_name": _safe_identifier(raw.get("target_name")),
        }

    raw_markers = release.get("flat_markers") if isinstance(release.get("flat_markers"), dict) else {}
    units: list[dict[str, Any]] = []
    raw_units = snapshot.get("systemd_units") if isinstance(snapshot.get("systemd_units"), list) else []
    allowed_units = set(observed_units)
    for raw in raw_units:
        if not isinstance(raw, dict) or raw.get("name") not in allowed_units:
            continue
        units.append(
            {
                "name": raw["name"],
                "observable": _bool(raw.get("observable")),
                "load_state": _safe_state(raw.get("load_state")),
                "active_state": _safe_state(raw.get("active_state")),
                "unit_file_state": _safe_state(raw.get("unit_file_state")),
                "fragment_path": _safe_path(raw.get("fragment_path")),
                "fragment_sha256": _safe_sha256(raw.get("fragment_sha256")),
                "fragment_readable": _bool(raw.get("fragment_readable")),
                "user": _safe_identifier(raw.get("user")),
                "group": _safe_identifier(raw.get("group")),
                "working_directory": _safe_path(raw.get("working_directory")),
                "app_role": _safe_state(raw.get("app_role")),
                "environment_mode": _safe_state(raw.get("environment_mode")),
                "claim_lane": _safe_state(raw.get("claim_lane")),
                "heartbeat_name": _safe_state(raw.get("heartbeat_name")),
            }
        )
    domains = sorted(
        {
            str(value).lower()
            for value in (nginx.get("domains") if isinstance(nginx.get("domains"), list) else [])
            if DOMAIN_RE.fullmatch(str(value).lower())
        }
    )
    database_name = str(database.get("database_name") or "")
    return {
        "collected_at": _safe_state(snapshot.get("collected_at")),
        "release_layout": {
            "root_exists": _bool(release.get("root_exists")),
            "state": _safe_state(release.get("state")),
            "flat_markers": {
                key: _bool(raw_markers.get(key))
                for key in ("backend", "frontend_dist", "environment", "build_git_sha")
            },
            "releases_directory": _bool(release.get("releases_directory")),
            "current": pointer("current"),
            "previous": pointer("previous"),
            "root_build_git_sha": _safe_sha40(release.get("root_build_git_sha")),
            "atomic_helper_present": _bool(release.get("atomic_helper_present")),
        },
        "environment": {
            "regular_file": _bool(environment.get("regular_file")),
            "app_user_nonroot": _bool(environment.get("app_user_nonroot")),
            "owner": _safe_identifier(environment.get("owner")),
            "group": _safe_identifier(environment.get("group")),
            "mode": (
                str(environment.get("mode"))
                if re.fullmatch(r"[0-7]{4}", str(environment.get("mode") or ""))
                else None
            ),
            "app_readable": _bool(environment.get("app_readable")),
            "app_writable": _bool(environment.get("app_writable")),
            "database_configured": _bool(environment.get("database_configured")),
            "redis_configured": _bool(environment.get("redis_configured")),
            "parse_ok": _bool(environment.get("parse_ok")),
        },
        "systemd_units": sorted(units, key=lambda item: observed_units.index(item["name"])),
        "database": {
            "reachable": _bool(database.get("reachable")),
            "read_only_session": _bool(database.get("read_only_session")),
            "database_name": database_name if DATABASE_NAME_RE.fullmatch(database_name) else None,
            "migration_max": _safe_migration(database.get("migration_max")),
            "migration_count": _int_or_none(database.get("migration_count")),
            "error_code": _safe_state(database.get("error_code")),
        },
        "redis": {
            "reachable": _bool(redis_state.get("reachable")),
            "aof_enabled": (
                redis_state.get("aof_enabled")
                if isinstance(redis_state.get("aof_enabled"), bool)
                else None
            ),
            "rdb_last_bgsave_status": _safe_state(redis_state.get("rdb_last_bgsave_status")),
            "aof_last_write_status": _safe_state(redis_state.get("aof_last_write_status")),
            "error_code": _safe_state(redis_state.get("error_code")),
        },
        "nginx": {
            "config_readable": _bool(nginx.get("config_readable")),
            "readable_file_count": _int_or_none(nginx.get("readable_file_count")),
            "domains": domains,
        },
        "health": {
            "reachable": _bool(health.get("reachable")),
            "status": _safe_state(health.get("status")),
            "server_git_sha": _safe_sha40(health.get("server_git_sha")),
            "client_git_sha": _safe_sha40(health.get("client_git_sha")),
            "sha_aligned": (
                health.get("sha_aligned")
                if isinstance(health.get("sha_aligned"), bool)
                else None
            ),
            "db_migration_max": _safe_migration(health.get("db_migration_max")),
            "worker_online": (
                health.get("worker_online")
                if isinstance(health.get("worker_online"), bool)
                else None
            ),
            "worker_fleet_present": _bool(health.get("worker_fleet_present")),
            "error_code": _safe_state(health.get("error_code")),
        },
        "backup": {
            "directory_readable": _bool(backup.get("directory_readable")),
            "candidate_count": _int_or_none(backup.get("candidate_count")),
            "latest_name": _safe_identifier(backup.get("latest_name")),
            "latest_age_hours": _float_or_none(backup.get("latest_age_hours")),
            "fresh": _bool(backup.get("fresh")),
            "dump_size_bytes": _int_or_none(backup.get("dump_size_bytes")),
            "checksum_present": _bool(backup.get("checksum_present")),
            "checksum_verified": _bool(backup.get("checksum_verified")),
            "catalog_verified": _bool(backup.get("catalog_verified")),
            "runtime_state_present": _bool(backup.get("runtime_state_present")),
            "media_manifest_present": _bool(backup.get("media_manifest_present")),
            "encrypted_environment_snapshot_present": _bool(
                backup.get("encrypted_environment_snapshot_present")
            ),
            "off_host_receipt_present": _bool(backup.get("off_host_receipt_present")),
        },
    }


def _migration_number(value: str | None) -> int | None:
    if not value or not MIGRATION_RE.fullmatch(value):
        return None
    return int(value.split("_", 1)[0])


def evaluate(
    observed: Mapping[str, Any],
    args: argparse.Namespace,
    *,
    core_runtime_units: Sequence[str],
    interactive_unit: str,
    bulk_units: Sequence[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def add(
        check_id: str, passed: bool, observed_value: object, *, blocking: bool = True
    ) -> None:
        checks.append(
            {
                "id": check_id,
                "pass": bool(passed),
                "blocking": blocking,
                "observed": observed_value,
            }
        )

    release = observed["release_layout"]
    environment = observed["environment"]
    database = observed["database"]
    redis_state = observed["redis"]
    nginx = observed["nginx"]
    health = observed["health"]
    backup = observed["backup"]
    units = {unit["name"]: unit for unit in observed["systemd_units"]}

    add(
        "release.root_recognized",
        release["root_exists"] and release["state"] in {"legacy_flat", "atomic"},
        release["state"],
    )
    current = release["current"]
    pointer_safe = release["state"] == "legacy_flat" and current["kind"] == "absent"
    pointer_safe = pointer_safe or (
        release["state"] == "atomic"
        and release["releases_directory"]
        and current["kind"] == "symlink"
        and current["safe_target"]
        and current["target_name"] is not None
    )
    add("release.current_pointer_safe", pointer_safe, current["kind"])
    previous = release["previous"]
    previous_safe = previous["kind"] == "absent" or (
        release["state"] == "atomic"
        and release["releases_directory"]
        and previous["kind"] == "symlink"
        and previous["safe_target"]
        and previous["target_name"] is not None
    )
    add("release.previous_pointer_safe", previous_safe, previous["kind"])
    add(
        "release.root_build_sha_present",
        release["root_build_git_sha"] is not None,
        release["root_build_git_sha"] is not None,
    )
    add(
        "release.atomic_helper_present",
        release["atomic_helper_present"],
        release["atomic_helper_present"],
    )

    core = [units.get(name) for name in core_runtime_units]
    add(
        "systemd.core_units_observable",
        all(unit and unit["observable"] and unit["load_state"] == "loaded" for unit in core),
        sum(bool(unit and unit["load_state"] == "loaded") for unit in core),
    )
    add(
        "systemd.core_units_active",
        all(unit and unit["active_state"] == "active" for unit in core),
        sum(bool(unit and unit["active_state"] == "active") for unit in core),
    )
    add(
        "systemd.unit_fragments_captured",
        all(unit and unit["fragment_readable"] and unit["fragment_sha256"] for unit in core),
        sum(bool(unit and unit["fragment_readable"] and unit["fragment_sha256"]) for unit in core),
    )
    add(
        "systemd.nonroot_app_identity",
        all(unit and unit["user"] == args.app_user for unit in core),
        sorted({str(unit["user"]) for unit in core if unit}),
    )
    interactive_lane = units.get(interactive_unit, {}).get("claim_lane")
    bulk_lanes = [units.get(name, {}).get("claim_lane") for name in bulk_units]
    add(
        "workers.lane_contract",
        interactive_lane == "interactive" and bulk_lanes == ["batch"] * len(bulk_units),
        {"interactive": interactive_lane, "bulk": bulk_lanes},
    )
    sync_timer = units.get("vkpi-sync-daily.timer")
    add(
        "rollback.sync_timer_state_captured",
        bool(sync_timer and sync_timer["observable"] and sync_timer["load_state"] == "loaded"),
        sync_timer["unit_file_state"] if sync_timer else None,
    )

    add(
        "environment.regular_parseable",
        environment["regular_file"] and environment["parse_ok"],
        environment["mode"],
    )
    add(
        "environment.app_user_nonroot",
        environment["app_user_nonroot"],
        environment["app_user_nonroot"],
    )
    add(
        "environment.app_readonly",
        environment["app_readable"] and not environment["app_writable"],
        {"readable": environment["app_readable"], "writable": environment["app_writable"]},
    )
    add(
        "environment.hardened_permissions",
        environment["owner"] == "root"
        and environment["group"] == args.app_user
        and environment["mode"] in {"0440", "0640"},
        {
            "owner": environment["owner"],
            "group": environment["group"],
            "mode": environment["mode"],
        },
    )
    add(
        "environment.runtime_endpoints_configured",
        environment["database_configured"] and environment["redis_configured"],
        {
            "database": environment["database_configured"],
            "redis": environment["redis_configured"],
        },
    )

    expected_number = _migration_number(args.expected_migration)
    actual_number = _migration_number(database["migration_max"])
    add(
        "database.readonly_query",
        database["reachable"] and database["read_only_session"],
        database["reachable"],
    )
    reviewed_database = database["database_name"] == "viltrox2_test" or bool(
        re.fullmatch(
            r"viltrox2_test_release_[0-9a-f]{20}", str(database["database_name"] or "")
        )
    )
    add("database.reviewed_source", reviewed_database, database["database_name"])
    migration_compatible = bool(
        actual_number is not None
        and expected_number is not None
        and (
            actual_number < expected_number
            or (
                actual_number == expected_number
                and database["migration_max"] == args.expected_migration
            )
        )
    )
    add("database.not_ahead_of_candidate", migration_compatible, database["migration_max"])
    add(
        "database.health_matches_direct",
        health["db_migration_max"] == database["migration_max"]
        and database["migration_max"] is not None,
        health["db_migration_max"],
    )

    add(
        "health.rollback_baseline_reachable",
        health["reachable"] and health["status"] == "ok",
        health["status"],
    )
    release_sha_aligned = bool(
        health["sha_aligned"] is True
        and health["server_git_sha"] is not None
        and health["server_git_sha"] == health["client_git_sha"]
    )
    add("health.release_sha_aligned", release_sha_aligned, health["sha_aligned"])
    add(
        "health.client_matches_root_build",
        health["client_git_sha"] is not None
        and health["client_git_sha"] == release["root_build_git_sha"],
        health["client_git_sha"] == release["root_build_git_sha"],
    )
    add("health.worker_online", health["worker_online"] is True, health["worker_online"])
    add(
        "health.worker_fleet_schema",
        health["worker_fleet_present"],
        health["worker_fleet_present"],
        blocking=False,
    )

    missing_domains = sorted(set(args.required_domain) - set(nginx["domains"]))
    add(
        "nginx.required_domains",
        nginx["config_readable"] and not missing_domains,
        missing_domains,
    )
    add("redis.reachable", redis_state["reachable"], redis_state["reachable"])
    add("redis.aof_enabled", redis_state["aof_enabled"] is True, redis_state["aof_enabled"])
    add(
        "redis.persistence_last_write_healthy",
        redis_state["rdb_last_bgsave_status"] == "ok"
        and redis_state["aof_last_write_status"] == "ok",
        {
            "rdb": redis_state["rdb_last_bgsave_status"],
            "aof": redis_state["aof_last_write_status"],
        },
    )

    add(
        "backup.fresh_database_dump",
        backup["fresh"] and bool(backup["dump_size_bytes"]) and backup["latest_name"] is not None,
        backup["latest_age_hours"],
    )
    add(
        "backup.dump_checksum",
        backup["checksum_present"] and backup["checksum_verified"],
        backup["checksum_verified"],
    )
    add("backup.dump_catalog", backup["catalog_verified"], backup["catalog_verified"])
    add(
        "backup.runtime_media_inventory",
        backup["runtime_state_present"] and backup["media_manifest_present"],
        {"runtime": backup["runtime_state_present"], "media": backup["media_manifest_present"]},
    )
    add(
        "backup.encrypted_environment_snapshot",
        backup["encrypted_environment_snapshot_present"],
        backup["encrypted_environment_snapshot_present"],
    )
    add(
        "backup.off_host_receipt",
        backup["off_host_receipt_present"],
        backup["off_host_receipt_present"],
    )

    pending = (
        actual_number is not None
        and expected_number is not None
        and actual_number < expected_number
    )
    rollback = {
        "legacy_source_tree_identified": release["state"] == "legacy_flat",
        "atomic_helper_present": release["atomic_helper_present"],
        "unit_fragments_captured": all(
            unit and unit["fragment_readable"] and unit["fragment_sha256"] for unit in core
        ),
        "database_backup_verified": backup["fresh"]
        and backup["checksum_verified"]
        and backup["catalog_verified"],
        "environment_recovery_evidence": backup["encrypted_environment_snapshot_present"]
        and backup["off_host_receipt_present"],
        "health_baseline_captured": health["reachable"] and health["status"] == "ok",
        "sync_timer_state_captured": bool(sync_timer and sync_timer["observable"]),
        "staging_clone_required": pending,
    }
    return checks, rollback


def contains_secret(value: object, *, key: str = "") -> bool:
    if key and SECRET_KEY_RE.search(key):
        return True
    if isinstance(value, Mapping):
        return any(
            contains_secret(item, key=str(item_key)) for item_key, item in value.items()
        )
    if isinstance(value, list):
        return any(contains_secret(item) for item in value)
    if isinstance(value, str):
        return bool(URL_CREDENTIAL_RE.search(value) or TOKEN_VALUE_RE.search(value))
    return False


def build_report(
    snapshot: Mapping[str, Any],
    args: argparse.Namespace,
    *,
    schema_version: int,
    report_type: str,
    generated_at: str,
    observed_units: Sequence[str],
    core_runtime_units: Sequence[str],
    interactive_unit: str,
    bulk_units: Sequence[str],
    error_type: type[Exception],
) -> dict[str, Any]:
    observed = project_snapshot(snapshot, observed_units)
    checks, rollback = evaluate(
        observed,
        args,
        core_runtime_units=core_runtime_units,
        interactive_unit=interactive_unit,
        bulk_units=bulk_units,
    )
    blockers = [check["id"] for check in checks if check["blocking"] and not check["pass"]]
    report = {
        "schema_version": schema_version,
        "report_type": report_type,
        "generated_at": generated_at,
        "mode": "remote_read_only_preflight",
        "target": {"ssh_target": args.ssh_target, "root": args.root},
        "candidate": {
            "expected_migration": args.expected_migration,
            "required_domains": sorted(args.required_domain),
            "max_backup_age_hours": args.max_backup_age_hours,
        },
        "safety_contract": {
            "remote_write_operations": [],
            "mutation_interface_present": False,
            "execution_allowed": False,
            "future_mutation_requires_distinct_explicit_approvals": 2,
            "future_mutation_authorization_implemented": False,
            "go_means_preflight_ready_only": True,
        },
        "observed": observed,
        "rollback_prerequisites": rollback,
        "checks": checks,
        "blocking_check_ids": blockers,
        "decision": "go" if not blockers else "no-go",
        "secret_free": True,
    }
    if contains_secret(report):
        raise error_type("secret_like_output_refused")
    return report


def latest_local_migration(script_path: Path, error_type: type[Exception]) -> str:
    root = script_path.resolve().parents[2]
    candidates = sorted(
        path.name
        for path in (root / "migrations").glob("*.sql")
        if not path.name.endswith("_down.sql") and MIGRATION_RE.fullmatch(path.name)
    )
    if not candidates:
        raise error_type("local_migration_manifest_empty")
    return candidates[-1]


def build_parser(description: str, defaults: Mapping[str, object]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--ssh-target", default=defaults["ssh_target"])
    parser.add_argument("--root", default=defaults["root"])
    parser.add_argument("--app-user", default=defaults["app_user"])
    parser.add_argument("--remote-python", default=defaults["remote_python"])
    parser.add_argument("--health-url", default=defaults["health_url"])
    parser.add_argument("--expected-migration", default=None)
    parser.add_argument("--required-domain", action="append", default=None)
    parser.add_argument("--max-backup-age-hours", type=float, default=24.0)
    parser.add_argument("--connect-timeout", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=150)
    parser.add_argument("--pretty", action="store_true")
    return parser


def validate_public_args(
    args: argparse.Namespace,
    *,
    validate_target: Callable[[str], str],
    validate_root: Callable[[str], str],
    validate_remote_python: Callable[[str, str], str],
    validate_health_url: Callable[[str], str],
    validate_app_user: Callable[[str], str],
    error_type: type[Exception],
) -> None:
    validate_target(args.ssh_target)
    root = validate_root(args.root)
    validate_remote_python(args.remote_python, root)
    validate_health_url(args.health_url)
    validate_app_user(args.app_user)
    if not MIGRATION_RE.fullmatch(args.expected_migration):
        raise error_type("invalid_expected_migration")
    if not (0 < args.max_backup_age_hours <= 168):
        raise error_type("invalid_backup_age")
    if not (1 <= args.connect_timeout <= 30 and 10 <= args.timeout <= 300):
        raise error_type("invalid_timeout")
    domains = []
    for value in args.required_domain:
        domain = str(value).lower()
        if not DOMAIN_RE.fullmatch(domain):
            raise error_type("invalid_required_domain")
        domains.append(domain)
    args.required_domain = sorted(set(domains))
