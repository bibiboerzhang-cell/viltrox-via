"""镜头出镜洞察(2026-08-22 车道 L4):纯读聚合端点。

GET /api/admin/vkpi/lens-insights/summary?scope=collection|all&staff_id=&limit=
    按镜头家族 / SKU 聚合:出镜视频数、KOL 数、总播放(点时实测 Σ view_count,未实测剔除
    并注明条数)、证据来源分布(画面 / 字幕·文字 / 口播 / 未注明)+ 覆盖率 + unresolved 原文。
    scope=collection(缺省)= MY KOL 收藏 ∪ 授权共享(员工恒本人,管理层缺省全团队,
    ?staff_id= 看指定成员);scope=all = 全部已深析视频,管理层专属。
GET /api/admin/vkpi/lens-insights/kol/{kol_pool_id}
    单 KOL 用过哪些镜头(行级门禁与 /my-kol/{id}/videos 同款 assert_target_readable)。

数据源:vkpi_kol_lens_evidence(迁移 287 派生表,回填脚本 scripts/ops/backfill_lens_evidence.py
从 final_v1 深析缓存抽取并按 vkpi_products 归一)。
红线:纯 SELECT;零 LLM / 零 provider;绝不写 viltrox_fit_score / 不触 rule_v0;
表未迁移 → 503 lens_evidence_table_missing(诚实,不装空)。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.db.connection import get_conn, table_exists
from app.domains.access import scope
from app.domains.kol import lens_evidence_store
from app.domains.kol.my_kol_paid_action_access import (
    MyKolPaidActionError,
    assert_target_readable,
)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-lens-insights"])


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _assert_table() -> None:
    if not table_exists("vkpi_kol_lens_evidence"):
        raise HTTPException(status_code=503, detail="lens_evidence_table_missing")


@router.get("/lens-insights/summary")
def lens_insights_summary_endpoint(
    scope_mode: str = Query(default="collection", alias="scope", pattern="^(collection|all)$"),
    staff_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=40, ge=1, le=60),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    _assert_table()
    scope_all = scope_mode == "all"
    if scope_all and not scope.can_view_all(staff):
        raise HTTPException(status_code=403, detail="lens_insights_all_scope_manager_only")
    target = None
    if not scope_all:
        target = scope.effective_staff_id(staff, staff_id)
        if target is None and not scope.can_view_all(staff):
            raise HTTPException(status_code=403, detail="no staff identity in scope")
    body = lens_evidence_store.lens_summary(
        get_conn(),
        staff_scope_id=target,
        scope_all=scope_all,
        limit=int(limit),
    )
    body["viewer_scope"] = scope.scope_context(staff, staff_id)
    return body


@router.get("/lens-insights/kol/{kol_pool_id}")
def lens_insights_kol_endpoint(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    _assert_table()
    pid = _int(kol_pool_id)
    if pid <= 0:
        raise HTTPException(status_code=400, detail="kol_pool_id required")
    conn = get_conn()
    try:
        assert_target_readable(conn, kol_pool_id=pid, staff=staff)
    except MyKolPaidActionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc
    return lens_evidence_store.kol_lenses(conn, kol_pool_id=pid)


__all__ = ["router"]
