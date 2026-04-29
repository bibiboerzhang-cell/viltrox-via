"""
services/creators/profile.py — 创作者档案管理（重导出）
"""
from app.services.scoring.creator import (
    get_creator_profile,
    update_creator_profile,
    compute_creator_trend,
)

__all__ = ["get_creator_profile", "update_creator_profile", "compute_creator_trend"]
