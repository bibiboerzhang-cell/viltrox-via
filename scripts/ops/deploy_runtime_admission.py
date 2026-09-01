#!/usr/bin/env python3
"""Prepare and validate one controller-owned local deploy-runtime admission.

The deploy candidate never receives the project's full dotenv.  This module
extracts the small local-runtime allowlist needed by the fenced browser gate,
pins the Phase-A manifest/static receipt, and emits controller-generated
Seatbelt profiles for the candidate web process and the canonical verifier.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import sys
from pathlib import Path
from typing import Mapping
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.ops.controller_static_receipt import (
    read_bound_regular_file,
    validate_controller_static_receipt,
)
from scripts.ops.freeze_worktree_contract import FreezeError, write_owned_file_exclusive
from scripts.ops.strict_runtime_seatbelt import candidate_profile, require_sandbox_exec
from scripts.ops.trusted_git import trusted_git_executable
from scripts.ops.trusted_npm_audit import (
    _trusted_node,
    _trusted_npm,
    _trusted_npm_package_root,
    _trusted_npx,
)


SCHEMA = "vkpi.deploy-runtime-admission/v1"
_NONCE = re.compile(r"[0-9a-f]{64}")
_SAFE_ENV_KEYS = frozenset(
    {
        "ADMIN_PASSWORD",
        "DB_RUNTIME_BACKEND",
        "DB_USE_PGBOUNCER",
        "JWT_SECRET",
        "JWT_SECRET_PREVIOUS",
        "LOCAL_DATABASE_URL",
        "LOG_LEVEL",
        "REDIS_NAMESPACE",
    }
)
_REQUIRED_ENV_KEYS = frozenset(
    {"ADMIN_PASSWORD", "JWT_SECRET", "LOCAL_DATABASE_URL"}
)
_BASH_HEREDOC_RULE = (
    '\n(allow file-read-metadata file-write* (literal "/private/tmp"))\n'
    '(allow file-read* file-write* '
    '(regex #"^/private/tmp/sh-thd-[0-9]+$"))\n'
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _parse_dotenv(data: bytes, *, label: str) -> dict[str, str]:
    try:
        text = data.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise FreezeError(f"{label} is not UTF-8") from exc
    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise FreezeError(f"{label} line {number} is not an assignment")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise FreezeError(f"{label} line {number} has an invalid key")
        if key in values:
            raise FreezeError(f"{label} contains duplicate key: {key}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
            raise FreezeError(f"{label} key {key} has an unsafe value")
        values[key] = value
    return values


def _protected_env(path: Path, *, label: str) -> tuple[dict[str, str], bytes]:
    data = read_bound_regular_file(path, label=label, required_mode=0o600)
    if len(data) > 1024 * 1024:
        raise FreezeError(f"{label} is too large")
    return _parse_dotenv(data, label=label), data


def _loopback_port(url: str, *, kind: str) -> int:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise FreezeError(f"candidate {kind} URL is invalid") from exc
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or port is None:
        raise FreezeError(f"candidate {kind} URL must use an explicit loopback port")
    if port == 8102 or not 1 <= port <= 65535 or "#" in url:
        raise FreezeError(f"candidate {kind} URL uses a forbidden port or fragment")
    if kind == "database":
        if parsed.scheme.lower() not in {"postgres", "postgresql"}:
            raise FreezeError("candidate database URL must be PostgreSQL")
        database_name = unquote(parsed.path[1:]) if parsed.path.startswith("/") else ""
        if not database_name or "/" in database_name or "\x00" in database_name:
            raise FreezeError("candidate database URL has an invalid database name")
    elif kind == "redis":
        if parsed.scheme.lower() not in {"redis", "rediss"}:
            raise FreezeError("candidate Redis URL has an invalid scheme")
        if not re.fullmatch(r"/[0-9]+", parsed.path) or parsed.query:
            raise FreezeError("candidate Redis URL has an invalid database selector")
    return port


def _database_url_with_gss_disabled(url: str) -> str:
    """Disable libpq GSS discovery for the fenced Darwin loopback runtime.

    The candidate server is a forked Gunicorn worker.  On Darwin, libpq's
    default GSS discovery enters CoreFoundation after that fork and crashes the
    worker before the health gate can answer.  GSS is neither useful nor
    permitted for this explicitly loopback-only database connection.
    """

    parsed = urlsplit(url)
    try:
        query = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=64,
        )
    except ValueError as exc:
        raise FreezeError("candidate database URL has an invalid query") from exc
    gss_modes = [value.lower() for key, value in query if key == "gssencmode"]
    if gss_modes:
        if gss_modes != ["disable"]:
            raise FreezeError("candidate database URL must disable GSS")
        query_text = parsed.query
    else:
        query_text = parsed.query + ("&" if parsed.query else "") + "gssencmode=disable"
    return urlunsplit(parsed._replace(query=query_text))


def _validate_runtime_root(path: Path) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise FreezeError("deploy runtime root must be an absolute non-symlink directory")
    resolved = path.resolve(strict=True)
    info = resolved.stat()
    if (
        resolved != path
        or not resolved.is_dir()
        or info.st_uid != os.geteuid()
        or (info.st_mode & 0o777) != 0o700
    ):
        raise FreezeError("deploy runtime root must be controller-owned and mode 0700")
    return resolved


def _child(root: Path, raw: str, *, label: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        raise FreezeError(f"{label} must be absolute")
    path = Path(os.path.abspath(os.fspath(path)))
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise FreezeError(f"{label} must be inside the deploy runtime root") from exc
    if path == root or path.exists() or path.is_symlink():
        raise FreezeError(f"{label} must be a new file inside the deploy runtime root")
    if path.parent != root / "controller":
        raise FreezeError(f"{label} must be directly inside the controller directory")
    return path


def _runtime_environment(
    source_values: Mapping[str, str], health_values: Mapping[str, str]
) -> tuple[bytes, int, int]:
    missing = sorted(key for key in _REQUIRED_ENV_KEYS if not source_values.get(key))
    if missing:
        raise FreezeError("candidate runtime environment is missing: " + ",".join(missing))
    health_token = health_values.get("OPS_HEALTH_TOKEN", "")
    if set(health_values) != {"OPS_HEALTH_TOKEN"} or not health_token:
        raise FreezeError("candidate health environment must contain only OPS_HEALTH_TOKEN")
    redis_url = source_values.get("LOCAL_REDIS_URL") or source_values.get("REDIS_URL")
    if not redis_url:
        raise FreezeError("candidate runtime environment is missing a Redis URL")
    database_url = source_values["LOCAL_DATABASE_URL"]
    database_port = _loopback_port(database_url, kind="database")
    database_url = _database_url_with_gss_disabled(database_url)
    redis_port = _loopback_port(redis_url, kind="redis")
    filtered = {
        key: source_values[key]
        for key in sorted(_SAFE_ENV_KEYS)
        if source_values.get(key)
    }
    filtered.update(
        {
            "LOCAL_DATABASE_URL": database_url,
            "LOCAL_REDIS_URL": redis_url,
            "OPS_HEALTH_TOKEN": health_token,
            "REDIS_URL": redis_url,
        }
    )
    payload = "".join(f"{key}={filtered[key]}\n" for key in sorted(filtered)).encode()
    return payload, database_port, redis_port


def _profile_payloads(
    *, candidate: Path, source: Path, runtime: Path, health_env: Path,
    web_port: int, database_port: int, redis_port: int,
) -> tuple[str, str, str]:
    require_sandbox_exec()
    venv = source / ".venv"
    node_modules = source / "frontend/node_modules"
    node = _trusted_node()
    npm = _trusted_npm()
    npm_package_root = _trusted_npm_package_root(npm)
    npx = _trusted_npx(npm)
    git = Path(trusted_git_executable())
    ports = tuple(sorted({web_port, database_port, redis_port}))
    if len(ports) != 3 or 8102 in ports:
        raise FreezeError("candidate runtime ports must be three unique non-8102 ports")
    web_profile = candidate_profile(
        candidate=candidate,
        clean_source=candidate,
        venv=venv,
        node_modules=node_modules,
        runtime_root=runtime,
        allowed_ports=ports,
        listener_ports=(web_port,),
        writable_paths=(
            runtime / "home",
            runtime / "tmp",
            runtime / "cache",
            runtime / "runtime",
        ),
        protect_clean_source=True,
        allow_runtime_root_write=False,
    ) + _BASH_HEREDOC_RULE
    verifier_profile = candidate_profile(
        candidate=candidate,
        clean_source=candidate,
        venv=venv,
        node_modules=node_modules,
        runtime_root=runtime,
        # Canonical acceptance mints its short-lived admin bearer from the
        # reviewed local PostgreSQL database before probing the candidate web
        # origin. Keep Redis unavailable: the verifier never needs to enqueue
        # work or inspect the queue directly.
        allowed_ports=(web_port, database_port),
        writable_paths=(
            runtime / "home",
            runtime / "tmp",
            runtime / "cache",
            runtime / "controller",
            runtime / "receipts",
        ),
        protect_clean_source=True,
        allow_runtime_root_write=False,
        executable_dirs=(runtime / "controller",),
        executable_paths=(node, npm, npx, git),
        readable_paths=(
            health_env,
            source / "frontend/package.json",
            npm_package_root,
        ),
    ) + _BASH_HEREDOC_RULE
    return web_profile, verifier_profile, ",".join(str(port) for port in ports)


def validate_runtime_binding_values(
    *, nonce: str, ports: str, health_url: str, base_url: str
) -> tuple[int, ...]:
    if not _NONCE.fullmatch(nonce):
        raise FreezeError("deploy runtime nonce must be 64 lowercase hex characters")
    try:
        values = tuple(int(item) for item in ports.split(","))
        health_port = urlsplit(health_url).port
        base_port = urlsplit(base_url).port
    except (TypeError, ValueError) as exc:
        raise FreezeError("deploy runtime ports are invalid") from exc
    if (
        not values
        or values != tuple(sorted(set(values)))
        or any(port == 8102 or not 1 <= port <= 65535 for port in values)
        or health_port is None
        or health_port != base_port
        or health_port not in values
    ):
        raise FreezeError("deploy runtime ports are not canonical or URL-bound")
    return values


def prepare_admission(args: argparse.Namespace) -> dict[str, object]:
    from scripts.ops.freeze_deploy_gate import verify_deploy_source

    runtime = _validate_runtime_root(Path(args.runtime_root))
    controller = runtime / "controller"
    controller.mkdir(mode=0o700, exist_ok=True)
    if controller.is_symlink() or (controller.stat().st_mode & 0o777) != 0o700:
        raise FreezeError("deploy runtime controller directory is unsafe")
    env_out = _child(runtime, args.env_out, label="candidate runtime env")
    web_profile_out = _child(runtime, args.web_profile_out, label="candidate web profile")
    verify_profile_out = _child(runtime, args.verify_profile_out, label="verifier profile")
    admission_out = _child(runtime, args.admission_out, label="runtime admission")

    before = verify_deploy_source(args)
    candidate = Path(str(before["snapshot"])).resolve()
    source = Path(args.source).resolve(strict=True)
    manifest_path = Path(args.manifest).resolve(strict=True)
    manifest_bytes = read_bound_regular_file(
        manifest_path, label="deploy admission manifest", required_mode=0o600
    )
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FreezeError("deploy admission manifest is invalid") from exc
    _static_receipt, static_receipt_bytes = validate_controller_static_receipt(
        manifest=manifest, snapshot=candidate
    )
    source_values, _source_bytes = _protected_env(
        Path(args.source_env_file), label="candidate source environment"
    )
    health_values, health_bytes = _protected_env(
        Path(args.health_env_file), label="candidate health environment"
    )
    runtime_env, database_port, redis_port = _runtime_environment(
        source_values, health_values
    )
    web_port = int(args.web_port)
    if not 1024 <= web_port <= 65535 or web_port == 8102:
        raise FreezeError("candidate web port is invalid")
    web_profile, verify_profile, runtime_ports = _profile_payloads(
        candidate=candidate,
        source=source,
        runtime=runtime,
        health_env=Path(args.health_env_file).resolve(strict=True),
        web_port=web_port,
        database_port=database_port,
        redis_port=redis_port,
    )
    nonce = secrets.token_hex(32)
    if not _NONCE.fullmatch(nonce):
        raise FreezeError("controller nonce generation failed")
    artifacts = {
        env_out: runtime_env,
        web_profile_out: web_profile.encode("utf-8"),
        verify_profile_out: verify_profile.encode("utf-8"),
    }
    created: list[Path] = []
    try:
        for path, data in artifacts.items():
            write_owned_file_exclusive(path, data)
            created.append(path)
        payload: dict[str, object] = {
            "schema": SCHEMA,
            "nonce": nonce,
            "runtime_root": str(runtime),
            "candidate": str(candidate),
            "candidate_sha256": str(before["content_sha256"]),
            "manifest": str(manifest_path),
            "manifest_sha256": _sha256(manifest_bytes),
            "static_receipt_sha256": _sha256(static_receipt_bytes),
            "health_env_file": str(Path(args.health_env_file).resolve(strict=True)),
            "health_env_sha256": _sha256(health_bytes),
            "runtime_env_file": str(env_out),
            "runtime_env_sha256": _sha256(runtime_env),
            "web_profile_file": str(web_profile_out),
            "web_profile_sha256": _sha256(web_profile.encode("utf-8")),
            "verify_profile_file": str(verify_profile_out),
            "verify_profile_sha256": _sha256(verify_profile.encode("utf-8")),
            "web_port": web_port,
            "database_port": database_port,
            "redis_port": redis_port,
            "runtime_ports": runtime_ports,
            "provider_credentials_forwarded": False,
            "external_network_allowed": False,
        }
        write_owned_file_exclusive(admission_out, _canonical_bytes(payload) + b"\n")
        created.append(admission_out)
        after = verify_deploy_source(args)
        if after.get("content_sha256") != before.get("content_sha256"):
            raise FreezeError("candidate changed while preparing runtime admission")
        return payload
    except BaseException:
        for path in reversed(created):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def load_admission(
    path: Path,
    *, runtime_root: Path,
    candidate: Path,
    manifest: Path,
    health_env_file: Path,
    health_url: str,
    base_url: str,
) -> dict[str, object]:
    data = read_bound_regular_file(path, label="deploy runtime admission", required_mode=0o600)
    try:
        payload = json.loads(data.decode("utf-8", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FreezeError("deploy runtime admission is invalid") from exc
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise FreezeError("deploy runtime admission schema mismatch")
    try:
        url_port = urlsplit(health_url).port
        base_port = urlsplit(base_url).port
    except ValueError as exc:
        raise FreezeError("deploy runtime admission URL is invalid") from exc
    expected_paths = {
        "runtime_root": runtime_root.resolve(strict=True),
        "candidate": candidate.resolve(strict=True),
        "manifest": manifest.resolve(strict=True),
        "health_env_file": health_env_file.resolve(strict=True),
    }
    for name, expected in expected_paths.items():
        value = payload.get(name)
        if not isinstance(value, str) or Path(value).resolve(strict=True) != expected:
            raise FreezeError(f"deploy runtime admission {name} mismatch")
    if (
        not isinstance(payload.get("web_port"), int)
        or payload.get("web_port") != url_port
        or url_port != base_port
        or payload.get("provider_credentials_forwarded") is not False
        or payload.get("external_network_allowed") is not False
    ):
        raise FreezeError("deploy runtime admission binding is invalid")
    validate_runtime_binding_values(
        nonce=str(payload.get("nonce", "")),
        ports=str(payload.get("runtime_ports", "")),
        health_url=health_url,
        base_url=base_url,
    )
    ports = payload.get("runtime_ports")
    raw_ports = (
        payload.get("web_port"),
        payload.get("database_port"),
        payload.get("redis_port"),
    )
    if any(not isinstance(port, int) for port in raw_ports):
        raise FreezeError("deploy runtime admission ports are invalid")
    expected_ports = sorted(set(raw_ports))
    if (
        len(expected_ports) != 3
        or 8102 in expected_ports
        or ports != ",".join(str(port) for port in expected_ports)
    ):
        raise FreezeError("deploy runtime admission ports are not canonical")
    bound_files: dict[str, bytes] = {}
    for path_name, digest_name in (
        ("runtime_env_file", "runtime_env_sha256"),
        ("web_profile_file", "web_profile_sha256"),
        ("verify_profile_file", "verify_profile_sha256"),
    ):
        raw_path = payload.get(path_name)
        digest = payload.get(digest_name)
        if not isinstance(raw_path, str) or not re.fullmatch(r"[0-9a-f]{64}", str(digest)):
            raise FreezeError(f"deploy runtime admission {path_name} is invalid")
        bound_path = Path(raw_path)
        if bound_path.parent != runtime_root / "controller":
            raise FreezeError(f"deploy runtime admission {path_name} escaped controller")
        bound_bytes = read_bound_regular_file(
            bound_path, label=f"deploy runtime {path_name}", required_mode=0o600
        )
        if _sha256(bound_bytes) != digest:
            raise FreezeError(f"deploy runtime admission {path_name} changed")
        bound_files[path_name] = bound_bytes
    try:
        payload["_verify_profile"] = bound_files["verify_profile_file"].decode(
            "utf-8", "strict"
        )
    except UnicodeDecodeError as exc:
        raise FreezeError("deploy verifier profile is not UTF-8") from exc
    return payload


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--manifest", required=True)
    result.add_argument("--snapshot", required=True)
    result.add_argument("--expected-head", required=True)
    result.add_argument("--expected-branch", required=True)
    result.add_argument("--source", required=True)
    result.add_argument("--runtime-root", required=True)
    result.add_argument("--source-env-file", required=True)
    result.add_argument("--health-env-file", required=True)
    result.add_argument("--web-port", required=True, type=int)
    result.add_argument("--env-out", required=True)
    result.add_argument("--web-profile-out", required=True)
    result.add_argument("--verify-profile-out", required=True)
    result.add_argument("--admission-out", required=True)
    result.set_defaults(action=prepare_admission)
    return result


def main() -> int:
    try:
        args = parser().parse_args()
        payload = args.action(args)
    except (FreezeError, OSError, RuntimeError, ValueError) as exc:
        raise SystemExit(f"deploy runtime admission failed: {exc}") from exc
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
