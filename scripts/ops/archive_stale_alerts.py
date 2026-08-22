#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「>N 天 open 且无更新」的 vkpi_alerts 标为 archived(非删除)。默认 dry-run 只列清单。

安全:
  * 默认 dry-run;真正改状态必须 ``--apply``。prod 上的 apply 由用户亲自跑。
  * 只 UPDATE status='archived'(+ metadata_json 追加 archived_at/archived_reason),绝不 DELETE。
  * 口径 = status='open' AND updated_at < now-N 天;upsert_alert 每次命中都会刷新 updated_at,
    所以「无更新」等价于规则 N 天内没再触发。
  * 输出只带 id/alert_key/severity/title/rule_key/updated_at,不打印 DSN。

用法:
  PYTHONPATH=backend .venv/bin/python scripts/ops/archive_stale_alerts.py              # dry-run, 30 天
  PYTHONPATH=backend .venv/bin/python scripts/ops/archive_stale_alerts.py --days 45     # dry-run
  PYTHONPATH=backend .venv/bin/python scripts/ops/archive_stale_alerts.py --apply       # 真归档
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
for _extra in (REPO / "scripts", REPO / "backend"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from stdout_utils import out, out_json  # noqa: E402

DEFAULT_DAYS = 30
ARCHIVED_STATUS = "archived"
ARCHIVE_REASON = "stale_open_no_update"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _meta(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def find_stale_open_alerts(conn: Any, *, now: datetime | None = None, days: int = DEFAULT_DAYS, limit: int = 1000) -> list[dict[str, Any]]:
    """只读:open 且 updated_at 早于 now-days 的告警清单(最老在前)。"""
    now = now or _utcnow()
    cutoff = _iso(now - timedelta(days=max(1, int(days))))
    rows = conn.execute(
        """
        SELECT id, alert_key, severity, title, rule_key, created_at, updated_at, metadata_json
        FROM vkpi_alerts
        WHERE status='open' AND updated_at < ?
        ORDER BY updated_at ASC
        LIMIT ?
        """,
        (cutoff, max(1, int(limit))),
    ).fetchall()
    return [dict(row) for row in rows]


def archive_alerts(conn: Any, rows: list[dict[str, Any]], *, now: datetime | None = None, days: int = DEFAULT_DAYS) -> int:
    """把清单里的行标 archived(逐行 UPDATE,带 metadata 追溯;不删)。返回实际改动行数。"""
    now_iso = _iso(now or _utcnow())
    changed = 0
    for item in rows:
        meta = _meta(item.get("metadata_json"))
        meta.update({"archived_at": now_iso, "archived_reason": ARCHIVE_REASON, "archived_after_days": int(days)})
        cursor = conn.execute(
            "UPDATE vkpi_alerts SET status=?, metadata_json=?, updated_at=? WHERE id=? AND status='open'",
            (ARCHIVED_STATUS, json.dumps(meta, ensure_ascii=False, default=str), now_iso, int(item["id"])),
        )
        rowcount = getattr(cursor, "rowcount", None)
        changed += int(rowcount) if isinstance(rowcount, int) and rowcount >= 0 else 1
    conn.commit()
    return changed


def _brief(item: dict[str, Any]) -> dict[str, Any]:
    return {k: item.get(k) for k in ("id", "alert_key", "severity", "title", "rule_key", "updated_at")}


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Archive stale open vkpi_alerts (dry-run by default)")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help=f"无更新天数阈值(默认 {DEFAULT_DAYS})")
    parser.add_argument("--limit", type=int, default=1000, help="单次最多处理条数(默认 1000)")
    parser.add_argument("--apply", action="store_true", help="真正写 archived;缺省 dry-run 只列清单")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args(argv)

    from app.db.connection import db_connection_sync_scope, get_conn, is_postgres_runtime, table_exists

    with db_connection_sync_scope():
        if not table_exists("vkpi_alerts"):
            out("vkpi_alerts 表缺失(迁移 023 未跑),无可归档")
            return 2
        conn = get_conn()
        rows = find_stale_open_alerts(conn, days=args.days, limit=args.limit)
        archived = archive_alerts(conn, rows, days=args.days) if args.apply and rows else 0
    mode = "apply" if args.apply else "dry-run"
    backend = "postgres" if is_postgres_runtime() else "sqlite"
    if args.json:
        out_json(
            {"mode": mode, "backend": backend, "days": args.days, "candidates": len(rows), "archived": archived,
             "rows": [_brief(r) for r in rows]},
            ensure_ascii=False, indent=2,
        )
        return 0
    out(f"[{mode}] backend={backend} 口径: open 且 updated_at 早于 {args.days} 天 → 候选 {len(rows)} 条")
    for item in rows:
        out(f"  #{item.get('id')} [{item.get('severity')}] {item.get('alert_key')} · {item.get('rule_key') or '-'} · 最后更新 {item.get('updated_at')}")
    if args.apply:
        out(f"已标 archived:{archived} 条(未删除任何行)")
    elif rows:
        out("dry-run 未写库;确认清单后加 --apply 执行(prod 请用户亲自跑)")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
