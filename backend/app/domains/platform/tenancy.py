"""Tenant Kernel(轴B)—— 显式租户解析。默认 Viltrox=org 1,现有数据隐式归 1。

红线:本刀只读解析,不改业务;后续逐表加 organization_id + scope 收窄。零触 viltrox_fit_score。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists

logger = get_logger(__name__)

DEFAULT_ORG_ID = 1


def default_organization_id() -> int:
    return DEFAULT_ORG_ID


def current_org_id(staff: dict[str, Any] | None = None) -> int:
    """据 staff 解析其租户;无成员记录/缺表 → 默认 1(向后兼容,现有数据归 Viltrox)。"""
    sid = None
    try:
        sid = int((staff or {}).get("id") or 0) or None
    except (TypeError, ValueError):
        sid = None
    if not sid or not table_exists("organization_members"):
        return DEFAULT_ORG_ID
    try:
        row = get_conn().execute(
            "SELECT organization_id FROM organization_members WHERE staff_id = ? ORDER BY id LIMIT 1", (sid,)
        ).fetchone()
        return int(dict(row)["organization_id"]) if row else DEFAULT_ORG_ID
    except Exception:
        logger.debug("tenancy.current_org_failed", exc_info=True)
        return DEFAULT_ORG_ID


def get_organization(org_id: int) -> dict[str, Any] | None:
    if not table_exists("organizations"):
        return None
    try:
        row = get_conn().execute(
            "SELECT id, name, slug, industry, scoring_template, brand_profile_json, status FROM organizations WHERE id = ?",
            (int(org_id),),
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def list_organizations() -> list[dict[str, Any]]:
    if not table_exists("organizations"):
        return []
    try:
        rows = get_conn().execute(
            "SELECT id, name, slug, industry, scoring_template, status FROM organizations ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def current_tenant(staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """当前租户摘要(给 /health / 顶栏显示)。"""
    oid = current_org_id(staff)
    org = get_organization(oid) or {"id": oid, "name": "Viltrox", "slug": "viltrox", "industry": "camera_lens"}
    return {"organization_id": oid, "name": org.get("name"), "slug": org.get("slug"),
            "industry": org.get("industry"), "scoring_template": org.get("scoring_template"),
            "note": "默认 Viltrox=org1;现有数据隐式归此租户;多租户 scope 后续逐表收窄。"}
