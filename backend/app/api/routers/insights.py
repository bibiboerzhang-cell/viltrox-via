"""
api/routers/insights.py — 市场洞察路由

NOTE: 从 main_original.py 提取 (~lines 7154-7250)
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/admin", tags=["insights"])

# TODO: Extract insights/benchmarks/growth endpoints
