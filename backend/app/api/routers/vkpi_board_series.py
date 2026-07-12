"""板块 KPI 按日时序统一路由(挂账迸发① · 2026-07-12,append-only)。

- GET /api/admin/vkpi/board-series?board=<key>&days=30
  → 单板 KPI 卡按日序列 + 环比(series/metrics/basis 三键逐指标对齐);
  board ∈ projects / events / kol-profile / autonomy / launchpad / sku360 /
  creative / dealers;kol-profile 必带 &kol_id=,sku360 必带 &sku=。
  实现在 app.domains.dashboard.board_series(纯读聚合,金样板=voice_report_ext
  kpi_series 模式:日轴 0 填齐右沿钳 now / 环比同等流逝窗 / 单指标降级)。

诚实态:board 非法 / 缺必带参 → 400;sku 解析不到 → 404;表未建/全表空由 domain
层返回 {status:"empty", reason};聚合内部异常不 500,回 {status:"error", reason}
(前端安静降级,KPI 卡保持 spempty 诚实虚线)。
红线:纯读展示,零写库、零 LLM;零触 viltrox_fit_score、不碰 rule_v0;
返回体零个人字段零明文联系方式(domain 层 SELECT 白名单保障)。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-board-series"])


@router.get("/board-series")
def get_board_series(
    board: str = Query(..., max_length=40),
    days: int = Query(default=30, ge=1, le=365),
    kol_id: int | None = Query(default=None, ge=1),
    sku: str | None = Query(default=None, max_length=120),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """单板 KPI 卡按日序列 + 环比(全只读,不写库,零 LLM)。"""
    del staff
    from app.domains.dashboard import board_series

    try:
        return board_series.build_board_series(board, days=days, kol_id=kol_id, sku=sku)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 聚合失败不炸接口,诚实回原因
        logger.warning("board_series failed for board=%s: %s", board, exc)
        return {"status": "error", "reason": str(exc)[:300], "board": board}
