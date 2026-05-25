"""KOL profile use cases."""
from __future__ import annotations

from typing import Any

from app.domains.kol import account as account_domain
from app.domains.kol import claims as claims_domain


def profile_with_dossier(kol_id: int, *, staff: dict[str, Any]) -> dict[str, Any]:
    result = claims_domain.profile(int(kol_id), staff=staff)
    try:
        result["dossier"] = account_domain.get_dossier(int(kol_id))
    except Exception:
        result["dossier"] = {}
    return result
