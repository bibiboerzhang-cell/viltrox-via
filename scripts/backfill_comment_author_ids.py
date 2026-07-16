#!/usr/bin/env python3
"""Backfill vkpi_comments.author_id / author_handle from raw_data_json.

背景:collector 早期映射只认嵌套 author.id/user.id(TikTok)与 from.*(Facebook),
而 Apify TikTok 评论批次 raw 顶层是扁平 uid/uniqueId,Facebook 评论 raw 是
profileName/profileId/profileUrl —— 历史行 author_id 落空。

规矩:
  - 只补空、不覆写(WHERE 再守一遍 author_id 仍为空),天然幂等,可重复跑。
  - 只动 author_id/author_handle 两列,绝不触碰任何评分字段。
  - SQL 兼容层:占位符用 ?,SQL 字符串里不出现字面百分号。

用法:
  .venv/bin/python scripts/backfill_comment_author_ids.py --dry-run   # 只看计数,不落库
  .venv/bin/python scripts/backfill_comment_author_ids.py             # 落库
"""
from __future__ import annotations
from stdout_utils import out as stdout_out

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# 绝对路径载 .env,防 cwd 陷阱(仿 scripts/build_kol_profile_index.py)。
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


def _get_path(data: Any, path: str) -> Any:
    """点号路径取嵌套字段;任何一级缺失返回 None。"""
    current = data
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _first(raw: dict, paths: list[str]) -> str:
    for path in paths:
        value = _get_path(raw, path)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return ""


def _fb_url_tail(raw: dict) -> str:
    """profileUrl 尾段兜底(去 query;profile.php 不是 handle)。"""
    profile_url = _first(raw, ["profileUrl"])
    tail = profile_url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1].strip()
    if tail and tail.lower() != "profile.php":
        return tail
    return ""


def extract_author(platform: str, raw: dict) -> tuple[str, str]:
    """返回 (author_id, author_handle);抽不到的位置给空串。"""
    platform = (platform or "").strip().lower()
    if platform == "tiktok":
        author_id = _first(raw, ["uid", "author.id", "user.id"])
        handle = _first(raw, ["uniqueId", "author.uniqueId", "user.uniqueId"])
        return author_id, handle
    if platform == "facebook":
        author_id = _first(raw, ["profileId", "from.id"])
        handle = _first(raw, ["profileName", "from.name"]) or _fb_url_tail(raw)
        return author_id, handle
    # 其它平台:只认任务口径里的扁平 uid/profileId 信号,避免误写。
    author_id = _first(raw, ["uid", "profileId"])
    handle = _first(raw, ["uniqueId", "profileName"])
    return author_id, handle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="只统计不落库")
    args = parser.parse_args()

    conn = get_conn()
    rows = conn.execute(
        """
        SELECT id, platform, author_id, author_handle, raw_data_json
        FROM vkpi_comments
        WHERE (author_id IS NULL OR TRIM(author_id) = '')
          AND raw_data_json IS NOT NULL
        ORDER BY id ASC
        """
    ).fetchall()

    scanned = len(rows)
    parse_failed = 0
    no_signal = 0
    author_id_filled = 0
    author_handle_filled = 0
    by_platform: Counter[str] = Counter()

    for row in rows:
        item = dict(row)
        try:
            raw = json.loads(str(item.get("raw_data_json") or ""))
        except Exception:
            parse_failed += 1
            continue
        if not isinstance(raw, dict):
            parse_failed += 1
            continue

        platform = str(item.get("platform") or "")
        author_id, handle = extract_author(platform, raw)
        if not author_id:
            no_signal += 1
            continue

        handle_empty = not str(item.get("author_handle") or "").strip()
        write_handle = handle if (handle_empty and handle) else ""

        if not args.dry_run:
            if write_handle:
                result = conn.execute(
                    """
                    UPDATE vkpi_comments
                    SET author_id = ?, author_handle = ?
                    WHERE id = ? AND (author_id IS NULL OR TRIM(author_id) = '')
                    """,
                    (author_id[:200], write_handle[:200], int(item["id"])),
                )
            else:
                result = conn.execute(
                    """
                    UPDATE vkpi_comments
                    SET author_id = ?
                    WHERE id = ? AND (author_id IS NULL OR TRIM(author_id) = '')
                    """,
                    (author_id[:200], int(item["id"])),
                )
            if not int(getattr(result, "rowcount", 0) or 0):
                continue  # 并发下别人先补了:守住只补空
        author_id_filled += 1
        if write_handle:
            author_handle_filled += 1
        by_platform[platform or "unknown"] += 1

    if not args.dry_run:
        conn.commit()

    stdout_out(json.dumps({
        "dry_run": bool(args.dry_run),
        "scanned_missing_author_id": scanned,
        "parse_failed": parse_failed,
        "no_signal": no_signal,
        "author_id_filled": author_id_filled,
        "author_handle_filled": author_handle_filled,
        "by_platform": dict(by_platform),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        try:
            asyncio.run(close_db_runtime())
        except Exception:
            pass
