"""KOL claim audit helpers."""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.services.vkpi import audit


logger = get_logger(__name__)


def log_kol_audit(
    *,
    actor_staff_id: int,
    action_type: str,
    kol_id: int,
    detail: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    if not actor_staff_id:
        return
    try:
        audit.log_business_event(
            staff_id=int(actor_staff_id),
            action_type=action_type,
            target_type="kol",
            target_id=int(kol_id),
            detail=detail,
            metadata=metadata or {},
        )
    except Exception as exc:
        logger.warning("kol lifecycle audit failed for %s/%s: %s", action_type, kol_id, exc)
        return
