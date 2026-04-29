"""
services/creators/social_accounts.py — 社交账号绑定服务
"""
from __future__ import annotations

from app.db.connection import get_conn
from app.utils.handles import normalize_platform, normalize_handle
