"""V-KPI MY KOL 每日学习摘要路由(A3)。

- GET /api/admin/vkpi/my-kol/daily-digest?days=&staff_id=
  → 收藏 KOL + 公司官号「这段时间发生了什么」五块聚合读数:
    🆕 新视频(evidence 新增)/ 📈 播放异动(环比)+ followers 环比 /
    💬 提及 Viltrox 的新内容(词表)/ 📇 联系方式新获得(不出明文)/
    🏢 官号昨日表现 + 最好一条。
  实现在 app.domains.kol.daily_digest(纯聚合已有数据,零新采集、零 LLM)。

范围:员工强制只看自己的 MY KOL 集合(scope.effective_staff_id);管理层
(can_view_all)默认全员并集,可传 ?staff_id= 看某成员。官号块是公司资产,不分人。
诚实态:数据不足的块由 domain 层返回 {status:"empty", reason};
聚合内部异常不 500,回 {status:"error", reason}(前端摘要卡安静降级,非阻塞)。
红线:纯读展示,零触 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-daily-digest"])


@router.get("/my-kol/daily-digest")
def get_my_kol_daily_digest(
    days: int = Query(default=1, ge=1, le=90),
    staff_id: int | None = Query(default=None, ge=1),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """MY KOL + 官号 每日学习摘要(全只读,不写库)。"""
    from app.domains.access import scope
    from app.domains.kol import daily_digest

    # 员工 → 自己;管理层未指名 → None(全员并集);管理层指名 → 该成员。
    target = scope.effective_staff_id(staff, staff_id)
    try:
        return daily_digest.digest(days=int(days), staff_id=target)
    except Exception as exc:  # noqa: BLE001 — 增益块失败不炸接口,诚实回原因
        logger.warning("daily_digest failed days=%s staff_id=%s: %s", days, target, exc)
        return {"status": "error", "reason": str(exc)[:300]}
