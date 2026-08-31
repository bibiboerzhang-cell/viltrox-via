"""events.service 的纯函数帮工(CC 战役 2026-08-30 从 service.py 平移/提取)。

内容:json 编解码(_loads/_dumps 原实现)、payload 取值(text_field)、数值列范围校验、
日期归一(_normalize_due_date 原实现)、分页参数/信封、各写口的 SET 子句与 VALUES 组装。
行为红线:SQL 片段/报错文案/默认值与老实现逐字节一致;本模块不 import service.py(防环)。
"""
from __future__ import annotations

import json
from typing import Any, Callable

# 数值列范围:越界会直撞 INTEGER / NUMERIC(p,s) 列上限触发 PG 原生 500(并把整行/DETAIL
# 泄露给客户端),故写前先把越界收敛成 ValueError → 路由 _guard 映射 400。
_INT32_MAX = 2_147_483_647
EVENT_INT_BOUNDS: dict[str, tuple[int, int]] = {
    "health_score": (0, 100),
    "budget_total": (0, _INT32_MAX),
    "leads": (0, _INT32_MAX),
    "videos": (0, _INT32_MAX),
    # 子资源 INTEGER 列:expense.amount / material.qty / product.qty(均 int32),
    # 越界直入撞列上限触发 PG 500,故收敛成 400。事件主体 payload 不带这些键 → 校验时跳过。
    "amount": (0, _INT32_MAX),
    "qty": (0, _INT32_MAX),
}
EVENT_FLOAT_BOUNDS: dict[str, tuple[float, float]] = {
    "roi": (-99_999_999.99, 99_999_999.99),  # NUMERIC(10,2)
    "location_lat": (-90.0, 90.0),           # NUMERIC(10,6),纬度物理界
    "location_lng": (-180.0, 180.0),         # NUMERIC(10,6),经度物理界
}

EVENT_UPDATABLE = {
    "title": str, "type_key": str, "status": str, "health_score": int, "note": str,
    "start_date": None, "end_date": None, "location_name": str, "location_city": str,
    "location_country": str, "location_lat": None, "location_lng": None,
    "budget_total": int, "retrospective": str, "roi": None, "leads": None, "videos": None,
    "product_sku": str, "product_name": str,
}
EVENT_JSON_FIELDS = {"budget_json", "team_ids", "related_project_ids", "invited_kols_json"}


