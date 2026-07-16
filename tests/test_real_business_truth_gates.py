from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

import pytest
from starlette.datastructures import Headers

from app.domains import business_truth
from app.domains.attribution import integrations
from app.domains.attribution import revenue
from app.domains.commerce import dealer_scrape, shopify_orders
from app.domains.events import inventory_service
from app.domains.projects import contracts
from app.services.ingestion import webhooks


class _Cursor:
    def __init__(self, row: dict[str, Any] | None = None):
        self._row = row

    def fetchone(self):
        return self._row


class _CaptureConn:
    def __init__(self):
        self.executions: list[tuple[str, tuple[Any, ...]]] = []
        self.attribution_params: tuple[Any, ...] | None = None

    def execute(self, sql: str, params: tuple[Any, ...] = ()):
        values = tuple(params)
        self.executions.append((" ".join(str(sql).split()), values))
        if "INSERT INTO vkpi_sales_attributions" in sql:
            self.attribution_params = values
            return _Cursor()
        if "SELECT * FROM vkpi_sales_attributions" in sql:
            assert self.attribution_params is not None
            return _Cursor(
                {
                    "source_platform": self.attribution_params[0],
                    "source_ref": self.attribution_params[1],
                    "shopify_order_snapshot_id": self.attribution_params[6],
                    "revenue_cents": self.attribution_params[10],
                    "confidence": self.attribution_params[14],
                    "evidence_json": self.attribution_params[17],
                }
            )
        return _Cursor()

    def commit(self):
        return None


def test_manual_business_truth_gate_fails_closed_and_stamps_server_actor(monkeypatch):
    monkeypatch.setattr(business_truth, "manual_writes_enabled", lambda: False)
    with pytest.raises(business_truth.BusinessTruthWriteBlocked) as disabled:
        business_truth.require_authorization_evidence(
            {},
            staff={"id": 7, "role": "admin"},
            action="manual_order",
        )
    assert disabled.value.reason == "feature_disabled"

    monkeypatch.setattr(business_truth, "manual_writes_enabled", lambda: True)
    evidence = {
        "authorization_evidence": {
            "authorization_ref": "TICKET-42",
            "reason": "repair an imported reference row",
            "confirmed_by_human": True,
            "actor_staff_id": 999999,
            "evidence_class": "provider_verified",
        }
    }
    with pytest.raises(business_truth.BusinessTruthWriteBlocked) as manager:
        business_truth.require_authorization_evidence(
            evidence,
            staff={"id": 8, "role": "manager"},
            action="manual_order",
        )
    assert manager.value.reason == "owner_or_admin_required"

    stamped = business_truth.require_authorization_evidence(
        evidence,
        staff={"id": 7, "role": "admin"},
        action="manual_order",
    )
    assert stamped["actor_staff_id"] == 7
    assert stamped["evidence_class"] == "human_authorized_manual_entry"


def test_body_cannot_self_promote_manual_attribution_to_gmv(monkeypatch):
    conn = _CaptureConn()
    monkeypatch.setattr(revenue, "ensure_vkpi_schema", lambda: None)
    monkeypatch.setattr(revenue, "get_conn", lambda: conn)
    monkeypatch.setattr(revenue, "_project_defaults", lambda _project_id: {})

    result = revenue.create_attribution(
        {
            "source_platform": "shopify",
            "source_ref": "manual-order-1",
            "revenue_cents": 123_45,
            "confidence": "confirmed",
            "system_trusted": True,
            "provider_verified": True,
            "shopify_order_snapshot_id": 999,
        },
        staff={"id": 7, "role": "admin"},
    )["attribution"]

    evidence = json.loads(result["evidence_json"])
    assert result["source_platform"] == "manual"
    assert result["confidence"] == "pending"
    assert result["shopify_order_snapshot_id"] == 999
    assert evidence["ingest_class"] == "manual_unverified"
    assert evidence["requested_source_claim"] == "shopify"
    assert evidence["counts_toward_gmv"] is False


