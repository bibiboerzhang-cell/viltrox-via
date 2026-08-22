#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日告警摘要:vkpi_alerts open / escalated / 24h 新增 / 24h 已解决 → markdown + webhook 出站。

只读 vkpi_alerts;唯一写入是 alert_outbound 的去重状态(persistent_cache)。出站 URL 只在 env,
本脚本的输出与日志永不带 URL。未配置出站时诚实打印 ``outbound: not_configured``,退出码仍为 0。
同日重跑:出站 key=alerts-digest,fingerprint=日期 → 6h 去重窗口内不重发。

用法:
  PYTHONPATH=backend .venv/bin/python scripts/ops/alerts_digest.py              # markdown + 出站
  PYTHONPATH=backend .venv/bin/python scripts/ops/alerts_digest.py --no-send    # 只打印
  PYTHONPATH=backend .venv/bin/python scripts/ops/alerts_digest.py --json       # 机器可读
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

DIGEST_KEY = "alerts-digest"
STALE_DAYS = 30
_OPEN_SCAN_LIMIT = 500
_SEVERITY_ORDER = {"danger": 0, "warning": 1, "info": 2}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _meta(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _count(conn: Any, sql: str, params: tuple = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    if not row:
        return 0
    try:
        return int(dict(row).get("n") or 0)
    except (TypeError, ValueError):
        return 0


def collect_digest(conn: Any, *, now: datetime | None = None, hours: int = 24, limit: int = 20) -> dict[str, Any]:
    """只读聚合。返回结构稳定,供 render_markdown / --json 共用。"""
    now = now or _utcnow()
    cutoff = _iso(now - timedelta(hours=max(1, int(hours))))
    stale_cutoff = now - timedelta(days=STALE_DAYS)
    open_rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT id, alert_key, severity, title, rule_key, target_type, created_at, updated_at, metadata_json
            FROM vkpi_alerts
            WHERE status='open'
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (_OPEN_SCAN_LIMIT,),
        ).fetchall()
    ]
    by_severity: dict[str, int] = {}
    by_rule: dict[str, int] = {}
    escalated: list[dict[str, Any]] = []
    stale = 0
    for item in open_rows:
        sev = str(item.get("severity") or "info")
        by_severity[sev] = by_severity.get(sev, 0) + 1
        rule = str(item.get("rule_key") or "(无规则)")
        by_rule[rule] = by_rule.get(rule, 0) + 1
        meta = _meta(item.get("metadata_json"))
        item["escalated"] = bool(meta.get("escalated"))
        if item["escalated"]:
            escalated.append(item)
        updated = _parse_dt(item.get("updated_at"))
        if updated is not None and updated < stale_cutoff:
            stale += 1
    open_rows.sort(key=lambda r: (_SEVERITY_ORDER.get(str(r.get("severity") or "info"), 9), str(r.get("updated_at") or "")))
    top = [
        {k: r.get(k) for k in ("id", "alert_key", "severity", "title", "rule_key", "updated_at", "escalated")}
        for r in open_rows[: max(1, int(limit))]
    ]
    return {
        "generated_at": _iso(now),
        "day": now.strftime("%Y-%m-%d"),
        "window_hours": int(hours),
        "open_total": _count(conn, "SELECT COUNT(*) AS n FROM vkpi_alerts WHERE status='open'"),
        "open_by_severity": by_severity,
        "open_by_rule": dict(sorted(by_rule.items(), key=lambda kv: (-kv[1], kv[0]))),
        "escalated_total": len(escalated),
        "escalated": [{k: r.get(k) for k in ("alert_key", "severity", "title", "updated_at")} for r in escalated[:limit]],
        "stale_open_total": stale,
        "new_in_window": _count(conn, "SELECT COUNT(*) AS n FROM vkpi_alerts WHERE created_at >= ?", (cutoff,)),
        "resolved_in_window": _count(
            conn, "SELECT COUNT(*) AS n FROM vkpi_alerts WHERE status='resolved' AND resolved_at >= ?", (cutoff,)
        ),
        "archived_total": _count(conn, "SELECT COUNT(*) AS n FROM vkpi_alerts WHERE status='archived'"),
        "top_open": top,
        "open_scan_truncated": len(open_rows) >= _OPEN_SCAN_LIMIT,
    }


