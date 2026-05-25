"""KOL decision use cases."""
from __future__ import annotations

from typing import Any

from app.services.vkpi import kol_decisions


def create_decision(body: dict[str, Any], *, staff: dict[str, Any]) -> dict[str, Any]:
    return kol_decisions.create_decision(body or {}, staff=staff)


def list_decisions(
    *,
    kol_pool_id: int = 0,
    decision_key: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    return kol_decisions.list_decisions(kol_pool_id=kol_pool_id, decision_key=decision_key, limit=limit)


def list_followups(
    *,
    status: str = "due",
    days_after: int = 30,
    decision_key: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    return kol_decisions.list_followup_queue(
        status=status,
        days_after=days_after,
        decision_key=decision_key,
        limit=limit,
    )


def create_followup(body: dict[str, Any], *, staff: dict[str, Any]) -> dict[str, Any]:
    return kol_decisions.create_followup(body or {}, staff=staff)
