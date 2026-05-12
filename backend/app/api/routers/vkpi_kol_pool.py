"""backend/app/api/routers/vkpi_kol_pool.py

R59: 独立 KOL Pool 路由 + 防火墙 + 审计装饰器集成示范.

这个文件是 R59 装饰器实战示范:
  - import 操作 → 防火墙 (require_budget) + 审计
  - link 操作 → 审计 (无防火墙,因为是内部数据修改)
  - list 操作 → 无装饰器 (read-only)

新增 endpoint:
  POST /api/admin/vkpi/kol-pool/import     # 一键导入 (防火墙 + 审计)
  GET  /api/admin/vkpi/kol-pool             # 列表
  GET  /api/admin/vkpi/kol-pool/{id}        # 详情
  POST /api/admin/vkpi/kol-pool/{id}/link   # 链接到 kols 主表 (审计)

注: 现有 vkpi_product_analysis.py 也有 import_items / list_pool 的暴露,
    本文件不替换那些,而是提供独立"KOL Pool 管理"入口,语义更清晰.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.services.vkpi import kol_pool
from app.services.vkpi.audit_decorator import audit_action
from app.services.vkpi.firewall_decorator import firewall_check


router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-kol-pool"])


# ─── Read endpoints (无装饰器) ──────────────────────


@router.get("/kol-pool")
def list_pool(
    limit: int = Query(default=100, ge=1, le=500),
    platform: str = Query(default=""),
    query: str = Query(default=""),
    data_status: str = Query(default=""),
    sort_by: str = Query(default="fit"),
    enrichable: bool | None = Query(default=None),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """列出 KOL Pool"""
    return kol_pool.list_pool(
        limit=limit,
        platform=platform,
        query=query,
        data_status=data_status,
        sort_by=sort_by,
        enrichable=enrichable,
    )


@router.get("/kol-pool/summary")
def get_pool_summary(
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """KOL Pool 资产池口径统计；不等于 Daily Top100 新候选。"""
    return kol_pool.summary()


@router.post("/kol-pool/batch-enrich")
@audit_action(
    action_type="kol_pool_batch_enrich",
    target_type="kol_pool",
    detail_extractor=lambda result, kwargs: f"batch enriched {result.get('enriched', 0)} attempted {result.get('attempted', 0)}",
    metadata_extractor=lambda result, kwargs: {
        "attempted": result.get("attempted", 0) if isinstance(result, dict) else 0,
        "enriched": result.get("enriched", 0) if isinstance(result, dict) else 0,
        "complete": result.get("complete", 0) if isinstance(result, dict) else 0,
        "partial": len(result.get("partial", [])) if isinstance(result, dict) else 0,
        "errors": len(result.get("errors", [])) if isinstance(result, dict) else 0,
        "capped": result.get("capped", False) if isinstance(result, dict) else False,
    },
)
def batch_enrich_pool_items(
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """小批量真实补齐候选池数据；服务端强制最多 5 条。"""
    ids = body.get("ids") or []
    if ids and not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="ids must be a list")
    return kol_pool.batch_enrich_items(
        ids=[int(value) for value in ids if str(value).strip().isdigit()] if ids else None,
        platform=str(body.get("platform") or ""),
        query=str(body.get("query") or ""),
        data_status=str(body.get("data_status") or "missing"),
        limit=max(1, min(int(body.get("limit") or 3), 5)),
        max_posts=max(1, min(int(body.get("max_posts") or 6), 24)),
        staff=staff,
    )


@router.get("/kol-pool/{kol_pool_id}")
def get_item(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """获取单个 KOL Pool 项"""
    try:
        return kol_pool.get_item(int(kol_pool_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/kol-pool/{kol_pool_id}/main-candidates")
def get_main_candidates(
    kol_pool_id: int,
    limit: int = Query(default=5, ge=1, le=20),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """查找 KOL Pool 项可能对应的 kols 主表记录。"""
    try:
        return kol_pool.main_candidates(int(kol_pool_id), limit=limit)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/kol-pool/{kol_pool_id}/promote")
@audit_action(
    action_type="kol_pool_promote_to_main",
    target_type="kol_pool",
    target_id_extractor=lambda result, kwargs: str(kwargs.get("kol_pool_id") or ""),
    detail_extractor=lambda result, kwargs: f"promote pool_id={kwargs.get('kol_pool_id')} mode={result.get('mode')} main_kol_id={result.get('main_kol_id')}",
    metadata_extractor=lambda result, kwargs: {
        "mode": result.get("mode") if isinstance(result, dict) else "",
        "main_kol_id": result.get("main_kol_id") if isinstance(result, dict) else None,
    },
)
def promote_to_main_kol(
    kol_pool_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """自动匹配或创建 kols 主表记录并链接，替代前端手动输入 ID。"""
    try:
        return kol_pool.promote_to_main(
            int(kol_pool_id),
            staff=staff,
            mode=str(body.get("mode") or "match_or_create"),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/kol-pool/{kol_pool_id}/enrich")
@audit_action(
    action_type="kol_pool_enrich",
    target_type="kol_pool",
    target_id_extractor=lambda result, kwargs: str(kwargs.get("kol_pool_id") or ""),
    detail_extractor=lambda result, kwargs: f"enriched pool_id={kwargs.get('kol_pool_id')} status={result.get('sync_status')}",
)
def enrich_pool_item(
    kol_pool_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """用真实平台 crawler 补齐单条候选的头像/播放/互动/适配度。"""
    try:
        return kol_pool.enrich_item(
            int(kol_pool_id),
            max_posts=max(1, min(int(body.get("max_posts") or 12), 50)),
            staff=staff,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ─── Write endpoints (装饰器集成示范) ────────────────


@router.post("/kol-pool/import")
@firewall_check(
    platform="",  # 平台从 body 动态取,防火墙在 service 层做(此处先用 feature_flag)
    feature_flag="",  # 暂时不用 feature_flag,只做 audit
    require_budget=False,  # import 本身不调外部 API,不用 budget
    bypass_param="force",
)
@audit_action(
    action_type="kol_pool_import",
    target_type="kol_pool",
    detail_extractor=lambda result, kwargs: f"imported {result.get('imported', 0)} skipped {result.get('skipped', 0)}",
    metadata_extractor=lambda result, kwargs: {
        "source_type": kwargs.get("body", {}).get("source_type") if isinstance(kwargs.get("body"), dict) else "",
        "platform": kwargs.get("body", {}).get("platform") if isinstance(kwargs.get("body"), dict) else "",
        "imported_count": result.get("imported", 0) if isinstance(result, dict) else 0,
    },
)
def import_pool(
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """
    一键导入 KOL 数据 (CSV / Apify / 手动).
    
    Body:
      items:        list[dict] - KOL 数据列表
      source_type:  str        - "manual" | "apify" | "csv" 等
      source_ref:   str        - 来源标识 (run_id / file_name 等)
      platform:     str        - 默认平台 (item 没指定时用)
      force:        bool       - owner 可用,bypass 防火墙
    
    返回:
      {imported: int, skipped: int, items: [...]}
    """
    items = body.get("items") or []
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="items must be a list")
    if not items:
        raise HTTPException(status_code=400, detail="items cannot be empty")
    
    return kol_pool.import_items(
        items,
        source_type=str(body.get("source_type") or "manual"),
        source_ref=str(body.get("source_ref") or ""),
        platform=str(body.get("platform") or ""),
        staff=staff,
    )


@router.post("/kol-pool/{kol_pool_id}/link")
@audit_action(
    action_type="kol_pool_link_to_main",
    target_type="kol_pool",
    detail_extractor=lambda result, kwargs: f"linked pool_id={kwargs.get('kol_pool_id')} to main_kol_id={kwargs.get('body', {}).get('main_kol_id')}",
)
def link_to_main_kol(
    kol_pool_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """
    把 KOL Pool 项链接到 kols 主表(作为活跃合作 KOL).
    
    Body:
      main_kol_id: int - kols 表的 id
    """
    main_kol_id = body.get("main_kol_id")
    if not main_kol_id:
        raise HTTPException(status_code=400, detail="main_kol_id is required")
    
    from app.db.connection import get_conn
    from datetime import datetime, UTC
    
    conn = get_conn()
    
    # 验证 kol_pool 存在
    pool_row = conn.execute(
        "SELECT id FROM vkpi_kol_pool WHERE id=?",
        (int(kol_pool_id),),
    ).fetchone()
    if not pool_row:
        raise HTTPException(status_code=404, detail="kol_pool item not found")
    
    # 验证 kols 主表存在
    main_row = conn.execute(
        "SELECT id FROM kols WHERE id=?",
        (int(main_kol_id),),
    ).fetchone()
    if not main_row:
        raise HTTPException(status_code=404, detail="main kol not found")
    
    # 链接
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "UPDATE vkpi_kol_pool SET linked_main_kol_id=?, updated_at=? WHERE id=?",
        (int(main_kol_id), now, int(kol_pool_id)),
    )
    conn.commit()
    
    return {
        "kol_pool_id": int(kol_pool_id),
        "main_kol_id": int(main_kol_id),
        "linked": True,
    }
