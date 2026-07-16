#!/usr/bin/env python3
"""Reproducible, side-effect-isolated benchmark for strategy read caching.

This benchmark starts a temporary Redis bound only to a private Unix socket.
It never imports either strategy domain builder, opens PostgreSQL, calls a
provider, or talks to the running V-KPI service.  The synthetic builder makes
cold/warm cache behavior and singleflight reproducible; it is deliberately not
presented as live endpoint capacity evidence.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import fcntl
import json
import logging
import math
import multiprocessing
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

# Keep stdout/stderr machine-readable and avoid printing configured integration
# hosts while importing the application cache module.
logging.disable(logging.CRITICAL)

from redis import Redis  # noqa: E402

from app.services.cache import memory_cache  # noqa: E402


STRATEGY_KEYS = (
    "vkpi_strategy:category_tracks:v2:org:1",
    "vkpi_strategy:industry_benchmark:v2:org:1:days:90",
)


class _FileBuildLock:
    """Process-shared lock used only when sandbox policy forbids socket bind."""

    def __init__(self, directory: str, key: str, blocking_timeout: float):
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        self._path = Path(directory) / f"{digest}.lock"
        self._held_path = Path(directory) / f"{digest}.held"
        self._blocking_timeout = float(blocking_timeout)
        self._handle = None
        self._owned = False

    def acquire(self, blocking=True):
        if not blocking:
            raise ValueError("benchmark file lock requires blocking=True")
        self._handle = self._path.open("a+b")
        deadline = time.monotonic() + self._blocking_timeout
        while True:
            try:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._owned = True
                self._held_path.write_text(str(os.getpid()), encoding="utf-8")
                return True
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    self._handle.close()
                    self._handle = None
                    return False
                time.sleep(0.005)

    def release(self):
        if not self._owned or self._handle is None:
            raise RuntimeError("benchmark lock not owned")
        self._held_path.unlink(missing_ok=True)
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()
        self._handle = None
        self._owned = False


class _FileRedisProtocolEmulator:
    """Small file/``flock`` Redis protocol emulator for sandboxed evidence.

    It implements only the Redis methods exercised by ``memory_cache``.  It is
    not a Redis replacement and is labelled as an emulator in every report.
    """

    def __init__(self, directory: str):
        self.directory = str(directory)
        Path(self.directory).mkdir(parents=True, exist_ok=True)

    def _cache_path(self, key: Any) -> Path:
        raw = key.decode("utf-8") if isinstance(key, bytes) else str(key)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return Path(self.directory) / f"{digest}.cache"

    def ping(self):
        return True

    def get(self, key):
        path = self._cache_path(key)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return None
        if len(raw) < 20 or raw[:4] != b"EXP:":
            return None
        newline = raw.find(b"\n")
        expires = float(raw[4:newline].decode("ascii"))
        if expires <= time.time():
            path.unlink(missing_ok=True)
            return None
        return raw[newline + 1 :]

    def setex(self, key, ttl, value):
        path = self._cache_path(key)
        expires = time.time() + int(ttl)
        body = f"EXP:{expires:.6f}\n".encode("ascii") + bytes(value)
        temp = path.with_suffix(f".tmp-{os.getpid()}-{threading.get_ident()}")
        temp.write_bytes(body)
        os.replace(temp, path)
        return True

    def delete(self, key):
        path = self._cache_path(key)
        existed = path.exists()
        path.unlink(missing_ok=True)
        return int(existed)

    def ttl(self, key):
        path = self._cache_path(key)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return -2
        newline = raw.find(b"\n")
        expires = float(raw[4:newline].decode("ascii"))
        return max(-2, int(math.ceil(expires - time.time())))

    def lock(self, key, *, timeout, blocking_timeout):
        del timeout  # process exit releases flock; Redis lease is unit-tested separately
        return _FileBuildLock(self.directory, str(key), blocking_timeout)

    def scan_iter(self, match=None):
        del match
        return iter(str(path).encode("utf-8") for path in Path(self.directory).glob("*.held"))

    def flushdb(self):
        for pattern in ("*.cache", "*.held"):
            for path in Path(self.directory).glob(pattern):
                path.unlink(missing_ok=True)
        return True

    def close(self):
        return None


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = max(0, min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1))
    return round(ordered[index], 4)


def _latency_summary(values_ms: list[float]) -> dict[str, float]:
    return {
        "p50_ms": _percentile(values_ms, 0.50),
        "p95_ms": _percentile(values_ms, 0.95),
        "p99_ms": _percentile(values_ms, 0.99),
        "max_ms": round(max(values_ms), 4) if values_ms else 0.0,
    }


@contextmanager
def _isolated_redis() -> Iterator[tuple[Redis, str, str]]:
    binary = shutil.which("redis-server")
    if not binary:
        raise RuntimeError("redis-server is required for the isolated benchmark")

    with tempfile.TemporaryDirectory(prefix="vkpi-strategy-cache-") as tmp:
        socket_path = str(Path(tmp) / "redis.sock")
        process = subprocess.Popen(
            [
                binary,
                "--port",
                "0",
                "--unixsocket",
                socket_path,
                "--unixsocketperm",
                "700",
                "--save",
                "",
                "--appendonly",
                "no",
                "--dir",
                tmp,
                "--loglevel",
                "warning",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        client = Redis(unix_socket_path=socket_path, decode_responses=False, socket_timeout=2)
        deadline = time.monotonic() + 5
        try:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    output = process.stdout.read() if process.stdout is not None else ""
                    tail = output.strip().splitlines()[-1] if output.strip() else "no log output"
                    raise RuntimeError(f"isolated redis exited with {process.returncode}: {tail}")
                try:
                    if client.ping():
                        break
                except Exception:
                    time.sleep(0.02)
            else:
                raise RuntimeError("isolated redis did not become ready")
            version = subprocess.check_output([binary, "--version"], text=True).strip()
            yield client, socket_path, version
        finally:
            try:
                client.close()
            except Exception:
                pass
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


@contextmanager
def _isolated_cache_transport():
    """Prefer real Redis, then truthfully fall back when bind is sandboxed."""

    redis_context = _isolated_redis()
    try:
        redis_resources = redis_context.__enter__()
    except RuntimeError as exc:
        binary = shutil.which("redis-server")
        version = (
            subprocess.check_output([binary, "--version"], text=True).strip()
            if binary
            else "redis-server unavailable"
        )
        error_text = str(exc)
        if "Failed opening Unix socket" in error_text and "Operation not permitted" in error_text:
            error_text = "unix_socket_bind_not_permitted_by_sandbox"
        with tempfile.TemporaryDirectory(prefix="vkpi-strategy-cache-file-") as tmp:
            client = _FileRedisProtocolEmulator(tmp)
            yield (
                client,
                tmp,
                version,
                "process_shared_file_lock_redis_protocol_emulator",
                error_text,
            )
        return

    try:
        client, endpoint, version = redis_resources
        yield client, endpoint, version, "private_temporary_unix_socket_redis", None
    finally:
        redis_context.__exit__(None, None, None)


def _configure_worker_cache(client: Redis) -> None:
    memory_cache._redis_client = client
    memory_cache._cache.clear()
    memory_cache._build_locks.clear()


def _payload(payload_bytes: int, *, built_by: int) -> dict[str, Any]:
    return {
        "status": "ready",
        "built_by": built_by,
        "payload": "x" * payload_bytes,
    }


def _benchmark_cold_warm(
    client: Redis,
    key: str,
    *,
    builder_ms: float,
    payload_bytes: int,
    warm_samples: int,
) -> dict[str, Any]:
    memory_cache.cache_delete(key)
    builder_calls = 0

    def builder() -> dict[str, Any]:
        nonlocal builder_calls
        builder_calls += 1
        time.sleep(builder_ms / 1000)
        return _payload(payload_bytes, built_by=os.getpid())

    started = time.perf_counter()
    cold_value = memory_cache.cache_get_or_build(key, builder, ttl=30)
    cold_ms = (time.perf_counter() - started) * 1000

    warm_latencies: list[float] = []
    for _ in range(warm_samples):
        started = time.perf_counter()
        warm_value = memory_cache.cache_get_or_build(key, builder, ttl=30)
        warm_latencies.append((time.perf_counter() - started) * 1000)
        if warm_value != cold_value:
            raise AssertionError("warm cache value changed")

    warm = _latency_summary(warm_latencies)
    return {
        "key": key,
        "builder_calls": builder_calls,
        "cold_ms": round(cold_ms, 4),
        "warm_samples": warm_samples,
        "warm": warm,
        "cold_to_warm_p95_speedup": round(cold_ms / max(warm["p95_ms"], 0.0001), 2),
        "redis_ttl_sec_after": int(client.ttl(memory_cache._full_key(key))),
        "value_sha256": hashlib.sha256(memory_cache._serialize(cold_value)).hexdigest(),
    }


def _benchmark_thread_burst(
    key: str,
    *,
    builder_ms: float,
    payload_bytes: int,
    workers: int,
) -> dict[str, Any]:
    memory_cache.cache_delete(key)
    builder_calls = 0
    builder_guard = threading.Lock()
    start_event = threading.Event()

    def builder() -> dict[str, Any]:
        nonlocal builder_calls
        with builder_guard:
            builder_calls += 1
        time.sleep(builder_ms / 1000)
        return _payload(payload_bytes, built_by=os.getpid())

    def run_one(_index: int) -> tuple[float, dict[str, Any]]:
        start_event.wait()
        started = time.perf_counter()
        value = memory_cache.cache_get_or_build(key, builder, ttl=30)
        return (time.perf_counter() - started) * 1000, value

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run_one, index) for index in range(workers)]
        burst_started = time.perf_counter()
        start_event.set()
        rows = [future.result(timeout=15) for future in futures]
    total_ms = (time.perf_counter() - burst_started) * 1000
    latencies = [row[0] for row in rows]
    digests = {hashlib.sha256(memory_cache._serialize(row[1])).hexdigest() for row in rows}
    return {
        "key": key,
        "workers": workers,
        "builder_calls": builder_calls,
        "unique_result_digests": len(digests),
        "wall_ms": round(total_ms, 4),
        "effective_rps": round(workers / max(total_ms / 1000, 0.000001), 2),
        "latency": _latency_summary(latencies),
    }


def _process_cache_call(
    transport: str,
    endpoint: str,
    key: str,
    builder_ms: float,
    payload_bytes: int,
    start_event,
    output_queue,
) -> None:
    if transport == "private_temporary_unix_socket_redis":
        client = Redis(unix_socket_path=endpoint, decode_responses=False, socket_timeout=5)
    else:
        client = _FileRedisProtocolEmulator(endpoint)
    memory_cache._redis_client = client
    memory_cache._cache.clear()
    memory_cache._build_locks.clear()

    def builder() -> dict[str, Any]:
        time.sleep(builder_ms / 1000)
        return _payload(payload_bytes, built_by=os.getpid())

    start_event.wait(timeout=10)
    started = time.perf_counter()
    try:
        value = memory_cache.cache_get_or_build(key, builder, ttl=30)
        output_queue.put(
            {
                "pid": os.getpid(),
                "latency_ms": (time.perf_counter() - started) * 1000,
                "built_by": int(value["built_by"]),
                "digest": hashlib.sha256(memory_cache._serialize(value)).hexdigest(),
            }
        )
    finally:
        client.close()


def _benchmark_process_burst(
    client,
    transport: str,
    endpoint: str,
    key: str,
    *,
    builder_ms: float,
    payload_bytes: int,
    workers: int,
) -> dict[str, Any]:
    memory_cache.cache_delete(key)
    context = multiprocessing.get_context("spawn")
    start_event = context.Event()
    output_queue = context.Queue()
    processes = [
        context.Process(
            target=_process_cache_call,
            args=(transport, endpoint, key, builder_ms, payload_bytes, start_event, output_queue),
        )
        for _ in range(workers)
    ]
    for process in processes:
        process.start()
    wall_started = time.perf_counter()
    start_event.set()
    for process in processes:
        process.join(timeout=20)
    wall_ms = (time.perf_counter() - wall_started) * 1000

    failed = [process for process in processes if process.is_alive() or process.exitcode != 0]
    for process in failed:
        if process.is_alive():
            process.terminate()
            process.join(timeout=3)
    if failed:
        raise RuntimeError(
            "isolated worker failure: "
            + ", ".join(f"pid={p.pid} exit={p.exitcode}" for p in failed)
        )

    rows = [output_queue.get(timeout=3) for _ in range(workers)]
    output_queue.close()
    latencies = [float(row["latency_ms"]) for row in rows]
    builder_pids = {int(row["built_by"]) for row in rows}
    digests = {str(row["digest"]) for row in rows}
    return {
        "key": key,
        "worker_processes": workers,
        "unique_builder_processes": len(builder_pids),
        "builder_pids_redacted_count": len(builder_pids),
        "unique_result_digests": len(digests),
        "wall_ms_including_spawn_ready": round(wall_ms, 4),
        "latency": _latency_summary(latencies),
        "redis_ttl_sec_after": int(client.ttl(memory_cache._full_key(key))),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    with _isolated_cache_transport() as (
        client,
        endpoint,
        redis_version,
        transport,
        redis_startup_error,
    ):
        _configure_worker_cache(client)
        client.flushdb()

        cold_warm = [
            _benchmark_cold_warm(
                client,
                key,
                builder_ms=args.builder_ms,
                payload_bytes=args.payload_bytes,
                warm_samples=args.warm_samples,
            )
            for key in STRATEGY_KEYS
        ]
        thread_burst = _benchmark_thread_burst(
            STRATEGY_KEYS[0] + ":thread-burst",
            builder_ms=args.builder_ms,
            payload_bytes=args.payload_bytes,
            workers=args.thread_workers,
        )
        process_burst = _benchmark_process_burst(
            client,
            transport,
            endpoint,
            STRATEGY_KEYS[1] + ":process-burst",
            builder_ms=args.builder_ms,
            payload_bytes=args.payload_bytes,
            workers=args.process_workers,
        )
        live_lock_keys = list(
            client.scan_iter(match=memory_cache._full_key("build_lock:vkpi_strategy:*"))
        )

        passed = (
            all(row["builder_calls"] == 1 for row in cold_warm)
            and all(row["cold_to_warm_p95_speedup"] > 1 for row in cold_warm)
            and thread_burst["builder_calls"] == 1
            and thread_burst["unique_result_digests"] == 1
            and process_burst["unique_builder_processes"] == 1
            and process_burst["unique_result_digests"] == 1
            and not live_lock_keys
        )

        return {
            "schema_version": "vkpi_strategy_cache_component_benchmark_v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "passed": passed,
            "scope": "isolated_component_benchmark_with_synthetic_read_builder",
            "strategy_keys": list(STRATEGY_KEYS),
            "settings": {
                "builder_ms": args.builder_ms,
                "payload_bytes": args.payload_bytes,
                "warm_samples_per_key": args.warm_samples,
                "thread_workers": args.thread_workers,
                "process_workers": args.process_workers,
                "cache_ttl_sec": 30,
                "distributed_lock_lease_sec": memory_cache._BUILD_LOCK_LEASE_SEC,
                "distributed_lock_blocking_timeout_sec": memory_cache._BUILD_LOCK_BLOCKING_TIMEOUT_SEC,
            },
            "runtime": {
                "python": sys.version.split()[0],
                "redis_server": redis_version,
                "transport": transport,
                "real_redis_startup_error": redis_startup_error,
            },
            "cold_warm": cold_warm,
            "thread_singleflight": thread_burst,
            "process_singleflight": process_burst,
            "live_build_lock_keys_after": len(live_lock_keys),
            "safety": {
                "postgres_opened": False,
                "running_backend_called": False,
                "provider_called": False,
                "business_data_written": False,
                "isolated_redis_persistence": False,
            },
            "limitations": [
                "Synthetic sleep models expensive read work; it does not measure Category Tracks or Industry Benchmark SQL/algorithm time.",
                (
                    "Sandbox policy blocked Unix-socket bind, so process singleflight used a file/flock Redis protocol emulator; "
                    "Redis lease mechanics are covered by unit tests, not this timing result."
                    if transport != "private_temporary_unix_socket_redis"
                    else "The temporary Unix-socket Redis omits production network and shared-host contention."
                ),
                "This proves cache semantics in the current code only; it does not prove the running backend has loaded these changes.",
            ],
        }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--builder-ms", type=float, default=250.0)
    parser.add_argument("--payload-bytes", type=int, default=32768)
    parser.add_argument("--warm-samples", type=int, default=500)
    parser.add_argument("--thread-workers", type=int, default=32)
    parser.add_argument("--process-workers", type=int, default=8)
    args = parser.parse_args()
    if args.builder_ms <= 0 or args.builder_ms > 10_000:
        parser.error("--builder-ms must be within (0, 10000]")
    if args.payload_bytes < 0 or args.payload_bytes > 5_000_000:
        parser.error("--payload-bytes must be within [0, 5000000]")
    if args.warm_samples < 1 or args.warm_samples > 100_000:
        parser.error("--warm-samples must be within [1, 100000]")
    if args.thread_workers < 1 or args.thread_workers > 256:
        parser.error("--thread-workers must be within [1, 256]")
    if args.process_workers < 2 or args.process_workers > 32:
        parser.error("--process-workers must be within [2, 32]")
    return args


if __name__ == "__main__":
    report = run(_parse_args())
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(0 if report["passed"] else 1)
