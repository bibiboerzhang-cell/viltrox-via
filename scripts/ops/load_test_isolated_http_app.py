"""Memory-only ASGI application used by the isolated HTTP load harness."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from collections import Counter
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qs


KNOWN_PATHS = ("/fixture/shell", "/fixture/read", "/fixture/aggregate")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _percentile(values: Sequence[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * pct / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _percentiles(values: Sequence[float]) -> dict[str, float | None]:
    def rounded(value: float | None) -> float | None:
        return round(value, 3) if value is not None else None

    return {
        "p50": rounded(_percentile(values, 50)),
        "p95": rounded(_percentile(values, 95)),
        "p99": rounded(_percentile(values, 99)),
        "max": rounded(max(values) if values else None),
    }


def _rate(numerator: int, denominator: int) -> dict[str, int | float | None]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": round(numerator / denominator, 8) if denominator else None,
    }


def _jitter(seed: int, *parts: object) -> float:
    digest = hashlib.sha256(
        ":".join([str(seed), *(str(part) for part in parts)]).encode("utf-8")
    ).digest()
    unit = int.from_bytes(digest[:8], "big") / float(2**64 - 1)
    return 0.9 + unit * 0.2


class TimedResourcePool:
    """An in-process async resource pool with measured queue wait."""

    def __init__(self, name: str, slots: int, contention_threshold_ms: float):
        self.name = name
        self.slots = slots
        self.contention_threshold_ms = contention_threshold_ms
        self._semaphore = asyncio.Semaphore(slots)
        self.active = 0
        self.waiting = 0
        self.max_active = 0
        self.max_waiting = 0
        self.acquisitions = 0
        self.wait_samples_ms: list[float] = []

    async def hold(self, duration_seconds: float) -> None:
        queued_at = time.perf_counter_ns()
        queued = self._semaphore.locked()
        if queued:
            self.waiting += 1
            self.max_waiting = max(self.max_waiting, self.waiting)
        await self._semaphore.acquire()
        acquired_at = time.perf_counter_ns()
        wait_ms = (acquired_at - queued_at) / 1_000_000.0
        if queued:
            self.waiting -= 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.acquisitions += 1
        self.wait_samples_ms.append(wait_ms)
        try:
            await asyncio.sleep(duration_seconds)
        finally:
            self.active -= 1
            self._semaphore.release()

    def snapshot(self) -> dict[str, Any]:
        waits = list(self.wait_samples_ms)
        contended = sum(
            1 for value in waits if value >= self.contention_threshold_ms
        )
        return {
            "resource": self.name,
            "slots": self.slots,
            "acquisitions": self.acquisitions,
            "max_active": self.max_active,
            "max_slot_utilization": round(self.max_active / self.slots, 6),
            "max_waiting": self.max_waiting,
            "contention_rate": _rate(contended, self.acquisitions),
            "wait_ms": {"sample_count": len(waits), **_percentiles(waits)},
        }


class FixtureState:
    def __init__(self, config: Any, *, nonce: str, trial_seed: int):
        self.config = config
        self.nonce = nonce
        self.trial_seed = trial_seed
        self.database = TimedResourcePool(
            "synthetic_database_slots",
            config.database_slots,
            config.contention_threshold_ms,
        )
        self.aggregate = TimedResourcePool(
            "synthetic_aggregate_slots",
            config.aggregate_slots,
            config.contention_threshold_ms,
        )
        self.accepted = 0
        self.completed = 0
        self.inflight = 0
        self.max_inflight = 0
        self.status_codes: Counter[str] = Counter()
        self.service_samples_ms: list[float] = []

    def begin_request(self) -> None:
        self.accepted += 1
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)

    def finish_request(self, *, status: int, service_ms: float) -> None:
        self.completed += 1
        self.inflight -= 1
        self.status_codes[str(status)] += 1
        self.service_samples_ms.append(service_ms)

    def snapshot(self) -> dict[str, Any]:
        services = list(self.service_samples_ms)
        return {
            "requests_accepted": self.accepted,
            "requests_completed": self.completed,
            "requests_inflight_at_snapshot": self.inflight,
            "max_http_handlers_inflight": self.max_inflight,
            "status_codes": dict(sorted(self.status_codes.items())),
            "service_time_ms": {
                "sample_count": len(services),
                **_percentiles(services),
            },
            "resources": {
                "database": self.database.snapshot(),
                "aggregate": self.aggregate.snapshot(),
            },
        }


class InProcessFixtureASGI:
    """Minimal ASGI HTTP app; it owns no listener or external state."""

    def __init__(self, state: FixtureState):
        self.state = state

    async def __call__(
        self,
        scope: Mapping[str, Any],
        _receive: Any,
        send: Any,
    ) -> None:
        began = time.perf_counter_ns()
        status = 500
        self.state.begin_request()
        try:
            if scope.get("type") != "http":
                raise RuntimeError("fixture accepts HTTP ASGI scopes only")
            method = str(scope.get("method", ""))
            path = str(scope.get("path", ""))
            query = parse_qs(
                bytes(scope.get("query_string", b"")).decode("ascii"),
                keep_blank_values=True,
            )
            headers = {
                key.decode("latin-1").lower(): value.decode("latin-1")
                for key, value in scope.get("headers", [])
            }
            status, payload = await self._response(method, path, query, headers)
            encoded = _canonical_json(payload)
            await send(
                {
                    "type": "http.response.start",
                    "status": status,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(encoded)).encode("ascii")),
                        (b"cache-control", b"no-store"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": encoded})
        finally:
            elapsed_ms = (time.perf_counter_ns() - began) / 1_000_000.0
            self.state.finish_request(status=status, service_ms=elapsed_ms)

    async def _response(
        self,
        method: str,
        path: str,
        query: Mapping[str, list[str]],
        headers: Mapping[str, str],
    ) -> tuple[int, Mapping[str, Any]]:
        if method != "GET":
            return 405, {"status": "method_not_allowed"}
        if path not in KNOWN_PATHS:
            return 404, {"status": "not_found"}
        if query.get("nonce") != [self.state.nonce]:
            return 403, {"status": "fixture_nonce_rejected"}
        vu_id = int(headers.get("x-vkpi-fixture-vu", "-1"))
        request_index = int(headers.get("x-vkpi-fixture-request-index", "-1"))
        if vu_id < 0 or request_index < 0:
            return 400, {"status": "synthetic_identity_missing"}
        jitter = _jitter(self.state.trial_seed, vu_id, request_index, path)
        if path == "/fixture/shell":
            await asyncio.sleep(self.state.config.shell_service_ms * jitter / 1_000.0)
        elif path == "/fixture/read":
            await self.state.database.hold(
                self.state.config.read_service_ms * jitter / 1_000.0
            )
        else:
            await self.state.aggregate.hold(
                self.state.config.aggregate_service_ms * jitter / 1_000.0
            )
        return 200, {"status": "success", "fixture": True, "endpoint": path}
