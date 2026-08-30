"""Stable public facade for Via conversational turn orchestration."""
from __future__ import annotations

from typing import Any

from app.services.via.session_reply_orchestration import _reply_in_via_session


async def reply_in_via_session(
    *,
    session_key: str,
    user_text: str,
    current_surface: str = "",
    event_bus: Any = None,
) -> dict[str, Any]:
    """Execute one Via conversation turn without changing the public contract."""
    return await _reply_in_via_session(
        session_key=session_key,
        user_text=user_text,
        current_surface=current_surface,
        event_bus=event_bus,
    )


__all__ = ["reply_in_via_session"]
