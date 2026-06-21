"""R20 · 数据主链路就绪度(只读编排:测 5 个手动断点的「待接」数量,零写零裁决)。

5 个断点(search → project → fulfillment):
  1 搜索已批准待建草案   approved_kol_ids 非空的会话
  2 草案待挂 KOL         discovery 阶段且零派单的项目
  3 已签收待开观察窗     delivered_at 非空的寄样
  4 观察窗待扫内容       pending/scanning 的观察窗口
  5 内容候选待推进复盘   status='matched' 的内容帖

红线:纯 SELECT 计数,绝不自动推进/花钱/绕人审;真正接线仍走各自人审端点(approve/
create-draft/add-kols/scan/advance-retrospective)。本模块只给「哪一环堆了多少待接」可见性。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists

logger = get_logger(__name__)


def _count(sql: str, params: tuple[Any, ...] = ()) -> int | None:
    try:
        row = get_conn().execute(sql, params).fetchone()
        return int(dict(row).get("n") or 0) if row else 0
    except Exception:
        logger.debug("pipeline_sequence.count_failed", extra={"sql": sql[:80]}, exc_info=True)
        return None


def pipeline_readiness(staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """5 个断点的「待接」聚合计数(只读概览)。表缺/查错 → 该项 count=None(诚实)。"""
    del staff  # 聚合运营概览,不做按人 scope(计数非逐记录敏感数据)。
    breakpoints: list[dict[str, Any]] = []

    # 1 搜索已批准待建草案(approved_kol_ids 非空 jsonb 数组)。
    c1 = None
    if table_exists("vkpi_kol_search_sessions"):
        c1 = _count(
            "SELECT COUNT(*) AS n FROM vkpi_kol_search_sessions "
            "WHERE approved_kol_ids IS NOT NULL "
            "AND jsonb_typeof(approved_kol_ids) = 'array' "
            "AND jsonb_array_length(approved_kol_ids) > 0"
        )
    breakpoints.append({
        "id": "search_approved_to_draft",
        "label": "搜索已批准 → 待建项目草案",
        "ready_count": c1,
        "next_endpoint": "POST /kol-search-sessions/{id}/create-project-draft",
    })

    # 2 草案待挂 KOL(discovery 阶段且零派单)。
    c2 = None
    if table_exists("vkpi_projects") and table_exists("vkpi_project_kol_assignments"):
        c2 = _count(
            "SELECT COUNT(*) AS n FROM vkpi_projects p "
            "WHERE LOWER(COALESCE(p.stage,''))='discovery' "
            "AND NOT EXISTS (SELECT 1 FROM vkpi_project_kol_assignments a WHERE a.project_id=p.id)"
        )
    breakpoints.append({
        "id": "draft_to_add_kols",
        "label": "项目草案 → 待挂 KOL",
        "ready_count": c2,
        "next_endpoint": "POST /projects/{id}/kols",
    })

    # 3 已签收待开观察窗(delivered_at 非空的寄样)。
    c3 = None
    if table_exists("vkpi_shipments"):
        c3 = _count("SELECT COUNT(*) AS n FROM vkpi_shipments WHERE delivered_at IS NOT NULL")
    breakpoints.append({
        "id": "delivered_to_window",
        "label": "已签收 → 待开观察窗",
        "ready_count": c3,
        "next_endpoint": "POST /projects/observation-windows/scan-delivered",
    })

    # 4 观察窗待扫内容(pending/scanning)。
    c4 = None
    if table_exists("vkpi_project_content_observation_windows"):
        c4 = _count(
            "SELECT COUNT(*) AS n FROM vkpi_project_content_observation_windows "
            "WHERE status IN ('pending','scanning')"
        )
    breakpoints.append({
        "id": "window_to_content_scan",
        "label": "观察窗 → 待扫内容",
        "ready_count": c4,
        "next_endpoint": "(scheduler) fulfillment_content_scan",
    })

    # 5 内容候选待推进复盘(status='matched')。
    c5 = None
    if table_exists("vkpi_project_content_posts"):
        c5 = _count("SELECT COUNT(*) AS n FROM vkpi_project_content_posts WHERE status='matched'")
    breakpoints.append({
        "id": "content_to_retrospective",
        "label": "内容候选 → 待推进复盘",
        "ready_count": c5,
        "next_endpoint": "POST /projects/{id}/content-posts/advance-retrospective",
    })

    total_ready = sum(b["ready_count"] for b in breakpoints if isinstance(b["ready_count"], int))
    return {
        "breakpoints": breakpoints,
        "total_ready": total_ready,
        "note": "只读概览;每环推进仍走各自人审端点(红线:不自动推进/花钱/绕审)。",
    }
