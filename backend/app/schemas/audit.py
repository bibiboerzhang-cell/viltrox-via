"""
schemas/audit.py — 审计请求模型
"""
from __future__ import annotations
from typing import Dict, List, Optional
from pydantic import BaseModel, Field

class MetricsInput(BaseModel):
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    favorites: int = 0


class HintsInput(BaseModel):
    logo: bool = False
    product: bool = False
    voice: bool = False
    review: bool = False


class UploadedVideoInput(BaseModel):
    video_id: str = ""
    asset_id: int = 0
    filename: str = ""
    mime_type: str = ""
    size_mb: float = 0.0
    path: str = ""
    r2_key: str = ""


class AuditRequest(BaseModel):
    url: str = ""
    user_handle: str = ""          # handle typed by creator in the form
    linked_handles: Dict[str, str] = Field(default_factory=dict)  # {"instagram": "@viltrox.official", ...}
    title: str = ""
    caption: str = ""
    raw_text: str = ""
    metrics: MetricsInput = Field(default_factory=MetricsInput)
    hints: HintsInput = Field(default_factory=HintsInput)
    uploaded_video: Optional[UploadedVideoInput] = None


# ──────────────────────────────────────────────
