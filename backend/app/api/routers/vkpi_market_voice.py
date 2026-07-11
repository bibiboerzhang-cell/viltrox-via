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

诚实态:每块/每数据源缺数据由 domain 层返回 {status:"empty", reason};
聚合内部异常不 500,回 {status:"error", reason}(前端安静降级,月报页非阻塞)。
红线:纯读展示,零触 viltrox_fit_score、不碰 rule_v0;只做判定与展示,不执行外部动作。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

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
