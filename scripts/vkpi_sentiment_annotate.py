#!/usr/bin/env python3
"""V0g 情绪批注手动点火入口 —— 默认 dry-run(零 LLM、零落库),--live 才真跑。

用法(仓库根目录,务必用 .venv 解释器):
  # 1) 先 dry-run:看待处理量 / 计划调用数 / 预估成本(不烧一分钱)
  .venv/bin/python -m scripts.vkpi_sentiment_annotate --batch 50

  # 2) 满意后小批量真跑 50 条(走 llm_gateway 预算闸 + 代理;记得 source runtime_env.sh 带上 HTTPS_PROXY)
  .venv/bin/python -m scripts.vkpi_sentiment_annotate --batch 50 --live

  # 3) 全量成本测算(只读)
  .venv/bin/python -m scripts.vkpi_sentiment_annotate --estimate-backlog

参数:
  --batch N   本轮处理条数(默认 50;再被 env VKPI_SENTIMENT_ANNOTATE_MAX_PER_RUN=200 硬钳)
  --pack N    单次 LLM 调用打包条数(默认 env/40,封顶 50)
  --live      真调 LLM 并落库(缺省即 dry-run)
  --dry-run   显式 dry-run(与 --live 互斥,防手滑)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="V0g packed comment sentiment annotate (default dry-run)")
    parser.add_argument("--batch", type=int, default=50, help="comments to process this run (default 50)")
    parser.add_argument("--pack", type=int, default=0, help="comments per LLM call (default env/40, max 50)")
    parser.add_argument("--live", action="store_true", help="actually call the LLM and write to DB")
    parser.add_argument("--dry-run", action="store_true", help="explicit dry-run (mutually exclusive with --live)")
    parser.add_argument("--estimate-backlog", action="store_true", help="read-only full-backlog cost estimate")
    return parser.parse_args(argv)


def run_from_args(args: argparse.Namespace) -> dict:
    if args.pack:
        os.environ["VKPI_SENTIMENT_ANNOTATE_PACK_SIZE"] = str(int(args.pack))
    from app.domains.market import sentiment_annotate

    if args.estimate_backlog:
        return sentiment_annotate.full_backlog_estimate()
    return sentiment_annotate.annotate_batch(int(args.batch), dry_run=not args.live)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.dry_run and args.live:
        print("--dry-run 与 --live 互斥:去掉一个再来。", file=sys.stderr)
        return 2
    result = run_from_args(args)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
