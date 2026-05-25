"""KOL contact use cases."""
from __future__ import annotations

from typing import Any

from app.domains.kol import claims as claims_domain
from app.domains.kol.payload_utils import _int, _json_loads
from app.domains.kol.profile_payloads import _contact_rows, _latest_kol_context


def contact_rows(kol_id: int, *, include_wrong: bool = False) -> dict[str, Any]:
    return _contact_rows(kol_id, include_wrong=include_wrong)


def add_contact(kol_id: int, body: dict[str, Any], *, staff: dict[str, Any]) -> dict[str, Any]:
    contact_type = str(body.get("contact_type") or body.get("type") or "").strip().lower()
    contact_value = str(body.get("contact_value") or body.get("value") or "").strip()
    if not contact_type or not contact_value:
        raise ValueError("contact_type and contact_value required")

    claims_domain.assert_kol_access(int(kol_id), staff, allow_unclaimed=True)
    ctx = _latest_kol_context(int(kol_id))
    kol = ctx["kol"]
    links = _json_loads(kol.get("contact_links_json"), [])
    if not isinstance(links, list):
        links = []
    existing_values = {
        str(item.get("value") or item.get("url") or "").strip().lower()
        for item in links
        if isinstance(item, dict)
    }
    payload: dict[str, Any] = {
        "contact_links": links,
        "notes": str(body.get("evidence") or body.get("note") or "").strip(),
    }
    if contact_type in {"email", "manager_email"} and "@" in contact_value:
        payload["contact_email"] = contact_value
    elif contact_type in {"phone", "whatsapp"}:
        payload["contact_phone"] = contact_value
    if contact_value.lower() not in existing_values:
        payload["contact_links"] = [
            *links,
            {
                "label": contact_type,
                "value": contact_value,
                "url": contact_value if contact_value.startswith("http") else "",
                "layer": _int(body.get("layer"), 5),
                "source": str(body.get("source") or "manual_input"),
                "confidence": _int(body.get("confidence"), 100),
                "evidence": str(body.get("evidence") or ""),
                "verified": True,
                "status": "active",
            },
        ]
    claims_domain.update_kol_manual(int(kol_id), payload, staff=staff)
    return _contact_rows(int(kol_id), include_wrong=True)
