"""concurrency_smoke.py — 只读并发冒烟(2026-07-22 多并发地基)。

asyncio + httpx 模拟 N 个浏览器用户并发轮询只读 GET 端点,输出 p50/p95/错误率。

安全红线:本脚本只发 GET,绝不打写端点;默认端点全是列表/汇总只读面。

用法(本地栈):
  .venv/bin/python scripts/ops/concurrency_smoke.py \
      --base-url http://127.0.0.1:8102 --users 10 --rounds 5 --token "$TOK"

  --token 缺省时读 VKPI_SMOKE_TOKEN 环境变量;两者都空则只打 /health(免鉴权)。
  --json-out PATH 可落一份机器可读汇总(供闸/报告消费)。
退出码:0=错误率 ≤ --max-error-rate;1=超阈值或全部失败。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import random
import sys
import time
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger("concurrency_smoke")

# 只读 GET 端点面板(与 cockpit 浏览器轮询同款形态);/health 免鉴权,其余需 Bearer。
DEFAULT_AUTH_ENDPOINTS = (
    "/api/admin/kol/kols?limit=50",
    "/api/admin/kol/candidates?limit=50",
    "/api/admin/kol/dashboard/staff-performance",
)
HEALTH_ENDPOINT = "/health"


@dataclass
class EndpointStats:
    latencies_ms: list[float] = field(default_factory=list)
    errors: int = 0
    statuses: dict[int, int] = field(default_factory=dict)


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return float("nan")
    rank = (pct / 100.0) * (len(sorted_values) - 1)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return sorted_values[low]
    frac = rank - low
    return sorted_values[low] * (1 - frac) + sorted_values[high] * frac


async def _user_loop(
    user_id: int,
    client: httpx.AsyncClient,
    endpoints: list[str],
    rounds: int,
    stats: dict[str, EndpointStats],
    lock: asyncio.Lock,
) -> None:
    # 错峰起跑,模拟真实用户而非整齐划一的压测波
    await asyncio.sleep(random.uniform(0.0, 0.3))
    for _ in range(rounds):
        for endpoint in endpoints:
            started = time.perf_counter()
            status = 0
            ok = False
            try:
                resp = await client.get(endpoint)
                status = resp.status_code
                ok = 200 <= status < 300
            except httpx.HTTPError as exc:
                logger.debug("user=%d endpoint=%s transport error: %s", user_id, endpoint, exc)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            async with lock:
                bucket = stats[endpoint]
                bucket.statuses[status] = bucket.statuses.get(status, 0) + 1
                if ok:
                    bucket.latencies_ms.append(elapsed_ms)
                else:
                    bucket.errors += 1


async def run_smoke(args: argparse.Namespace) -> int:
    token = args.token or os.environ.get("VKPI_SMOKE_TOKEN", "")
    if args.endpoints:
        endpoints = [e.strip() for e in args.endpoints.split(",") if e.strip()]
    else:
        endpoints = [HEALTH_ENDPOINT]
        if token:
            endpoints.extend(DEFAULT_AUTH_ENDPOINTS)
        else:
            logger.warning("无 token(--token/VKPI_SMOKE_TOKEN),只打 /health;鉴权端点被跳过")
    for endpoint in endpoints:
        if not endpoint.startswith("/"):
            logger.error("端点必须以 / 开头: %s", endpoint)
            return 1

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    stats: dict[str, EndpointStats] = {e: EndpointStats() for e in endpoints}
    lock = asyncio.Lock()
    limits = httpx.Limits(max_connections=args.users * 2, max_keepalive_connections=args.users)
    timeout = httpx.Timeout(args.timeout_sec)

    wall_started = time.perf_counter()
    async with httpx.AsyncClient(
        base_url=args.base_url, headers=headers, limits=limits, timeout=timeout
    ) as client:
        await asyncio.gather(
            *(
                _user_loop(uid, client, endpoints, args.rounds, stats, lock)
                for uid in range(args.users)
            )
        )
    wall_seconds = time.perf_counter() - wall_started

    total_requests = 0
    total_errors = 0
    all_latencies: list[float] = []
    report: dict[str, object] = {
        "base_url": args.base_url,
        "users": args.users,
        "rounds": args.rounds,
        "wall_seconds": round(wall_seconds, 3),
        "endpoints": {},
    }
    for endpoint, bucket in stats.items():
        count = len(bucket.latencies_ms) + bucket.errors
        total_requests += count
        total_errors += bucket.errors
        all_latencies.extend(bucket.latencies_ms)
        ordered = sorted(bucket.latencies_ms)
        p50 = _percentile(ordered, 50)
        p95 = _percentile(ordered, 95)
        err_rate = (bucket.errors / count) if count else 0.0
        logger.info(
            "endpoint=%s n=%d err=%d(%.1f%%) p50=%.1fms p95=%.1fms max=%.1fms statuses=%s",
            endpoint, count, bucket.errors, err_rate * 100,
            p50, p95, (ordered[-1] if ordered else float("nan")),
            dict(sorted(bucket.statuses.items())),
        )
        report["endpoints"][endpoint] = {  # type: ignore[index]
            "count": count,
            "errors": bucket.errors,
            "error_rate": round(err_rate, 4),
            "p50_ms": round(p50, 1) if ordered else None,
            "p95_ms": round(p95, 1) if ordered else None,
            "max_ms": round(ordered[-1], 1) if ordered else None,
            "statuses": {str(k): v for k, v in sorted(bucket.statuses.items())},
        }

    overall_err_rate = (total_errors / total_requests) if total_requests else 1.0
    ordered_all = sorted(all_latencies)
    overall_p50 = _percentile(ordered_all, 50)
    overall_p95 = _percentile(ordered_all, 95)
    logger.info(
        "OVERALL users=%d requests=%d wall=%.1fs rps=%.1f err_rate=%.2f%% p50=%.1fms p95=%.1fms",
        args.users, total_requests, wall_seconds,
        (total_requests / wall_seconds) if wall_seconds > 0 else 0.0,
        overall_err_rate * 100, overall_p50, overall_p95,
    )
    report["overall"] = {
        "requests": total_requests,
        "errors": total_errors,
        "error_rate": round(overall_err_rate, 4),
        "p50_ms": round(overall_p50, 1) if ordered_all else None,
        "p95_ms": round(overall_p95, 1) if ordered_all else None,
        "rps": round(total_requests / wall_seconds, 1) if wall_seconds > 0 else 0.0,
    }

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        logger.info("json 汇总已写 %s", args.json_out)

    if overall_err_rate > args.max_error_rate:
        logger.error("错误率 %.2f%% 超过阈值 %.2f%%", overall_err_rate * 100, args.max_error_rate * 100)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="只读并发冒烟:N 用户并发打 GET 端点,报 p50/p95/错误率")
    parser.add_argument("--base-url", default="http://127.0.0.1:8102", help="目标栈根地址(默认本地 8102)")
    parser.add_argument("--users", type=int, default=10, help="并发模拟用户数(默认 10)")
    parser.add_argument("--rounds", type=int, default=5, help="每用户轮询轮数(默认 5;每轮打全部端点各一次)")
    parser.add_argument("--token", default="", help="Bearer token(缺省读 VKPI_SMOKE_TOKEN)")
    parser.add_argument("--endpoints", default="", help="逗号分隔覆盖端点列表(只允许只读 GET 路径)")
    parser.add_argument("--timeout-sec", type=float, default=30.0, help="单请求超时秒(默认 30)")
    parser.add_argument("--max-error-rate", type=float, default=0.01, help="错误率阈值(默认 0.01)")
    parser.add_argument("--json-out", default="", help="机器可读汇总输出路径(可选)")
    args = parser.parse_args()
    if not args.base_url.startswith(("http://", "https://")):
        logger.error("--base-url 必须是 http(s) 地址")
        return 2
    if args.users < 1 or args.rounds < 1:
        logger.error("--users/--rounds 必须 >= 1")
        return 2
    return asyncio.run(run_smoke(args))


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )
    # httpx 每请求一行 INFO 会淹没汇总,压到 WARNING
    logging.getLogger("httpx").setLevel(logging.WARNING)
    raise SystemExit(main())
