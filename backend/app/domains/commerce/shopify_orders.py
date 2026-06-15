"""V-KPI Shopify 订单 ingest + GMV 聚合(Attributed GMV / ROI 真数据源)。

迁移 138(vkpi_shopify_orders,UNIQUE(shop_domain, order_id))。
- ingest_order(payload):把一条(归一化或 Shopify 原始形态)订单幂等写入 ——
  以 (shop_domain, order_id) 为键 upsert,重复 ingest 同一单只更新、不重复计数。
- summarize_gmv(window_days=None, discount_codes=None):对真订单的 total_price_cents
  求和,返回 {gmv_cents, order_count, currency}。表为空 → 全 0 → 上游(account_picker
  ._build_attributed_gmv_roi)继续走诚实「待接入」路径,绝不编数。

DB 走 get_conn(? 占位)应用路径,镜像 events/inventory service。
与 KOL Pool / 评分域物理隔离;绝不碰 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.connection import get_conn


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _str_or_none(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _dumps(value: Any) -> str | None:
    if value in (None, "", [], {}):
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str, ensure_ascii=False)
    except Exception:
        return None


def _to_cents(value: Any) -> int:
    """Accept either explicit *_cents ints or a decimal/string price -> integer cents."""
    if value in (None, ""):
        return 0
    if isinstance(value, int):
        return value
    try:
        # decimal dollars (e.g. "129.00" / 129.0) -> cents
        return int(round(float(value) * 100))
    except Exception:
        return 0


def _price_cents(payload: dict[str, Any]) -> int:
    """Resolve total in cents, preferring an explicit cents field, else a dollar price."""
    if payload.get("total_price_cents") not in (None, ""):
        return _as_int(payload.get("total_price_cents"))
    # Shopify-native shapes: total_price / current_total_price (decimal strings).
    for key in ("total_price", "current_total_price", "total"):
        if payload.get(key) not in (None, ""):
            return _to_cents(payload.get(key))
    return 0


def _discount_code(payload: dict[str, Any]) -> str | None:
    """Single discount code: explicit field, else first of Shopify discount_codes[]."""
    if payload.get("discount_code") not in (None, ""):
        return _str_or_none(payload.get("discount_code"))
    codes = payload.get("discount_codes")
    if isinstance(codes, list) and codes:
        first = codes[0]
        if isinstance(first, dict):
            return _str_or_none(first.get("code"))
        return _str_or_none(first)
    return None


def _ordered_at(payload: dict[str, Any]) -> Any:
    for key in ("ordered_at", "created_at", "processed_at"):
        val = payload.get(key)
        if val not in (None, ""):
            return val
    return None


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if hasattr(row, "get"):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:
        return default


def ingest_order(payload: dict[str, Any]) -> dict[str, Any]:
    """Idempotent upsert of one Shopify order, keyed on (shop_domain, order_id).

    Accepts either a normalized payload (total_price_cents, ordered_at, ...) or a
    Shopify-native order dict (id, total_price, created_at, discount_codes[]).
    Re-ingesting the same (shop_domain, order_id) updates the row in place — it
    never double-counts. Returns {ok, shop_domain, order_id}.
    Raises ValueError when shop_domain or order_id is missing.
    """
    payload = payload or {}
    shop_domain = _str_or_none(
        payload.get("shop_domain")
        or payload.get("shopDomain")
        or payload.get("shop")
    )
    order_id = _str_or_none(
        payload.get("order_id")
        or payload.get("orderId")
        or payload.get("id")
        or payload.get("name")
    )
    if not shop_domain:
        raise ValueError("shop_domain is required")
    if not order_id:
        raise ValueError("order_id is required")

    total_price_cents = _price_cents(payload)
    currency = _str_or_none(payload.get("currency") or payload.get("currency_code"))
    financial_status = _str_or_none(payload.get("financial_status"))
    discount_code = _discount_code(payload)
    customer_email = _str_or_none(payload.get("customer_email") or payload.get("email"))
    line_items_json = _dumps(payload.get("line_items_json") or payload.get("line_items"))
    attributed_kol_pool_id = payload.get("attributed_kol_pool_id")
    attributed_kol_pool_id = (
        _as_int(attributed_kol_pool_id) if attributed_kol_pool_id not in (None, "") else None
    )
    ordered_at = _ordered_at(payload)

    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_shopify_orders
          (shop_domain, order_id, total_price_cents, currency, financial_status,
           discount_code, customer_email, line_items_json, attributed_kol_pool_id,
           ordered_at, ingested_at)
        VALUES (?,?,?,?,?,?,?,?::jsonb,?,?, NOW())
        ON CONFLICT (shop_domain, order_id) DO UPDATE SET
            total_price_cents = excluded.total_price_cents,
            currency = excluded.currency,
            financial_status = excluded.financial_status,
            discount_code = excluded.discount_code,
            customer_email = excluded.customer_email,
            line_items_json = excluded.line_items_json,
            attributed_kol_pool_id = COALESCE(
                excluded.attributed_kol_pool_id, vkpi_shopify_orders.attributed_kol_pool_id
            ),
            ordered_at = excluded.ordered_at,
            ingested_at = NOW()
        """,
        (
            shop_domain,
            order_id,
            total_price_cents,
            currency,
            financial_status,
            discount_code,
            customer_email,
            line_items_json,
            attributed_kol_pool_id,
            ordered_at,
        ),
    )
    conn.commit()
    return {"ok": True, "shop_domain": shop_domain, "order_id": order_id}


def ingest_orders(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    """Batch wrapper around ingest_order. Returns {ingested, errors:[...]}."""
    ingested = 0
    errors: list[dict[str, Any]] = []
    for idx, payload in enumerate(payloads or []):
        try:
            ingest_order(payload)
            ingested += 1
        except ValueError as exc:
            errors.append({"index": idx, "error": str(exc)})
    return {"ingested": ingested, "errors": errors}


def summarize_gmv(
    window_days: int | None = None,
    discount_codes: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Real attributed GMV over the ingested order ledger.

    - window_days: when set (>0), only orders with ordered_at >= now-window_days.
    - discount_codes: when non-empty, only orders whose discount_code is in the set
      (KOL-coupon attribution). Empty/None -> all orders.
    Returns {gmv_cents, order_count, currency}. Empty table -> {0, 0, None} so the
    dashboard stays honest 待接入.
    """
    where: list[str] = []
    params: list[Any] = []

    if window_days is not None:
        try:
            days = int(window_days)
        except Exception:
            days = 0
        if days > 0:
            cutoff = _now() - timedelta(days=days)
            where.append("ordered_at IS NOT NULL AND ordered_at >= ?")
            params.append(cutoff)

    codes = [str(c).strip() for c in (discount_codes or []) if str(c or "").strip()]
    if codes:
        placeholders = ",".join(["?"] * len(codes))
        where.append(f"discount_code IN ({placeholders})")
        params.extend(codes)

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    conn = get_conn()
    row = conn.execute(
        f"""
        SELECT
            COALESCE(SUM(total_price_cents), 0) AS gmv_cents,
            COUNT(*) AS order_count,
            MAX(currency) AS currency
        FROM vkpi_shopify_orders
        {where_sql}
        """,
        params,
    ).fetchone()

    return {
        "gmv_cents": _as_int(_row_get(row, "gmv_cents")),
        "order_count": _as_int(_row_get(row, "order_count")),
        "currency": _str_or_none(_row_get(row, "currency")),
    }
