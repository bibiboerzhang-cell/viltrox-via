"""V-KPI 市场之声月报路由(件 C · 账二第 6 层,反哺产品部)。

- GET /api/admin/vkpi/market/voice-report?month=YYYY-MM
  → 四块聚合读数:抱怨聚类 / 愿望清单 / 需求空白焦段 / 给产品部的建议条目。
  实现在 app.domains.market.market_voice(纯词表/正则聚合已有数据,零新采集、零 LLM)。
  month 缺省 = 近 30 天;格式非法 400。
- GET /api/admin/vkpi/market/voice-report-ext?month=YYYY-MM
  → 报表增强 6 组字段:kpi_series / kpi_prev / sentiment_summary / alerts_state /
    platform_dist / line_voice(sparkline/环比药丸/情绪双线/告警彩条/平台条形/产品线声量榜)。
  实现在 app.domains.market.voice_report_ext(独立模块,窗口口径与 voice-report 一致,
  纯读增量端点,现有 voice-report 契约零改动)。
- POST /api/admin/vkpi/market/prd-referrals(require_tab vkpi write)
  → 市场之声「转产品部」真闭环:一条声音(vkpi_comments)/ 一条规则建议(lexicon_reco)
  落 vkpi_market_prd_referrals 一行(迁移 234;幂等,已转返回已有行 already_referred=true)。
  评论不存在 404;来源/入参非法、表未建等诚实 400 带 reason。
- GET /api/admin/vkpi/market/prd-referrals?status=&limit=
  → 转交列表(status ∈ referred/accepted/rejected;LIMIT 双层封顶 50;只读)。
  实现在 app.domains.market.prd_referrals;「已转产品部」KPI 窗口计数走 voice-report-ext。

诚实态:每块/每数据源缺数据由 domain 层返回 {status:"empty", reason};
聚合内部异常不 500,回 {status:"error", reason}(前端安静降级,月报页非阻塞)。
红线:纯读展示 + 转交登记,零触 viltrox_fit_score、不碰 rule_v0;只做判定与登记,不执行外部动作;
返回体不含任何评论作者个人字段(显示层宪法,domain 键白名单保障)。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-market-voice"])


@router.get("/market/voice-report")
def get_voice_report(
    month: str | None = None,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """市场之声 → 产品线月报:用户反馈聚类(全只读,不写库,零 LLM)。"""
    del staff
    from app.domains.market import market_voice

    try:
        return market_voice.voice_report(month=month)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 月报聚合失败不炸接口,诚实回原因
        logger.warning("voice_report failed for month=%s: %s", month, exc)
        return {"status": "error", "reason": str(exc)[:300], "month": month or None}


@router.get("/market/voice-report-ext")
def get_voice_report_ext(
    month: str | None = None,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """市场之声报表增强字段:趋势/环比/情绪/告警/平台/产品线声量(全只读,零 LLM)。"""
    del staff
    from app.domains.market import voice_report_ext

    try:
        return voice_report_ext.voice_report_ext(month=month)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 增强字段聚合失败不炸接口,诚实回原因
        logger.warning("voice_report_ext failed for month=%s: %s", month, exc)
        return {"status": "error", "reason": str(exc)[:300], "month": month or None}


class PrdReferralBody(BaseModel):
    """转产品部入参(domain 层还会做来源枚举 / 存在性 / 截断复核)。"""

    source_table: str = Field(..., description="vkpi_comments(反馈流)或 lexicon_reco(规则建议)")
    source_id: str = Field(..., min_length=1, max_length=200, description="评论 id 文本化 / 建议稳定 key")
    title: str = Field(..., min_length=1, max_length=300, description="转交标题(正文截断 / 建议标题)")
    detail: str = Field(default="", max_length=2000)
    category: str = Field(default="", max_length=80)


@router.post("/market/prd-referrals")
def api_create_prd_referral(
    body: PrdReferralBody,
    staff=Depends(require_tab("vkpi", "write")),
) -> dict[str, Any]:
    """市场之声「转产品部」:落 vkpi_market_prd_referrals 一行(幂等,已转返回已有行)。

    UNIQUE(source_table, source_id) 幂等锚:已转交 → already_referred=true 带已有行;
    评论不存在 404;来源非法 / 表未建等诚实 400 带 reason(绝不假装成功)。
    """
    from app.domains.market import prd_referrals

    result = prd_referrals.create_referral(
        body.source_table,
        body.source_id,
        body.title,
        detail=body.detail,
        category=body.category,
        staff=staff,
    )
    if not result.get("ok"):
        reason = str(result.get("reason") or "refer_failed")
        if reason == "comment_not_found":
            raise HTTPException(status_code=404, detail=reason)
        raise HTTPException(status_code=400, detail=reason)
    return result


@router.get("/market/prd-referrals")
def api_list_prd_referrals(
    status: str = Query(default="", description="referred / accepted / rejected(留空=全量)"),
    limit: int = Query(default=50, ge=1, le=50),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict[str, Any]:
    """转交列表(只读;LIMIT 双层封顶 50;表未建 → status=absent 诚实空列表)。"""
    del staff
    from app.domains.market import prd_referrals

    result = prd_referrals.list_referrals(status=status, limit=limit)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=str(result.get("reason") or "invalid_request"))
    return result
