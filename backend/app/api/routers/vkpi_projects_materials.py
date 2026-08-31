"""项目物料库路由簇(行为不变搬迁,治 fan-out)。

从 vkpi_projects.py 整组 move 来的 GET/POST /projects/{project_id}/materials 两端点
(子 router 无 prefix);父文件在原位置 include_router 兜住,路由顺序与路径逐字节不变。
文件本体仍走既有 evidence uploads 存储;零新表、零触 viltrox_fit_score。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies.perms import require_tab
from app.api.routers.vkpi_projects_helpers import _material_row_to_item
from app.api.routers.vkpi_projects_masking import _scope_403
from app.core.logging import get_logger
from app.domains.access import policy, scope

router = APIRouter()

logger = get_logger(__name__)


@router.get("/projects/{project_id}/materials")
def list_project_materials(project_id: int, staff=Depends(require_tab("vkpi", "read"))):
    """项目物料库列表(P1,2026-07-03):复用 vkpi_content_assets 表——asset_type='material'
    且 post_id 为空的行即项目物料(零新表;文件本体走既有 evidence uploads 存储)。
    读端按项目级 scope 把关,与 timeline/retrospective 同口径。"""
    try:
        policy.require_project_read(int(project_id), staff)
    except policy.ScopeDenied as exc:
        raise _scope_403(exc) from exc
    # 懒 import:线上旧布局下函数内引用零风险(部署陷阱口诀)。
    from app.db.connection import get_conn
    from app.platform.db.schema import ensure_vkpi_schema

    ensure_vkpi_schema()
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT id, project_id, asset_url, asset_type, usage_rights, metadata_json, created_at
        FROM vkpi_content_assets
        WHERE project_id = ? AND asset_type = 'material' AND post_id IS NULL
        ORDER BY id DESC
        LIMIT 200
        """,
        (int(project_id),),
    ).fetchall()
    items = [_material_row_to_item(dict(row)) for row in rows]
    return {"project_id": int(project_id), "items": items, "count": len(items)}


@router.post("/projects/{project_id}/materials")
def add_project_material(project_id: int, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    """登记一条项目物料(P1):文件先经 POST /evidence/uploads 落盘拿 file_url,这里只收
    地址与元信息入库归档。复用 vkpi_content_assets:post_id 置空 + asset_type='material'
    区分,不建新表、不动 content post 相关聚合(其均按 post_id join,空值互不干扰)。"""
    payload = body or {}
    asset_url = str(payload.get("asset_url") or payload.get("file_url") or "").strip()
    if not asset_url:
        raise HTTPException(status_code=400, detail="asset_url required")
    try:
        scope.assert_project_access(int(project_id), staff, write=True)
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc
    import json as _json
    from datetime import datetime as _dt, timezone as _tz

    from app.db.connection import get_conn
    from app.platform.db.schema import ensure_vkpi_schema

    ensure_vkpi_schema()
    conn = get_conn()
    if not conn.execute("SELECT 1 FROM vkpi_projects WHERE id=?", (int(project_id),)).fetchone():
        raise HTTPException(status_code=404, detail="project not found")
    try:
        size = int(payload.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    meta = {
        "file_name": str(payload.get("file_name") or "")[:200],
        "file_type": str(payload.get("file_type") or "")[:100],
        "size": size,
        "note": str(payload.get("note") or "")[:500],
        "uploaded_by_staff_id": scope.actor_staff_id(staff) or None,
    }
    now = _dt.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        """
        INSERT INTO vkpi_content_assets (
            post_id, project_id, asset_url, asset_type, usage_rights, metadata_json, created_at
        ) VALUES (?,?,?,?,?,?,?)
        """,
        (None, int(project_id), asset_url, "material", "internal", _json.dumps(meta, ensure_ascii=False), now),
    )
    conn.commit()
    row = conn.execute(
        """
        SELECT id, project_id, asset_url, asset_type, usage_rights, metadata_json, created_at
        FROM vkpi_content_assets
        WHERE project_id = ? AND asset_type = 'material' AND post_id IS NULL
        ORDER BY id DESC
        LIMIT 1
        """,
        (int(project_id),),
    ).fetchone()
    item = _material_row_to_item(dict(row)) if row else {}
    # 审计留痕:best-effort,记账失败不影响物料入库主流程。
    try:
        from app.domains import audit

        audit.log_business_event(
            staff_id=scope.actor_staff_id(staff),
            action_type="project_material_add",
            target_type="project",
            target_id=int(project_id),
            detail=asset_url,
            metadata={"asset_id": item.get("id"), "file_name": meta["file_name"]},
        )
    except Exception:
        logger.debug("项目素材活动流记录失败(best-effort,不影响入库结果)", exc_info=True)
    return {"status": "stored", "item": item}
