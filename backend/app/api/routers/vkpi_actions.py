"""V-KPI Action Inbox API (W1 · dry-run only).

GET  /api/admin/vkpi/actions/inbox          — scope 过滤后的今日建议(成员只见自己 owner 的)。
POST /api/admin/vkpi/actions/generate-daily — 跑 8 类生产者、幂等落库(dry_run=true,恒不执行不写业务表)。
红线:这两个端点都不会触发任何业务写或 LLM 执行;execute 走 W2 的 approve/执行器(尚未开放)。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.dependencies.perms import require_tab
from app.domains.actions import inbox

router = APIRouter(prefix="/api/admin/vkpi/actions", tags=["vkpi-actions"])


@router.get("/inbox")
def get_action_inbox(
    status: str = Query(default="suggested"),
    category: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict[str, Any]:
    return inbox.list_inbox(staff, status=status, category=category, limit=limit)


@router.post("/generate-daily")
def generate_daily(
    dry_run: bool = Query(default=True),
    persist: bool = Query(default=True),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict[str, Any]:
    # W1 恒 dry-run:dry_run 参数保留向前兼容,但生成器不会执行/不写业务表。
    return inbox.generate_daily_action_inbox(staff, dry_run=True, persist=persist)
