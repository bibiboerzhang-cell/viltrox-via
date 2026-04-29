"""
core/logging.py — 日志配置（可扩展 structlog / loguru）
"""
from __future__ import annotations

import logging
import os


_LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").strip().upper() or "INFO"

logging.basicConfig(
    level=getattr(logging, _LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger("viltrox")


def get_logger(name: str | None = None) -> logging.Logger:
    if not name:
        return logger
    if name.startswith("app."):
        name = name[4:]
    return logger.getChild(name)
