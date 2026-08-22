#!/usr/bin/env python3
"""回填「镜头出镜证据」派生表(vkpi_kol_lens_evidence,迁移 287)。

从 vkpi_analysis_cache(derive_method=video_analysis_final_v1, status=ready)的六层
散文里抽 Viltrox 产品提及,经 vkpi_products 目录(+ products/product_aliases_lens 口语
别名表)归一后落表;同时写扫描账本 vkpi_kol_lens_evidence_scan(含零提及行),让聚合
端点能如实报覆盖率。v_relevance(confirmed / likely / none)是只读投影,不落表。

真值边界 / 红线:
* 只写 vkpi_kol_lens_evidence / vkpi_kol_lens_evidence_scan 两张派生表;
  绝不触碰 viltrox_fit_score / rule_v0 / 任何 KOL 池或 evidence 列。
* 幂等:按 (cache_id, mention_norm) UPSERT;扫描账本记抽取器版本 + 缓存时间戳,
  未变的缓存行默认跳过(--force 全量重扫;抽取器版本升级后旧账本行自动判定需重扫)。
* 诚实:目录匹配不到的提及标 unresolved 保留原文,绝不杜撰 SKU;零 LLM、零外调。
* 默认 dry-run(只打印统计);--apply 才真正写表。

对照模式(prod 零写):
  --cache-id 12 --cache-id 30    只看这些缓存行,逐行打印抽取轨迹(锚点 / 截取 / 归一 / v_relevance
                                  + 扫描账本旧结果);可加 --trace-out 落 JSON 供另一侧 --diff。
  --trace-out FILE               dry-run 同时导出抽取器实际消费的文本(prod 上跑,只读);
  --replay FILE                  本地用当前抽取器 + 本地目录重放导出文本:would_write / unresolved /
                                  v_relevance 分布 + 账本状态迁移(= 新代码在 prod 会抽到什么);
  --diff FILE                    与另一侧 --cache-id --trace-out 的轨迹按 cache_id 对照。

用法(本地 / 隔离库验证;prod 执行留给主会话,见 docs/runbooks/LENS_EVIDENCE_RESCAN.md):
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
from typing import Any

# 统计 JSON 走专属 logger 直出 stdout(不碰 root,应用自身日志仍走 stderr 不混进 JSON)。
LOG = logging.getLogger("viltrox.ops.backfill_lens_evidence")
LOG.setLevel(logging.INFO)
LOG.propagate = False
_STDOUT = logging.StreamHandler(stream=sys.stdout)
_STDOUT.setFormatter(logging.Formatter("%(message)s"))
LOG.addHandler(_STDOUT)

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def _emit(payload: Any) -> None:
    LOG.info(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _dump_json(path: str, payload: Any) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, default=str)


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="真正写表(缺省 dry-run 只打印统计)")
    parser.add_argument("--dry-run", action="store_true", help="显式 dry-run(与缺省等价;与 --apply 互斥)")
    parser.add_argument("--limit", type=int, default=5000, help="本次最多扫描的缓存行数(默认 5000)")
    parser.add_argument("--force", action="store_true", help="忽略扫描账本,全量重扫(幂等)")
    parser.add_argument("--cache-id", type=int, action="append", default=[], help="只看这些缓存行并打印抽取轨迹(可重复;强制 dry-run)")
    parser.add_argument("--trace-out", default="", help="把轨迹 / 导出文本写到此 JSON 文件(只读导出)")
    parser.add_argument("--replay", default="", help="重放另一侧 --trace-out 导出的文本(不读缓存表,只读目录)")
    parser.add_argument("--diff", default="", help="与另一侧 --cache-id --trace-out 的轨迹按 cache_id 对照")
    args = parser.parse_args(argv)
    if args.apply and (args.dry_run or args.cache_id or args.replay or args.diff):
        parser.error("--apply 不能与 --dry-run / --cache-id / --replay / --diff 同用(对照模式永远只读)")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)

    from app.db.connection import get_conn, table_exists
    from app.domains.kol import lens_evidence as extractor
    from app.domains.kol import lens_evidence_store

    conn = get_conn()
    if args.replay:
        records = _load_json(args.replay)
        if not isinstance(records, list):
            _emit({"status": "blocked", "reason": "replay_file_not_a_list"})
            return 2
        stats = lens_evidence_store.replay_trace(records, extractor.load_catalog_index(conn))
        stats["status"] = "replay"
        stats["source_file"] = args.replay
        _emit(stats)
        return 0

    if not table_exists("vkpi_kol_lens_evidence"):
        _emit({"status": "blocked", "reason": "migration_287_not_applied"})
        return 2

    if args.cache_id:
        traces = lens_evidence_store.explain_cache_rows(conn, cache_ids=args.cache_id)
        report: dict[str, Any] = {"status": "explain", "cache_ids": sorted(set(args.cache_id)), "traces": traces}
        if args.diff:
            other = _load_json(args.diff)
            report["diff"] = lens_evidence_store.diff_traces(traces, other.get("traces") if isinstance(other, dict) else other)
        if args.trace_out:
            _dump_json(args.trace_out, report)
            report["trace_out"] = args.trace_out
        _emit(report)
        return 0

    stats = lens_evidence_store.backfill_lens_evidence(
        conn,
        apply=bool(args.apply),
        limit=max(1, int(args.limit)),
        force=bool(args.force),
    )
    stats["status"] = "applied" if args.apply else "dry_run"
    if args.trace_out and not args.apply:
        records = lens_evidence_store.export_trace(conn, limit=max(1, int(args.limit)))
        _dump_json(args.trace_out, records)
        stats["trace_out"] = args.trace_out
        stats["trace_records"] = len(records)
    _emit(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
