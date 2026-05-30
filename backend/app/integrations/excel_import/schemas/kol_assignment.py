"""KOL assignment records extracted from project rows."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class KOLAssignmentRecord(BaseModel):
    source_sheet: str
    source_row: int
    source_token: str
    project_name: str
    project_product_model: str = ""
    planned_product: str = ""
    platform: str = "unknown"
    kol_handle: str = ""
    normalized_handle: str = ""
    status: Literal["expected"] = "expected"
    classification: Literal["owned", "kol", "unknown"] = "unknown"
    source_columns: dict[str, Any] = Field(default_factory=dict)

