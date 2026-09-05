"""Bounded offset pagination; incomplete or malformed responses are never complete."""
import hashlib
import json
import time
from typing import Any


def paginate_rows(path: str, base_params: dict[str, Any], list_keys: tuple[str, ...], *, page_limit: int = 100, max_pages: int = 2000) -> dict[str, Any]:
    from app.domains.integrations import goaffpro_connect as api

    limit = max(1, min(100, int(page_limit)))
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    total = None
    offset = 0
    deadline = time.monotonic() + 60
    out: dict[str, Any] = {"ok": False, "rows": rows, "total": None, "partial": False, "error": ""}
    for _ in range(max(1, min(200, int(max_pages)))):
        if time.monotonic() >= deadline:
            out.update(partial=True, error="pagination_deadline_exceeded")
            break
        result = api._get(path, {**base_params, "limit": limit, "offset": offset})
        if not result.get("ok"):
            out.update(partial=True, error=result.get("error") or result.get("reason") or "page_fetch_failed")
            out.update({k: result[k] for k in ("reason", "status_code") if k in result})
            break
        data = result.get("data")
        error = api._soft_error(data)
        page = data if isinstance(data, list) else next((data[k] for k in list_keys if isinstance(data, dict) and isinstance(data.get(k), list)), None)
        if error or page is None or any(not isinstance(row, dict) for row in page):
            out.update(partial=True, error=error or "malformed_page")
            break
        if isinstance(data, dict) and data.get("total_results") is not None:
            try:
                reported_total = int(data["total_results"])
                if reported_total < 0 or (total is not None and total != reported_total):
                    raise ValueError("unstable total")
                total = reported_total
            except (ValueError, TypeError, OverflowError):
                out.update(partial=True, error="invalid_or_changing_total")
                break
        fingerprint = hashlib.sha256(json.dumps(page, sort_keys=True, default=str).encode()).hexdigest()
        if page and fingerprint in seen:
            out.update(partial=True, error="repeated_page")
            break
        seen.add(fingerprint)
        rows.extend(page)
        offset += len(page)
        out["ok"] = True
        if total is not None and offset >= total:
            break
        if not page:
            if total is not None and offset < total:
                out.update(partial=True, error="truncated_page")
            break
        if total is None and len(page) < limit:
            break
    else:
        out.update(partial=True, error="pagination_page_limit_exceeded")
    out.update(total=total, next_offset=offset)
    return out
