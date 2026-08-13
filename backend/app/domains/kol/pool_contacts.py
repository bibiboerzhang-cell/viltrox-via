"""Canonical contact projection for audited single-item KOL pool reads."""
from __future__ import annotations

import json
import re
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

_CONTACT_TYPE_RE = re.compile(r"^[a-z0-9_.-]{1,40}$")


def _contact_record(contact_type: Any, contact_value: Any) -> dict[str, Any] | None:
    channel = _channel(contact_type)
    text = str(contact_value or "").strip()
    if not channel or not text:
        return None
    return {"contact_type": channel, "contact_value": text}


def _normalize_snapshot_entry(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        if any(key in value for key in ("contact_value", "value", "email", "phone")):
            record = dict(value)
            contact_value = record.get("contact_value") or record.get("value")
            contact_type = record.get("contact_type") or record.get("type")
            if not contact_value:
                contact_type = "email" if record.get("email") else "phone"
                contact_value = record.get("email") or record.get("phone")
            normalized = _contact_record(contact_type, contact_value)
            if not normalized:
                return []
            return [{**record, **normalized}]
        records: list[dict[str, Any]] = []
        for contact_type, nested in value.items():
            values = nested if isinstance(nested, list) else [nested]
            records.extend(
                record
                for item in values
                if (record := _contact_record(contact_type, item)) is not None
            )
        return records
    if isinstance(value, str):
        text = value.strip()
        prefix, separator, remainder = text.partition(":")
        if separator and _channel(prefix):
            record = _contact_record(prefix, remainder)
        else:
            record = _contact_record("email" if "@" in text else "contact", text)
        return [record] if record else []
    return []


def _loads_contacts(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, (list, dict)):
        parsed = value
    else:
        try:
            parsed = json.loads(str(value or "[]"))
        except Exception:
            return []
    if isinstance(parsed, list):
        return [record for item in parsed for record in _normalize_snapshot_entry(item)]
    if isinstance(parsed, dict):
        return _normalize_snapshot_entry(parsed)
    return []


def _canonical_rows(conn: Any, kol_pool_id: int) -> list[dict[str, Any]]:
    try:
        rows = conn.execute(
            """
            SELECT contact_type, contact_value, contact_source, consent_basis,
                   is_public_declared, confidence, first_seen_at, last_seen_at, created_at
            FROM vkpi_kol_pool_contacts
            WHERE kol_pool_id=?
            ORDER BY id
            """,
            (int(kol_pool_id),),
        ).fetchall()
    except Exception:
        logger.warning("canonical KOL contacts unavailable kol=%s", kol_pool_id, exc_info=True)
        return []
    return [dict(row) for row in rows]


def _channel(value: Any) -> str:
    channel = str(value or "").strip().lower().replace(" ", "_")
    if "email" in channel:
        return "email"
    return channel if _CONTACT_TYPE_RE.fullmatch(channel) else ""


def merge_canonical_contacts(item: dict[str, Any], *, conn: Any, kol_pool_id: int) -> dict[str, Any]:
    """Merge canonical truth without returning evidence/source URL free text."""
    result = dict(item)
    contacts = _loads_contacts(result.get("other_contacts_json"))
    seen = {
        (_channel(row.get("contact_type")), str(row.get("contact_value") or "").strip().lower())
        for row in contacts
    }
    for row in _canonical_rows(conn, int(kol_pool_id)):
        contact_type = _channel(row.get("contact_type"))
        contact_value = str(row.get("contact_value") or "").strip()
        key = (contact_type, contact_value.lower())
        if not contact_type or not contact_value or key in seen:
            continue
        contacts.append(
            {
                "contact_type": contact_type,
                "contact_value": contact_value,
                "is_public_declared": bool(row.get("is_public_declared")),
                "confidence": row.get("confidence"),
                "first_seen_at": row.get("first_seen_at"),
                "last_seen_at": row.get("last_seen_at"),
                "created_at": row.get("created_at"),
            }
        )
        seen.add(key)
    contacts.sort(key=lambda row: (str(row.get("contact_type") or ""), str(row.get("contact_value") or "").lower()))
    result["other_contacts_json"] = json.dumps(contacts, ensure_ascii=False, default=str)
    if not str(result.get("email") or "").strip():
        result["email"] = next(
            (
                str(row.get("contact_value") or "")
                for row in contacts
                if _channel(row.get("contact_type")) == "email"
            ),
            "",
        )
    result["contact_masked"] = False
    return result


__all__ = ["merge_canonical_contacts"]
