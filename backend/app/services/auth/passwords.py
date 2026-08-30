"""
services/auth/passwords.py — 密码哈希（重导出自 core.security）
"""
from app.core.passwords import hash_password, verify_password

__all__ = ["hash_password", "verify_password"]
