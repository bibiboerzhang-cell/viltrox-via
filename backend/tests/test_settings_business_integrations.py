from __future__ import annotations

from app.domains.settings import business_integrations as subject


def test_shopify_waits_for_authorization_without_credentials(monkeypatch):
    monkeypatch.setattr(
        subject.shopify_connect,
        "connection_status",
        lambda: {
            "status": "not_configured",
            "source": "none",
            "shop_domain": None,
            "token_configured": False,
            "webhook_secret_configured": False,
        },
    )
    monkeypatch.setattr(subject, "table_exists", lambda _table: False)
    monkeypatch.setattr(subject, "_count", lambda *_args, **_kwargs: 0)

    card = subject._shopify_card()

    assert card["state"] == "not_configured"
    assert card["operator_status"] == "awaiting_authorization"
    assert card["operator_label"] == "待授权"
    assert card["evidence"]["orders"] == 0
    assert "token" not in card


def test_shopify_credentials_are_not_connected_without_live_success(monkeypatch):
    monkeypatch.setattr(
        subject.shopify_connect,
        "connection_status",
        lambda: {
            "status": "configured",
            "source": "encrypted_db",
            "shop_domain": "configured.example",
            "token_configured": True,
            "webhook_secret_configured": True,
        },
    )
    monkeypatch.setattr(subject, "table_exists", lambda table: table == "vkpi_shopify_sync_runs")
    monkeypatch.setattr(subject, "_count", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(subject, "_row", lambda *_args, **_kwargs: {"status": "never_run"})

    card = subject._shopify_card()

    assert card["state"] == "pending"
    assert card["operator_status"] == "awaiting_configuration"
    assert card["operator_label"] == "待配置"


def test_shopify_client_credentials_are_pending_without_token_or_business_evidence(monkeypatch):
    monkeypatch.setattr(
        subject.shopify_connect,
        "connection_status",
        lambda: {
            "status": "pending",
            "source": "db",
            "shop_domain": "demo.myshopify.com",
            "auth_mode": "client_credentials",
            "client_id_configured": True,
            "client_secret_configured": True,
            "token_configured": False,
            "token_fresh": False,
            "refresh_required": True,
            "access_token_expires_at": None,
            "granted_scopes": [],
            "last_refresh_at": None,
            "webhook_secret_configured": True,
        },
    )
    monkeypatch.setattr(subject, "table_exists", lambda _table: False)
    monkeypatch.setattr(subject, "_count", lambda *_args, **_kwargs: 0)

    card = subject._shopify_card()

    assert card["state"] == "pending"
    assert card["operator_status"] == "awaiting_configuration"
    assert card["evidence"]["auth_mode"] == "client_credentials"
    assert card["evidence"]["client_id_configured"] is True
    assert card["evidence"]["client_secret_configured"] is True
    assert card["evidence"]["refresh_required"] is True
    assert "client_id" not in card["evidence"]
    assert "client_secret" not in card["evidence"]


def test_shopify_native_hmac_snapshot_is_real_connection_evidence(monkeypatch):
    monkeypatch.setattr(
        subject.shopify_connect,
        "connection_status",
        lambda: {
            "status": "connected",
            "source": "encrypted_db",
            "shop_domain": "demo.myshopify.com",
            "token_configured": True,
            "webhook_secret_configured": True,
        },
    )

    def fake_count(table, where="", params=()):
        if table == "vkpi_shopify_order_snapshots" and "provider_auth_mode='shopify-hmac'" in where:
            return 1
        return 0

    monkeypatch.setattr(subject, "_count", fake_count)
    monkeypatch.setattr(subject, "table_exists", lambda _table: False)

    card = subject._shopify_card()

    assert card["state"] == "connected"
    assert card["data_quality"] == "real"
    assert card["evidence"]["normalized_orders"] == 0
    assert card["evidence"]["native_webhook_snapshots"] == 1
    assert card["evidence"]["orders"] == 1
    assert "vkpi_shopify_order_snapshots" in card["source"]


def test_dealer_directory_readiness_is_information_and_map_not_authorization(monkeypatch):
    def fake_count(table, where="", params=()):
        assert table == "vkpi_dealers"
        if "authorization_status" in where:
            return 0
        if "source_status" in where:
            return 5
        if "address" in where:
            return 5
        if "phone" in where:
            return 4
        if "website_url" in where:
            return 5
        if "brand_listing_url" in where:
            return 5
        if "lat IS NOT NULL" in where:
            return 5
        return 5

    monkeypatch.setattr(subject, "_count", fake_count)
    monkeypatch.setattr(subject, "table_exists", lambda table: table == "vkpi_dealer_brand_relationships")
    monkeypatch.setattr(subject, "_row", lambda *_args, **_kwargs: {"n": 5})

    card = subject._dealer_card()

    assert card["state"] == "pending"
    assert card["operator_status"] == "awaiting_configuration"
    assert card["operator_label"] == "待配置"
    assert card["evidence"]["public_listing_verified"] == 5
    assert card["evidence"]["authorized_confirmed_secondary"] == 0
    assert card["evidence"]["map_visible"] == 5
    assert card["evidence"]["contact_complete"] == 4
    assert "授权不是地图上图前置条件" in card["next_action"]


def test_dealer_directory_can_be_verified_without_viltrox_authorization(monkeypatch):
    monkeypatch.setattr(
        subject,
        "_count",
        lambda _table, where="", params=(): 0 if "authorization_status" in where else 3,
    )
    monkeypatch.setattr(subject, "table_exists", lambda _table: False)

    card = subject._dealer_card()

    assert card["state"] == "connected"
    assert card["operator_status"] == "verified"
    assert card["evidence"]["authorized_confirmed_secondary"] == 0


def test_public_contract_has_seven_secret_free_cards_and_operator_counts(monkeypatch):
    monkeypatch.setattr(
        subject,
        "_shopify_card",
        lambda: subject._card(
            "shopify",
            "Shopify",
            "not_configured",
            "waiting",
            data_quality="empty",
            evidence={},
            source="test",
            next_action="authorize",
            operator_status="awaiting_authorization",
        ),
    )
    monkeypatch.setattr(
        subject,
        "_dealer_card",
        lambda: subject._card("dealers", "Dealers", "pending", "waiting", data_quality="partial", evidence={}, source="test", next_action="review", operator_status="awaiting_authorization"),
    )
    monkeypatch.setattr(
        subject,
        "_inventory_card",
        lambda: subject._card("inventory", "Inventory", "pending", "waiting", data_quality="unverified", evidence={}, source="test", next_action="configure"),
    )
    monkeypatch.setattr(
        subject,
        "_cost_card",
        lambda: subject._card("costs", "Costs", "pending", "waiting", data_quality="unverified", evidence={}, source="test", next_action="configure"),
    )
    monkeypatch.setattr(
        subject,
        "_attribution_card",
        lambda _shopify_state: subject._card("attribution", "Attribution", "not_configured", "waiting", data_quality="empty", evidence={}, source="test", next_action="authorize", operator_status="awaiting_authorization"),
    )
    monkeypatch.setattr(
        subject,
        "_r2_card",
        lambda: subject._card("r2", "R2", "error", "broken", data_quality="empty", evidence={}, source="test", next_action="repair"),
    )
    monkeypatch.setattr(
        subject,
        "_outcomes_card",
        lambda: subject._card("outcomes", "Outcomes", "not_configured", "waiting", data_quality="empty", evidence={}, source="test", next_action="configure"),
    )

    payload = subject.business_integrations_status()

    assert payload["claim_status"] == "descriptive_only"
    assert payload["write_performed"] is False
    assert payload["secrets_returned"] is False
    assert len(payload["integrations"]) == 7
    assert payload["operator_counts"] == {
        "verified": 0,
        "awaiting_authorization": 3,
        "awaiting_configuration": 3,
        "error": 1,
    }


def test_one_broken_integration_does_not_blank_other_cards(monkeypatch):
    monkeypatch.setattr(subject, "_shopify_card", lambda: (_ for _ in ()).throw(RuntimeError("stale schema")))
    monkeypatch.setattr(
        subject,
        "_dealer_card",
        lambda: subject._card("dealers", "Dealers", "pending", "waiting", data_quality="partial", evidence={}, source="test", next_action="review"),
    )
    monkeypatch.setattr(
        subject,
        "_inventory_card",
        lambda: subject._card("inventory", "Inventory", "pending", "waiting", data_quality="unverified", evidence={}, source="test", next_action="configure"),
    )
    monkeypatch.setattr(
        subject,
        "_cost_card",
        lambda: subject._card("costs", "Costs", "pending", "waiting", data_quality="unverified", evidence={}, source="test", next_action="configure"),
    )
    monkeypatch.setattr(
        subject,
        "_attribution_card",
        lambda _shopify_state: subject._card("attribution", "Attribution", "not_configured", "waiting", data_quality="empty", evidence={}, source="test", next_action="authorize"),
    )
    monkeypatch.setattr(
        subject,
        "_r2_card",
        lambda: subject._card("r2", "R2", "pending", "waiting", data_quality="partial", evidence={}, source="test", next_action="configure"),
    )
    monkeypatch.setattr(
        subject,
        "_outcomes_card",
        lambda: subject._card("outcomes", "Outcomes", "not_configured", "waiting", data_quality="empty", evidence={}, source="test", next_action="configure"),
    )

    payload = subject.business_integrations_status()

    assert len(payload["integrations"]) == 7
    assert payload["integrations"][0]["key"] == "shopify"
    assert payload["integrations"][0]["operator_status"] == "error"
    assert payload["integrations"][0]["evidence"] == {"diagnostic": "schema_or_runtime_error"}
    assert payload["integrations"][1]["key"] == "dealers"
