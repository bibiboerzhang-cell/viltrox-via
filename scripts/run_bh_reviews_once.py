#!/usr/bin/env python3
"""手动触发一次 B&H 产品评论抓取(powerai/bhphotovideo-product-reviews-scraper)。

背景(用户令 2026-07-02):付费 search actor 抓的产品列表与库内 100% 重复零增量,
search 已停;竞品口碑改走本脚本 + reviews actor,落 vkpi_bh_reviews(迁移 207)。

用法(必须用 .venv 解释器,裸 python3 缺依赖):
    .venv/bin/python scripts/run_bh_reviews_once.py --dry-run          # 零成本:只看会选哪些产品 + 成本预估
    BH_REVIEWS_ENABLED=1 .venv/bin/python scripts/run_bh_reviews_once.py --limit 1   # 首次付费小跑验证字段
    BH_REVIEWS_ENABLED=1 .venv/bin/python scripts/run_bh_reviews_once.py --limit 20  # 全量(硬上限 20)

约束:
  - 不接任何 cron,只手动跑。
  - BH_REVIEWS_ENABLED 默认 0(烧钱动作默认关);不设 1 时 fetch 直接拒跑。
  - 单次上限 20 个产品(每个产品 = 1 次付费 actor call)。
"""
from __future__ import annotations

from stdout_utils import out

import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - local dependency guard.
    load_dotenv = None  # type: ignore[assignment]

if load_dotenv is not None:
    # 绝对路径载 .env(不依赖 cwd)
    load_dotenv(ROOT / ".env")


def _est_cost_per_call() -> float:
    """成本预估口径:每个产品 1 次付费 actor call。

    reviews actor 单价未实测,默认按 search actor 同家口径 ~$2/次 保守估;
    实测后可用 env BH_REVIEWS_EST_COST_PER_CALL_USD 校准。实际成本以
    fetch 返回的 actor_cost_usd(Apify run usageTotalUsd,已记账
    vkpi_ai_cost_ledger provider:apify)为准。
    """
    try:
        return float(os.environ.get("BH_REVIEWS_EST_COST_PER_CALL_USD", "2.0"))
    except (TypeError, ValueError):
        return 2.0


async def _main() -> int:
    parser = argparse.ArgumentParser(description="Run one manual B&H product reviews fetch (paid, gated).")
    parser.add_argument("--limit", type=int, default=20, help="最多抓多少个产品(硬上限 20,默认 20)")
    parser.add_argument("--per-product", type=int, default=30, help="每个产品最多抓多少条评论(默认 30)")
    parser.add_argument("--urls", type=str, default="", help="逗号分隔的产品 URL,给了就不从 bh_products 自选")
    parser.add_argument("--dry-run", action="store_true", help="零成本:只打印会选哪些产品与成本预估,不调 actor")
    args = parser.parse_args()

    limit = max(1, min(args.limit, 20))
    urls = [u.strip() for u in args.urls.split(",") if u.strip()] or None

    from app.services.intelligence.bh_repository import select_bh_review_targets

    if urls:
        targets = [{"url": u, "title": "(explicit url)", "bucket": "explicit"} for u in urls[:limit]]
    else:
        targets = select_bh_review_targets(limit=limit)

    est = _est_cost_per_call()
    out(f"[plan] products={len(targets)} per_product_limit={args.per_product}")
    for t in targets:
        out(f"  - [{t.get('bucket', '-')}] {str(t.get('title', ''))[:60]} | {t.get('url', '')}")
    out(f"[cost] 预估 = {len(targets)} 产品 x ~${est:.2f}/actor call = ~${len(targets) * est:.2f}"
          f"(口径:同家 search actor ~$2/次 保守估,env BH_REVIEWS_EST_COST_PER_CALL_USD 可校准;实际以 Apify usageTotalUsd 记账为准)")

    if args.dry_run:
        out("[dry-run] 不调 actor,零成本退出。")
        return 0

    if os.environ.get("BH_REVIEWS_ENABLED", "0").strip().lower() not in {"1", "true", "yes", "on"}:
        out("[blocked] BH_REVIEWS_ENABLED 默认 0(烧钱动作默认关)。确认要花钱就:")
        out("          BH_REVIEWS_ENABLED=1 .venv/bin/python scripts/run_bh_reviews_once.py --limit 1")
        return 1

    from app.services.jobs.queue import build_job_queue

    queue = build_job_queue()
    if queue is None:
        out("[blocked] durable job queue unavailable; paid review fetch was not started")
        return 1
    selected_urls = [str(t.get("url") or "").strip() for t in targets if str(t.get("url") or "").strip()]
    digest = hashlib.sha256("\n".join(selected_urls).encode("utf-8")).hexdigest()[:16]
    try:
        task_id = await queue.enqueue(
            "intel_bh_reviews",
            {
                "product_urls": selected_urls,
                "limit_per_product": max(1, min(int(args.per_product), 100)),
                "max_products": limit,
                "requested_by": "cli",
            },
            lock_key=f"intel_bh_reviews:{digest}",
            timeout_seconds=3600,
        )
    finally:
        await queue.close()
    out("[queued]")
    out(json.dumps({"status": "queued", "job_id": task_id}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
