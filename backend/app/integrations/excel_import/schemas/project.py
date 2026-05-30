"""Project records extracted from the launch-plan sheet."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ProjectRecord(BaseModel):
    source_sheet: str
    source_row: int
    name: str
    product_model: str = ""
    category_l1: str = ""
    category_l2: str = ""
    launch_date: str = ""
    price_raw: str = ""
    discount_terms_raw: str = ""
    slogan: str = ""
    hashtags: list[str] = Field(default_factory=list)
    source_columns: dict[str, Any] = Field(default_factory=dict)

