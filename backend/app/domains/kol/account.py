"""KOL account dossier and scan use cases."""
from __future__ import annotations

from typing import Any

from app.domains.kol import claims as claims_domain
from app.shared.account_dossier_port import AccountDossierPort


def _required_port(dossier_port: AccountDossierPort | None) -> AccountDossierPort:
    if dossier_port is None:
        raise RuntimeError("AccountDossierPort is required at the composition boundary")
    return dossier_port


def get_dossier(
    kol_id: int,
    *,
    dossier_port: AccountDossierPort | None = None,
) -> dict[str, Any]:
    return _required_port(dossier_port).get_dossier(kol_id)  # type: ignore[return-value]


def dossier_for_request(
    kol_id: int,
    *,
    staff: dict[str, Any],
    dossier_port: AccountDossierPort | None = None,
) -> dict[str, Any]:
    claims_domain.assert_kol_access(int(kol_id), staff, allow_unclaimed=True)
    from app.domains.kol.contact_access import mask_contact_payload

    result = (
        get_dossier(int(kol_id))
        if dossier_port is None
        else get_dossier(int(kol_id), dossier_port=dossier_port)
    )
    return mask_contact_payload(result)


async def scan_account(
    kol_id: int,
    *,
    max_posts: int = 24,
    dossier_port: AccountDossierPort | None = None,
) -> dict[str, Any]:
    return await _required_port(dossier_port).scan_account(  # type: ignore[return-value]
        kol_id,
        max_posts=max_posts,
    )


async def scan_account_for_request(
    kol_id: int,
    *,
    max_posts: int = 24,
    staff: dict[str, Any],
    dossier_port: AccountDossierPort | None = None,
) -> dict[str, Any]:
    claims_domain.assert_kol_access(int(kol_id), staff, allow_unclaimed=True)
    from app.domains.kol.contact_access import mask_contact_payload

    if dossier_port is None:
        result = await scan_account(int(kol_id), max_posts=max_posts)
    else:
        result = await scan_account(
            int(kol_id),
            max_posts=max_posts,
            dossier_port=dossier_port,
        )
    return mask_contact_payload(result)


async def analyze_account(
    kol_id: int,
    *,
    product_sku: str = "",
    snapshot_id: int | str | None = None,
    dossier_port: AccountDossierPort | None = None,
) -> dict[str, Any]:
    return await _required_port(dossier_port).analyze_account(  # type: ignore[return-value]
        kol_id,
        product_sku=product_sku,
        snapshot_id=snapshot_id,
    )


async def analyze_account_for_request(
    kol_id: int,
    *,
    product_sku: str = "",
    snapshot_id: int | str | None = None,
    staff: dict[str, Any],
    dossier_port: AccountDossierPort | None = None,
) -> dict[str, Any]:
    claims_domain.assert_kol_access(int(kol_id), staff, allow_unclaimed=True)
    from app.domains.kol.contact_access import mask_contact_payload

    if dossier_port is None:
        result = await analyze_account(
            int(kol_id),
            product_sku=product_sku,
            snapshot_id=snapshot_id,
        )
    else:
        result = await analyze_account(
            int(kol_id),
            product_sku=product_sku,
            snapshot_id=snapshot_id,
            dossier_port=dossier_port,
        )
    return mask_contact_payload(result)
