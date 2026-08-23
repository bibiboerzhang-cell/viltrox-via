#!/usr/bin/env python3
"""幂等回填 vkpi_comments.language_detected(优化波 B · D 车道;零 LLM、零采集)。

只处理 language_detected 为空 / 'und' / 'unknown' 的评论(--force 则全量重判,但只在判出结果时覆盖,
绝不把已有语种抹成空),判定走 app.domains.comments.language_detection.language_detect
(保守口径:短文本 / 纯 emoji / 混合字系 -> None,诚实留空)。
默认 dry-run:只统计「能判出多少」并给出覆盖率预估;--apply 才按批 UPDATE。
红线:只写 language_detected 一列,不触 viltrox_fit_score / rule_v0。

用法(本地 / 隔离库):
  PYTHONPATH=.:scripts:backend APP_ROLE=admin-web ENABLE_SCHEDULER=0 \\
    .venv/bin/python scripts/ops/backfill_comment_language.py [--limit 100000] [--platform tiktok]
  ... backfill_comment_language.py --apply [--batch 500]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path
from typing import Any

LOG = logging.getLogger("viltrox.ops.backfill_comment_language")
LOG.setLevel(logging.INFO)
LOG.propagate = False
_STDOUT = logging.StreamHandler(stream=sys.stdout)
_STDOUT.setFormatter(logging.Formatter("%(message)s"))
LOG.addHandler(_STDOUT)

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

EMPTY_CODES = ("", "und", "unknown")


def _emit(payload: Any) -> None:
    LOG.info(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="真正写列(缺省 dry-run 只统计)")
    parser.add_argument("--dry-run", action="store_true", help="显式 dry-run(与缺省等价;与 --apply 互斥)")
    parser.add_argument("--limit", type=int, default=200000, help="本次最多处理的评论行数(默认 200000)")
    parser.add_argument("--batch", type=int, default=1000, help="每批读取 / 提交的行数(默认 1000)")
    parser.add_argument("--platform", default="", help="只处理该平台")
    parser.add_argument("--force", action="store_true", help="全量重判(只在判出结果时覆盖)")
    parser.add_argument("--sample", type=int, default=8, help="报告里附带的判定样例条数(默认 8,0 关闭)")
    args = parser.parse_args(argv)
    if args.apply and args.dry_run:
        parser.error("--apply 与 --dry-run 互斥")
    return args


def _coverage(conn: Any, platform: str) -> dict[str, Any]:
    where = ""
    params: tuple[Any, ...] = ()
    if platform:
        where = " WHERE platform = ?"
        params = (platform,)
    row = conn.execute(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN language_detected IS NULL OR language_detected IN ('', 'und', 'unknown') THEN 1 ELSE 0 END) AS missing "
        f"FROM vkpi_comments{where}",
        params,
    ).fetchone()
    rec = dict(row or {})
    total = int(rec.get("total") or 0)
    missing = int(rec.get("missing") or 0)
    return {
        "total": total,
        "missing": missing,
        "detected": total - missing,
        "coverage_pct": round(100.0 * (total - missing) / total, 1) if total else 0.0,
    }


def _fetch_batch(conn: Any, *, after_id: int, batch: int, platform: str, force: bool) -> list[dict[str, Any]]:
    where = ["id > ?"]
    params: list[Any] = [int(after_id)]
    if not force:
        where.append("(language_detected IS NULL OR language_detected IN ('', 'und', 'unknown'))")
    if platform:
        where.append("platform = ?")
        params.append(platform)
    params.append(int(batch))
    rows = conn.execute(
        "SELECT id, platform, comment_text, language_detected FROM vkpi_comments "
        f"WHERE {' AND '.join(where)} ORDER BY id LIMIT ?",
        tuple(params),
    ).fetchall()
    return [dict(row) for row in rows]


def run(conn: Any, args: argparse.Namespace) -> dict[str, Any]:
    from app.domains.comments.language_detection import (
        MIN_LETTERS,
        MIN_WORDS,
        language_detect,
        script_profile,
        strip_noise,
    )

    def _why_undetermined(text: str) -> str:
        cleaned = strip_noise(text)
        profile = script_profile(cleaned)
        if not profile["total"]:
            return "no_letters"  # 纯 emoji / 符号 / 空:本来就没有语言
        words = [w for w in cleaned.split() if any(ch.isalpha() for ch in w)]
        if profile["total"] < MIN_LETTERS or len(words) < MIN_WORDS:
            return "too_short"
        return "ambiguous"

    platform = str(args.platform or "").strip().lower()
    before = _coverage(conn, platform)
    stats: dict[str, Any] = {
        "coverage_before": before,
        "scanned": 0,
        "detected": 0,
        "undetermined": 0,
        "undetermined_reason": {"no_letters": 0, "too_short": 0, "ambiguous": 0},
        "unchanged": 0,
        "written": 0,
        "by_language": {},
        "by_platform": {},
        "samples_detected": [],
        "samples_undetermined": [],
        "errors": 0,
    }
    by_language: Counter = Counter()
    by_platform: dict[str, Counter] = {}
    after_id = 0
    remaining = max(1, int(args.limit))
    batch_size = max(1, min(5000, int(args.batch)))
    while remaining > 0:
        rows = _fetch_batch(conn, after_id=after_id, batch=min(batch_size, remaining), platform=platform, force=bool(args.force))
        if not rows:
            break
        updates: list[tuple[str, int]] = []
        for row in rows:
            after_id = int(row["id"])
            remaining -= 1
            stats["scanned"] += 1
            plat = str(row.get("platform") or "unknown")
            bucket = by_platform.setdefault(plat, Counter())
            bucket["scanned"] += 1
            text = str(row.get("comment_text") or "")
            try:
                code = language_detect(text)
            except Exception:
                stats["errors"] += 1
                continue
            if not code:
                stats["undetermined"] += 1
                bucket["undetermined"] += 1
                stats["undetermined_reason"][_why_undetermined(text)] += 1
                if args.sample and len(stats["samples_undetermined"]) < args.sample:
                    stats["samples_undetermined"].append(text[:80])
                continue
            current = str(row.get("language_detected") or "").strip().lower()
            if current == code:
                stats["unchanged"] += 1
                bucket["unchanged"] += 1
                continue
            stats["detected"] += 1
            bucket["detected"] += 1
            by_language[code] += 1
            if args.sample and len(stats["samples_detected"]) < args.sample:
                stats["samples_detected"].append({"lang": code, "text": text[:80]})
            updates.append((code, int(row["id"])))
        if args.apply and updates:
            try:
                for code, comment_id in updates:
                    conn.execute("UPDATE vkpi_comments SET language_detected=? WHERE id=?", (code, comment_id))
                conn.commit()
                stats["written"] += len(updates)
            except Exception:
                stats["errors"] += 1
                LOG.warning("batch update failed up to id=%s", after_id, exc_info=True)
                try:
                    conn.rollback()
                except Exception:
                    pass
        if len(rows) < min(batch_size, remaining + len(rows)):
            break
    stats["by_language"] = dict(by_language.most_common())
    stats["by_platform"] = {plat: dict(counter) for plat, counter in sorted(by_platform.items())}
    projected_detected = before["detected"] + (stats["detected"] if not args.force else 0)
    total = before["total"]
    no_letters = int(stats["undetermined_reason"]["no_letters"])
    stats["coverage_projected"] = {
        "detected": projected_detected,
        "total": total,
        "coverage_pct": round(100.0 * projected_detected / total, 1) if total else 0.0,
        # 有文字的评论口径:纯 emoji / 符号评论本无语言,不计分母
        "text_bearing_total": total - no_letters,
        "text_bearing_coverage_pct": (
            round(100.0 * projected_detected / (total - no_letters), 1) if total > no_letters else 0.0
        ),
    }
    if args.apply:
        stats["coverage_after"] = _coverage(conn, platform)
    return stats


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    from app.db.connection import get_conn, table_exists

    if not table_exists("vkpi_comments"):
        _emit({"status": "blocked", "reason": "vkpi_comments_missing"})
        return 2
    conn = get_conn()
    stats = run(conn, args)
    stats["status"] = "applied" if args.apply else "dry_run"
    stats["write_db"] = bool(args.apply)
    _emit(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
