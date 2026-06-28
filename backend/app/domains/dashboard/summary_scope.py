"""Dashboard summary scope/stage SQL helpers (moved from summary.py, behavior unchanged)."""
from __future__ import annotations

from app.domains.projects import stage_canonical


# 四环漏斗在役口径(与 pool_favorites.list_favorites 的排除集对齐)
_FUNNEL_EXCLUDED_STAGES_SQL = "('churned','cancelled','lost')"
# 已发布口径(波3 R1 裁定:content_posted/published)
# P14:已发布/执行阶段的 raw 别名统一从 stage_canonical 取,杜绝同一项目在 funnel 与
# active_campaigns 算法不同(此前 funnel=('content_posted','published')、active只认 content_posted、
# canonical 还有 content_published)。现 published=(content_posted,content_published,published)。
_FUNNEL_PUBLISHED_STAGES_SQL = stage_canonical.raw_sql_tuple("content_published")
_EXECUTION_STAGES_SQL = stage_canonical.raw_sql_tuple("shipped", "delivered", "content_published")


def _actor_projects_sql(staff_scope_id: int) -> str:
    """P1 隔离:某 staff「拥有」的项目 id 子查询(assigned / created / 被共享成员)。
    staff_scope_id 已是 scope.effective_staff_id 出来的可信 int(admin→None 不会进这里),
    内联整数安全(非注入面),与本文件既有 {days} f-string 口径一致。"""
    sid = int(staff_scope_id)
    return (
        f"SELECT id FROM vkpi_projects WHERE assigned_staff_id={sid} "
        f"OR created_by_staff_id={sid} "
        f"OR id IN (SELECT project_id FROM vkpi_project_members WHERE staff_id={sid})"
    )


def _actor_kols_sql(staff_scope_id: int) -> str:
    """P1 隔离:某 staff「拥有」的 KOL(kol_pool_id)= 本人关注(收藏)∪ 本人项目里合作的 KOL。
    公司/官方账号属公共资产,不走此过滤(各聚合里 account_type<>'kol' / company 维度保持全局)。"""
    sid = int(staff_scope_id)
    return (
        f"SELECT kol_pool_id FROM vkpi_kol_pool_favorites WHERE staff_id={sid} "
        f"UNION SELECT a.kol_pool_id FROM vkpi_project_kol_assignments a "
        f"WHERE a.project_id IN ({_actor_projects_sql(sid)})"
    )
