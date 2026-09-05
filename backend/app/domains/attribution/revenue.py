"""Unified V-KPI revenue attribution helpers."""
from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains import audit, business_truth
from app.domains.access import scope
from app.platform.db.schema import ensure_vkpi_schema
from app.domains.projects.workflow import staff_id
from app.domains.attribution.integrations_money import currency_code, exact_cents

SOURCE_PLATFORMS = {"shopify", "amazon", "webhook", "manual", "custom"}
ATTRIBUTION_INGEST_CLASSES = {
    "manual_unverified",
    "human_verified",
    "provider_observed",
    "provider_verified",
    "provider_refund",
}
logger = get_logger(__name__)


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _load_json(value: Any) -> Any:
    if not value:
        return {}
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception as exc:
        logger.warning("vkpi attribution json parse failed: %s", exc)
        return {}


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _money_like_cents(value: Any) -> int:
    if value is None:
        return 0
    try:
        raw = str(value).strip().replace("$", "").replace(",", "")
        if not raw:
            return 0
        return int(round(float(raw) * 100))
    except (TypeError, ValueError):
        return _int(value)


def _amount_cents(body: dict[str, Any], key: str = "revenue") -> int:
    cents_key = f"{key}_cents"
    if cents_key in body:
        return _int(body.get(cents_key))
    for raw_key in (f"{key}_usd", key, "amount_usd", "amount"):
        if raw_key in body:
            return _money_like_cents(body.get(raw_key))
    return 0


def _first(item: dict[str, Any], keys: tuple[str, ...], default: Any = "") -> Any:
    for key in keys:
        if key in item and item.get(key) not in (None, ""):
            return item.get(key)
    return default


