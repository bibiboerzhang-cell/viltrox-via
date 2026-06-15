"""V-KPI 公司库存(Company Inventory)路由 — CRUD + 调动审计账本。

前缀 /api/admin/vkpi/inventory;读 require_tab("vkpi","read")、写 require_tab("vkpi","write")。
库存是公司公共表(跨 Event 共享),不按员工 owner scope 过滤 —— 任何能读 vkpi 的员工都可见;
但每一次写都带登录员工身份落审计流水(谁/何时/delta/事由),由 service 层在事务里写入。
与 KOL Pool / 评分域物理隔离;迁移 135(vkpi_inventory)+ 136(vkpi_inventory_movements)。
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.domains.events import inventory_service


router = APIRouter(prefix="/api/admin/vkpi/inventory", tags=["vkpi-inventory"])


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — surface DB/serialize errors as 500 with msg
        raise HTTPException(status_code=500, detail=f"inventory error: {exc}") from exc


# ── Inventory CRUD ──────────────────────────────────────────────────────────
@router.get("")
def list_inventory(staff=Depends(require_tab("vkpi", "read"))):
    return _guard(inventory_service.list_inventory)


@router.post("")
def create_item(body: dict = Body(default_factory=dict), staff=Depends(require_tab("vkpi", "write"))):
    return _guard(inventory_service.create_item, body or {}, staff)


# 调动记录:必须在动态 /{sku} 之前注册,否则 "movements"-shaped 路径会被 /{sku} 抢走。
@router.get("/{sku}/movements")
def list_item_movements(
    sku: str,
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    return _guard(inventory_service.list_movements, sku, limit)


@router.patch("/{sku}")
def update_item(sku: str, body: dict = Body(default_factory=dict), staff=Depends(require_tab("vkpi", "write"))):
    return _guard(inventory_service.update_item, sku, body or {}, staff)


@router.delete("/{sku}")
def delete_item(sku: str, body: dict = Body(default_factory=dict), staff=Depends(require_tab("vkpi", "write"))):
    return _guard(inventory_service.delete_item, sku, body or {}, staff)