def test_manual_shopify_order_cannot_claim_paid_status(monkeypatch):
    conn = _CaptureConn()
    monkeypatch.setattr(shopify_orders, "get_conn", lambda: conn)

    result = shopify_orders.ingest_order(
        {
            "shop_domain": "store.myshopify.com",
            "order_id": "1001",
            "total_price_cents": 9000,
            "currency": "USD",
            "financial_status": "paid",
            "line_items": [{"sku": "AF-35"}],
        },
        authorization_evidence={"authorization_ref": "TICKET-42"},
    )

    insert_params = next(params for sql, params in conn.executions if "INSERT INTO vkpi_shopify_orders" in sql)
    line_items = json.loads(str(insert_params[7]))
    assert result["financial_status"] == shopify_orders.MANUAL_PENDING_STATUS
    assert result["counts_toward_gmv"] is False
    assert insert_params[4] == shopify_orders.MANUAL_PENDING_STATUS
    assert line_items["ingest_class"] == "manual_unverified"


def test_internal_order_ledger_cannot_claim_provider_without_native_proof(monkeypatch):
    monkeypatch.setattr(
        shopify_orders,
        "get_conn",
        lambda: pytest.fail("DB must not be reached before provider proof validation"),
    )
    with pytest.raises(ValueError, match="native HMAC evidence"):
        shopify_orders.ingest_order(
            {
                "shop_domain": "store.myshopify.com",
                "order_id": "1001",
                "total_price_cents": 9000,
                "financial_status": "paid",
                "system_trusted": True,
                "provider_verified": True,
            },
            ingest_class="provider_verified",
            provider_evidence={"auth_mode": "shared-secret", "raw_payload_hash": "f" * 64},
        )


def test_shopify_hmac_uses_canonical_credential_resolver(monkeypatch):
    secret = "db-canonical-secret"
    body = b'{"id":1001}'
    signature = base64.b64encode(
        hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    ).decode("utf-8")
    monkeypatch.setattr(webhooks, "PLATFORM_INGEST_SHARED_SECRET", "shared-is-configured")
    from app.domains.commerce import shopify_connect

    monkeypatch.setattr(
        shopify_connect,
        "get_credentials",
        lambda: {"shop_domain": "store.myshopify.com", "webhook_secret": secret},
    )

    mode = webhooks.verify_webhook_request(
        "shopify",
        Headers({"x-shopify-hmac-sha256": signature}),
        body,
        client_host="203.0.113.8",
    )
    assert mode == "shopify-hmac"


def test_shopify_rejects_shared_secret_and_loopback_without_native_hmac(monkeypatch):
    from app.domains.commerce import shopify_connect

    monkeypatch.setattr(webhooks, "PLATFORM_INGEST_SHARED_SECRET", "fleet-shared")
    monkeypatch.setattr(
        shopify_connect,
        "get_credentials",
        lambda: {"shop_domain": "store.myshopify.com", "webhook_secret": "native-secret"},
    )
    with pytest.raises(PermissionError, match="shopify hmac"):
        webhooks.verify_webhook_request(
            "shopify",
            Headers({"x-viltrox-ingest-secret": "fleet-shared"}),
            b'{"id":1001}',
            client_host="127.0.0.1",
        )

    monkeypatch.setattr(shopify_connect, "get_credentials", lambda: {"webhook_secret": ""})
    monkeypatch.setattr(webhooks, "SHOPIFY_WEBHOOK_SECRET", "")
    with pytest.raises(RuntimeError, match="shopify webhook secret not configured"):
        webhooks.verify_webhook_request(
            "shopify",
            Headers({}),
            b'{"id":1001}',
            client_host="127.0.0.1",
        )


