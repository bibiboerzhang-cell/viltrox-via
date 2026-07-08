"""
api/dependencies/auth.py — FastAPI 认证依赖注入
"""
from __future__ import annotations

from fastapi import HTTPException, Request

from app.core.security import get_current_user_async, require_admin_async


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
    """FastAPI Depends: 同 get_user_required，但额外允许 token 走 ?access_token= 查询参数。

    只给 SSE / EventSource 端点用 —— 浏览器原生 EventSource 无法带 Authorization header，
    否则实时流会 403。普通端点仍走 get_user_required(不接受 URL token)。
    """
    user = await get_current_user_async(request, allow_query_token=True)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


async def get_admin(request: Request):
    """FastAPI Depends: 要求管理员权限"""
    return await require_admin_async(request)
