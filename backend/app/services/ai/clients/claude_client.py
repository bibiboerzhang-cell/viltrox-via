"""
services/ai/clients/claude_client.py — Anthropic Claude SDK 初始化
Fix: .env 加载前读取环境变量的问题
"""
from __future__ import annotations
import os
from pathlib import Path

from app.core.logging import get_logger


logger = get_logger(__name__)


def _read_env_key(k: str) -> str:
    """直接读 .env 文件，绕过模块加载时序问题"""
    try:
        for line in Path(".env").read_text().splitlines():
            if line.startswith(k + "="):
                return line[len(k)+1:].strip()
    except Exception:
        pass
    return ""


try:
    import anthropic
    _api_key = os.environ.get("ANTHROPIC_API_KEY", "") or _read_env_key("ANTHROPIC_API_KEY")
    if not _api_key:
        logger.warning("ai.claude.disabled_missing_key")
        ANTHROPIC_AVAILABLE = False
        _anthropic_client = None
    else:
        _anthropic_client = anthropic.Anthropic(api_key=_api_key)
        ANTHROPIC_AVAILABLE = True
        logger.info("ai.claude.ready", extra={"key_prefix": _api_key[:8]})
except ImportError:
    anthropic = None  # type: ignore
    _anthropic_client = None
    ANTHROPIC_AVAILABLE = False
    logger.warning("ai.claude.sdk_missing")


def get_claude_client():
    """获取 Claude client，如果初始化失败则重试"""
    if _anthropic_client:
        return _anthropic_client
    key = os.environ.get("ANTHROPIC_API_KEY", "") or _read_env_key("ANTHROPIC_API_KEY")
    if key and anthropic:
        return anthropic.Anthropic(api_key=key)
    return None
