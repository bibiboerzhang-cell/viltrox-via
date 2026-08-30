"""Default infrastructure adapter for the KOL account-dossier port."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from app.services.kol import account_dossier


class ServiceAccountDossierAdapter:
    def get_dossier(self, kol_id: int) -> Mapping[str, Any]:
        return account_dossier.get_kol_dossier(kol_id)

    async def scan_account(
        self,
        kol_id: int,
        *,
        max_posts: int = 24,
    ) -> Mapping[str, Any]:
        return await account_dossier.scan_kol_account(kol_id, max_posts=max_posts)

    async def analyze_account(
        self,
        kol_id: int,
        *,
        product_sku: str = "",
        snapshot_id: int | str | None = None,
    ) -> Mapping[str, Any]:
        return await account_dossier.analyze_kol_account(
            kol_id,
            product_sku=product_sku,
            snapshot_id=snapshot_id,
        )


DEFAULT_ACCOUNT_DOSSIER_ADAPTER: Final = ServiceAccountDossierAdapter()


__all__ = ["DEFAULT_ACCOUNT_DOSSIER_ADAPTER", "ServiceAccountDossierAdapter"]
