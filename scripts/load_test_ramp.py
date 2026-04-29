#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import math
import os
import random
import time
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

try:
    import aiohttp
except Exception as exc:  # pragma: no cover
    raise SystemExit(
        "aiohttp is required for load_test_2000.py. Install dependencies from requirements.txt first."
    ) from exc

from runtime_env import apply_runtime_env
from stdout_utils import out, out_json

apply_runtime_env()

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "runtime" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

PUBLIC_BASE = os.environ.get("LOAD_TEST_PUBLIC_BASE", "http://127.0.0.1:8101").rstrip("/")
ADMIN_BASE = os.environ.get("LOAD_TEST_ADMIN_BASE", "http://127.0.0.1:8102").rstrip("/")
CONCURRENCY = int(os.environ.get("LOAD_TEST_CONCURRENCY", "2200"))
TOTAL_REQUESTS = int(os.environ.get("LOAD_TEST_TOTAL_REQUESTS", str(CONCURRENCY)))
TIMEOUT_SEC = float(os.environ.get("LOAD_TEST_TIMEOUT_SEC", "20"))
LOAD_TEST_INCLUDE_ADMIN = os.environ.get("LOAD_TEST_INCLUDE_ADMIN", "1").strip().lower() not in {
    "0",
    "false",
    "no",
}
RAMP_PHASES = [
    max(1, int(part.strip()))
    for part in os.environ.get("LOAD_TEST_PHASES", "100,250,500,1000,1500,2000").split(",")
    if part.strip()
]
REQUESTS_PER_PHASE = int(os.environ.get("LOAD_TEST_REQUESTS_PER_PHASE", "600"))
PHASE_PAUSE_SEC = float(os.environ.get("LOAD_TEST_PHASE_PAUSE_SEC", "1.0"))
ADMIN_EMAIL = os.environ.get("LOAD_TEST_ADMIN_EMAIL", "admin@viltrox.com").strip()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "AdminPass123!").strip()


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, math.ceil((pct / 100.0) * len(ordered)) - 1))
    return ordered[idx]


async def login_admin(session: aiohttp.ClientSession) -> str:
    async with session.post(
        f"{ADMIN_BASE}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
    ) as response:
        payload = await response.json(content_type=None)
        if response.status >= 400 or payload.get("status") != "success" or not payload.get("token"):
            raise RuntimeError(f"Admin login failed: {response.status} {payload}")
        return str(payload["token"])


def build_plan(admin_token: str | None = None) -> list[dict[str, object]]:
    plan: list[dict[str, object]] = [
        {
            "name": "public_index",
            "method": "GET",
            "url": f"{PUBLIC_BASE}/",
            "headers": {},
            "weight": 0.28,
        },
        {
            "name": "public_health",
            "method": "GET",
            "url": f"{PUBLIC_BASE}/health",
            "headers": {},
            "weight": 0.18,
        },
        {
            "name": "public_rewards",
            "method": "GET",
            "url": f"{PUBLIC_BASE}/api/rewards",
            "headers": {},
            "weight": 0.16,
        },
        {
            "name": "public_leaderboard",
            "method": "GET",
            "url": f"{PUBLIC_BASE}/api/leaderboard?period=month",
            "headers": {},
            "weight": 0.12,
        },
    ]
    if admin_token:
        plan.extend(
            [
                {
                    "name": "admin_shell",
                    "method": "GET",
                    "url": f"{ADMIN_BASE}/admin/login",
                    "headers": {},
                    "weight": 0.08,
                },
                {
                    "name": "admin_stats",
                    "method": "GET",
                    "url": f"{ADMIN_BASE}/api/admin/stats",
                    "headers": {"Authorization": f"Bearer {admin_token}"},
                    "weight": 0.08,
                },
                {
                    "name": "admin_submissions",
                    "method": "GET",
                    "url": f"{ADMIN_BASE}/api/admin/submissions?limit=20",
                    "headers": {"Authorization": f"Bearer {admin_token}"},
                    "weight": 0.06,
                },
                {
                    "name": "admin_vios_dashboard",
                    "method": "GET",
                    "url": f"{ADMIN_BASE}/api/vios/dashboard",
                    "headers": {"Authorization": f"Bearer {admin_token}"},
                    "weight": 0.04,
                },
            ]
        )
    return plan


def expand_workload(plan: list[dict[str, object]], total: int) -> list[dict[str, object]]:
    weighted: list[dict[str, object]] = []
    for item in plan:
        count = max(1, int(round(total * float(item["weight"]))))
        weighted.extend([item] * count)
    if len(weighted) > total:
        weighted = weighted[:total]
    elif len(weighted) < total:
        weighted.extend(random.choices(plan, k=total - len(weighted)))
    random.shuffle(weighted)
    return weighted