def loads_json(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def dumps_json(value: Any) -> str:
    return json.dumps(value if value is not None else None, default=str, ensure_ascii=False)


def text_field(payload: dict[str, Any], *keys: str, default: Any = "") -> str:
    """`str(payload.get(a) or payload.get(b) or default)` 的等价物(falsy 一律落默认)。"""
    for key in keys:
        value = payload.get(key)
        if value:
            return str(value)
    return str(default)


def validate_int_bounds(payload: dict[str, Any], bounds: dict[str, tuple[int, int]]) -> None:
    for key, (lo, hi) in bounds.items():
        if key not in payload or payload[key] is None:
            continue
        try:
            v = int(payload[key])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be an integer") from exc
        if v < lo or v > hi:
            raise ValueError(f"{key} out of range [{lo}, {hi}]")


def validate_float_bounds(payload: dict[str, Any], bounds: dict[str, tuple[float, float]]) -> None:
    for key, (flo, fhi) in bounds.items():
        if key not in payload or payload[key] is None:
            continue
        try:
            fv = float(payload[key])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be a number") from exc
        if fv < flo or fv > fhi:
            raise ValueError(f"{key} out of range [{flo}, {fhi}]")


def normalize_due_date(raw: Any) -> str | None:
    """各种来路的日期 → ISO YYYY-MM-DD;TBD/空/无法解析 → None(绝不把坏值塞进 DATE 列)。

    修因:前端曾把 due_date 传成 "06/21"(MM/DD)直进 Postgres DATE → invalid input syntax。
    """
    import re
    from datetime import datetime, timezone

    s = str(raw or "").strip()
    if not s or s.upper() in {"TBD", "TBA", "N/A", "NULL"} or s in {"待定", "未定"}:
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    cur_year = datetime.now(timezone.utc).year
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%Y.%m.%d", "%m/%d", "%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            if fmt in ("%m/%d", "%m-%d"):
                dt = dt.replace(year=cur_year)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            continue
    return None


def normalize_page_args(limit: Any, offset: Any, *, default_limit: int, max_limit: int) -> tuple[int, int]:
    safe_limit = max(1, min(int(limit or default_limit), max_limit))
    try:
        safe_offset = int(offset or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("offset must be a non-negative integer") from exc
    if safe_offset < 0:
        raise ValueError("offset must be a non-negative integer")
    return safe_limit, safe_offset


def normalized_status_filter(status: Any) -> str | None:
    return str(status or "").strip().casefold() or None


def normalized_owner_filter(owner_id: Any) -> int | None:
    if owner_id in (None, ""):
        return None
    if isinstance(owner_id, bool):
        raise ValueError("owner_id must be a positive integer")
    try:
        normalized = int(owner_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("owner_id must be a positive integer") from exc
    if normalized <= 0:
        raise ValueError("owner_id must be a positive integer")
    return normalized


def total_count_scalar(row: Any) -> int:
    return int((dict(row) if row is not None else {}).get("n") or 0)


def page_envelope(items: list, *, total_count: int, safe_offset: int, safe_limit: int) -> dict[str, Any]:
    next_offset = safe_offset + len(items)
    has_more = next_offset < total_count
    return {
        "items": items,
        "count": len(items),
        "total_count": total_count,
        "offset": safe_offset,
        "limit": safe_limit,
        "page": {
            "limit": safe_limit,
            "offset": safe_offset,
            "returned": len(items),
            "next_offset": next_offset if has_more else None,
            "has_more": has_more,
        },
    }


def upcoming_event_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "title": row.get("title"),
        "type_key": row.get("type_key"),
        "status": row.get("status"),
        "health_score": row.get("health_score"),
        "start_date": str(row.get("start_date") or ""),
        "end_date": str(row.get("end_date") or ""),
        "location_name": row.get("location_name") or "",
        "location_city": row.get("location_city") or "",
        "location_country": row.get("location_country") or "",
        "location_lat": row.get("location_lat"),
        "location_lng": row.get("location_lng"),
        "budget_total": row.get("budget_total") or 0,
    }


def event_insert_values(
    payload: dict[str, Any], *, eid: str, owner_id: Any, team_ids: list[Any], today: str
) -> tuple:
    return (
        eid,
        text_field(payload, "title", default="未命名活动"),
        text_field(payload, "type_key", default="other"),
        text_field(payload, "status", default="planning"),
        # Unknown is not perfect: only persist a health score when the caller
        # explicitly provides one. Existing rows are intentionally untouched.
        int(payload["health_score"]) if payload.get("health_score") is not None else None,
        text_field(payload, "note"),
        normalize_due_date(payload.get("start_date")) or today,
        normalize_due_date(payload.get("end_date")) or today,
        text_field(payload, "location_name"),
        text_field(payload, "location_city"),
        text_field(payload, "location_country"),
        payload.get("location_lat"),
        payload.get("location_lng"),
        int(payload.get("budget_total") or 0),
        dumps_json(payload.get("budget_json") or {}),
        owner_id,
        dumps_json(team_ids),
        dumps_json(payload.get("related_project_ids") or []),
        dumps_json(payload.get("invited_kols_json") or []),
        text_field(payload, "product_sku"),
        text_field(payload, "product_name"),
        text_field(payload, "retrospective"),
    )


def event_scalar_update_sets(payload: dict[str, Any], sets: list[str], vals: list[Any]) -> None:
    for key, caster in EVENT_UPDATABLE.items():
        if key not in payload:
            continue
        v = payload[key]
        if key in ("start_date", "end_date"):
            # start_date/end_date 是 NOT NULL DATE 列。坏/空日期经 normalize_due_date 归一为
            # None 后绝不能 SET NULL —— 那会违反 NOT NULL 触发 PG 500 且 DETAIL 泄露整行。
            # 显式给了却解析不出 → 400(而非静默跳过,避免「以为改了其实没改」)。
            normalized = normalize_due_date(v)
            if normalized is None:
                raise ValueError(f"{key} must be a valid date (YYYY-MM-DD)")
            sets.append(f"{key} = ?")
            vals.append(normalized)
        else:
            sets.append(f"{key} = ?")
            vals.append(caster(v) if (caster and v is not None) else v)


def event_json_update_sets(
    payload: dict[str, Any],
    sets: list[str],
    vals: list[Any],
    *,
    validate_team: Callable[[Any], None],
) -> None:
    for key in EVENT_JSON_FIELDS:
        if key not in payload:
            continue
        if key == "team_ids":
            validate_team(payload[key])
        sets.append(f"{key} = ?::jsonb")
        vals.append(dumps_json(payload[key]))


def task_update_sets(payload: dict[str, Any], now: Any) -> tuple[list[str], list[Any]]:
    sets: list[str] = []
    vals: list[Any] = []
    for key in ("title", "phase", "owner", "due_date", "kind"):
        if key in payload:
            sets.append(f"{key} = ?")
            vals.append(normalize_due_date(payload[key]) if key == "due_date" else payload[key])
    if "done" in payload:
        sets.append("done = ?")
        vals.append(bool(payload["done"]))
        sets.append("done_at = ?")
        vals.append(now if payload["done"] else None)
        if "done_by" in payload:
            sets.append("done_by = ?")
            vals.append(text_field(payload, "done_by"))
    for key in ("collaborators", "checklist", "details"):
        if key in payload:
            sets.append(f"{key} = ?::jsonb")
            vals.append(dumps_json(payload[key]))
    return sets, vals


def scalar_str_sets(payload: dict[str, Any], keys: tuple[str, ...], sets: list[str], vals: list[Any]) -> None:
    for key in keys:
        if key in payload:
            sets.append(f"{key} = ?")
            vals.append(str(payload[key]) if payload[key] is not None else "")


def _qty_and_tracking_sets(payload: dict[str, Any], sets: list[str], vals: list[Any]) -> None:
    if "qty" in payload:
        sets.append("qty = ?")
        vals.append(int(payload.get("qty") or 0))
    if "trackingNo" in payload or "tracking_no" in payload:
        sets.append("tracking_no = ?")
        vals.append(text_field(payload, "trackingNo", "tracking_no"))


def material_update_sets(payload: dict[str, Any]) -> tuple[list[str], list[Any]]:
    sets: list[str] = []
    vals: list[Any] = []
    scalar_str_sets(payload, ("name", "category", "source", "status", "owner", "note", "alert"), sets, vals)
    _qty_and_tracking_sets(payload, sets, vals)
    if "fileUrl" in payload or "file_url" in payload:
        sets.append("file_url = ?")
        vals.append(text_field(payload, "fileUrl", "file_url"))
    return sets, vals


def return_after_flag(payload: dict[str, Any]) -> bool:
    ra = payload.get("returnAfter")
    if ra is None:
        ra = payload.get("return_after")
    return bool(ra or False)


def product_update_sets(payload: dict[str, Any]) -> tuple[list[str], list[Any]]:
    sets: list[str] = []
    vals: list[Any] = []
    scalar_str_sets(payload, ("name", "category", "source", "status", "owner", "note"), sets, vals)
    _qty_and_tracking_sets(payload, sets, vals)
    if "arriveBy" in payload or "arrive_by" in payload:
        sets.append("arrive_by = ?")
        vals.append(normalize_due_date(payload.get("arriveBy") or payload.get("arrive_by")) or "")
    if "returnAfter" in payload or "return_after" in payload:
        sets.append("return_after = ?")
        vals.append(return_after_flag(payload))
    return sets, vals


def material_insert_values(payload: dict[str, Any], *, mid: str, event_id: str) -> tuple:
    return (
        mid, str(event_id), text_field(payload, "name"), text_field(payload, "category", default="display"),
        text_field(payload, "source", default="ship"), int(payload.get("qty") or 1),
        text_field(payload, "status", default="pending"), text_field(payload, "owner"),
        text_field(payload, "note"), text_field(payload, "trackingNo", "tracking_no"),
        text_field(payload, "fileUrl", "file_url"), text_field(payload, "alert"),
    )


def product_insert_values(payload: dict[str, Any], *, pid: str, event_id: str) -> tuple:
    return (
        pid, str(event_id), text_field(payload, "name"), text_field(payload, "category", default="lens"),
        text_field(payload, "source", default="new_purchase"), int(payload.get("qty") or 1),
        text_field(payload, "status", default="ordered"), text_field(payload, "owner"),
        text_field(payload, "note"), text_field(payload, "trackingNo", "tracking_no"),
        normalize_due_date(payload.get("arriveBy") or payload.get("arrive_by")) or "",
        return_after_flag(payload),
    )