@pytest.mark.parametrize(
    ("payload", "topic", "eligible"),
    [
        ({"financial_status": "paid"}, "orders/create", True),
        ({"financial_status": "partially_paid"}, "orders/updated", True),
        ({"financial_status": "pending"}, "orders/create", False),
        ({"financial_status": "voided"}, "orders/updated", False),
        ({"financial_status": "paid", "cancelled_at": "2026-07-14T12:00:00Z"}, "orders/updated", False),
        ({"financial_status": "paid"}, "orders/cancelled", False),
    ],
)
def test_shopify_gmv_eligibility_requires_paid_non_cancelled_state(payload, topic, eligible):
    assert integrations._shopify_order_is_gmv_eligible(payload, topic) is eligible


def test_signed_but_unpaid_shopify_order_cannot_promote_to_provider_verified(monkeypatch):
    captured: dict[str, Any] = {}
    monkeypatch.setattr(integrations, "ensure_vkpi_schema", lambda: None)
    monkeypatch.setattr(integrations, "verify_webhook_request", lambda *_args, **_kwargs: "shopify-hmac")
    monkeypatch.setattr(
        integrations,
        "_shopify_ref_context",
        lambda _payload: {
            "match": {"project_id": 1, "kol_id": 2, "staff_id": 3},
            "match_source": "discount_code",
            "product_sku": "AF-35",
            "discount_codes": ["KOL10"],
        },
    )

    def fake_snapshot(_payload, _raw_body, *, auth_mode):
        captured["snapshot_auth_mode"] = auth_mode
        return 77

    def fake_create(body, *, ingest_class, **_kwargs):
        captured["body"] = body
        captured["ingest_class"] = ingest_class
        return {"attribution": {"id": 9, "confidence": "unmatched"}}

    monkeypatch.setattr(integrations, "_upsert_shopify_order_snapshot", fake_snapshot)
    monkeypatch.setattr(integrations.attribution, "create_attribution", fake_create)
    result = integrations.ingest_shopify_order_webhook(
        Headers({"content-type": "application/json", "x-shopify-topic": "orders/create"}),
        json.dumps(
            {
                "id": 1001,
                "financial_status": "pending",
                "subtotal_price": "99.00",
                "currency": "USD",
            }
        ).encode("utf-8"),
        client_host="203.0.113.8",
    )

    assert captured["snapshot_auth_mode"] == "shopify-hmac"
    assert captured["ingest_class"] == "provider_observed"
    assert captured["body"]["evidence"]["financially_eligible"] is False
    assert result["financially_eligible"] is False


def test_shopify_snapshot_binds_native_proof_to_raw_hash(monkeypatch):
    class SnapshotConn:
        def __init__(self):
            self.params: tuple[Any, ...] | None = None

        def execute(self, sql: str, params: tuple[Any, ...] = ()):
            if "INSERT INTO vkpi_shopify_order_snapshots" in sql:
                self.params = tuple(params)
                assert sql.count("?") == len(self.params)
                return _Cursor()
            if "SELECT id FROM vkpi_shopify_order_snapshots" in sql:
                return _Cursor({"id": 77})
            raise AssertionError(sql)

        def commit(self):
            return None

    conn = SnapshotConn()
    monkeypatch.setattr(integrations, "get_conn", lambda: conn)
    raw = b'{"id":1001,"financial_status":"paid"}'
    snapshot_id = integrations._upsert_shopify_order_snapshot(
        {"id": 1001, "financial_status": "paid", "currency": "USD"},
        raw,
        auth_mode="shopify-hmac",
    )
    assert snapshot_id == 77
    assert conn.params is not None
    assert conn.params[12] == "shopify-hmac"
    assert conn.params[13]
    assert conn.params[18] == hashlib.sha256(raw).hexdigest()


def test_canonical_shopify_predicate_requires_native_proof_and_financial_state():
    predicate = business_truth.verified_shopify_attribution_sql("sa")
    assert "provider_auth_mode='shopify-hmac'" in predicate
    assert "provider_verified_at IS NOT NULL" in predicate
    assert "raw_payload_hash" in predicate
    assert "financial_status" in predicate
    assert "cancelled_at IS NULL" in predicate


