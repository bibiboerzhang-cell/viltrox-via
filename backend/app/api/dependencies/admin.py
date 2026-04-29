"""
api/dependencies/admin.py — 管理员依赖（重导出）
"""
from app.api.dependencies.auth import get_admin

__all__ = ["get_admin"]
