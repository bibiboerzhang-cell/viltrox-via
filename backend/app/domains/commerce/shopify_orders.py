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


def _primary_currency(by_currency: list[dict[str, Any]]) -> dict[str, Any]:
    """Collapse a per-currency GMV breakdown to one reportable figure WITHOUT cross-currency
    addition. There is no FX table, so summing EUR cents onto USD cents would fabricate money;
    instead we pick the dominant currency (largest gross GMV, tie-break by order_count) and
    report only its sum, while carrying the full by_currency breakdown and a mixed_currency flag
    so callers can annotate that other currencies exist rather than silently folding them in.
    Empty / all-zero-row breakdown -> {0, 0, None}. Returns
    {gmv_cents, order_count, currency, by_currency, mixed_currency}.
    """
    clean = [b for b in by_currency if _as_int(b.get("order_count")) > 0]
    if not clean:
        return {"gmv_cents": 0, "order_count": 0, "currency": None, "by_currency": [], "mixed_currency": False}
    primary = max(clean, key=lambda b: (_as_int(b.get("gmv_cents")), _as_int(b.get("order_count"))))
    return {
        "gmv_cents": _as_int(primary.get("gmv_cents")),
        "order_count": _as_int(primary.get("order_count")),
        "currency": _str_or_none(primary.get("currency")),
        "by_currency": clean,
        "mixed_currency": len(clean) > 1,
    }


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


