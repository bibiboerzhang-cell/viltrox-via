"""Content asset records extracted from the official material sheet."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ContentAssetRecord(BaseModel):
    source_sheet: str
    source_row: int
    product_model: str = ""
    project_name: str = ""
    owner_internal: str = ""
    status: str = ""
    content_type: str = ""
    description: str = ""
    format: str = ""
    source_url: str = ""
    dimensions: str = ""
    publish_status: str = ""
    release_date: str = ""
    creator_handle: str = ""
    official_usage_raw: str = ""
    source_columns: dict[str, Any] = Field(default_factory=dict)