def render_markdown(digest: dict[str, Any]) -> str:
    sev = digest.get("open_by_severity") or {}
    lines = [
        f"# V-KPI 告警日报 {digest.get('day')}",
        "",
        f"- 未关闭(open):**{digest.get('open_total', 0)}**"
        f"(danger {sev.get('danger', 0)} / warning {sev.get('warning', 0)} / info {sev.get('info', 0)})",
        f"- 已升级(escalated):**{digest.get('escalated_total', 0)}**",
        f"- 近 {digest.get('window_hours', 24)}h 新增:**{digest.get('new_in_window', 0)}**",
        f"- 近 {digest.get('window_hours', 24)}h 已解决:**{digest.get('resolved_in_window', 0)}**",
        f"- 超 {STALE_DAYS} 天无更新的陈旧 open:{digest.get('stale_open_total', 0)}(归档见 archive_stale_alerts.py)",
        f"- 已归档累计:{digest.get('archived_total', 0)}",
    ]
    if digest.get("open_scan_truncated"):
        lines.append(f"- 注意:open 超过 {_OPEN_SCAN_LIMIT} 条,按规则/严重度的分项只统计了前 {_OPEN_SCAN_LIMIT} 条")
    rules = digest.get("open_by_rule") or {}
    if rules:
        lines += ["", "## open 按规则", ""]
        lines += [f"- {rule}: {count}" for rule, count in list(rules.items())[:15]]
    escalated = digest.get("escalated") or []
    if escalated:
        lines += ["", "## 已升级", ""]
        lines += [f"- [{r.get('severity')}] {r.get('title')} ({r.get('alert_key')})" for r in escalated]
    top = digest.get("top_open") or []
    if top:
        lines += ["", "## 最值得看的 open", ""]
        for r in top:
            flag = " ⬆" if r.get("escalated") else ""
            lines.append(f"- [{r.get('severity')}] {r.get('title')}{flag} — {r.get('rule_key') or '-'} · {r.get('updated_at') or ''}")
    else:
        lines += ["", "_当前没有 open 告警。_"]
    lines += ["", f"_generated {digest.get('generated_at')} UTC_"]
    return "\n".join(lines)


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="V-KPI alerts daily digest (markdown + webhook)")
    parser.add_argument("--hours", type=int, default=24, help="新增/解决统计窗口(小时,默认 24)")
    parser.add_argument("--limit", type=int, default=20, help="列出的 open 条数上限(默认 20)")
    parser.add_argument("--no-send", action="store_true", help="只打印不出站")
    parser.add_argument("--json", action="store_true", help="输出 JSON(含 markdown 与出站结果)")
    args = parser.parse_args(argv)

    from app.db.connection import db_connection_sync_scope, get_conn, table_exists
    from app.domains.ops import alert_outbound

    with db_connection_sync_scope():
        if not table_exists("vkpi_alerts"):
            out("vkpi_alerts 表缺失(迁移 023 未跑),无摘要可生成")
            return 2
        digest = collect_digest(get_conn(), hours=args.hours, limit=args.limit)
        markdown = render_markdown(digest)
        outbound: dict[str, Any] = {"sent": False, "reason": "skipped_by_flag"}
        if not args.no_send:
            outbound = alert_outbound.send_digest(
                markdown=markdown,
                title=f"V-KPI 告警日报 {digest['day']}:open {digest['open_total']} / 升级 {digest['escalated_total']}",
                day=str(digest["day"]),
            )
    if args.json:
        out_json({"digest": digest, "markdown": markdown, "outbound": outbound}, ensure_ascii=False, indent=2)
    else:
        out(markdown)
        out("")
        out(f"outbound: kind={outbound.get('kind', '-')} sent={outbound.get('sent')} reason={outbound.get('reason')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
