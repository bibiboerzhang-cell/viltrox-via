"""KOL profile use cases."""
from __future__ import annotations

from typing import Any

from app.domains.kol import account as account_domain
from app.domains.kol import claims as claims_domain
from app.shared.account_dossier_port import AccountDossierPort


def profile_with_dossier(
    kol_id: int,
    *,
    staff: dict[str, Any],
    dossier_port: AccountDossierPort | None = None,
) -> dict[str, Any]:
    result = claims_domain.profile(int(kol_id), staff=staff)
    try:
        result["dossier"] = (
            account_domain.get_dossier(int(kol_id))
            if dossier_port is None
            else account_domain.get_dossier(
                int(kol_id),
                dossier_port=dossier_port,
            )
        )
    except Exception:
        result["dossier"] = {}
    from app.domains.kol.contact_access import project_profile_contacts

    return project_profile_contacts(result, reveal=False)
