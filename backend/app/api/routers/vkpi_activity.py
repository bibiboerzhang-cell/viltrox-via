"""V-KPI ActivityFeed 思考流(件2 · 只读聚合).

GET /api/admin/vkpi/activity/recent?limit=30
  把线上已有的真实事件流聚合成统一的一条条「思考流」条目:
    {ts(UTC), kind, icon, text(一句话), link}
  数据源(全部只读现存表,不赌新表 event_ledger):
    - apify_jobs 近期 done(视频深析 / 评论采集 / URL 深爬 完成)
    - vkpi_alerts 近期(信号 / 预警)
    - vkpi_kol_search_sessions 近期推进(智能搜索会话)
    - vkpi_staff_outreach_digests 近期生成(触达简报,若表存在)

红线:零业务写、零 LLM、零触 viltrox_fit_score / rule_v0。
每个数据源函数内懒 import + try/except,缺表 / 缺列直接跳过(降级不炸)。
时间统一透传 UTC(compat 已把 datetime 归一成 "...Z" 字符串),由前端展示层本地化。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.dependencies.perms import require_tab

router = APIRouter(prefix="/api/admin/vkpi/activity", tags=["vkpi-activity"])


# ── 内部工具 ───────────────────────────────────────────────────────────────
def _text(value: Any) -> str:
    """把任意值安全转成去空白字符串(None→'')。"""
    if value is None:
        return ""
    return str(value).strip()


def _loads(value: Any) -> dict:
    """把 payload/metadata(compat 返回的 JSON 字符串或已是 dict)安全解析成 dict。"""
    if value in (None, ""):
        return {}
    if isinstance(value, dict):
        return value
    try:
        import json

        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _clip(text: str, limit: int = 120) -> str:
    """一句话截断,避免超长文案撑爆前端行。"""
    text = _text(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


# apify_jobs.job_type → (中文动作, 图标名)。未命中回退到通用「任务」。
_JOB_TYPE_LABELS: dict[str, tuple[str, str]] = {
    "kol_content_fit_analysis": ("视频深度分析", "video"),
    "video_analysis": ("视频深度分析", "video"),
    "kol_pool_comments_collect": ("评论采集", "message-circle"),
    "comment_intelligence": ("评论意向分析", "message-circle"),
    "account_dossier_extract": ("账号档案深爬", "search"),
    "kol_lookup": ("KOL 深爬", "search"),
    "kol_auto_poll": ("KOL 自动巡检", "refresh-cw"),
    "contract_invoice_extract": ("合同发票解析", "file-text"),
    "contract_polish": ("合同润色", "file-text"),
    "project_retrospective_aggregate": ("项目复盘聚合", "clipboard-check"),
    "audit_submission": ("提报审核", "clipboard-check"),
}


# ── 数据源:每个函数只读一张(组)表,全部内部容错,返回统一条目列表 ──────────
def _source_apify_jobs(limit: int) -> list[dict[str, Any]]:
    """近期完成的 Apify 任务(视频深析 / 评论 / 深爬 完成)。"""
    try:
        from app.db.connection import get_conn, table_exists

        if not table_exists("apify_jobs"):
            return []
        conn = get_conn()
        rows = conn.execute(
            """
            SELECT id, job_type, payload, status, updated_at
            FROM apify_jobs
            WHERE status = 'done'
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    except Exception:
        return []

    items: list[dict[str, Any]] = []
    for row in rows or []:
        job_type = _text(row["job_type"])
        payload = _loads(row["payload"])
        label, icon = _JOB_TYPE_LABELS.get(job_type, ("后台任务", "cpu"))
        # 尽量从 payload 拼出主体(KOL 名 / URL / 频道),拼不出就只报动作。
        subject = (
            _text(payload.get("kol_name"))
            or _text(payload.get("handle"))
            or _text(payload.get("channel_title"))
            or _text(payload.get("profile_url"))
            or _text(payload.get("video_url"))
        )
        text = f"{label}完成" if not subject else f"{label}完成 · {_clip(subject, 60)}"
        kol_pool_id = payload.get("kol_pool_id")
        link = f"/vkpi/kol-pool?kol={kol_pool_id}" if kol_pool_id else None
        items.append(
            {
                "ts": _text(row["updated_at"]),
                "kind": "job",
                "icon": icon,
                "text": _clip(text),
                "link": link,
            }
        )
    return items


