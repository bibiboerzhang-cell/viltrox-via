"""Admin router aggregate.

Endpoint implementations live in admin_* modules. This facade preserves the
existing app.api.routers.admin.router import contract.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.routers import admin_accounts, admin_learning, admin_ops, admin_submissions

router = APIRouter(tags=["admin"])
router.include_router(admin_accounts.router)
router.include_router(admin_submissions.router)
router.include_router(admin_ops.router)
router.include_router(admin_learning.router)