def _row_hash(item: dict[str, Any]) -> str:
    return hashlib.sha1(json.dumps(item, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()[:12]


def _normalize_amazon_row(item: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    tag = str(_first(item, ("amazon_tag", "tag", "tracking_id", "trackingId", "associates_tag", "campaign_id", "campaignId"), body.get("amazon_tag") or body.get("campaign_id") or "")).strip()
    campaign = str(_first(item, ("campaign_id", "campaignId", "campaign", "campaign_name", "campaignName"), tag)).strip()
    asin = str(_first(item, ("asin", "ASIN", "product_asin"), body.get("asin") or "")).strip()
    marketplace = str(_first(item, ("marketplace", "marketplace_id", "country"), body.get("marketplace") or "US")).strip().upper()
    report_date = str(_first(item, ("report_date", "date", "reportDate", "start_date", "startDate"), body.get("report_date") or utcnow()[:10])).strip()
    # The CSV adapter already supplies cents; never parse them as dollars or
    # silently replace them with zero. JSON imports share the same contract.
    for key in ("revenue", "commission"):
        cents_key = f"{key}_cents"
        raw = _first(item, (cents_key,), body.get(cents_key))
        aliases = ("revenue_usd", "revenue", "sales", "sales_usd", "ordered_revenue", "orderedRevenue", "amount") if key == "revenue" else ("commission_usd", "commission", "fees", "fee")
        value = exact_cents(raw, already_cents=True) if raw is not None else exact_cents(_first(item, aliases, _first(body, aliases, 0)))
        if key == "revenue":
            revenue_cents = value
        else:
            commission_cents = value
    currency = currency_code(item.get("currency") or body.get("currency") or (
        "USD" if any(k in item or k in body for k in ("revenue_usd", "commission_usd")) else ""
    ))
    if str(item.get("transaction_type") or "").lower() in {"refund", "return", "refunded", "returned"}:
        revenue_cents, commission_cents = -abs(revenue_cents), -abs(commission_cents)
    clicks = _int(_first(item, ("clicks", "click", "valid_clicks", "validClicks"), 0))
    orders = _int(_first(item, ("orders", "purchases", "units_sold", "unitsSold", "conversions"), 0))
    source_ref = str(item.get("source_ref") or item.get("sourceRef") or "").strip()
    if not source_ref:
        source_ref = f"amazon:{campaign or tag or 'unknown'}:{asin or 'unknown'}:{marketplace}:{report_date}:{_row_hash(item)}"
    return {
        "tag": tag,
        "campaign": campaign or tag,
        "asin": asin,
        "marketplace": marketplace,
        "report_date": report_date,
        "source_ref": source_ref,
        "revenue_cents": revenue_cents,
        "commission_cents": commission_cents,
        "currency": currency,
        "clicks": clicks,
        "orders": orders,
        "product_sku": str(_first(item, ("product_sku", "sku", "SKU"), body.get("product_sku") or asin)).strip(),
    }


def _project_defaults(project_id: int) -> dict[str, Any]:
    if not project_id:
        return {}
    row = get_conn().execute("SELECT * FROM vkpi_projects WHERE id=?", (int(project_id),)).fetchone()
    return dict(row) if row else {}


def _active_project_filter(alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    return (
        f"({prefix}project_id IS NULL OR EXISTS ("
        f"SELECT 1 FROM vkpi_projects p WHERE p.id = {prefix}project_id "
        "AND COALESCE(p.stage_status, '') != 'deleted'))"
    )


def create_attribution(
    body: dict[str, Any],
    *,
    staff: dict[str, Any] | None = None,
    ingest_class: str = "manual_unverified",
) -> dict[str, Any]:
    ensure_vkpi_schema()
    ingest_class = str(ingest_class or "manual_unverified").strip().lower()
    if ingest_class not in ATTRIBUTION_INGEST_CLASSES:
        raise ValueError("unsupported attribution ingest_class")
    requested_source = str(body.get("source_platform") or body.get("source") or "manual").strip().lower()
    provider_ingest = ingest_class in {"provider_observed", "provider_verified", "provider_refund"}
    source = requested_source if ingest_class != "manual_unverified" else "manual"
    if source not in SOURCE_PLATFORMS:
        raise ValueError("unsupported source_platform")
    source_ref = str(body.get("source_ref") or body.get("external_id") or "").strip()
    if not source_ref:
        raise ValueError("source_ref required")
    project_id = _int(body.get("project_id"))
    if project_id and not provider_ingest:
        scope.assert_project_access(project_id, staff, write=True)
    project = _project_defaults(project_id)
    now = utcnow()
    actor_staff_id = staff_id(staff)
    requested_staff_owner_id = _int(body.get("staff_id"), _int(project.get("assigned_staff_id"))) or actor_staff_id or None
    staff_owner_id = scope.effective_staff_id(staff, requested_staff_owner_id) or requested_staff_owner_id
    kol_id = _int(body.get("kol_id"), _int(project.get("kol_id"))) or None
    shopify_order_snapshot_id = _int(body.get("shopify_order_snapshot_id")) or None
    conn = get_conn()
    evidence = body.get("evidence") or body.get("metadata") or {}
    if not isinstance(evidence, dict):
        evidence = {"raw": evidence}
    if ingest_class == "provider_verified":
        confidence = "confirmed"
    elif ingest_class == "provider_refund":
        confidence = "refund"
    elif ingest_class == "provider_observed":
        confidence = "unmatched"
    elif ingest_class == "human_verified":
        confidence = "human_verified"
    else:
        confidence = "pending"
    evidence = {
        **evidence,
        "ingest_class": ingest_class,
        "requested_source_claim": requested_source,
        "requested_confidence_claim": str(body.get("confidence") or ""),
        "counts_toward_gmv": bool(
            requested_source == "shopify"
            and shopify_order_snapshot_id
            and ingest_class in {"provider_verified", "provider_refund"}
        ),
    }
    order_id = _int(body.get("order_id"))
    if order_id:
        order = conn.execute("SELECT id FROM orders WHERE id=?", (order_id,)).fetchone()
        if not order:
            evidence = {**evidence, "external_order_id": str(body.get("order_id"))}
            order_id = 0
    conn.execute(
        """
        INSERT INTO vkpi_sales_attributions (
            source_platform, source_ref, project_id, link_id, kol_id, staff_id,
            shopify_order_snapshot_id, product_sku, order_id, amazon_campaign_id, revenue_cents, commission_cents,
            currency, attribution_model, confidence, occurred_at, imported_at, evidence_json, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(source_platform, source_ref) DO UPDATE SET
            project_id=COALESCE(excluded.project_id, vkpi_sales_attributions.project_id),
            link_id=COALESCE(excluded.link_id, vkpi_sales_attributions.link_id),
            kol_id=COALESCE(excluded.kol_id, vkpi_sales_attributions.kol_id),
            staff_id=COALESCE(excluded.staff_id, vkpi_sales_attributions.staff_id),
            shopify_order_snapshot_id=COALESCE(excluded.shopify_order_snapshot_id, vkpi_sales_attributions.shopify_order_snapshot_id),
            product_sku=COALESCE(NULLIF(excluded.product_sku, ''), vkpi_sales_attributions.product_sku),
            order_id=COALESCE(excluded.order_id, vkpi_sales_attributions.order_id),
            amazon_campaign_id=COALESCE(NULLIF(excluded.amazon_campaign_id, ''), vkpi_sales_attributions.amazon_campaign_id),
            revenue_cents=excluded.revenue_cents,
            commission_cents=excluded.commission_cents,
            currency=excluded.currency,
            attribution_model=excluded.attribution_model,
            confidence=excluded.confidence,
            occurred_at=COALESCE(excluded.occurred_at, vkpi_sales_attributions.occurred_at),
            imported_at=excluded.imported_at,
            evidence_json=excluded.evidence_json
        """,
        (
            source,
            source_ref,
            project_id or None,
            _int(body.get("link_id")) or None,
            kol_id,
            staff_owner_id,
            shopify_order_snapshot_id,
            str(body.get("product_sku") or project.get("product_sku") or ""),
            order_id or None,
            str(body.get("amazon_campaign_id") or body.get("amazon_tag") or ""),
            _amount_cents(body, "revenue"),
            _amount_cents(body, "commission"),
            str(body.get("currency") or "USD"),
            str(body.get("attribution_model") or "last_touch"),
            confidence,
            str(body.get("occurred_at") or now),
            now,
            _json(evidence),
            now,
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM vkpi_sales_attributions WHERE source_platform=? AND source_ref=?",
        (source, source_ref),
    ).fetchone()
    return {"attribution": dict(row) if row else {}}


def create_manual_attribution(
    body: dict[str, Any],
    *,
    authorization_evidence: dict[str, Any],
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a human repair row without allowing provider-truth promotion.

    The caller-facing source/confidence values are retained only as audit
    claims.  The persisted row is always ``manual`` + ``pending`` and is thus
    excluded from Shopify GMV until a signed provider event reconciles it.
    """

    payload = dict(body or {})
    existing_evidence = payload.get("evidence") or payload.get("metadata") or {}
    if not isinstance(existing_evidence, dict):
        existing_evidence = {"raw": existing_evidence}
    requested_source = str(payload.get("source_platform") or payload.get("source") or "manual")
    requested_confidence = str(payload.get("confidence") or "")
    payload.update(
        {
            "source_platform": "manual",
            "confidence": "pending",
            "system_trusted": False,
            "trusted_ingest": False,
            "evidence": {
                **existing_evidence,
                "evidence_class": "human_authorized_manual_entry",
                "authorization_evidence": authorization_evidence,
                "requested_source_claim": requested_source,
                "requested_confidence_claim": requested_confidence,
                "counts_toward_gmv": False,
            },
        }
    )
    result = create_attribution(payload, staff=staff, ingest_class="manual_unverified")
    result["truth_status"] = "manual_pending_verification"
    result["counts_toward_gmv"] = False
    return result


def verify_manual_attribution(
    attribution_id: int,
    *,
    authorization_evidence: dict[str, Any],
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record a human review while keeping the row outside provider GMV.

    Human review is useful operational evidence, but is not a Shopify HMAC or
    Admin-API receipt.  The row therefore becomes ``human_verified`` rather
    than ``confirmed`` and its source remains ``manual``.
    """

    ensure_vkpi_schema()
    conn = get_conn()
    row = conn.execute("SELECT * FROM vkpi_sales_attributions WHERE id=?", (int(attribution_id),)).fetchone()
    if not row:
        raise LookupError("attribution not found")
    current = dict(row)
    if str(current.get("source_platform") or "").lower() != "manual":
        raise ValueError("only manual attribution rows can be human-verified")
    project_id = _int(current.get("project_id"))
    if project_id:
        scope.assert_project_access(project_id, staff, write=True)
    evidence = _load_json(current.get("evidence_json"))
    if not isinstance(evidence, dict):
        evidence = {}
    now = utcnow()
    evidence.update(
        {
            "human_verification": authorization_evidence,
            "human_verified_at": now,
            "counts_toward_gmv": False,
        }
    )
    conn.execute(
        "UPDATE vkpi_sales_attributions SET confidence='human_verified', evidence_json=? WHERE id=?",
        (_json(evidence), int(attribution_id)),
    )
    conn.commit()
    fresh = conn.execute("SELECT * FROM vkpi_sales_attributions WHERE id=?", (int(attribution_id),)).fetchone()
    actor = staff_id(staff) or 0
    audit.log_business_event(
        staff_id=actor,
        action_type="manual_attribution_verify",
        target_type="attribution",
        target_id=int(attribution_id),
        detail=str(authorization_evidence.get("reason") or "manual attribution reviewed"),
        metadata={
            "attribution_id": int(attribution_id),
            "source_platform": "manual",
            "confidence": "human_verified",
            "counts_toward_gmv": False,
            "authorization_ref": authorization_evidence.get("authorization_ref"),
        },
    )
    return {
        "attribution": dict(fresh) if fresh else {},
        "human_verified": True,
        "counts_toward_gmv": False,
    }


def import_amazon(body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = body.get("rows") or body.get("items") or []
    if not isinstance(rows, list):
        raise ValueError("rows must be a list")
    imported: list[dict[str, Any]] = []
    unmatched_count = 0
    if not rows or any(not isinstance(item, dict) for item in rows):
        raise ValueError("rows must contain accounting objects")
    # Validate the whole batch before the first write (including JSON callers).
    normalized_rows = [_normalize_amazon_row(item, body) for item in rows]
    for item, normalized in zip(rows, normalized_rows):
        payload = {
            **body,
            **item,
            "source_platform": "amazon",
            "source_ref": normalized["source_ref"],
            "amazon_campaign_id": normalized["campaign"],
            "product_sku": normalized["product_sku"],
            "revenue_cents": normalized["revenue_cents"],
            "commission_cents": normalized["commission_cents"],
            "currency": normalized["currency"],
            "confidence": "human_verified",
            "occurred_at": item.get("occurred_at") or f"{normalized['report_date']}T00:00:00Z",
            "evidence": {
                "source": "amazon_attribution_import",
                "evidence_class": "human_authorized_report_import",
                "authorization_evidence": body.get("_authorization_evidence") or {},
                "counts_toward_shopify_gmv": False,
                "row": item,
                "normalized": normalized,
                "asin": normalized["asin"],
                "marketplace": normalized["marketplace"],
            },
        }
        created = create_attribution(payload, staff=staff, ingest_class="human_verified")["attribution"]
        imported.append(created)
        if not _int(payload.get("project_id")) or not _int(created.get("project_id")) or not _int(created.get("staff_id")):
            import app.domains.attribution.reconciliation as reconciliation

            reconciliation.enqueue_unmatched(
                {
                    "source_platform": "amazon",
                    "source_ref": normalized["source_ref"],
                    "order_id": str(item.get("order_id") or normalized.get("orders") or ""),
                    "revenue_cents": normalized["revenue_cents"],
                    "currency": payload.get("currency") or "USD",
                    "occurred_at": payload.get("occurred_at"),
                    "product_sku": normalized["product_sku"],
                    "raw_payload": {"source": "amazon_attribution_import", "row": item, "normalized": normalized, "created_attribution_id": created.get("id")},
                },
                staff=staff,
            )
            unmatched_count += 1
    audit.log_business_event(
        staff_id=staff_id(staff) or 0,
        action_type="amazon_import",
        target_type="attribution",
        target_id="amazon",
        detail=f"Amazon Attribution import {len(imported)} rows",
        metadata={
            "row_count": len(rows),
            "imported_count": len(imported),
            "unmatched_count": unmatched_count,
            "source": "amazon",
            "batch_marker": body.get("batch_marker") or body.get("marker") or "",
        },
    )
    return {"imported": imported, "count": len(imported), "unmatched_count": unmatched_count}


def list_amazon_attributions(staff_id: int | None = None, limit: int = 100, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    return list_attributions(staff_id=staff_id, source="amazon", limit=limit, staff=staff)


def amazon_summary(staff_id: int | None = None, limit: int = 100, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return imported Amazon rows as reference diagnostics, never verified GMV.

    The current Amazon import is human-authorized but has no cryptographic
    provider receipt equivalent to the Shopify HMAC snapshot.  Preserve its
    amounts for reconciliation while removing them from the standard summable
    money columns.
    """
    ensure_vkpi_schema()
    where: list[str] = ["sa.source_platform='amazon'", _active_project_filter("sa")]
    params: list[Any] = []
    scope_clause, scope_params = scope.row_staff_filter("sa", staff, staff_id)
    if scope_clause:
        where.append(scope_clause)
        params.extend(scope_params)
    rows = get_conn().execute(
        f"""
        SELECT
            COALESCE(NULLIF(sa.amazon_campaign_id, ''), 'unknown') AS amazon_campaign_id,
            COALESCE(NULLIF(sa.product_sku, ''), 'unknown') AS product_sku,
            sa.currency,
            COUNT(*) AS rows,
            COALESCE(SUM(sa.revenue_cents), 0) AS revenue_cents,
            COALESCE(SUM(sa.commission_cents), 0) AS commission_cents,
            COALESCE(SUM(CASE WHEN sa.project_id IS NULL OR sa.staff_id IS NULL OR sa.kol_id IS NULL THEN 1 ELSE 0 END), 0) AS needs_reconciliation
        FROM vkpi_sales_attributions sa
        WHERE {' AND '.join(where)}
        GROUP BY amazon_campaign_id, product_sku, sa.currency
        ORDER BY revenue_cents DESC, rows DESC
        LIMIT ?
        """,
        (*params, max(1, min(500, int(limit or 100)))),
    ).fetchall()
    totals = get_conn().execute(
        f"""
        SELECT COUNT(*) AS rows,
               COALESCE(SUM(sa.revenue_cents), 0) AS revenue_cents,
               COALESCE(SUM(sa.commission_cents), 0) AS commission_cents
        FROM vkpi_sales_attributions sa
        WHERE {' AND '.join(where)}
        """,
        tuple(params),
    ).fetchone()
    def reference_payload(raw: Any) -> dict[str, Any]:
        item = dict(raw) if raw else {"rows": 0, "revenue_cents": 0, "commission_cents": 0}
        item["reference_revenue_cents"] = int(item.get("revenue_cents") or 0)
        item["reference_commission_cents"] = int(item.get("commission_cents") or 0)
        item["revenue_cents"] = None
        item["commission_cents"] = None
        item["business_truth_status"] = "reference_only"
        item["counts_toward_verified_gmv"] = False
        return item

    return {
        "items": [reference_payload(row) for row in rows],
        "totals": reference_payload(totals),
        "business_truth_status": "reference_only",
        "counts_toward_verified_gmv": False,
    }


def amazon_attribution_detail(attribution_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    from app.platform.db.schema_reconciliation import ensure_vkpi_reconciliation_schema

    ensure_vkpi_reconciliation_schema()
    conn = get_conn()
    row = conn.execute(
        """
        SELECT
            sa.*,
            p.project_uid, p.project_name, p.stage AS project_stage, p.stage_status AS project_status,
            p.product_name AS project_product_name, p.product_sku AS project_product_sku,
            p.amazon_asin AS project_amazon_asin, p.amazon_attribution_link, p.amazon_associates_link,
            p.assigned_staff_id AS project_assigned_staff_id,
            p.created_by_staff_id AS project_created_by_staff_id,
            l.slug AS link_slug, l.destination_url AS link_destination_url, l.status AS link_status,
            l.staff_id AS link_staff_id, l.created_by_staff_id AS link_created_by_staff_id,
            k.channel_name AS kol_name, k.channel_url AS kol_url, k.platform AS kol_platform, k.contact_email AS kol_email,
            u.name AS staff_name, u.email AS staff_email
        FROM vkpi_sales_attributions sa
        LEFT JOIN vkpi_projects p ON p.id = sa.project_id
        LEFT JOIN vkpi_links l ON l.id = sa.link_id
        LEFT JOIN kols k ON k.id = sa.kol_id
        LEFT JOIN staff st ON st.id = sa.staff_id
        LEFT JOIN users u ON u.id = st.user_id
        WHERE sa.id=? AND sa.source_platform='amazon' AND ({active_filter})
        """.format(active_filter=_active_project_filter("sa")),
        (int(attribution_id),),
    ).fetchone()
    if not row:
        raise LookupError("amazon attribution not found")
    item = dict(row)
    if not scope.can_view_all(staff, domain="finance"):
        actor = scope.actor_staff_id(staff)
        allowed = {
            _int(item.get("staff_id")),
            _int(item.get("project_assigned_staff_id")),
            _int(item.get("project_created_by_staff_id")),
            _int(item.get("link_staff_id")),
            _int(item.get("link_created_by_staff_id")),
        }
        if not actor or actor not in allowed:
            raise scope.ScopeDenied("amazon attribution scope denied")
    evidence = _load_json(item.get("evidence_json"))
    normalized = evidence.get("normalized") if isinstance(evidence, dict) else {}
    if not isinstance(normalized, dict):
        normalized = {}
    queue_rows = conn.execute(
        """
        SELECT *
        FROM vkpi_reconciliation_queue
        WHERE source_platform='amazon'
          AND (source_ref=? OR resolved_attribution_id=?)
        ORDER BY created_at DESC, id DESC
        LIMIT 25
        """,
        (item.get("source_ref"), int(attribution_id)),
    ).fetchall()
    adjustments = conn.execute(
        """
        SELECT *
        FROM vkpi_attribution_adjustments
        WHERE attribution_id=? OR metadata_json LIKE ?
        ORDER BY adjusted_at DESC, id DESC
        LIMIT 25
        """,
        (int(attribution_id), f'%"created_attribution_id": {int(attribution_id)}%'),
    ).fetchall()
    audit_rows = conn.execute(
        """
        SELECT *
        FROM vkpi_business_audit_logs
        WHERE (target_type='attribution' AND (target_id=? OR target_id='amazon'))
           OR metadata_json LIKE ?
           OR detail LIKE ?
        ORDER BY created_at DESC, id DESC
        LIMIT 25
        """,
        (str(attribution_id), f"%{item.get('source_ref')}%", f"%{item.get('source_ref')}%"),
    ).fetchall()
    return {
        "attribution": {
            "id": item.get("id"),
            "source_platform": item.get("source_platform"),
            "source_ref": item.get("source_ref"),
            "amazon_campaign_id": item.get("amazon_campaign_id"),
            "product_sku": item.get("product_sku"),
            "revenue_cents": item.get("revenue_cents"),
            "commission_cents": item.get("commission_cents"),
            "currency": item.get("currency"),
            "confidence": item.get("confidence"),
            "attribution_model": item.get("attribution_model"),
            "occurred_at": item.get("occurred_at"),
            "imported_at": item.get("imported_at"),
        },
        "amazon": {
            "campaign": normalized.get("campaign") or item.get("amazon_campaign_id") or "",
            "tag": normalized.get("tag") or "",
            "asin": normalized.get("asin") or item.get("project_amazon_asin") or "",
            "marketplace": normalized.get("marketplace") or "",
            "report_date": normalized.get("report_date") or "",
            "clicks": normalized.get("clicks"),
            "orders": normalized.get("orders"),
        },
        "project": {
            "id": item.get("project_id"),
            "project_uid": item.get("project_uid"),
            "project_name": item.get("project_name"),
            "stage": item.get("project_stage"),
            "status": item.get("project_status"),
            "product_name": item.get("project_product_name"),
            "product_sku": item.get("project_product_sku"),
            "amazon_attribution_link": item.get("amazon_attribution_link"),
            "amazon_associates_link": item.get("amazon_associates_link"),
        },
        "kol": {
            "id": item.get("kol_id"),
            "name": item.get("kol_name"),
            "url": item.get("kol_url"),
            "platform": item.get("kol_platform"),
            "contact_email": item.get("kol_email"),
        },
        "staff": {
            "id": item.get("staff_id"),
            "name": item.get("staff_name"),
            "email": item.get("staff_email"),
        },
        "link": {
            "id": item.get("link_id"),
            "slug": item.get("link_slug"),
            "destination_url": item.get("link_destination_url"),
            "status": item.get("link_status"),
        },
        "evidence": evidence,
        "reconciliation": {
            "queue": [dict(queue) for queue in queue_rows],
            "adjustments": [dict(adjustment) for adjustment in adjustments],
        },
        "audit_events": [dict(audit_row) for audit_row in audit_rows],
    }


def list_attributions(project_id: int | None = None, staff_id: int | None = None, source: str = "", limit: int = 100, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    where: list[str] = [_active_project_filter("sa")]
    params: list[Any] = []
    if project_id:
        scope.assert_project_access(int(project_id), staff)
        where.append("sa.project_id=?")
        params.append(int(project_id))
    scope_clause, scope_params = scope.row_staff_filter("sa", staff, staff_id)
    if scope_clause:
        where.append(scope_clause)
        params.extend(scope_params)
    if source:
        where.append("sa.source_platform=?")
        params.append(source)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    rows = get_conn().execute(
        f"""
        SELECT sa.*,
               CASE WHEN {business_truth.verified_shopify_attribution_sql('sa')}
                    THEN 1 ELSE 0 END AS is_verified_business_truth
        FROM vkpi_sales_attributions sa
        {clause}
        ORDER BY occurred_at DESC, id DESC
        LIMIT ?
        """,
        (*params, max(1, min(500, int(limit or 100)))),
    ).fetchall()
    attributions = [dict(row) for row in rows]
    for row in attributions:
        row["business_truth_status"] = (
            "provider_verified"
            if int(row.get("is_verified_business_truth") or 0) == 1
            else "reference_only"
        )
    return {"attributions": attributions}


def unmatched(limit: int = 100, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    scope_clause, scope_params = scope.row_staff_filter("sa", staff)
    rows = get_conn().execute(
        f"""
        SELECT *
        FROM vkpi_sales_attributions sa
        WHERE ({_active_project_filter("sa")})
          AND (project_id IS NULL OR staff_id IS NULL OR kol_id IS NULL)
          {"AND " + scope_clause if scope_clause else ""}
        ORDER BY imported_at DESC, id DESC
        LIMIT ?
        """,
        (*scope_params, max(1, min(500, int(limit or 100)))),
    ).fetchall()
    return {"items": [dict(row) for row in rows]}


def summary() -> dict[str, Any]:
    ensure_vkpi_schema()
    rows = get_conn().execute(
        f"""
        SELECT source_platform, confidence,
               COUNT(*) AS rows,
               COALESCE(SUM(revenue_cents), 0) AS revenue_cents,
               COALESCE(SUM(commission_cents), 0) AS commission_cents
        FROM vkpi_sales_attributions sa
        WHERE {_active_project_filter("sa")}
          AND {business_truth.verified_shopify_attribution_sql('sa')}
        GROUP BY source_platform, confidence
        ORDER BY revenue_cents DESC
        """
    ).fetchall()
    return {
        "by_source": [
            {**dict(row), "business_truth_status": "provider_verified"}
            for row in rows
        ]
    }
