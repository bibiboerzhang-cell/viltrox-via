"""
schemas/auth.py — 认证相关请求模型
"""
from pydantic import BaseModel


class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str
    student_id: str = ""


class LoginRequest(BaseModel):
    email: str
    password: str
