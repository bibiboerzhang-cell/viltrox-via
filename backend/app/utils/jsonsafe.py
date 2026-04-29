"""
utils/jsonsafe.py — JSON 安全处理工具
"""
import json


def safe_json_loads(raw: str, fallback=None):
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return fallback if fallback is not None else {}
