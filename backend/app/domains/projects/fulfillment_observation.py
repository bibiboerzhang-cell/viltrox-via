"""Projects 履约观测系统 · 刀1+P0(只读地基,零写零 schema):
- deliverable_stages_summary:扫真实 assignment stage 分布(P0「统一状态词」的盘点依据)。
- due_list:列「shipment 已签收满 N 天、但项目签收后无内容证据」的待观察项(刀1 due-list)。

纯 SELECT;绝不写库、绝不碰 viltrox_fit_score / rule_v0。为后续刀2-7(物流同步→观察窗口→
内容匹配)提供只读基线;当前 vkpi_shipments 多无 delivered 数据,due_list 可能为空(诚实反映
物流断流——正是自动观察起不来的根因)。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.db.connection import get_conn
from app.domains.projects.workflow_common import normalize_assignment_stage


def deliverable_stages_summary() -> dict[str, Any]:
    """assignment 的 stage / stage_status 真实分布(P0-1:状态词统一)。

    读侧归一:把溢出的项目层词(discovery/shipped/received/measured/content_published…)
    合并到 assignment 规范词(discovered/device_sent/arrived/reviewed/content_posted),
    再在 Python 侧重聚合,避免 discovery 与 discovered 等被算成两档导致 due-list/复盘错。
    同时透出 raw_stages(归一前分布)便于核对。
    """
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT COALESCE(NULLIF(stage, ''), '') AS stage,
               COALESCE(NULLIF(stage_status, ''), '(空)') AS stage_status,
               COUNT(*) AS n
        FROM vkpi_project_kol_assignments
        GROUP BY 1, 2
        ORDER BY n DESC
        """
    ).fetchall()
    raw_items = [dict(r) for r in rows]
    merged: dict[tuple[str, str], int] = {}
    for r in raw_items:
        canon = normalize_assignment_stage(r.get("stage") or "") or "(空)"
        key = (canon, str(r.get("stage_status") or "(空)"))
        merged[key] = merged.get(key, 0) + int(r.get("n") or 0)
    stages = [
        {"stage": k[0], "stage_status": k[1], "n": n}
        for k, n in sorted(merged.items(), key=lambda kv: kv[1], reverse=True)
    ]
    # 归一前后是否有合并(诚实透出),raw 里把空串还原成「(空)」展示
    for r in raw_items:
        if not r.get("stage"):
            r["stage"] = "(空)"
    return {
        "status": "ok",
        "total": sum(s["n"] for s in stages),
        "stages": stages,
        "raw_stages": raw_items,
        "normalized": True,
    }


def due_list(days_overdue: int = 7, limit: int = 100) -> dict[str, Any]:
    """待观察项:有 delivered shipment 早于 (now - days_overdue),且项目零视频证据。

    纯只读。cutoff 在 Python 算后作参数传(避开 psycopg 字面 % / INTERVAL 翻译层脆弱)。
    """
    days = max(0, min(int(days_overdue or 7), 90))
    cutoff = datetime.utcnow() - timedelta(days=days)
    safe_limit = max(1, min(int(limit or 100), 500))
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT p.id AS project_id,
               p.project_name,
               p.product_name,
               MIN(s.delivered_at) AS delivered_at,
               COUNT(DISTINCT a.id) AS assignment_count
        FROM vkpi_shipments s
        JOIN vkpi_projects p ON p.id = s.project_id
        LEFT JOIN vkpi_project_kol_assignments a ON a.project_id = p.id
        WHERE s.delivered_at IS NOT NULL
          AND s.delivered_at < ?
          AND NOT EXISTS (
            SELECT 1 FROM vkpi_kol_video_evidence e WHERE e.project_id = p.id
          )
        GROUP BY p.id, p.project_name, p.product_name
        ORDER BY MIN(s.delivered_at) ASC
        LIMIT ?
        """,
        (cutoff, safe_limit),
    ).fetchall()
    items: list[dict[str, Any]] = []
    now = datetime.utcnow()
    for r in rows:
        row = dict(r)
        delivered = row.get("delivered_at")
        days_since = None
        if isinstance(delivered, datetime):
            days_since = (now - delivered.replace(tzinfo=None)).days
        items.append(
            {
                "project_id": row.get("project_id"),
                "project_name": row.get("project_name"),
                "product_name": row.get("product_name"),
                "delivered_at": str(delivered) if delivered is not None else None,
                "days_since_delivered": days_since,
                "assignment_count": int(row.get("assignment_count") or 0),
            }
        )
    return {
        "status": "ok",
        "days_overdue": days,
        "count": len(items),
        "items": items,
        "note": "已签收满 N 天但项目零内容证据的待观察项;空=无 delivered shipment 数据(物流断流,见刀2)。",
    }
