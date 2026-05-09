"""Deprecated V-KPI workflow asset router shell.

Routes were split into vkpi_projects, vkpi_evidence_assets, and vkpi_costs.
This module intentionally exposes no mounted endpoints.
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-workflow-assets-deprecated"])
