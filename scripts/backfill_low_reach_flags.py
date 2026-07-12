#!/usr/bin/env python3
"""存量清洗:给 vkpi_kol_pool 里触达门槛命中的行补打 low_reach 标(第二道闸回填)。

背景(2026-07-12 用户 live 实锤,kol_pool 12297「Independent film maker」2 粉号):
发现时 followers=NULL 按「不误杀」放行 → 档案补全回填 followers=2 后没有第二道闸,
照样出现在推荐面。增量链已修(pool_enrich / profile_basics 写完 followers 即重过闸);
本脚本清洗**存量**:全表逐行走 discovery_filters._reach_floor_reason 单一真源重过闸,
命中 → raw_platform_data JSON 打 low_reach 标;已打标但现已达标 → 摘标。

规矩:
  - 只打标不删行(落库≠推荐:库里保留资产,只是不推荐)。
  - 判据零复制:evaluate_low_reach_stamp 直接吃 reach_floor_regate(单一真源)。
  - 绝不触碰任何评分字段(viltrox_fit_score / rule_v0 等),只写 raw_platform_data 一列。
  - 默认 dry-run,只打印前后计数;加 --write 才落库。幂等可重复跑。
  - SQL 兼容层:占位符用 ?,SQL 字符串里不出现字面百分号。

用法:
  .venv/bin/python scripts/backfill_low_reach_flags.py            # dry-run
  .venv/bin/python scripts/backfill_low_reach_flags.py --write    # 落库
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# 绝对路径载 .env,防 cwd 陷阱(仿 scripts/backfill_pool_lang_geo.py)。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
BACKEND = PROJECT_ROOT / "backend"


def load_dotenv() -> None:
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv()
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.db.connection import close_db_runtime, get_conn  # noqa: E402
from app.domains.kol.discovery_filters import (  # noqa: E402
    LOW_REACH_FLAG_LIKE_PATTERN,
    _reach_floor_min_followers,
)
from app.domains.kol.pool_common import _clear_kol_pool_read_cache  # noqa: E402
from app.domains.kol.reach_floor_regate import evaluate_low_reach_stamp  # noqa: E402


def flagged_count(conn) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM vkpi_kol_pool WHERE raw_platform_data LIKE ?",
        (LOW_REACH_FLAG_LIKE_PATTERN,),
    ).fetchone()
    return int(dict(row)["n"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="落库(默认 dry-run 只打印)")
    args = parser.parse_args()

    conn = get_conn()
    floor = _reach_floor_min_followers()
    before_flagged = flagged_count(conn)
    total_row = conn.execute("SELECT COUNT(*) AS n FROM vkpi_kol_pool").fetchone()
    total = int(dict(total_row)["n"])
    below_row = conn.execute(
        "SELECT COUNT(*) AS n FROM vkpi_kol_pool WHERE followers IS NOT NULL AND followers < ?",
        (floor,),
    ).fetchone()
    below = int(dict(below_row)["n"])
    null_row = conn.execute(
        "SELECT COUNT(*) AS n FROM vkpi_kol_pool WHERE followers IS NULL"
    ).fetchone()
    null_followers = int(dict(null_row)["n"])

    print(f"[before] total={total} followers<{floor}={below} followers=NULL={null_followers} 已打标={before_flagged}")

    rows = conn.execute(
        """
        SELECT id, handle, followers, avg_views, avg_comments, engagement_rate, raw_platform_data
        FROM vkpi_kol_pool
        ORDER BY id
        """
    ).fetchall()

    stamped = 0
    unstamped = 0
    unchanged = 0
    samples: list[str] = []
    for raw_row in rows:
        row = dict(raw_row)
        verdict = evaluate_low_reach_stamp(row)
        if not verdict["changed"]:
            unchanged += 1
            continue
        if verdict["flagged"]:
            stamped += 1
            if len(samples) < 10:
                samples.append(f"  id={row['id']} handle={str(row.get('handle'))[:40]!r} reason={verdict['reason']}")
        else:
            unstamped += 1
        if args.write:
            conn.execute(
                "UPDATE vkpi_kol_pool SET raw_platform_data=? WHERE id=?",
                (verdict["raw_json"], int(row["id"])),
            )
    if args.write:
        conn.commit()
        _clear_kol_pool_read_cache()

    mode = "WRITE" if args.write else "DRY-RUN"
    print(f"[{mode}] 打标={stamped} 摘标={unstamped} 不变={unchanged}")
    if samples:
        print("[打标样本(前 10)]")
        print("\n".join(samples))
    after_flagged = flagged_count(conn) if args.write else before_flagged
    print(f"[after] 已打标={after_flagged}(before={before_flagged})")
    asyncio.run(close_db_runtime())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