def _attribution_gmv_cents(
    window_days: int | None = None,
    discount_codes: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Net confirmed Shopify GMV from the live attribution ledger (vkpi_sales_attributions).

    Caliber matches dashboard account_picker._build_attributed_gmv_roi (confidence='confirmed')
    so /shopify/gmv and the dashboard GMV card report the *same* number, PLUS refund rows:
      GMV = SUM(revenue_cents)
            WHERE source_platform='shopify' AND confidence IN ('confirmed','refund').
    Refund webhooks append *negative* revenue_cents rows with confidence='refund', so including
    that bucket means SUM() is already net of refunds — no separate subtraction needed. Unmatched
    / excluded rows are dropped, matching the confirmed-only dashboard caliber (no double-count,
    no fabrication).

    The window/discount filters are reused from the order-ledger caliber:
      - window_days -> COALESCE(occurred_at, created_at) >= cutoff
      - discount_codes -> only rows whose evidence_json carries one of the codes
        (matched with a compat-safe LIKE per code; '%' literals stay inside the bind value).
    Returns {gmv_cents, order_count, currency, by_currency, mixed_currency} for the dominant
    currency (no cross-currency addition). Empty ledger -> {0, 0, None, [], False}.
    """
    where: list[str] = ["source_platform = 'shopify'", "confidence IN ('confirmed','refund')"]
    params: list[Any] = []

    if window_days is not None:
        try:
            days = int(window_days)
        except Exception:
            days = 0
        if days > 0:
            cutoff = _now() - timedelta(days=days)
            where.append("COALESCE(occurred_at, created_at) >= ?")
            params.append(cutoff)

    codes = [str(c).strip() for c in (discount_codes or []) if str(c or "").strip()]
    if codes:
        # discount_codes are not a first-class column on vkpi_sales_attributions; they live
        # inside evidence_json. Match each code with a compat LIKE (literal '%' kept inside the
        # bound value so the '?'-placeholder compat layer never sees a bare % to choke on).
        code_clauses = []
        for code in codes:
            code_clauses.append("evidence_json LIKE ?")
            params.append(f"%{code}%")
        where.append("(" + " OR ".join(code_clauses) + ")")

    where_sql = " WHERE " + " AND ".join(where)
    conn = get_conn()
    # Group by currency so refund rows net against same-currency confirmed rows and no
    # cross-currency addition happens (MAX(currency) + SUM over all currencies used to add
    # EUR cents onto USD). _primary_currency picks the dominant currency; by_currency carries
    # the rest for auditability. NULLIF on the TEXT currency column (not a timestamptz).
    rows = conn.execute(
        f"""
        SELECT
            COALESCE(NULLIF(currency, ''), 'USD') AS currency,
            COALESCE(SUM(revenue_cents), 0) AS gmv_cents,
            COUNT(*) AS order_count
        FROM vkpi_sales_attributions
        {where_sql}
        GROUP BY COALESCE(NULLIF(currency, ''), 'USD')
        ORDER BY gmv_cents DESC
        """,
        params,
    ).fetchall()
    by_currency = [
        {
            "currency": _str_or_none(_row_get(r, "currency")) or "USD",
            "gmv_cents": _as_int(_row_get(r, "gmv_cents")),
            "order_count": _as_int(_row_get(r, "order_count")),
        }
        for r in rows
    ]
    return _primary_currency(by_currency)


def summarize_gmv(
    window_days: int | None = None,
    discount_codes: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Reconciled attributed GMV — single source of truth across both ledgers.

    Caliber (口径,显式合并,两个数不打架):
      1. Primary  = ingested Shopify order ledger (vkpi_shopify_orders, SUM total_price_cents).
         This is the most granular source once live Shopify creds land.
      2. Fallback = live attribution ledger (vkpi_sales_attributions, NET of refunds) when the
         order ledger is empty. The attribution ledger is what the refund webhook + GMV outcome
         bridge write into, and what dashboard _build_attributed_gmv_roi already reads — so
         folding it in here keeps /shopify/gmv and the dashboard GMV card on the *same* number
         instead of one reading 0 (empty orders table) while the other reads the real ledger.

    Both ledgers are reconciled under one caliber and the chosen source is reported in
    `gmv_source` for auditability. `attribution_gmv_cents` always carries the net-attribution
    figure so callers can cross-check the two sources.

    - window_days: when set (>0), restricts by ordered_at (orders) / occurred_at (attributions).
    - discount_codes: when non-empty, restricts to KOL-coupon orders / attributions.
    Returns {gmv_cents, order_count, currency, mixed_currency, gmv_source, attribution_gmv_cents,
    order_ledger_gmv_cents}. GMV is the dominant currency's sum only (no cross-currency addition);
    mixed_currency flags that other currencies exist. Both ledgers empty -> all 0 so the dashboard
    stays honest 待接入.
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
    # Group by currency so mixed-currency order ledgers do not add EUR cents onto USD (the old
    # MAX(currency) + SUM over all rows did exactly that). _primary_currency reports the dominant
    # currency only; order_by_currency carries the rest. NULLIF on the TEXT currency column.
    order_rows = conn.execute(
        f"""
        SELECT
            COALESCE(NULLIF(currency, ''), 'USD') AS currency,
            COALESCE(SUM(total_price_cents), 0) AS gmv_cents,
            COUNT(*) AS order_count
        FROM vkpi_shopify_orders
        {where_sql}
        GROUP BY COALESCE(NULLIF(currency, ''), 'USD')
        ORDER BY gmv_cents DESC
        """,
        params,
    ).fetchall()
    order_by_currency = [
        {
            "currency": _str_or_none(_row_get(r, "currency")) or "USD",
            "gmv_cents": _as_int(_row_get(r, "gmv_cents")),
            "order_count": _as_int(_row_get(r, "order_count")),
        }
        for r in order_rows
    ]
    order_primary = _primary_currency(order_by_currency)
    order_gmv_cents = _as_int(order_primary.get("gmv_cents"))
    order_count = _as_int(order_primary.get("order_count"))
    order_currency = _str_or_none(order_primary.get("currency"))
    order_mixed_currency = bool(order_primary.get("mixed_currency"))

    # Net-attribution figure (refund-inclusive) under the same window/discount caliber. Always
    # computed so it can be returned for cross-check; degrade to zeros on any read error so the
    # primary order-ledger answer is never disturbed.
    try:
        attr = _attribution_gmv_cents(window_days=window_days, discount_codes=discount_codes)
    except Exception:
        attr = {"gmv_cents": 0, "order_count": 0, "currency": None, "mixed_currency": False}
    attribution_gmv_cents = _as_int(attr.get("gmv_cents"))

    if order_count > 0:
        # Order ledger has rows -> it is the granular source of truth (dominant currency).
        gmv_cents = order_gmv_cents
        out_count = order_count
        out_currency = order_currency
        mixed_currency = order_mixed_currency
        gmv_source = "vkpi_shopify_orders.total_price_cents[ingested]"
    elif attribution_gmv_cents != 0 or _as_int(attr.get("order_count")) > 0:
        # Order ledger empty but the live attribution ledger has data -> reconcile onto it
        # (net of refunds) so /gmv matches the dashboard instead of the two sources diverging.
        gmv_cents = attribution_gmv_cents
        out_count = _as_int(attr.get("order_count"))
        out_currency = _str_or_none(attr.get("currency"))
        mixed_currency = bool(attr.get("mixed_currency"))
        gmv_source = "vkpi_sales_attributions.revenue_cents[shopify,confirmed+refund,net]"
    else:
        # Both empty -> honest 待接入.
        gmv_cents = 0
        out_count = 0
        out_currency = None
        mixed_currency = False
        gmv_source = "awaiting_data"

    return {
        "gmv_cents": gmv_cents,
        "order_count": out_count,
        "currency": out_currency,
        "mixed_currency": mixed_currency,
        "gmv_source": gmv_source,
        "attribution_gmv_cents": attribution_gmv_cents,
        "order_ledger_gmv_cents": order_gmv_cents,
    }
