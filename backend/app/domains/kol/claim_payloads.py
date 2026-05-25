"""KOL claim serialization helpers."""
from __future__ import annotations

import json
from typing import Any


def json_object(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def json_array(value: Any) -> str:
    return json.dumps(value if isinstance(value, list) else [], ensure_ascii=False, default=str)


def claim_payload(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    data = dict(row)
    data["is_active"] = str(data.get("status") or "") == "active"
    return data