def test_contract_delete_fails_closed_before_loading_contract(monkeypatch):
    monkeypatch.setattr(business_truth, "manual_writes_enabled", lambda: False)
    monkeypatch.setattr(
        contracts,
        "get_contract",
        lambda *_args, **_kwargs: pytest.fail("contract must not load before truth gate"),
    )
    with pytest.raises(business_truth.BusinessTruthWriteBlocked) as blocked:
        contracts.delete_contract(
            1,
            2,
            authorization_evidence={
                "authorization_ref": "TICKET-42",
                "reason": "remove duplicate contract",
                "confirmed_by_human": True,
            },
            staff={"id": 7, "role": "admin"},
        )
    assert blocked.value.reason == "feature_disabled"


def test_inventory_body_cannot_self_confirm_quantity(monkeypatch):
    class InventoryConn:
        def __init__(self):
            self.inventory_params: tuple[Any, ...] | None = None

        def execute(self, sql: str, params: tuple[Any, ...] = ()):
            normalized = " ".join(sql.split())
            if normalized.startswith("INSERT INTO vkpi_inventory ("):
                self.inventory_params = tuple(params)
            if "SELECT * FROM vkpi_inventory WHERE sku" in sql:
                if self.inventory_params is None:
                    return _Cursor()
                return _Cursor(
                    {
                        "sku": self.inventory_params[1],
                        "qty": self.inventory_params[4],
                        "quantity_status": self.inventory_params[9],
                        "quantity_source": self.inventory_params[10],
                    }
                )
            return _Cursor()

        def commit(self):
            return None

    conn = InventoryConn()
    monkeypatch.setattr(inventory_service, "get_conn", lambda: conn)
    result = inventory_service.create_item(
        {
            "sku": "AF-35",
            "qty": 25,
            "quantity_status": "source_confirmed",
            "quantity_source": "wms_provider",
            "quantity_verified_at": "2026-07-14T12:00:00Z",
        },
        {"id": 7, "role": "admin"},
    )["item"]
    assert result["quantity_status"] == "unverified"
    assert result["quantity_source"] == "manual_reference"


def test_dealer_body_cannot_self_claim_review_or_authorization(monkeypatch):
    class DealerConn:
        def __init__(self):
            self.upsert_params: tuple[Any, ...] | None = None

        def execute(self, sql: str, params: tuple[Any, ...] = ()):
            # The create-only safety gate now reads the matching row id so a
            # real 409 can identify the existing Dealer.  This truth-boundary
            # test only needs an empty identity lookup; do not couple it to the
            # old SELECT-list spelling.
            if "FROM vkpi_dealers" in sql and "WHERE name = ? AND address = ?" in sql:
                return _Cursor()
            if "INSERT INTO vkpi_dealers" in sql:
                self.upsert_params = tuple(params)
                return _Cursor(
                    {
                        "id": 1,
                        "source_status": self.upsert_params[10],
                        "authorization_status": self.upsert_params[11],
                        "source_checked_at": None,
                        "verification_note": self.upsert_params[13],
                    }
                )
            raise AssertionError(sql)

        def commit(self):
            return None

    conn = DealerConn()
    monkeypatch.setattr(dealer_scrape, "get_conn", lambda: conn)
    monkeypatch.setattr(dealer_scrape, "_geocode", lambda _payload: (40.7, -74.0))
    result = dealer_scrape.upsert_dealer(
        {
            "name": "Reference Camera Shop",
            "address": "1 Main St",
            "source_status": "public_listing_verified",
            "authorization_status": "authorized_confirmed",
            "source_checked_at": "2026-07-14T12:00:00Z",
        }
    )
    assert result["source_status"] == "unverified"
    assert result["authorization_status"] == "needs_viltrox_confirmation"
