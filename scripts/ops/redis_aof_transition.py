#!/usr/bin/env python3
"""Inspect and explicitly enable Redis AOF on a loopback production target.

The helper deliberately has a narrow authority boundary:

* the Redis URL must come from one explicitly supplied protected dotenv;
* only TCP loopback ``redis://`` and ``rediss://`` targets are accepted;
* ``inspect`` is deterministic and emits no credential-bearing values;
* ``enable`` is bound to an exact pre-state digest and an exclusive receipt;
* persistence changes are verified before a success receipt is committed.

It does not start or stop Redis, edit application files, or perform deployment.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import grp
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlsplit

from dotenv import dotenv_values


SCHEMA = "vkpi.redis-aof-transition.inspect.v1"
RECEIPT_SCHEMA = "vkpi.redis-aof-transition.receipt.v1"
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
_SAFE_ENV_MODES = {0o400, 0o440, 0o600, 0o640}
_MAX_ENV_BYTES = 1024 * 1024
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}")
_REDIS_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?REDIS_URL\s*=", re.MULTILINE)
_CONFIG_KEYS = ("appendonly", "appendfsync", "save")


class TransitionError(RuntimeError):
    """A bounded, credential-free transition failure."""


class RedisClient(Protocol):
    def ping(self) -> object: ...

    def dbsize(self) -> object: ...

    def config_get(self, pattern: str) -> object: ...

    def info(self, section: str) -> object: ...

    def bgsave(self) -> object: ...

    def config_set(self, name: str, value: str) -> object: ...

    def config_rewrite(self) -> object: ...

    def close(self) -> object: ...


@dataclass(frozen=True)
class RedisTarget:
    """Validated Redis target; the credential-bearing URL is never represented."""

    scheme: str
    host: str
    port: int
    db: int
    _url: str = field(repr=False, compare=False)

    @property
    def safe_metadata(self) -> dict[str, object]:
        return {
            "host_kind": "loopback",
            "port": self.port,
            "db": self.db,
        }


ClientFactory = Callable[[str], RedisClient]


def _canonical_bytes(payload: Mapping[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _read_protected_dotenv(
    path: Path,
    *,
    allowed_group_ids: set[int] | None = None,
) -> str:
    """Read a small protected dotenv without following its final path."""

    env_path = Path(path).expanduser()
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    try:
        descriptor = os.open(env_path, flags)
    except OSError as exc:
        raise TransitionError("protected dotenv cannot be opened safely") from exc
    try:
        metadata = os.fstat(descriptor)
        mode = stat.S_IMODE(metadata.st_mode)
        permitted_owners = {0}
        if hasattr(os, "geteuid"):
            permitted_owners.add(os.geteuid())
        if not stat.S_ISREG(metadata.st_mode):
            raise TransitionError("protected dotenv must be a regular file")
        if metadata.st_nlink != 1:
            raise TransitionError("protected dotenv must have exactly one hard link")
        if metadata.st_uid not in permitted_owners:
            raise TransitionError("protected dotenv owner is not trusted")
        if mode not in _SAFE_ENV_MODES:
            raise TransitionError("protected dotenv mode must be 0400/0440/0600/0640")
        if mode in {0o440, 0o640}:
            effective_groups = (
                set(allowed_group_ids)
                if allowed_group_ids is not None
                else {os.getegid(), *os.getgroups()}
            )
            if metadata.st_gid not in effective_groups:
                raise TransitionError("protected dotenv group is not explicitly allowed")
        if metadata.st_size > _MAX_ENV_BYTES:
            raise TransitionError("protected dotenv exceeds the safety size limit")

        chunks: list[bytes] = []
        remaining = _MAX_ENV_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        encoded = b"".join(chunks)
        if len(encoded) > _MAX_ENV_BYTES:
            raise TransitionError("protected dotenv exceeds the safety size limit")
        if b"\x00" in encoded:
            raise TransitionError("protected dotenv contains unsupported binary data")
        try:
            return encoded.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise TransitionError("protected dotenv must be UTF-8") from exc
    finally:
        os.close(descriptor)


def _target_from_dotenv(
    env_file: Path,
    *,
    allowed_group_ids: set[int] | None = None,
) -> RedisTarget:
    text = _read_protected_dotenv(env_file, allowed_group_ids=allowed_group_ids)
    if len(_REDIS_ASSIGNMENT.findall(text)) != 1:
        raise TransitionError("protected dotenv must contain exactly one REDIS_URL assignment")
    try:
        values = dotenv_values(stream=StringIO(text), interpolate=False)
    except Exception as exc:  # noqa: BLE001 - parser details may contain secret material.
        raise TransitionError("protected dotenv cannot be parsed") from exc
    raw_url = values.get("REDIS_URL")
    if not isinstance(raw_url, str) or not raw_url.strip():
        raise TransitionError("REDIS_URL is not configured")
    url = raw_url.strip()
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        port = parsed.port or 6379
    except ValueError as exc:
        raise TransitionError("REDIS_URL endpoint is malformed") from exc
    if parsed.scheme not in {"redis", "rediss"}:
        raise TransitionError("REDIS_URL scheme must be redis or rediss")
    if host not in _LOOPBACK_HOSTS:
        raise TransitionError("REDIS_URL target must be TCP loopback")
    if parsed.query or parsed.fragment:
        raise TransitionError("REDIS_URL query and fragment fields are not accepted")
    if not 1 <= port <= 65535:
        raise TransitionError("REDIS_URL port is out of range")
    path_value = parsed.path or "/0"
    if not re.fullmatch(r"/[0-9]+", path_value):
        raise TransitionError("REDIS_URL must contain one numeric database path")
    db = int(path_value[1:])
    if db > 2**31 - 1:
        raise TransitionError("REDIS_URL database is out of range")
    return RedisTarget(
        scheme=parsed.scheme,
        host=host,
        port=port,
        db=db,
        _url=url,
    )


def _default_client_factory(url: str) -> RedisClient:
    try:
        import redis  # Imported only after the local target and dotenv are validated.
    except ImportError as exc:
        raise TransitionError("redis client package is unavailable") from exc
    try:
        return redis.Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=10,
            retry_on_timeout=False,
        )
    except Exception as exc:  # noqa: BLE001 - never echo credential-bearing constructor data.
        raise TransitionError("Redis client could not be created") from exc


def _config_value(client: RedisClient, key: str) -> str:
    try:
        result = client.config_get(key)
    except Exception as exc:  # noqa: BLE001 - Redis errors are intentionally redacted.
        raise TransitionError("Redis persistence configuration could not be inspected") from exc
    if not isinstance(result, Mapping) or set(result) != {key}:
        raise TransitionError("Redis returned an unexpected persistence configuration shape")
    value = result[key]
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise TransitionError("Redis persistence configuration is not UTF-8") from exc
    if not isinstance(value, str) or "\x00" in value:
        raise TransitionError("Redis returned an invalid persistence configuration value")
    return value


def _persistence_info(client: RedisClient) -> Mapping[str, object]:
    try:
        payload = client.info("persistence")
    except Exception as exc:  # noqa: BLE001 - Redis errors are intentionally redacted.
        raise TransitionError("Redis persistence status could not be inspected") from exc
    if not isinstance(payload, Mapping):
        raise TransitionError("Redis returned an unexpected persistence status shape")
    return payload


def _integer_field(payload: Mapping[str, object], key: str, *, binary: bool = False) -> int:
    value = payload.get(key)
    if isinstance(value, bool):
        raise TransitionError("Redis returned an invalid persistence integer field")
    try:
        parsed = int(value)  # Redis-py may decode INFO fields as either ints or strings.
    except (TypeError, ValueError) as exc:
        raise TransitionError("Redis returned an invalid persistence integer field") from exc
    if parsed < 0 or (binary and parsed not in {0, 1}):
        raise TransitionError("Redis returned an out-of-range persistence integer field")
    return parsed


def _status_field(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if isinstance(value, bytes):
        try:
            value = value.decode("ascii")
        except UnicodeDecodeError as exc:
            raise TransitionError("Redis returned a non-ASCII persistence status") from exc
    if not isinstance(value, str) or value not in {"ok", "err"}:
        raise TransitionError("Redis returned an unexpected persistence status")
    return value


def _state_from_client(client: RedisClient, target: RedisTarget) -> dict[str, object]:
    try:
        if client.ping() is not True:
            raise TransitionError("Redis PING did not return success")
        raw_dbsize = client.dbsize()
    except TransitionError:
        raise
    except Exception as exc:  # noqa: BLE001 - Redis errors are intentionally redacted.
        raise TransitionError("Redis loopback target is unavailable") from exc
    if isinstance(raw_dbsize, bool):
        raise TransitionError("Redis returned an invalid database size")
    try:
        dbsize = int(raw_dbsize)
    except (TypeError, ValueError) as exc:
        raise TransitionError("Redis returned an invalid database size") from exc
    if dbsize < 0:
        raise TransitionError("Redis returned an invalid database size")

    config = {key: _config_value(client, key) for key in _CONFIG_KEYS}
    if config["appendonly"] not in {"yes", "no"}:
        raise TransitionError("Redis appendonly configuration is unexpected")
    if config["appendfsync"] not in {"always", "everysec", "no"}:
        raise TransitionError("Redis appendfsync configuration is unexpected")
    info = _persistence_info(client)
    state: dict[str, object] = {
        "schema": SCHEMA,
        **target.safe_metadata,
        "dbsize": dbsize,
        "appendonly": config["appendonly"],
        "appendfsync": config["appendfsync"],
        "aof_enabled": _integer_field(info, "aof_enabled", binary=True),
        "aof_last_write_status": _status_field(info, "aof_last_write_status"),
        "rdb_last_save_time": _integer_field(info, "rdb_last_save_time"),
        "rdb_bgsave_in_progress": _integer_field(
            info, "rdb_bgsave_in_progress", binary=True
        ),
        "rdb_last_bgsave_status": _status_field(info, "rdb_last_bgsave_status"),
        "aof_rewrite_in_progress": _integer_field(
            info, "aof_rewrite_in_progress", binary=True
        ),
        "persistence_config": config,
    }
    if (state["appendonly"] == "yes") != (state["aof_enabled"] == 1):
        raise TransitionError("Redis AOF configuration and runtime state disagree")
    state["pre_state_sha256"] = _digest(state)
    return state


def inspect_redis(
    *,
    env_file: Path,
    allowed_group_ids: set[int] | None = None,
    client_factory: ClientFactory | None = None,
) -> dict[str, object]:
    """Inspect a protected loopback target and return a credential-free state."""

    target = _target_from_dotenv(env_file, allowed_group_ids=allowed_group_ids)
    factory = client_factory or _default_client_factory
    client = factory(target._url)
    try:
        return _state_from_client(client, target)
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001 - closing cannot change inspection truth.
            pass


def _success(result: object, operation: str) -> None:
    # redis-py normalizes BGSAVE and CONFIG SET to ``True`` but returns the
    # raw ``OK`` response for CONFIG REWRITE in supported 5.x releases.
    if result is not True and result not in {"OK", b"OK"}:
        raise TransitionError(f"Redis {operation} did not return success")


def _wait_for_snapshot(
    client: RedisClient,
    *,
    before_last_save: int,
    timeout_seconds: float,
    sleep: Callable[[float], None],
) -> tuple[int, str]:
    """Complete one BGSAVE and prove a current valid RDB snapshot exists."""

    deadline = time.monotonic() + timeout_seconds
    initial = _persistence_info(client)
    while _integer_field(initial, "rdb_bgsave_in_progress", binary=True) == 1:
        if time.monotonic() >= deadline:
            raise TransitionError("an existing Redis background save did not finish")
        sleep(0.1)
        initial = _persistence_info(client)
    try:
        _success(client.bgsave(), "BGSAVE")
    except TransitionError:
        raise
    except Exception as exc:  # noqa: BLE001 - Redis errors are intentionally redacted.
        raise TransitionError("Redis BGSAVE could not be started") from exc

    saw_in_progress = False
    while True:
        info = _persistence_info(client)
        in_progress = _integer_field(info, "rdb_bgsave_in_progress", binary=True)
        saw_in_progress = saw_in_progress or in_progress == 1
        if in_progress == 0:
            status = _status_field(info, "rdb_last_bgsave_status")
            last_save = _integer_field(info, "rdb_last_save_time")
            # Redis timestamps have one-second precision. An immediate valid BGSAVE
            # may complete in the same second, so status=ok and a non-regressing
            # timestamp is an acceptable completed snapshot.
            if status == "ok" and last_save >= before_last_save:
                return last_save, "advanced" if last_save > before_last_save else "valid_same_second"
            raise TransitionError("Redis BGSAVE did not produce a valid current snapshot")
        if time.monotonic() >= deadline:
            qualifier = "after starting" if saw_in_progress else "before observation"
            raise TransitionError(f"Redis BGSAVE timed out {qualifier}")
        sleep(0.1)


def _wait_for_aof(
    client: RedisClient,
    *,
    timeout_seconds: float,
    sleep: Callable[[float], None],
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        info = _persistence_info(client)
        enabled = _integer_field(info, "aof_enabled", binary=True)
        status = _status_field(info, "aof_last_write_status")
        if enabled == 1 and status == "ok":
            return
        if time.monotonic() >= deadline:
            raise TransitionError("Redis AOF did not reach a healthy enabled state")
        sleep(0.1)


class _ReceiptReservation:
    """Reserve a receipt before mutation and commit it durably after verification."""

    def __init__(self, receipt_path: Path) -> None:
        path = Path(receipt_path).expanduser()
        if path.name in {"", ".", ".."}:
            raise TransitionError("receipt path must name one new regular file")
        directory_flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            directory_flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            directory_flags |= os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            directory_flags |= os.O_CLOEXEC
        try:
            self._directory_fd = os.open(path.parent, directory_flags)
        except OSError as exc:
            raise TransitionError("receipt directory cannot be opened safely") from exc
        file_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            file_flags |= os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            file_flags |= os.O_CLOEXEC
        try:
            self._file_fd = os.open(
                path.name,
                file_flags,
                0o600,
                dir_fd=self._directory_fd,
            )
            os.fchmod(self._file_fd, 0o600)
        except OSError as exc:
            os.close(self._directory_fd)
            raise TransitionError("receipt already exists or cannot be reserved safely") from exc
        self._name = path.name
        self._closed = False

    def commit(self, payload: Mapping[str, object]) -> None:
        if self._closed:
            raise TransitionError("receipt reservation is already closed")
        encoded = _canonical_bytes(payload) + b"\n"
        try:
            offset = 0
            while offset < len(encoded):
                written = os.write(self._file_fd, encoded[offset:])
                if written <= 0:
                    raise OSError("short receipt write")
                offset += written
            os.fsync(self._file_fd)
            os.close(self._file_fd)
            os.fsync(self._directory_fd)
            os.close(self._directory_fd)
            self._closed = True
        except OSError as exc:
            self.abort()
            raise TransitionError("receipt could not be committed durably") from exc

    def abort(self) -> None:
        if self._closed:
            return
        try:
            os.close(self._file_fd)
        except OSError:
            pass
        try:
            os.unlink(self._name, dir_fd=self._directory_fd)
            os.fsync(self._directory_fd)
        except OSError:
            pass
        try:
            os.close(self._directory_fd)
        except OSError:
            pass
        self._closed = True


def enable_aof(
    *,
    env_file: Path,
    expected_pre_state_sha256: str,
    confirm: str,
    receipt_path: Path,
    allowed_group_ids: set[int] | None = None,
    client_factory: ClientFactory | None = None,
    timeout_seconds: float = 60.0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """Enable and prove AOF, bound to an exact inspected pre-state digest."""

    if not _HEX_SHA256.fullmatch(expected_pre_state_sha256):
        raise TransitionError("expected pre-state SHA-256 must be 64 lowercase hex characters")
    if confirm != expected_pre_state_sha256:
        raise TransitionError("confirmation does not match the expected pre-state SHA-256")
    if not 1.0 <= float(timeout_seconds) <= 300.0:
        raise TransitionError("timeout must be between 1 and 300 seconds")

    target = _target_from_dotenv(env_file, allowed_group_ids=allowed_group_ids)
    factory = client_factory or _default_client_factory
    client = factory(target._url)
    reservation: _ReceiptReservation | None = None
    try:
        pre_state = _state_from_client(client, target)
        if pre_state["pre_state_sha256"] != expected_pre_state_sha256:
            raise TransitionError("Redis pre-state drifted from the confirmed inspection")

        # Reserve before the first mutating command so an existing receipt can
        # never be discovered only after Redis has already changed.
        reservation = _ReceiptReservation(receipt_path)
        snapshot_after, snapshot_proof = _wait_for_snapshot(
            client,
            before_last_save=int(pre_state["rdb_last_save_time"]),
            timeout_seconds=float(timeout_seconds),
            sleep=sleep,
        )
        try:
            _success(client.config_set("appendonly", "yes"), "CONFIG SET appendonly")
            _success(
                client.config_set("appendfsync", "everysec"),
                "CONFIG SET appendfsync",
            )
            _success(client.config_rewrite(), "CONFIG REWRITE")
        except TransitionError:
            raise
        except Exception as exc:  # noqa: BLE001 - Redis errors are intentionally redacted.
            raise TransitionError("Redis AOF configuration could not be applied") from exc

        _wait_for_aof(
            client,
            timeout_seconds=float(timeout_seconds),
            sleep=sleep,
        )
        post_state = _state_from_client(client, target)
        if (
            post_state["appendonly"] != "yes"
            or post_state["appendfsync"] != "everysec"
            or post_state["aof_enabled"] != 1
            or post_state["aof_last_write_status"] != "ok"
        ):
            raise TransitionError("Redis AOF verification failed after configuration rewrite")
        receipt: dict[str, object] = {
            "schema": RECEIPT_SCHEMA,
            "action": "enable_aof",
            **target.safe_metadata,
            "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "pre_state_sha256": expected_pre_state_sha256,
            "post_state_sha256": post_state["pre_state_sha256"],
            "snapshot": {
                "method": "BGSAVE",
                "before_last_save_time": pre_state["rdb_last_save_time"],
                "after_last_save_time": snapshot_after,
                "proof": snapshot_proof,
            },
            "verified": {
                "appendonly": "yes",
                "appendfsync": "everysec",
                "aof_enabled": 1,
                "aof_last_write_status": "ok",
                "config_rewrite": True,
            },
        }
        reservation.commit(receipt)
        reservation = None
        return receipt
    finally:
        if reservation is not None:
            reservation.abort()
        try:
            client.close()
        except Exception:  # noqa: BLE001 - close failure cannot justify a success claim.
            pass


def _allowed_group_ids(values: list[str]) -> set[int]:
    result = {os.getegid(), *os.getgroups()}
    for value in values:
        token = value.strip()
        if not token:
            raise TransitionError("allowed group cannot be empty")
        if token.isdecimal():
            result.add(int(token))
            continue
        try:
            result.add(grp.getgrnam(token).gr_gid)
        except KeyError as exc:
            raise TransitionError("allowed group does not exist") from exc
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect or explicitly enable AOF on one protected loopback Redis target."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("--env-file", type=Path, required=True)
    inspect_parser.add_argument("--allowed-group", action="append", default=[])

    enable_parser = subparsers.add_parser("enable")
    enable_parser.add_argument("--env-file", type=Path, required=True)
    enable_parser.add_argument("--allowed-group", action="append", default=[])
    enable_parser.add_argument("--expected-pre-state-sha256", required=True)
    enable_parser.add_argument("--confirm", required=True)
    enable_parser.add_argument("--receipt", type=Path, required=True)
    enable_parser.add_argument("--timeout-seconds", type=float, default=60.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        groups = _allowed_group_ids(args.allowed_group)
        if args.command == "inspect":
            payload = inspect_redis(env_file=args.env_file, allowed_group_ids=groups)
        elif args.command == "enable":
            payload = enable_aof(
                env_file=args.env_file,
                expected_pre_state_sha256=args.expected_pre_state_sha256,
                confirm=args.confirm,
                receipt_path=args.receipt,
                allowed_group_ids=groups,
                timeout_seconds=args.timeout_seconds,
            )
        else:  # pragma: no cover - argparse enforces the closed command set.
            raise TransitionError("unsupported command")
    except TransitionError as exc:
        sys.stderr.write(f"redis AOF transition failed: {exc}\n")
        return 2
    except Exception:  # noqa: BLE001 - never echo untrusted exceptions or credentials.
        sys.stderr.write("redis AOF transition failed: unexpected internal error\n")
        return 2
    sys.stdout.write(_canonical_bytes(payload).decode("ascii") + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