def _source_alerts(limit: int) -> list[dict[str, Any]]:
    """近期信号 / 预警(vkpi_alerts)。"""
    try:
        from app.db.connection import get_conn, table_exists

        if not table_exists("vkpi_alerts"):
            return []
        conn = get_conn()
        rows = conn.execute(
            """
            SELECT id, severity, title, body, created_at
            FROM vkpi_alerts
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    except Exception:
        return []

    # severity → 图标;critical/warning 用告警图标,其余用信息图标。
    icon_by_sev = {
        "critical": "alert-triangle",
        "high": "alert-triangle",
        "warning": "alert-triangle",
    }
    items: list[dict[str, Any]] = []
    for row in rows or []:
        severity = _text(row["severity"]).lower()
        title = _text(row["title"]) or "新信号"
        items.append(
            {
                "ts": _text(row["created_at"]),
                "kind": "alert",
                "icon": icon_by_sev.get(severity, "bell"),
                "text": _clip(title),
                "link": "/vkpi/dashboard",
            }
        )
    return items


def _source_search_sessions(limit: int) -> list[dict[str, Any]]:
    """近期智能搜索会话推进(vkpi_kol_search_sessions)。"""
    try:
        from app.db.connection import get_conn, table_exists

        if not table_exists("vkpi_kol_search_sessions"):
            return []
        conn = get_conn()
        rows = conn.execute(
            """
            SELECT id, query_text, query_type, status, updated_at
            FROM vkpi_kol_search_sessions
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    except Exception:
        return []

    # status → 一句话谓语。
    verb_by_status = {
        "planned": "已规划",
        "running": "搜索中",
        "ready": "结果就绪",
        "partial": "部分完成",
        "failed": "搜索失败",
        "cancelled": "已取消",
    }
    items: list[dict[str, Any]] = []
    for row in rows or []:
        status = _text(row["status"]).lower()
        query = _text(row["query_text"]) or "(空查询)"
        verb = verb_by_status.get(status, "推进")
        text = f"智能搜索{verb} · {_clip(query, 50)}"
        items.append(
            {
                "ts": _text(row["updated_at"]),
                "kind": "search",
                "icon": "search",
                "text": _clip(text),
                "link": "/vkpi/kol-pool",
            }
        )
    return items


def _source_outreach_digests(limit: int) -> list[dict[str, Any]]:
    """近期触达简报生成(vkpi_staff_outreach_digests,若表存在)。"""
    try:
        from app.db.connection import get_conn, table_exists

        if not table_exists("vkpi_staff_outreach_digests"):
            return []
        conn = get_conn()
        rows = conn.execute(
            """
            SELECT id, staff_id, digest_date, item_count, status, generated_at
            FROM vkpi_staff_outreach_digests
            WHERE status = 'ready'
            ORDER BY generated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    except Exception:
        return []

    items: list[dict[str, Any]] = []
    for row in rows or []:
        count = _text(row["item_count"]) or "0"
        text = f"触达简报生成 · {count} 条待触达内容"
        items.append(
            {
                "ts": _text(row["generated_at"]),
                "kind": "outreach",
                "icon": "mail",
                "text": _clip(text),
                "link": "/vkpi/kol-pool",
            }
        )
    return items


_SOURCES = (
    _source_apify_jobs,
    _source_alerts,
    _source_search_sessions,
    _source_outreach_digests,
)


@router.get("/recent")
def recent_activity(
    limit: int = Query(default=30, ge=1, le=100),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict[str, Any]:
    """聚合多源真实事件成统一「思考流」,按 ts 倒序合并截 limit。

    每源多取一些(limit 条),合并后统一排序再截断,保证跨源的时间顺序正确。
    任一源缺表 / 报错自动跳过(降级),永不因单源失败而整体 500。
    """
    merged: list[dict[str, Any]] = []
    for source in _SOURCES:
        try:
            merged.extend(source(limit))
        except Exception:
            # 单源兜底:即便某源函数意外抛出也不影响其余源。
            continue

    # ts 是 "...Z" 字符串,字典序即时间序;缺 ts 的行排最后。
    merged.sort(key=lambda item: item.get("ts") or "", reverse=True)
    return {"items": merged[:limit], "count": min(len(merged), limit)}
