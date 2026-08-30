"""Typed boundary between KOL use cases and account-dossier infrastructure."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class AccountDossierPort(Protocol):
    def get_dossier(self, kol_id: int) -> Mapping[str, Any]: ...

    async def scan_account(
        self,
        kol_id: int,
        *,
        max_posts: int = 24,
    ) -> Mapping[str, Any]: ...

    async def analyze_account(
        self,
        kol_id: int,
        *,
        product_sku: str = "",
        snapshot_id: int | str | None = None,
    ) -> Mapping[str, Any]: ...


__all__ = ["AccountDossierPort"]
