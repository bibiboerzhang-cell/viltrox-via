"""
schemas/rewards.py — 奖品相关请求模型
"""
from pydantic import BaseModel


class RewardItemRequest(BaseModel):
    title: str
    description: str = ""
    category: str
    points_cost: int
    meta_label: str = ""
    image_url: str = ""
    stock: int = 0
    sort_order: int = 100
    status: str = "draft"
