"""Anthropic client helper for Claude vision analyzers."""
from __future__ import annotations

try:
    import anthropic
except ImportError:  # pragma: no cover - optional dependency
    anthropic = None

from app.core.config import ANTHROPIC_API_KEY
from app.services.ai.clients.claude_client import ANTHROPIC_AVAILABLE


def _build_anthropic_client():
    if not ANTHROPIC_AVAILABLE or not ANTHROPIC_API_KEY:
        return None
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
