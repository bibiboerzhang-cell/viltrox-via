"""V-KPI KOL 质量分聚合 + FTC 披露扫描路由(两个读端小件合一个域)。

- GET /api/admin/vkpi/kol-pool/{kol_pool_id}/quality-score
  → 综合质量分:聚合该 KOL 已深析视频的 final_v1 六维读数 → 0-100 综合分
    + 分项条 + 样本数/置信度诚实标注。
- GET /api/admin/vkpi/kol-pool/{kol_pool_id}/ftc-scan
  → FTC 披露扫描:词表法(否定感知)扫标题/描述披露标记 + 合作迹象共现,
    输出 disclosed / undisclosed_suspect / clean 三组清单与 risk 条目
    (info/warn,措辞「疑似未披露」,绝不下法律结论)。

实现在 app.domains.kol.quality_compliance(纯聚合已有数据,零新采集、零 LLM)。
诚实态:KOL 不存在 404;缺数据由 domain 层返回 {status:"empty", reason};
聚合内部异常不 500,回 {status:"error", reason}(前端安静缺席,增益块非阻塞)。
红线:纯读展示,独立展示分绝不写回任何表;零触 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-quality-compliance"])


@router.get("/kol-pool/{kol_pool_id}/quality-score")
def get_kol_quality_score(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """KOL 综合质量分:final_v1 深析六维聚合(全只读,不写库)。"""
    del staff
    from app.domains.kol import quality_compliance

    try:
        return quality_compliance.quality_score(int(kol_pool_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 增益块失败不炸接口,诚实回原因
        logger.warning("quality_score failed for kol_pool_id=%s: %s", kol_pool_id, exc)
        return {"status": "error", "reason": str(exc)[:300], "kol_pool_id": int(kol_pool_id)}


@router.get("/kol-pool/{kol_pool_id}/ftc-scan")
def get_kol_ftc_scan(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """KOL FTC 披露扫描:词表法三组清单 + 疑似未披露 risk 条目(全只读,不写库)。"""
    del staff
    from app.domains.kol import quality_compliance

    try:
        return quality_compliance.ftc_scan(int(kol_pool_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 增益块失败不炸接口,诚实回原因
        logger.warning("ftc_scan failed for kol_pool_id=%s: %s", kol_pool_id, exc)
        return {"status": "error", "reason": str(exc)[:300], "kol_pool_id": int(kol_pool_id)}
