"""Thin HTTP adapters for the canonical My KOL target policy."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException


def assert_paid_target_writable(kol_pool_id: int, staff: dict[str, Any] | None) -> None:
    from app.db.connection import get_conn
    from app.domains.kol.my_kol_paid_action_access import (
        MyKolPaidActionError,
        assert_target_writable,
    )

    try:
        assert_target_writable(get_conn(), kol_pool_id=int(kol_pool_id), staff=staff)
    except MyKolPaidActionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc


def build_paid_target_fence(
    kol_pool_id: int,
    staff: dict[str, Any] | None,
    *,
    action: str,
) -> tuple[str, dict[str, Any]]:
    from app.db.connection import get_conn
    from app.domains.kol.my_kol_paid_action_access import (
        FENCE_KEY,
        MyKolPaidActionError,
        build_target_fence,
    )

    try:
        fence = build_target_fence(
            get_conn(), action=action, kol_pool_id=int(kol_pool_id), staff=staff
        )
    except MyKolPaidActionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc
    return FENCE_KEY, fence
