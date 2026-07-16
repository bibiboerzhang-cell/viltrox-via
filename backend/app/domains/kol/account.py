"""KOL account dossier and scan use cases."""
from __future__ import annotations

from typing import Any

from app.domains.kol import claims as claims_domain
from app.services.kol import account_dossier


def get_dossier(kol_id: int) -> dict[str, Any]:
    return account_dossier.get_kol_dossier(kol_id)


def dossier_for_request(kol_id: int, *, staff: dict[str, Any]) -> dict[str, Any]:
    claims_domain.assert_kol_access(int(kol_id), staff, allow_unclaimed=True)
    from app.domains.kol.contact_access import authorize_plaintext_contacts, mask_contact_payload

    reveal = authorize_plaintext_contacts(
        staff,
        resource_type="kol",
        resource_id=int(kol_id),
        page_path=f"/kols/{int(kol_id)}/dossier",
        metadata={"surface": "dossier"},
    )
    result = get_dossier(int(kol_id))
    return result if reveal else mask_contact_payload(result)


async def scan_account(kol_id: int, *, max_posts: int = 24) -> dict[str, Any]:
    return await account_dossier.scan_kol_account(kol_id, max_posts=max_posts)


async def scan_account_for_request(kol_id: int, *, max_posts: int = 24, staff: dict[str, Any]) -> dict[str, Any]:
    claims_domain.assert_kol_access(int(kol_id), staff, allow_unclaimed=True)
    from app.domains.kol.contact_access import authorize_plaintext_contacts, mask_contact_payload

    reveal = authorize_plaintext_contacts(
        staff,
        resource_type="kol",
        resource_id=int(kol_id),
        page_path=f"/kols/{int(kol_id)}/scan-account",
        metadata={"surface": "scan_account"},
    )
    result = await scan_account(int(kol_id), max_posts=max_posts)
    return result if reveal else mask_contact_payload(result)


async def analyze_account(
    kol_id: int,
    *,
    product_sku: str = "",
    snapshot_id: int | str | None = None,
) -> dict[str, Any]:
    return await account_dossier.analyze_kol_account(kol_id, product_sku=product_sku, snapshot_id=snapshot_id)


async def analyze_account_for_request(
    kol_id: int,
    *,
    product_sku: str = "",
    snapshot_id: int | str | None = None,
    staff: dict[str, Any],
) -> dict[str, Any]:
    claims_domain.assert_kol_access(int(kol_id), staff, allow_unclaimed=True)
    from app.domains.kol.contact_access import authorize_plaintext_contacts, mask_contact_payload

    reveal = authorize_plaintext_contacts(
        staff,
        resource_type="kol",
        resource_id=int(kol_id),
        page_path=f"/kols/{int(kol_id)}/analyze",
        metadata={"surface": "analyze_account"},
    )
    result = await analyze_account(int(kol_id), product_sku=product_sku, snapshot_id=snapshot_id)
    return result if reveal else mask_contact_payload(result)
