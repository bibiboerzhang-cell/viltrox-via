"""V-KPI 公司库存(Company Inventory)路由 — CRUD + 调动审计账本。

前缀 /api/admin/vkpi/inventory;读 require_tab("vkpi","read")、写 require_tab("vkpi","write")。
库存是公司公共表(跨 Event 共享),不按员工 owner scope 过滤 —— 任何能读 vkpi 的员工都可见;
但每一次写都带登录员工身份落审计流水(谁/何时/delta/事由),由 service 层在事务里写入。
与 KOL Pool / 评分域物理隔离;迁移 135(vkpi_inventory)+ 136(vkpi_inventory_movements)。
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.domains import business_truth
from app.domains.access import scope
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


def _bounded_body(body: dict, allowed: set[str]) -> None:
    unknown = sorted(set(body or {}) - allowed)
    if unknown:
        raise HTTPException(
            status_code=400,
            detail="unsupported request fields: " + ", ".join(unknown),
        )


def _authorization_or_http(body: dict, *, staff: dict, action: str) -> dict:
    try:
        return business_truth.require_authorization_evidence(body, staff=staff, action=action)
    except business_truth.BusinessTruthWriteBlocked as exc:
        status_code = 409 if exc.reason == "feature_disabled" else 400
        raise HTTPException(
            status_code=status_code,
            detail={"reason": exc.reason, "message": str(exc)},
        ) from exc


def _verification_guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except inventory_service.InventoryVerificationConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ── Inventory CRUD ──────────────────────────────────────────────────────────
@router.get("")
def list_inventory(staff=Depends(require_tab("vkpi", "read"))):
    return _guard(inventory_service.list_inventory)


@router.post("")
def create_item(body: dict = Body(default_factory=dict), staff=Depends(require_tab("vkpi", "write"))):
    return _guard(inventory_service.create_item, body or {}, staff)


# ── Groups 组团 + SKU 导入:必须在动态 /{sku} 之前注册,否则 "groups"/"sku" 路径会被 /{sku} 抢走。
@router.get("/groups")
def list_groups(event_id: str | None = Query(default=None), staff=Depends(require_tab("vkpi", "read"))):
    return _guard(inventory_service.list_groups, event_id)


@router.post("/groups")
def create_group(body: dict = Body(default_factory=dict), staff=Depends(require_tab("vkpi", "write"))):
    return _guard(inventory_service.create_group, body or {}, staff)


@router.patch("/groups/{group_id}")
def update_group(group_id: str, body: dict = Body(default_factory=dict), staff=Depends(require_tab("vkpi", "write"))):
    return _guard(inventory_service.update_group, group_id, body or {}, staff)


@router.delete("/groups/{group_id}")
def delete_group(group_id: str, body: dict = Body(default_factory=dict), staff=Depends(require_tab("vkpi", "write"))):
    return _guard(inventory_service.delete_group, group_id, body or {}, staff)


@router.post("/groups/{group_id}/items")
def add_to_group(group_id: str, body: dict = Body(default_factory=dict), staff=Depends(require_tab("vkpi", "write"))):
    return _guard(inventory_service.add_to_group, group_id, body or {}, staff)


@router.delete("/groups/{group_id}/items/{item_id}")
def remove_from_group(group_id: str, item_id: str, staff=Depends(require_tab("vkpi", "write"))):
    return _guard(inventory_service.remove_from_group, group_id, item_id, staff)


@router.get("/sku/{sku}/groups")
def sku_groups(sku: str, staff=Depends(require_tab("vkpi", "read"))):
    return _guard(inventory_service.find_sku_groups, sku)


@router.post("/import-from-catalog")
def import_from_catalog(staff=Depends(require_tab("vkpi", "write"))):
    """把产品库里有、库存表没有的 SKU 批量补进库存(qty=0,幂等只增不改)。"""
    return _guard(inventory_service.import_missing_from_catalog, staff)


# 调动记录:必须在动态 /{sku} 之前注册,否则 "movements"-shaped 路径会被 /{sku} 抢走。
@router.get("/{sku}/movements")
def list_item_movements(
    sku: str,
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    return _guard(inventory_service.list_movements, sku, limit)


@router.post("/{sku}/verify")
def verify_quantity(
    sku: str,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "admin")),
):
    """Bind an existing unchanged quantity to a source receipt; never sets qty."""

    payload = body or {}
    _bounded_body(
        payload,
        {
            "source_type",
            "source_ref",
            "source_observed_at",
            "evidence_sha256",
            "expected_id",
            "expected_qty",
            "expected_row_version",
            "expected_updated_at",
            "authorization_evidence",
        },
    )
    authorization = _authorization_or_http(
        payload, staff=staff, action="inventory_quantity_verify"
    )
    return _verification_guard(
        inventory_service.verify_quantity,
        sku,
        payload,
        authorization_evidence=authorization,
        staff=staff,
    )


@router.post("/{sku}/verification/revoke")
def revoke_quantity_verification(
    sku: str,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "admin")),
):
    """Invalidate a quantity receipt without changing the stored quantity."""

    payload = body or {}
    _bounded_body(
        payload,
        {
            "expected_id",
            "expected_qty",
            "expected_row_version",
            "expected_updated_at",
            "authorization_evidence",
        },
    )
    authorization = _authorization_or_http(
        payload, staff=staff, action="inventory_quantity_verification_revoke"
    )
    return _verification_guard(
        inventory_service.revoke_quantity_verification,
        sku,
        payload,
        authorization_evidence=authorization,
        staff=staff,
    )


@router.patch("/{sku}")
def update_item(sku: str, body: dict = Body(default_factory=dict), staff=Depends(require_tab("vkpi", "write"))):
    return _guard(inventory_service.update_item, sku, body or {}, staff)


@router.delete("/{sku}")
def delete_item(sku: str, body: dict = Body(default_factory=dict), staff=Depends(require_tab("vkpi", "write"))):
    return _guard(inventory_service.delete_item, sku, body or {}, staff)
