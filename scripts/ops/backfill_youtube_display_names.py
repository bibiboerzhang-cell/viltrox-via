#!/usr/bin/env python3
"""Backfill real YouTube channel names for pool rows enrolled before channels.list 富化上线.

背景(2026-07-21 身份不精准案):YT search.list 无 @handle 时后端用 UC 频道 ID 占 handle,
富化上线前的入库行 display_name 为空/也是裸 ID → 前端卡片把「UCFIRm1Fv1VC4DZxmYyvNOTQ」
当名字。此脚本用 YouTube Data API channels.list(≤50 频道/次,1 quota unit/次)把存量
UC 裸 ID 行的真频道名补进 display_name。

Truth boundaries / red lines:

* 只写 vkpi_kol_pool.display_name(以及可选的 raw 观测输出);绝不触碰 viltrox_fit_score /
  rule_v0 / followers / handle 等任何其他列。
* 幂等:只挑 display_name 为空或本身是 UC 裸 ID 的行;已有真名的行永不覆盖。重跑只报 skip。
* 诚实:API 缺 key / 配额尽 / 单批失败 → 该批跳过并如实计数,绝不杜撰名字。
* 默认 dry-run(只打印将写入的行);--apply 才真正 UPDATE。

用法(本地验证;prod 执行留给主会话):
  APP_ROLE=admin-web ENABLE_SCHEDULER=0 .venv/bin/python scripts/ops/backfill_youtube_display_names.py
  APP_ROLE=admin-web ENABLE_SCHEDULER=0 .venv/bin/python scripts/ops/backfill_youtube_display_names.py --apply
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout)
LOG = logging.getLogger("viltrox.ops.backfill_youtube_display_names")

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

UC_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
BATCH_SIZE = 50  # channels.list 单次上限;1 quota unit/批


def _text(value: Any) -> str:
    return str(value or "").strip()


def _needs_backfill(row: dict[str, Any]) -> bool:
    handle = _text(row.get("handle"))
    display_name = _text(row.get("display_name"))
    if not UC_CHANNEL_ID_RE.match(handle):
        return False
    # display_name 缺 / 也是裸 ID / 等于 handle → 需要补真名;已有真名的行绝不覆盖(幂等)。
    return not display_name or display_name == handle or bool(UC_CHANNEL_ID_RE.match(display_name))


def _load_candidates(conn: Any) -> list[dict[str, Any]]:
    # SQL compat:占位符 ?、禁字面 %(用 strpos 预筛),Python 端再用正则严判。
    rows = conn.execute(
        """
        SELECT id, handle, display_name
        FROM vkpi_kol_pool
        WHERE platform = ?
          AND strpos(handle, ?) = 1
          AND length(handle) = 24
        ORDER BY id
        """,
        ("youtube", "UC"),
    ).fetchall()
    return [dict(row) for row in rows if _needs_backfill(dict(row))]


def _fetch_channel_names(crawler: Any, channel_ids: list[str]) -> dict[str, dict[str, str]]:
    """channels.list part=snippet → {channel_id: {title, custom_url}}。单批失败只跳过该批(诚实降级)。"""
    out: dict[str, dict[str, str]] = {}
    for start in range(0, len(channel_ids), BATCH_SIZE):
        batch = channel_ids[start:start + BATCH_SIZE]
        payload = crawler._request(
            "channels",
            {"part": "snippet", "id": ",".join(batch), "maxResults": BATCH_SIZE},
        )
        if _text(payload.get("provider_status")) != "ok":
            LOG.warning("channels.list batch failed status=%s reason=%s", payload.get("provider_status"), payload.get("provider_reason"))
            continue
        for row in payload.get("items") or []:
            if not isinstance(row, dict):
                continue
            cid = _text(row.get("id"))
            snippet = row.get("snippet") if isinstance(row.get("snippet"), dict) else {}
            title = _text(snippet.get("title"))
            if cid and title:
                out[cid] = {
                    "title": title,
                    "custom_url": _text(snippet.get("customUrl")),
                }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="真正写库(默认 dry-run 只打印)")
    parser.add_argument("--limit", type=int, default=0, help="最多处理 N 行(0=全部)")
    args = parser.parse_args()

    from app.db.connection import get_conn
    from app.platform.industry_crawlers.youtube_crawler import YouTubeCrawler

    crawler = YouTubeCrawler()
    if not crawler.api_key:
        LOG.error("YOUTUBE_API_KEY 未配置,无法调用 channels.list;不杜撰,直接退出。")
        return 2

    conn = get_conn()
    candidates = _load_candidates(conn)
    if args.limit > 0:
        candidates = candidates[: args.limit]
    LOG.info("UC 裸 ID 待补名行数: %d", len(candidates))
    if not candidates:
        return 0

    names = _fetch_channel_names(crawler, [row["handle"] for row in candidates])
    quota_units = (len(candidates) + BATCH_SIZE - 1) // BATCH_SIZE
    LOG.info("channels.list 命中 %d/%d · 配额约 %d unit", len(names), len(candidates), quota_units)

    updated = 0
    missing = 0
    plan: list[dict[str, Any]] = []
    for row in candidates:
        info = names.get(_text(row.get("handle")))
        if not info:
            missing += 1  # 频道被删/隐藏/该批失败 → 如实跳过,不编名字
            continue
        plan.append({"id": row["id"], "handle": row["handle"], "display_name": info["title"], "custom_url": info["custom_url"]})

    for entry in plan:
        if args.apply:
            # 幂等防竞写:仅当行仍处「无真名」状态才写(display_name 空/等于 handle/本身是 UC 裸 ID)。
            conn.execute(
                """
                UPDATE vkpi_kol_pool
                SET display_name = ?
                WHERE id = ?
                  AND (
                    display_name IS NULL OR display_name = '' OR display_name = handle
                    OR (strpos(display_name, ?) = 1 AND length(display_name) = 24)
                  )
                """,
                (entry["display_name"], int(entry["id"]), "UC"),
            )
            updated += 1
        LOG.info("%s", json.dumps({"action": "apply" if args.apply else "dry_run", **entry}, ensure_ascii=False))
    if args.apply:
        conn.commit()
    LOG.info(
        "done mode=%s candidates=%d resolved=%d updated=%d unresolved=%d",
        "apply" if args.apply else "dry-run", len(candidates), len(plan),
        updated if args.apply else 0, missing,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
