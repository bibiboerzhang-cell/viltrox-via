from __future__ import annotations

from typing import Any

from app.domains.settings import business_integrations


class _Cursor:
    def __init__(self, row: dict[str, Any] | None):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConn:
    def __init__(self, *, shopify_orders: int = 0, inventory_total: int = 384, inventory_samples: int = 3):
        self.shopify_orders = shopify_orders
        self.inventory_total = inventory_total
        self.inventory_samples = inventory_samples

    def execute(self, sql: str, params: tuple[Any, ...] = ()):
        normalized = " ".join(sql.split())
        if "FROM vkpi_shopify_sync_runs ORDER BY" in normalized:
            return _Cursor({"status": "not_configured", "started_at": "2026-07-13T00:00:00Z"})
        if "COUNT(*) AS n FROM vkpi_shopify_sync_runs" in normalized:
            return _Cursor({"n": 0})
        if "COUNT(*) AS n FROM vkpi_shopify_orders" in normalized:
            return _Cursor({"n": self.shopify_orders})
        if "COUNT(*) AS n FROM vkpi_shopify_order_snapshots" in normalized:
            return _Cursor({"n": 0})
        if "COUNT(*) AS n FROM vkpi_dealers" in normalized:
            if "authorization_status" in normalized:
                return _Cursor({"n": 0})
            if "source_status='public_listing_verified'" in normalized:
                return _Cursor({"n": 5})
            if "lat IS NOT NULL" in normalized:
                return _Cursor({"n": 3})
            return _Cursor({"n": 5})
        if "COUNT(DISTINCT dealer_id) AS n FROM vkpi_dealer_brand_relationships" in normalized:
            return _Cursor({"n": 5})
        if "COUNT(*) AS n FROM vkpi_inventory" in normalized:
            if "quantity_status IN" in normalized:
                return _Cursor({"n": 0})
            if "quantity_source='catalog_reference'" in normalized:
                return _Cursor({"n": 369})
            if "is_sample,FALSE)=TRUE" in normalized:
                return _Cursor({"n": self.inventory_samples})
            if "qty>0" in normalized:
                return _Cursor({"n": 11})
            return _Cursor({"n": self.inventory_total})
        if "COUNT(*) AS n FROM vkpi_product_cost_catalog" in normalized:
            if "verification_status='verified'" in normalized:
                return _Cursor({"n": 0})
            return _Cursor({"n": 667})
        if "COUNT(*) AS n FROM vkpi_sales_attributions" in normalized:
            return _Cursor({"n": 0})
        if "COUNT(*) AS n FROM vkpi_media_cache_assets" in normalized:
            return _Cursor({"n": 10358})
        raise AssertionError(f"unexpected SQL: {normalized}; params={params}")


def _configure(
    monkeypatch,
    *,
    configured_shopify: bool = False,
    shopify_orders: int = 0,
    inventory_total: int = 384,
    inventory_samples: int = 3,
) -> None:
    conn = _FakeConn(
        shopify_orders=shopify_orders,
        inventory_total=inventory_total,
        inventory_samples=inventory_samples,
    )
    monkeypatch.setattr(business_integrations, "get_conn", lambda: conn)
    monkeypatch.setattr(business_integrations, "table_exists", lambda _table: True)
    monkeypatch.setattr(
        business_integrations.shopify_connect,
        "connection_status",
        lambda: {
            "status": "connected" if configured_shopify else "not_configured",
            "source": "db" if configured_shopify else "none",
            "shop_domain": "store.myshopify.com" if configured_shopify else "",
            "token_configured": configured_shopify,
            "webhook_secret_configured": configured_shopify,
        },
    )
    monkeypatch.setattr(
        business_integrations.data_readiness,
        "build_learning_readiness",
        lambda: {
            "status": "not_ready",
            "facts": {
                "evidence_backed_finalized_outcomes": 0,
                "distinct_prediction_outcomes_with_verified_actual": 0,
                "real_human_feedback": 0,
            },
        },
    )
    for name in business_integrations._R2_REQUIRED_ENV:
        monkeypatch.setenv(name, "configured-but-never-returned")


def test_business_integrations_keep_reference_data_pending(monkeypatch) -> None:
    _configure(monkeypatch)

    result = business_integrations.business_integrations_status()
    cards = {row["key"]: row for row in result["integrations"]}

    assert result["claim_status"] == "descriptive_only"
    assert result["write_performed"] is False
    assert result["secrets_returned"] is False
    assert cards["shopify"]["state"] == "not_configured"
    assert cards["dealers"]["state"] == "pending"
    assert cards["inventory"]["state"] == "pending"
    assert cards["inventory"]["data_quality"] == "unverified"
    assert cards["costs"]["state"] == "pending"
    assert cards["attribution"]["state"] == "not_configured"
    assert cards["r2"]["state"] == "pending"
    assert cards["outcomes"]["state"] == "not_configured"
    assert "configured-but-never-returned" not in repr(result)


def test_shopify_credential_presence_is_pending_until_real_success(monkeypatch) -> None:
    _configure(monkeypatch, configured_shopify=True, shopify_orders=0)
    result = business_integrations.business_integrations_status()
    shopify = next(row for row in result["integrations"] if row["key"] == "shopify")
    assert shopify["state"] == "pending"
    assert shopify["data_quality"] == "partial"


def test_shopify_real_order_is_connected_evidence(monkeypatch) -> None:
    _configure(monkeypatch, configured_shopify=True, shopify_orders=1)
    result = business_integrations.business_integrations_status()
    shopify = next(row for row in result["integrations"] if row["key"] == "shopify")
    assert shopify["state"] == "connected"
    assert shopify["data_quality"] == "real"


def test_sample_only_inventory_never_becomes_connected(monkeypatch) -> None:
    _configure(monkeypatch, inventory_total=3, inventory_samples=3)
    result = business_integrations.business_integrations_status()
    inventory = next(row for row in result["integrations"] if row["key"] == "inventory")
    assert inventory["state"] == "pending"
    assert inventory["evidence"]["non_sample_rows"] == 0