async def fire_one(
    session: aiohttp.ClientSession,
    item: dict[str, object],
    semaphore: asyncio.Semaphore,
    bucket: list[dict[str, object]],
) -> None:
    async with semaphore:
        started = time.perf_counter()
        status_code = 0
        error = ""
        size = 0
        try:
            async with session.request(
                str(item["method"]),
                str(item["url"]),
                headers={**dict(item["headers"]), "Connection": "close"},
            ) as response:
                payload = await response.read()
                status_code = response.status
                size = len(payload)
        except Exception as exc:  # pragma: no cover
            error = exc.__class__.__name__
        latency_ms = (time.perf_counter() - started) * 1000
        bucket.append(
            {
                "name": str(item["name"]),
                "status": status_code,
                "latency_ms": latency_ms,
                "ok": not error and 200 <= status_code < 400,
                "error": error,
                "bytes": size,
            }
        )


def summarize_results(
    results: list[dict[str, object]],
    *,
    elapsed: float,
    concurrency: int,
) -> dict[str, object]:
    latencies = [float(item["latency_ms"]) for item in results]
    successes = [item for item in results if bool(item["ok"])]
    failures = [item for item in results if not bool(item["ok"])]
    by_name: dict[str, list[dict[str, object]]] = defaultdict(list)
    for item in results:
        by_name[str(item["name"])].append(item)
    return {
        "concurrency": concurrency,
        "total_requests": len(results),
        "success_count": len(successes),
        "failure_count": len(failures),
        "success_rate": round((len(successes) / len(results)) if results else 0.0, 4),
        "elapsed_sec": round(elapsed, 3),
        "requests_per_sec": round((len(results) / elapsed) if elapsed > 0 else 0.0, 2),
        "latency_ms": {
            "avg": round(mean(latencies), 2) if latencies else 0.0,
            "p50": round(percentile(latencies, 50), 2),
            "p95": round(percentile(latencies, 95), 2),
            "p99": round(percentile(latencies, 99), 2),
            "max": round(max(latencies), 2) if latencies else 0.0,
        },
        "status_codes": dict(Counter(int(item["status"]) for item in results)),
        "error_types": dict(Counter(str(item["error"]) for item in failures if item["error"])),
        "scenarios": {
            name: {
                "count": len(entries),
                "success_rate": round(sum(1 for item in entries if bool(item["ok"])) / len(entries), 4) if entries else 0.0,
                "avg_ms": round(mean(float(item["latency_ms"]) for item in entries), 2) if entries else 0.0,
                "p95_ms": round(percentile([float(item["latency_ms"]) for item in entries], 95), 2) if entries else 0.0,
            }
            for name, entries in sorted(by_name.items())
        },
    }


async def main() -> None:
    timeout = aiohttp.ClientTimeout(total=TIMEOUT_SEC)
    connector = aiohttp.TCPConnector(limit=0, limit_per_host=0, ssl=False, force_close=True, enable_cleanup_closed=True)

    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        admin_token = await login_admin(session) if LOAD_TEST_INCLUDE_ADMIN else None
        plan = build_plan(admin_token)
        phase_results: list[dict[str, object]] = []
        for phase_concurrency in RAMP_PHASES:
            phase_total = max(phase_concurrency, REQUESTS_PER_PHASE)
            workload = expand_workload(plan, phase_total)
            started = time.perf_counter()
            results: list[dict[str, object]] = []
            semaphore = asyncio.Semaphore(phase_concurrency)
            await asyncio.gather(*(fire_one(session, item, semaphore, results) for item in workload))
            elapsed = time.perf_counter() - started
            phase_summary = summarize_results(results, elapsed=elapsed, concurrency=phase_concurrency)
            phase_results.append(phase_summary)
            if phase_concurrency != RAMP_PHASES[-1]:
                await asyncio.sleep(PHASE_PAUSE_SEC)

    summary = {
        "public_base": PUBLIC_BASE,
        "admin_base": ADMIN_BASE,
        "target_max_concurrency": CONCURRENCY,
        "phase_request_budget": REQUESTS_PER_PHASE,
        "phases": phase_results,
    }

    stamp = time.strftime("%Y%m%d-%H%M%S")
    report_path = LOG_DIR / f"load-test-{stamp}.json"
    report_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    out_json(summary, indent=2)
    out(f"\nreport_path={report_path}")


if __name__ == "__main__":
    asyncio.run(main())
