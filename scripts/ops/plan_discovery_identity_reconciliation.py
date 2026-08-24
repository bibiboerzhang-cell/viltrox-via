#!/usr/bin/env python3
"""Generate a read-only discovery identity reconciliation plan.

There is deliberately no ``--apply`` option.  The output may be reviewed and
used to design a later guarded migration, but this command cannot update,
delete, merge, or quarantine a database row.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
SCRIPTS = ROOT / "scripts"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(1, str(SCRIPTS))

from stdout_utils import out as stdout_out  # noqa: E402


def _make_read_only(conn: Any) -> None:
    try:
        conn.execute("SET TRANSACTION READ ONLY")
        return
    except Exception:
        conn.rollback()
    conn.execute("PRAGMA query_only=ON")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="只读生成发现墙 identity/头像/官号历史对账计划；不支持 apply",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="完整 JSON 审计计划输出路径；省略时完整计划写 stdout",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="stdout 只显示摘要（与 --output 搭配保留完整计划）",
    )
    args = parser.parse_args()

    from app.db.connection import get_conn
    from app.domains.kol.identity_reconciliation_plan import (
        build_identity_reconciliation_plan,
        plan_summary,
    )
    from app.domains.kol.search_sessions_serde import _row_to_item

    conn = get_conn()
    try:
        _make_read_only(conn)
        pool_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT id, platform, handle, profile_url, display_name,
                       avatar_url, bio, followers, source_type,
                       raw_platform_data, dashboard_account_type,
                       duplicate_of_id
                FROM vkpi_kol_pool
                ORDER BY id
                """
            ).fetchall()
        ]
        alias_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT id, kol_pool_id, platform, handle, profile_url,
                       metadata_json
                FROM vkpi_kol_pool_aliases
                ORDER BY id
                """
            ).fetchall()
        ]
        item_rows = conn.execute(
            """
            SELECT i.*, s.archived_at AS session_archived_at
            FROM vkpi_kol_search_session_items i
            JOIN vkpi_kol_search_sessions s ON s.id=i.session_id
            ORDER BY i.session_id, i.rank NULLS LAST, i.id
            """
        ).fetchall()
        session_items: list[dict[str, Any]] = []
        for row in item_rows:
            item = _row_to_item(row)
            item["session_archived_at"] = dict(row).get("session_archived_at")
            session_items.append(item)
        stamp_row = conn.execute(
            "SELECT CURRENT_TIMESTAMP AS generated_at"
        ).fetchone()
        generated_at = str(dict(stamp_row).get("generated_at") if stamp_row else "")
        plan = build_identity_reconciliation_plan(
            pool_rows=pool_rows,
            alias_rows=alias_rows,
            session_items=session_items,
            generated_at=generated_at,
        )
    finally:
        conn.rollback()
        conn.close()

    full_json = json.dumps(plan, ensure_ascii=False, indent=2, default=str) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(full_json, encoding="utf-8")
    rendered = plan_summary(plan) if args.summary_only or args.output else plan
    stdout_out(json.dumps(rendered, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
