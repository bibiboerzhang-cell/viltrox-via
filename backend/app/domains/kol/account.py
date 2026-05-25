"""KOL account dossier and scan use cases."""
from __future__ import annotations

from typing import Any

from app.services.kol import account_dossier


def get_dossier(kol_id: int) -> dict[str, Any]:
    return account_dossier.get_kol_dossier(kol_id)


async def scan_account(kol_id: int, *, max_posts: int = 24) -> dict[str, Any]:
    return await account_dossier.scan_kol_account(kol_id, max_posts=max_posts)


async def analyze_account(
    kol_id: int,
    *,
    product_sku: str = "",
    snapshot_id: int | str | None = None,
) -> dict[str, Any]:
    return await account_dossier.analyze_kol_account(kol_id, product_sku=product_sku, snapshot_id=snapshot_id)
