#!/usr/bin/env python3
"""回填会话项来源列 origin(迁移 301)。

背景:``vkpi_kol_search_session_items`` 直到迁移 301 才有来源列,存量行(线上 2026-08-25
实测 3939 条)全是 NULL。本脚本按 ``search_sessions_item_origin.explain_item_origin``
——与写端完全同一个纯函数——把存量补齐,让历史会话也能一眼看出「哪些人是自有库里
捞的、哪些是本次现场从平台上新找到的」。

硬约束:
  * 默认 dry-run,只读只算只打印;必须显式 ``--apply`` 才写库。
  * 只填空值:SQL 侧 ``WHERE origin IS NULL`` 兜底,永不覆盖已有值。
  * payload 里已有 origin 且与推断不一致的行,判为冲突 -> 整行跳过并报出来,交人裁决。
  * 不改 ``updated_at``:这是元数据补录,不是业务事件,不该把会话顶到「刚刚更新」。
  * 不接进任何自动流程(无 scheduler 任务、无 systemd unit、无调用方),由人手动跑。
  * 绝不触 viltrox_fit_score / rule_v0 / KOL 归属。

用法(先看 dry-run 分布,确认无误再 --apply):
  PYTHONPATH=.:backend APP_ROLE=admin-web ENABLE_SCHEDULER=0 \\
    .venv/bin/python scripts/ops/backfill_item_origin.py
  ... backfill_item_origin.py --apply [--limit 500] [--session-id 1129]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path
from typing import Any

LOG = logging.getLogger("viltrox.ops.backfill_item_origin")
LOG.setLevel(logging.INFO)
LOG.propagate = False
_STDOUT = logging.StreamHandler(stream=sys.stdout)
_STDOUT.setFormatter(logging.Formatter("%(message)s"))
LOG.addHandler(_STDOUT)

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.db.connection import get_conn  # noqa: E402
from app.domains.kol.search_sessions_item_origin import (  # noqa: E402
    ITEM_ORIGIN_VALUES,
    explain_item_origin,
    origin_breakdown_from_pairs,
    payload_origin,
)

_ORIGIN_FIELD = "origin"
_ORIGIN_REASON_FIELD = "origin_reason"
_DEFAULT_BATCH_SIZE = 500
_CONFLICT_SAMPLE_LIMIT = 20


def _emit(payload: Any) -> None:
    LOG.info(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _loads(value: Any) -> dict[str, Any]:
    """payload_json 在 Postgres 下回读是 dict,在兼容层下可能是字符串。"""
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            # 解析不了就当空 payload:推断会退到 item_type,判不出即 unknown,方向安全。
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _fetch_batch(
    conn: Any,
    *,
    after_id: int,
    batch_size: int,
    session_id: int | None,
) -> list[dict[str, Any]]:
    if session_id is None:
        rows = conn.execute(
            """
            SELECT id AS id,
                   session_id AS session_id,
                   item_type AS item_type,
                   payload_json AS payload_json
            FROM vkpi_kol_search_session_items
            WHERE origin IS NULL
              AND id > ?
            ORDER BY id
            LIMIT ?
            """,
            (int(after_id), int(batch_size)),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id AS id,
                   session_id AS session_id,
                   item_type AS item_type,
                   payload_json AS payload_json
            FROM vkpi_kol_search_session_items
            WHERE origin IS NULL
              AND session_id = ?
              AND id > ?
            ORDER BY id
            LIMIT ?
            """,
            (int(session_id), int(after_id), int(batch_size)),
        ).fetchall()
    return [dict(row) for row in rows or []]


def _plan_row(row: dict[str, Any]) -> dict[str, Any]:
    """算出这一行该写什么。纯函数级决策,dry-run 与 --apply 走的是同一条路径。"""
    payload = _loads(row.get("payload_json"))
    verdict = explain_item_origin(row.get("item_type"), payload)
    inferred = verdict["origin"]
    existing = payload_origin(payload)
    if existing and existing != inferred:
        return {
            "id": int(row.get("id") or 0),
            "session_id": row.get("session_id"),
            "item_type": row.get("item_type"),
            "action": "conflict",
            "origin": inferred,
            "payload_origin": existing,
            "reason": verdict["reason"],
        }
    patched = dict(payload)
    patched[_ORIGIN_FIELD] = inferred
    patched.setdefault(_ORIGIN_REASON_FIELD, verdict["reason"])
    return {
        "id": int(row.get("id") or 0),
        "session_id": row.get("session_id"),
        "item_type": row.get("item_type"),
        "action": "fill",
        "origin": inferred,
        "reason": verdict["reason"],
        "payload_json": json.dumps(patched, ensure_ascii=False, default=str),
        "payload_changed": patched != payload,
    }


