"""
api/dependencies/auth.py — FastAPI 认证依赖注入
"""
from __future__ import annotations

from fastapi import HTTPException, Request

from app.core.security import get_current_user_async, get_current_user_stream_async, require_admin_async


async def get_user(request: Request):
    """FastAPI Depends: 获取当前用户（可选）"""
    return await get_current_user_async(request)


async def get_user_required(request: Request):
    """FastAPI Depends: 获取当前用户（必须）"""
    user = await get_current_user_async(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


async def get_user_required_stream(request: Request):
    """Require a path-bound, short-lived and one-time SSE ticket."""
    user = await get_current_user_stream_async(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


async def get_admin(request: Request):
    """FastAPI Depends: 要求管理员权限"""
    return await require_admin_async(request)
