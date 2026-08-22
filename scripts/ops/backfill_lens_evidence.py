#!/usr/bin/env python3
"""回填「镜头出镜证据」派生表(vkpi_kol_lens_evidence,迁移 287)。

从 vkpi_analysis_cache(derive_method=video_analysis_final_v1, status=ready)的六层
散文里抽 Viltrox 产品提及,经 vkpi_products 目录归一后落表;同时写扫描账本
vkpi_kol_lens_evidence_scan(含零提及行),让聚合端点能如实报覆盖率。

真值边界 / 红线:
* 只写 vkpi_kol_lens_evidence / vkpi_kol_lens_evidence_scan 两张派生表;
  绝不触碰 viltrox_fit_score / rule_v0 / 任何 KOL 池或 evidence 列。
* 幂等:按 (cache_id, mention_norm) UPSERT;扫描账本记抽取器版本 + 缓存时间戳,
  未变的缓存行默认跳过(--force 全量重扫)。
* 诚实:目录匹配不到的提及标 unresolved 保留原文,绝不杜撰 SKU;零 LLM、零外调。
* 默认 dry-run(只打印统计);--apply 才真正写表。

用法(本地 / 隔离库验证;prod 执行留给主会话):
  PYTHONPATH=.:scripts:backend APP_ROLE=admin-web ENABLE_SCHEDULER=0 \\
    .venv/bin/python scripts/ops/backfill_lens_evidence.py
  ... backfill_lens_evidence.py --apply [--limit 5000] [--force]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s", stream=sys.stderr)

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="真正写表(缺省 dry-run 只打印统计)")
    parser.add_argument("--limit", type=int, default=5000, help="本次最多扫描的缓存行数(默认 5000)")
    parser.add_argument("--force", action="store_true", help="忽略扫描账本,全量重扫")
    args = parser.parse_args(argv)

    from app.db.connection import get_conn, table_exists
    from app.domains.kol import lens_evidence_store

    if not table_exists("vkpi_kol_lens_evidence"):
        print(json.dumps({"status": "blocked", "reason": "migration_287_not_applied"}, ensure_ascii=False))
        return 2
    conn = get_conn()
    stats = lens_evidence_store.backfill_lens_evidence(
        conn,
        apply=bool(args.apply),
        limit=max(1, int(args.limit)),
        force=bool(args.force),
    )
    stats["status"] = "applied" if args.apply else "dry_run"
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