def _write_plan(conn: Any, plan: dict[str, Any]) -> int:
    """只填空值:``AND origin IS NULL`` 让并发写端永远赢,回填绝不覆盖。"""
    cursor = conn.execute(
        """
        UPDATE vkpi_kol_search_session_items
        SET origin=?,
            payload_json=?::jsonb
        WHERE id=?
          AND origin IS NULL
        """,
        (plan["origin"], plan["payload_json"], int(plan["id"])),
    )
    changed = getattr(cursor, "rowcount", None)
    return int(changed) if isinstance(changed, int) and changed >= 0 else 1


def run(
    *,
    apply_changes: bool,
    limit: int | None,
    batch_size: int,
    session_id: int | None,
) -> dict[str, Any]:
    conn = get_conn()
    after_id = 0
    scanned = 0
    written = 0
    payload_touched = 0
    by_origin: Counter[str] = Counter()
    by_pair: Counter[tuple[str, str]] = Counter()
    by_reason: Counter[str] = Counter()
    conflicts: list[dict[str, Any]] = []
    conflict_count = 0

    while True:
        remaining = None if limit is None else max(0, limit - scanned)
        if remaining == 0:
            break
        take = batch_size if remaining is None else min(batch_size, remaining)
        rows = _fetch_batch(
            conn,
            after_id=after_id,
            batch_size=take,
            session_id=session_id,
        )
        if not rows:
            break
        for row in rows:
            scanned += 1
            after_id = max(after_id, int(row.get("id") or 0))
            plan = _plan_row(row)
            if plan["action"] == "conflict":
                conflict_count += 1
                by_reason[f"conflict:{plan['payload_origin']}->{plan['origin']}"] += 1
                if len(conflicts) < _CONFLICT_SAMPLE_LIMIT:
                    conflicts.append(plan)
                continue
            origin = plan["origin"]
            item_type = str(plan.get("item_type") or "unknown")
            by_origin[origin] += 1
            by_pair[(origin, item_type)] += 1
            by_reason[plan["reason"]] += 1
            if plan["payload_changed"]:
                payload_touched += 1
            if apply_changes:
                written += _write_plan(conn, plan)
        if len(rows) < take:
            break

    if apply_changes:
        conn.commit()

    breakdown = origin_breakdown_from_pairs(
        [(origin, item_type, count) for (origin, item_type), count in by_pair.items()]
    )
    return {
        "mode": "apply" if apply_changes else "dry_run",
        "scope": {"session_id": session_id, "limit": limit, "batch_size": batch_size},
        "scanned_null_origin_rows": scanned,
        "planned_fill_rows": sum(by_origin.values()),
        "written_rows": written if apply_changes else 0,
        "payload_origin_field_added_rows": payload_touched,
        "conflict_rows_skipped": conflict_count,
        "by_origin": {value: by_origin.get(value, 0) for value in ITEM_ORIGIN_VALUES},
        "by_item_type_and_origin": {
            f"{item_type}/{origin}": count for (origin, item_type), count in sorted(by_pair.items())
        },
        "by_reason": dict(sorted(by_reason.items())),
        "breakdown": breakdown,
        "conflict_samples": conflicts,
        "viltrox_fit_score_untouched": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="回填 vkpi_kol_search_session_items.origin(默认 dry-run)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真正写库。不加这个参数就只读只算只打印,一行不写。",
    )
    parser.add_argument("--limit", type=int, default=None, help="最多处理多少行(默认全量)")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=_DEFAULT_BATCH_SIZE,
        help=f"每批取多少行(默认 {_DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument("--session-id", type=int, default=None, help="只回填某个会话")
    args = parser.parse_args(argv)

    if args.batch_size <= 0:
        parser.error("--batch-size 必须为正整数")
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit 必须为正整数")

    result = run(
        apply_changes=bool(args.apply),
        limit=args.limit,
        batch_size=int(args.batch_size),
        session_id=args.session_id,
    )
    _emit(result)
    if not args.apply:
        LOG.info("dry-run 结束:未写入任何一行。确认分布无误后加 --apply 重跑。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
