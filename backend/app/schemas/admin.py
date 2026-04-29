"""
schemas/admin.py — 管理后台请求模型
"""
from __future__ import annotations
from typing import Dict, Optional
from pydantic import BaseModel

class ManualSubmissionRequest(BaseModel):
    platform:       str = "Instagram"
    extracted_handle: str = ""
    url:            str = ""
    title:          str = ""
    detection_status: str = "confirmed"
    product_series: str = ""
    product_label:  str = ""
    final_score:    int = 0
    creator_score:  int = 0
    overall_score:  int = 0
    views:          int = 0
    likes:          int = 0
    comments:       int = 0
    shares:         int = 0
    recommendation: str = "Manually added by admin"
    memo:           str = ""

class VerifyRegisterRequest(BaseModel):
    platform: str
    handle:   str
    code:     str

class ManualApproveRequest(BaseModel):
    campaign_score: Optional[int] = None   # override if provided
    creator_score:  Optional[int] = None
    overall_score:  Optional[int] = None
    product_series: Optional[str] = None
    product_label:  Optional[str] = None
    memo_append:    Optional[str] = None
    hints: Optional[Dict[str, bool]] = None  # logo/product/voice/review

class ReanalyzeRequest(BaseModel):
    url: Optional[str] =  None