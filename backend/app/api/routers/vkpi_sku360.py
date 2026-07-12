"""件③ SKU 360° 路由 —— SKU×内容表现 + 360° 档案(纯聚合只读,零 LLM)。

端点(全部 require_tab("vkpi", "read")):
  GET /api/admin/vkpi/sku/list?query=&limit=          前端选择器搜 vkpi_products
  GET /api/admin/vkpi/sku/{sku_id_or_code}/content-performance
  GET /api/admin/vkpi/sku/{sku_id_or_code}/profile
  GET /api/admin/vkpi/sku/{sku_id_or_code}/persona    产品知识库画像(vkpi_product_persona 只读)

领域逻辑全在 app.domains.products.sku_performance;本文件只做接线 + 404 转换。
路由声明顺序:/sku/list 在参数路由之前,避免被 {sku_id_or_code} 吞掉。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.domains.products import sku_performance

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-sku360"])


@router.get("/sku/list")
def sku_list(
    query: str = Query(default="", max_length=120),
    limit: int = Query(default=30, ge=1, le=200),
    staff=Depends(require_tab("vkpi", "read")),
):
    return sku_performance.list_skus(query=query, limit=limit)


@router.get("/sku/{sku_id_or_code}/content-performance")
def sku_content_performance(
    sku_id_or_code: str,
    staff=Depends(require_tab("vkpi", "read")),
):
    payload = sku_performance.sku_content_performance(sku_id_or_code)
    if payload.get("status") == "not_found":
        raise HTTPException(status_code=404, detail=f"SKU not found: {sku_id_or_code}")
    return payload


@router.get("/sku/{sku_id_or_code}/profile")
def sku_profile(
    sku_id_or_code: str,
    staff=Depends(require_tab("vkpi", "read")),
):
    payload = sku_performance.sku_profile(sku_id_or_code)
    if payload.get("status") == "not_found":
        raise HTTPException(status_code=404, detail=f"SKU not found: {sku_id_or_code}")
    return payload


@router.get("/sku/{sku_id_or_code}/persona")
def sku_persona(
    sku_id_or_code: str,
    staff=Depends(require_tab("vkpi", "read")),
):
    payload = sku_performance.sku_persona(sku_id_or_code)
    if payload.get("status") == "not_found":
        raise HTTPException(status_code=404, detail=f"SKU not found: {sku_id_or_code}")
    return payload
