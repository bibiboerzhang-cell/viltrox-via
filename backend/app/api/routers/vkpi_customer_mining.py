"""V-KPI 客户库挖 KOL 路由(B4 件④)。

- GET /api/admin/vkpi/discovery/customer-miners
    侦察本地 Shopify / GOAFFPRO 订单与客户真落点 → 邮箱撞库 + 名字模糊匹配出
    「你的买家里的创作者」候选;本地无客户数据诚实 empty + ready_when 说明。

实现在 app.domains.discovery.customer_mining(懒 import,只读侦察零写库)。
诚实态:无数据回 {status:"empty", reason, ready_when};内部异常不 500,
回 {status:"error", reason}(前端安静降级)。
合规红线:输出邮箱一律脱敏,真值联系方式仅经既有 contact_reveal 门控查看;
零 LLM;绝不触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-customer-mining"])


@router.get("/discovery/customer-miners")
def customer_miners(
    staff=Depends(require_tab("vkpi", "read")),
) -> dict[str, Any]:
    """客户库挖 KOL:买家 × KOL 池撞库候选(只读侦察;无客户数据诚实 empty)。"""
    del staff
    from app.domains.discovery import customer_mining

    try:
        return customer_mining.mine_customers()
    except Exception as exc:  # noqa: BLE001 — 侦察失败诚实回原因,不 500
        logger.warning("mine_customers failed: %s", exc, exc_info=True)
        return {"status": "error", "reason": str(exc)[:300], "candidates": [], "sources": []}
