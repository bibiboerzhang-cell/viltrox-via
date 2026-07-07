"""V-KPI deterministic natural search routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger
from app.domains.search import natural_search


router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-search"])

logger = get_logger(__name__)


@router.get("/search")
def vkpi_search(
    q: str = Query(..., min_length=1),
    limit: int = Query(default=20, ge=1, le=100),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    del staff
    try:
        return natural_search.search(q, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/global-search")
def vkpi_global_search(
    q: str = Query(..., min_length=1, max_length=80),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """顶栏全局搜索(X3):一个关键词同时搜 KOL(名字/handle)/项目(名字)/活动(名字)。

    轻端点定位:三表各 LIKE 取 5 条,只回名字/id 级轻字段,不回敏感数据
    (联系方式/预算/评分等一概不带)。可见范围口径:登录 + vkpi:read 即可搜
    ——管理层全量;非管理层套 C3 轻隔离口径(X4-MEDIUM 修复 2026-07-03):
    KOL 限本人可见集(收藏∪项目合作∪共享∪认领)、项目限本人参与集、活动限本人在队。
    """
    _role = str(staff.get("role") or "").strip().lower()
    _is_manager = int(staff.get("is_owner") or 0) == 1 or _role in {
        "admin", "manager", "lead", "marketing_lead", "marketing_manager", "marketing-manager"
    }
    _me = 0 if _is_manager else int(staff.get("staff_id") or 0)
    keyword = str(q or "").strip().lower()
    if not keyword:
        raise HTTPException(status_code=400, detail="q 不能为空")
    # 懒 import:线上旧布局下给已存在文件顶层新增 import 有风险,函数内引入最稳。
    from app.db.connection import get_conn

    conn = get_conn()
    like = f"%{keyword}%"

    def _rows(sql: str, params: tuple) -> list[dict]:
        # 单表查询挂了(迁移未跑/列缺失)不拖垮整个搜索,失败该组回空。
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        except Exception:
            logger.warning("global_search 子查询失败", exc_info=True)
            return []

    # 非管理层的可见集 SQL(服务端从鉴权 staff 推导,绝不信客户端;识别不出身份=只回空,诚实)。
    _kol_scope_sql = ""
    _proj_scope_sql = ""
    if not _is_manager:
        if _me <= 0:
            return {"q": keyword, "kols": [], "projects": [], "events": []}
        try:
            from app.domains.dashboard.kol_distribution import _staff_visible_kols_sql
            from app.domains.dashboard.summary_scope import _actor_projects_sql

            _kol_scope_sql = f" AND id IN ({_staff_visible_kols_sql(_me)})"
            _proj_scope_sql = f" AND id IN ({_actor_projects_sql(_me)})"
        except Exception:
            # 旧布局缺可见集模块时宁可回空,不泄露全量(与 C3 隔离口径一致)。
            return {"q": keyword, "kols": [], "projects": [], "events": []}

    # KOL:名字/handle 命中,粉丝多的排前(COALESCE 兜 NULL,跨库可移植)。
    kols = _rows(
        f"""
        SELECT id, platform, handle, display_name, avatar_url, followers
        FROM vkpi_kol_pool
        WHERE (LOWER(COALESCE(display_name, '')) LIKE ?
           OR LOWER(COALESCE(handle, '')) LIKE ?){_kol_scope_sql}
        ORDER BY COALESCE(followers, 0) DESC, id DESC
        LIMIT 5
        """,
        (like, like),
    )
    # 项目:按名字命中,新项目排前。
    projects = _rows(
        f"""
        SELECT id, project_uid, project_name, stage, stage_status, platform
        FROM vkpi_projects
        WHERE LOWER(COALESCE(project_name, '')) LIKE ?{_proj_scope_sql}
        ORDER BY id DESC
        LIMIT 5
        """,
        (like,),
    )
    # 活动:按标题命中,近期活动排前;非管理层取 25 条后按 team_ids 在队成员后过滤(jsonb
    # 语义在 compat 问号占位下不便直查,量小后过滤等价且稳)。
    events = _rows(
        f"""
        SELECT id, title, status, start_date, end_date{", team_ids" if not _is_manager else ""}
        FROM vkpi_events
        WHERE LOWER(COALESCE(title, '')) LIKE ?
        ORDER BY start_date DESC, created_at DESC
        LIMIT {25 if not _is_manager else 5}
        """,
        (like,),
    )
    if not _is_manager:
        import json as _json

        def _in_team(row: dict) -> bool:
            raw = row.pop("team_ids", None)
            try:
                ids = raw if isinstance(raw, list) else _json.loads(raw or "[]")
                return any(int(x) == _me for x in ids if str(x).strip().lstrip("-").isdigit())
            except Exception:
                return False

        events = [r for r in events if _in_team(r)][:5]
    # DATE 列诚实转 ISO 串(不让序列化差异漏到前端)。
    for row in events:
        for key in ("start_date", "end_date"):
            if row.get(key) is not None:
                row[key] = str(row[key])
    return {"q": keyword, "kols": kols, "projects": projects, "events": events}
