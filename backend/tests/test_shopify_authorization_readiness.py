from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from app.domains.commerce import shopify_connect as subject
from app.domains.commerce import shopify_client_credentials as client_subject


class _FakeConn:
    def __init__(self) -> None:
        self.executions: list[tuple[str, tuple]] = []
        self.commits = 0

    def execute(self, sql: str, params: tuple = ()):
        self.executions.append((sql, params))
        return self

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        return None


def test_save_accepts_existing_ui_domain_alias_and_never_echoes_secret(monkeypatch):
    conn = _FakeConn()
    monkeypatch.setattr(subject, "_load_row", lambda: {})
    monkeypatch.setattr(subject, "get_conn", lambda: conn)

    result = subject.save_credentials(
        {
            "store_domain": "Demo.MyShopify.com",
            "access_token": "shpat_super_secret",
            "webhook_secret": "webhook_super_secret",
        }
    )

    assert result == {
        "ok": True,
        "shop_domain": "demo.myshopify.com",
        "token_configured": True,
        "webhook_secret_configured": True,
        "auth_mode": "legacy_access_token",
        "api_version": "2026-04",
        "status": "pending",
        "source": "db",
    }
    assert "token" not in result
    assert "webhook_secret" not in result
    assert conn.commits == 1
    params = conn.executions[0][1]
    assert params[1] == "demo.myshopify.com"
    assert params[5] == "pending"
    assert params[6] is None
    assert "shpat_super_secret" not in repr(conn.executions)
    assert "webhook_super_secret" not in repr(conn.executions)


def test_production_credential_write_requires_explicit_encryption_key(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    for name in ("VKPI_CHANNELS_ENCRYPTION_KEY", "JWT_SECRET", "APP_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(subject, "_load_row", lambda: {})
    monkeypatch.setattr(
        subject,
        "get_conn",
        lambda: (_ for _ in ()).throw(AssertionError("unsafe credential reached persistence")),
    )

    with pytest.raises(
        subject.ShopifyEncryptionKeyUnavailable,
        match="encryption key is not configured",
    ):
        subject.save_credentials(
            {
                "shop_domain": "demo.myshopify.com",
                "access_token": "shpat_super_secret",
            }
        )


def test_production_explicit_app_secret_keeps_compatible_encryption(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("VKPI_CHANNELS_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.setenv("APP_SECRET", "explicit-production-secret")

    encrypted = subject._encrypt("shopify_secret")

    assert encrypted != "shopify_secret"
    assert subject._decrypt(encrypted) == "shopify_secret"


@pytest.mark.parametrize(
    "domain",
    [
        "169.254.169.254",
        "demo.myshopify.com:443",
        "user@demo.myshopify.com",
        "https://demo.myshopify.com",
        "https://demo.myshopify.com/admin",
        "demo.myshopify.com/path",
        "demo.myshopify.com?preview=1",
        "demo.myshopify.com#fragment",
        "evil.example.com",
    ],
)
def test_save_rejects_noncanonical_shop_domain_before_persistence(monkeypatch, domain):
    monkeypatch.setattr(
        subject,
        "_load_row",
        lambda: (_ for _ in ()).throw(AssertionError("invalid domain reached persistence")),
    )
    monkeypatch.setattr(
        subject,
        "get_conn",
        lambda: (_ for _ in ()).throw(AssertionError("invalid domain opened database")),
    )

    with pytest.raises(ValueError, match=r"canonical \*\.myshopify\.com"):
        subject.save_credentials(
            {
                "shop_domain": domain,
                "access_token": "shpat_secret",
                "webhook_secret": "hook_secret",
            }
        )


def test_admin_endpoint_rejects_legacy_invalid_domain_before_network_use():
    with pytest.raises(ValueError, match=r"canonical \*\.myshopify\.com"):
        subject._admin_endpoint(
            {
                "shop_domain": "127.0.0.1:8102",
                "api_version": "2026-04",
            }
        )


@pytest.mark.parametrize("version", ["latest", "unstable", "2026-02", "2026-04/../../oauth"])
def test_api_version_rejects_floating_or_nondated_values_before_network(monkeypatch, version):
    monkeypatch.setattr(
        subject,
        "get_conn",
        lambda: (_ for _ in ()).throw(AssertionError("invalid version reached persistence")),
    )
    with pytest.raises(ValueError, match="dated Shopify version"):
        subject.save_credentials(
            {
                "shop_domain": "demo.myshopify.com",
                "access_token": "shpat_secret",
                "webhook_secret": "hook_secret",
                "api_version": version,
            }
        )
    with pytest.raises(ValueError, match="dated Shopify version"):
        subject._admin_endpoint(
            {
                "shop_domain": "demo.myshopify.com",
                "api_version": version,
            }
        )


def test_connection_status_preserves_provider_receipt_without_secret(monkeypatch):
    monkeypatch.setattr(
        subject,
        "get_credentials",
        lambda: {
            "shop_domain": "demo.myshopify.com",
            "access_token": "shpat_secret",
            "webhook_secret": "hook_secret",
            "api_version": "2026-04",
            "status": "connected",
            "connected_at": "2026-07-15T12:00:00Z",
            "source": "db",
        },
    )

    result = subject.connection_status()

    assert result["status"] == "connected"
    assert result["last_verified_at"] == "2026-07-15T12:00:00Z"
    assert result["token_configured"] is True
    assert "token" not in result
    assert "webhook_secret" not in result


def test_probe_success_requires_valid_provider_shop_identity(monkeypatch):
    receipts: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        subject,
        "get_credentials",
        lambda: {
            "shop_domain": "demo.myshopify.com",
            "access_token": "shpat_secret",
        },
    )
    monkeypatch.setattr(
        subject,
        "post_graphql",
        lambda _query: {
            "ok": True,
            "data": {
                "shop": {
                    "id": "gid://shopify/Shop/123",
                    "name": "Demo",
                    "myshopifyDomain": "demo.myshopify.com",
                }
            },
        },
    )
    monkeypatch.setattr(
        subject,
        "_persist_probe_state",
        lambda status, connected_at=None: receipts.append((status, connected_at)),
    )

    result = subject.probe_connection()

    assert result["ok"] is True
    assert result["status"] == "connected"
    assert result["shop"]["id"] == "gid://shopify/Shop/123"
    assert receipts == [("connected", result["verified_at"])]
    assert "shpat_secret" not in repr(result)


def test_probe_rejection_is_fail_closed_and_sanitized(monkeypatch):
    receipts: list[str] = []
    monkeypatch.setattr(
        subject,
        "get_credentials",
        lambda: {
            "shop_domain": "demo.myshopify.com",
            "access_token": "shpat_secret",
        },
    )
    monkeypatch.setattr(
        subject,
        "post_graphql",
        lambda _query: {
            "ok": False,
            "status_code": 401,
            "error": "provider response containing sensitive diagnostics",
        },
    )
    monkeypatch.setattr(
        subject,
        "_persist_probe_state",
        lambda status, connected_at=None: receipts.append(status),
    )

    result = subject.probe_connection()

    assert result == {
        "ok": False,
        "status": "revoked",
        "reason": "provider_rejected_credentials",
        "shop_domain": "demo.myshopify.com",
    }
    assert receipts == ["revoked"]
    assert "sensitive diagnostics" not in repr(result)


def _formal_row(**overrides):
    row = {
        "id": 1,
        "shop_domain": "demo.myshopify.com",
        "auth_mode": "client_credentials",
        "client_id": "client_id_12345",
        "client_secret_encrypted": subject._encrypt("client_secret_1234567890"),
        "access_token_encrypted": "",
        "webhook_secret_encrypted": subject._encrypt("client_secret_1234567890"),
        "access_token_expires_at": None,
        "granted_scopes": "",
        "last_refresh_at": None,
        "revoked_at": None,
        "api_version": "2026-04",
        "status": "pending",
        "connected_at": None,
    }
    row.update(overrides)
    return row


def test_formal_credentials_encrypt_secret_for_token_and_webhook_without_echo(monkeypatch):
    conn = _FakeConn()
    monkeypatch.setattr(client_subject, "get_conn", lambda: conn)

    result = client_subject.save_client_credentials(
        {
            "shop_domain": "Demo.MyShopify.com",
            "client_id": "client_id_12345",
            "client_secret": "client_secret_1234567890",
        }
    )

    assert result == {
        "ok": True,
        "shop_domain": "demo.myshopify.com",
        "auth_mode": "client_credentials",
        "client_id_configured": True,
        "client_secret_configured": True,
        "webhook_secret_configured": True,
        "token_configured": False,
        "status": "pending",
        "source": "db",
    }
    params = conn.executions[0][1]
    assert params[3] == params[11]
    assert subject._decrypt(params[3]) == "client_secret_1234567890"
    assert "client_secret_1234567890" not in repr(conn.executions)
    assert "client_secret" not in result
    assert "client_id" not in result


def test_formal_connect_checks_production_encryption_before_provider_calls(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    for name in ("VKPI_CHANNELS_ENCRYPTION_KEY", "JWT_SECRET", "APP_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        client_subject,
        "_request_token",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("missing key reached Shopify provider")
        ),
    )

    with pytest.raises(subject.ShopifyEncryptionKeyUnavailable):
        client_subject.connect_client_credentials(
            {
                "shop_domain": "demo.myshopify.com",
                "client_id": "client_id_12345",
                "client_secret": "client_secret_1234567890",
            }
        )


def test_formal_connection_status_returns_configuration_truth_without_identity_or_secret(monkeypatch):
    monkeypatch.setattr(
        subject,
        "get_credentials",
        lambda: {
            "shop_domain": "demo.myshopify.com",
            "auth_mode": "client_credentials",
            "client_id": "client_id_12345",
            "client_secret": "client_secret_1234567890",
            "access_token": "short_lived_access_token",
            "webhook_secret": "client_secret_1234567890",
            "access_token_expires_at": "2099-07-17T12:00:00Z",
            "granted_scopes": ["read_orders"],
            "last_refresh_at": "2026-07-16T12:00:00Z",
            "revoked_at": None,
            "api_version": "2026-04",
            "status": "pending",
            "connected_at": None,
            "source": "db",
        },
    )

    result = subject.connection_status()

    assert result["auth_mode"] == "client_credentials"
    assert result["client_id_configured"] is True
    assert result["client_secret_configured"] is True
    assert result["token_fresh"] is True
    assert result["granted_scopes"] == ["read_orders"]
    assert "client_id" not in result
    assert "client_secret" not in result
    assert "access_token" not in result
    assert "webhook_secret" not in result


def test_token_exchange_persists_encrypted_token_expiry_and_scopes(monkeypatch):
    now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    row = _formal_row()
    conn = _FakeConn()
    captured: dict[str, object] = {}

    class _Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "access_token": "short_lived_access_token",
                "scope": "write_orders,read_orders,write_orders",
                "expires_in": 86400,
            }

    class _Client:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, url, **kwargs):
            captured["url"] = url
            captured["request"] = kwargs
            return _Response()

    monkeypatch.setattr(subject, "_load_row", lambda conn=None: dict(row))
    monkeypatch.setattr(client_subject, "get_conn", lambda: conn)
    monkeypatch.setattr(client_subject, "is_postgres_runtime", lambda: False)
    monkeypatch.setattr(client_subject.httpx, "Client", _Client)

    result = client_subject.refresh_access_token(force=True, now=now)

    assert result["ok"] is True
    assert result["access_token_expires_at"] == "2026-07-17T12:00:00Z"
    assert result["granted_scopes"] == ["read_orders", "write_orders"]
    assert captured["url"] == "https://demo.myshopify.com/admin/oauth/access_token"
    request = captured["request"]
    assert request["data"] == {
        "grant_type": "client_credentials",
        "client_id": "client_id_12345",
        "client_secret": "client_secret_1234567890",
    }
    update_sql, update_params = conn.executions[-1]
    assert "access_token_expires_at" in update_sql
    assert subject._decrypt(update_params[0]) == "short_lived_access_token"
    assert update_params[1] == "2026-07-17T12:00:00Z"
    assert update_params[2] == "read_orders,write_orders"
    assert "short_lived_access_token" not in repr(conn.executions)


def test_connect_rejection_preserves_last_known_good_without_database_write(monkeypatch):
    monkeypatch.setattr(
        client_subject,
        "_request_token",
        lambda _creds, *, now: client_subject._TokenGrantResult(
            None,
            "provider_rejected_credentials",
            provider_rejected=True,
        ),
    )
    monkeypatch.setattr(
        client_subject,
        "get_conn",
        lambda: (_ for _ in ()).throw(AssertionError("failed candidate touched database")),
    )

    result = client_subject.connect_client_credentials(
        {
            "shop_domain": "new-store.myshopify.com",
            "client_id": "new_client_id_123",
            "client_secret": "mistyped_client_secret_12345",
        }
    )

    assert result["ok"] is False
    assert result["reason"] == "provider_rejected_credentials"
    assert result["preserved_existing"] is True
    assert "mistyped_client_secret_12345" not in repr(result)
    assert "new_client_id_123" not in repr(result)


def test_connect_success_persists_candidate_and_grant_in_one_transaction(monkeypatch):
    now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    conn = _FakeConn()
    grant = client_subject._TokenGrant(
        access_token="validated_short_lived_token",
        granted_scopes=("read_orders", "write_orders"),
        expires_at=now + timedelta(hours=24),
        refreshed_at=now,
    )
    monkeypatch.setattr(client_subject, "_now_utc", lambda: now)
    monkeypatch.setattr(
        client_subject,
        "_request_token",
        lambda _creds, *, now: client_subject._TokenGrantResult(grant, ""),
    )
    monkeypatch.setattr(client_subject, "get_conn", lambda: conn)
    monkeypatch.setattr(client_subject, "is_postgres_runtime", lambda: False)
    monkeypatch.setattr(
        subject,
        "_probe_credentials",
        lambda _creds: {
            "ok": True,
            "status": "connected",
            "verified_at": "2026-07-16T12:00:00Z",
        },
    )
    monkeypatch.setattr(
        subject,
        "_register_webhooks_with_credentials",
        lambda _creds: {
            "ok": True,
            "registered": [{"id": "gid://shopify/WebhookSubscription/1"}],
            "registered_count": 3,
            "required_count": 3,
        },
    )

    result = client_subject.connect_client_credentials(
        {
            "shop_domain": "new-store.myshopify.com",
            "client_id": "new_client_id_123",
            "client_secret": "validated_client_secret_12345",
            "api_version": "2026-04",
        }
    )

    assert result["ok"] is True
    assert result["status"] == "connected"
    assert result["preserved_existing"] is False
    assert all(stage["status"] == "success" for stage in result["phases"].values())
    assert result["access_token_expires_at"] == "2026-07-17T12:00:00Z"
    assert conn.commits == 1
    assert len(conn.executions) == 1
    params = conn.executions[0][1]
    assert subject._decrypt(params[2]) == "validated_short_lived_token"
    assert subject._decrypt(params[3]) == "validated_client_secret_12345"
    assert params[3] == params[11]
    assert params[12] == "2026-07-17T12:00:00Z"
    assert params[13] == "read_orders,write_orders"
    assert "validated_client_secret_12345" not in repr(conn.executions)
    assert "validated_short_lived_token" not in repr(conn.executions)


def test_connect_zero_row_write_rolls_back_and_reports_preserved_existing(monkeypatch):
    now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)

    class _Cursor:
        rowcount = 0

    class _ZeroRowConn(_FakeConn):
        def __init__(self):
            super().__init__()
            self.rollbacks = 0

        def execute(self, sql: str, params: tuple = ()):
            self.executions.append((sql, params))
            return _Cursor()

        def rollback(self):
            self.rollbacks += 1

    conn = _ZeroRowConn()
    grant = client_subject._TokenGrant(
        access_token="validated_short_lived_token",
        granted_scopes=("read_orders",),
        expires_at=now + timedelta(hours=24),
        refreshed_at=now,
    )
    monkeypatch.setattr(client_subject, "_now_utc", lambda: now)
    monkeypatch.setattr(
        client_subject,
        "_request_token",
        lambda _creds, *, now: client_subject._TokenGrantResult(grant, ""),
    )
    monkeypatch.setattr(client_subject, "get_conn", lambda: conn)
    monkeypatch.setattr(client_subject, "is_postgres_runtime", lambda: False)
    monkeypatch.setattr(
        subject,
        "_probe_credentials",
        lambda _creds: {
            "ok": True,
            "status": "connected",
            "verified_at": "2026-07-16T12:00:00Z",
        },
    )
    monkeypatch.setattr(
        subject,
        "_register_webhooks_with_credentials",
        lambda _creds: {
            "ok": True,
            "registered": [],
            "registered_count": 3,
            "required_count": 3,
        },
    )
    monkeypatch.setattr(subject, "_cleanup_webhooks", lambda *_args: {"attempted": 0, "deleted": 0, "failed": 0})

    result = client_subject.connect_client_credentials(
        {
            "shop_domain": "new-store.myshopify.com",
            "client_id": "new_client_id_123",
            "client_secret": "validated_client_secret_12345",
        }
    )

    assert result["ok"] is False
    assert result["reason"] == "credential_persist_failed"
    assert result["preserved_existing"] is True
    assert conn.commits == 0
    assert conn.rollbacks == 1


def test_connect_probe_failure_never_persists_candidate(monkeypatch):
    now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    grant = client_subject._TokenGrant(
        access_token="candidate_token",
        granted_scopes=("read_orders",),
        expires_at=now + timedelta(hours=24),
        refreshed_at=now,
    )
    monkeypatch.setattr(client_subject, "_now_utc", lambda: now)
    monkeypatch.setattr(
        client_subject,
        "_request_token",
        lambda _creds, *, now: client_subject._TokenGrantResult(grant, ""),
    )
    monkeypatch.setattr(
        subject,
        "_probe_credentials",
        lambda _creds: {
            "ok": False,
            "status": "revoked",
            "reason": "provider_rejected_credentials",
        },
    )
    monkeypatch.setattr(
        client_subject,
        "get_conn",
        lambda: (_ for _ in ()).throw(AssertionError("failed probe touched database")),
    )

    result = client_subject.connect_client_credentials(
        {
            "shop_domain": "candidate.myshopify.com",
            "client_id": "candidate_client_123",
            "client_secret": "candidate_secret_123456789",
        }
    )

    assert result["ok"] is False
    assert result["reason"] == "provider_rejected_credentials"
    assert result["preserved_existing"] is True
    assert result["phases"]["authorization"]["status"] == "success"
    assert result["phases"]["probe"]["status"] == "error"
    assert result["phases"]["webhooks"]["status"] == "pending"


def test_partial_webhook_registration_cleans_candidate_and_preserves_existing(monkeypatch):
    now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    grant = client_subject._TokenGrant(
        access_token="candidate_token",
        granted_scopes=("read_orders",),
        expires_at=now + timedelta(hours=24),
        refreshed_at=now,
    )
    monkeypatch.setattr(client_subject, "_now_utc", lambda: now)
    monkeypatch.setattr(
        client_subject,
        "_request_token",
        lambda _creds, *, now: client_subject._TokenGrantResult(grant, ""),
    )
    monkeypatch.setattr(
        subject,
        "_probe_credentials",
        lambda _creds: {
            "ok": True,
            "status": "connected",
            "verified_at": "2026-07-16T12:00:00Z",
        },
    )
    monkeypatch.setattr(
        subject,
        "_register_webhooks_with_credentials",
        lambda _creds: {
            "ok": False,
            "registered_count": 1,
            "required_count": 3,
            "cleanup": {"attempted": 1, "deleted": 1, "failed": 0},
        },
    )
    monkeypatch.setattr(
        client_subject,
        "get_conn",
        lambda: (_ for _ in ()).throw(AssertionError("partial webhooks touched database")),
    )

    result = client_subject.connect_client_credentials(
        {
            "shop_domain": "candidate.myshopify.com",
            "client_id": "candidate_client_123",
            "client_secret": "candidate_secret_123456789",
        }
    )

    assert result["ok"] is False
    assert result["reason"] == "provider_webhook_error"
    assert result["preserved_existing"] is True
    assert result["phases"]["webhooks"] == {
        "status": "error",
        "reason": "provider_webhook_error",
        "registered_count": 1,
        "required_count": 3,
        "cleanup": {"attempted": 1, "deleted": 1, "failed": 0},
    }


def test_webhook_partial_creation_runs_best_effort_cleanup(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://vkpi.example")
    calls: list[tuple[str, dict]] = []

    def request(_creds, query, variables=None):
        variables = variables or {}
        calls.append((query, variables))
        if "webhookSubscriptionDelete" in query:
            return {
                "ok": True,
                "data": {
                    "webhookSubscriptionDelete": {
                        "deletedWebhookSubscriptionId": variables["id"],
                        "userErrors": [],
                    }
                },
            }
        if variables.get("topic") == "ORDERS_CREATE":
            return {
                "ok": True,
                "data": {
                    "webhookSubscriptionCreate": {
                        "webhookSubscription": {"id": "gid://shopify/WebhookSubscription/1"},
                        "userErrors": [],
                    }
                },
            }
        return {"ok": False, "reason": "provider_unreachable"}

    monkeypatch.setattr(subject, "_post_graphql_with_credentials", request)
    result = subject._register_webhooks_with_credentials(
        {"shop_domain": "demo.myshopify.com", "access_token": "candidate_token"}
    )

    assert result["ok"] is False
    assert result["registered_count"] == 1
    assert result["cleanup"] == {"attempted": 1, "deleted": 1, "failed": 0}
    assert any("webhookSubscriptionDelete" in query for query, _ in calls)


def test_refresh_commits_short_lease_before_provider_http(monkeypatch):
    now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    row = _formal_row(
        access_token_encrypted=subject._encrypt("expired_token"),
        access_token_expires_at="2026-07-16T11:00:00Z",
    )
    events: list[str] = []

    class _LeaseCursor:
        rowcount = 1

    class _LeaseConn(_FakeConn):
        def execute(self, sql: str, params: tuple = ()):
            self.executions.append((sql, params))
            if "SET refresh_lease_owner" in sql:
                row["refresh_lease_owner"] = params[0]
                row["refresh_lease_expires_at"] = params[1]
                events.append("lease_write")
            return _LeaseCursor()

        def commit(self):
            events.append("commit")
            super().commit()

    conn = _LeaseConn()

    def request(_creds, *, now):
        events.append("provider_http")
        assert "commit" in events
        return client_subject._TokenGrantResult(
            client_subject._TokenGrant(
                access_token="fresh_token",
                granted_scopes=("read_orders",),
                expires_at=now + timedelta(hours=24),
                refreshed_at=now,
            ),
            "",
        )

    monkeypatch.setattr(subject, "get_credentials", lambda: subject._credentials_from_row(dict(row)))
    monkeypatch.setattr(subject, "_load_row", lambda conn=None: dict(row))
    monkeypatch.setattr(client_subject, "get_conn", lambda: conn)
    monkeypatch.setattr(client_subject, "_request_token", request)
    monkeypatch.setattr(client_subject, "_persist_grant", lambda *_args, **_kwargs: events.append("persist"))

    result = client_subject.refresh_access_token(now=now)

    assert result["ok"] is True
    assert events.index("commit") < events.index("provider_http")
    assert not any("pg_advisory_xact_lock" in sql for sql, _ in conn.executions)


def test_provider_unreachable_backoff_prevents_immediate_retry(monkeypatch):
    now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    current = subject._credentials_from_row(
        _formal_row(
            access_token_encrypted=subject._encrypt("expired_token"),
            access_token_expires_at="2026-07-16T11:00:00Z",
        )
    )
    requests = 0

    def request(_creds, *, now):
        nonlocal requests
        requests += 1
        return client_subject._TokenGrantResult(None, "provider_unreachable")

    def persist_failure(_conn, **kwargs):
        current["status"] = "error"
        current["refresh_retry_after"] = "2026-07-16T12:00:15Z"
        return kwargs["reason"], current["refresh_retry_after"]

    monkeypatch.setattr(subject, "get_credentials", lambda: dict(current))
    monkeypatch.setattr(subject, "_load_row", lambda conn=None: _formal_row())
    monkeypatch.setattr(client_subject, "get_conn", _FakeConn)
    monkeypatch.setattr(client_subject, "_claim_refresh_lease", lambda *_args, **_kwargs: (True, dict(current)))
    monkeypatch.setattr(client_subject, "_request_token", request)
    monkeypatch.setattr(client_subject, "_persist_refresh_failure", persist_failure)

    first = client_subject.refresh_access_token(now=now)
    second = client_subject.refresh_access_token(now=now)

    assert first["reason"] == "provider_unreachable"
    assert second["reason"] == "provider_backoff_active"
    assert second["retry_at"] == "2026-07-16T12:00:15Z"
    assert requests == 1


def test_expired_formal_token_is_refreshed_before_use(monkeypatch):
    stale = subject._credentials_from_row(
        _formal_row(
            access_token_encrypted=subject._encrypt("expired_token"),
            access_token_expires_at="2026-07-16T11:00:00Z",
        )
    )
    fresh = {
        **stale,
        "access_token": "fresh_token",
        "access_token_expires_at": "2026-07-17T12:00:00Z",
    }
    values = iter([stale, fresh])
    refreshes: list[bool] = []
    monkeypatch.setattr(subject, "get_credentials", lambda: next(values))
    monkeypatch.setattr(
        client_subject,
        "refresh_access_token",
        lambda: refreshes.append(True) or {"ok": True},
    )

    assert client_subject.credentials_with_fresh_token()["access_token"] == "fresh_token"
    assert refreshes == [True]


def test_concurrent_expired_refresh_is_single_flight(monkeypatch):
    now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    row = _formal_row(
        access_token_encrypted=subject._encrypt("expired_token"),
        access_token_expires_at="2026-07-16T11:00:00Z",
    )
    request_count = 0
    request_guard = threading.Lock()

    def request_token(_creds, *, now):
        nonlocal request_count
        with request_guard:
            request_count += 1
        time.sleep(0.05)
        return client_subject._TokenGrantResult(
            client_subject._TokenGrant(
                access_token="one_fleet_token",
                granted_scopes=("read_orders",),
                expires_at=now + timedelta(hours=24),
                refreshed_at=now,
            ),
            "",
        )

    def persist(_conn, grant, *, lease_owner=""):
        row["access_token_encrypted"] = subject._encrypt(grant.access_token)
        row["access_token_expires_at"] = client_subject.iso_timestamp(grant.expires_at)
        row["granted_scopes"] = ",".join(grant.granted_scopes)
        row["last_refresh_at"] = client_subject.iso_timestamp(grant.refreshed_at)

    monkeypatch.setattr(subject, "_load_row", lambda conn=None: dict(row))
    monkeypatch.setattr(client_subject, "get_conn", _FakeConn)
    monkeypatch.setattr(client_subject, "is_postgres_runtime", lambda: False)
    monkeypatch.setattr(client_subject, "_request_token", request_token)
    monkeypatch.setattr(client_subject, "_persist_grant", persist)

    results: list[dict] = []
    threads = [
        threading.Thread(
            target=lambda: results.append(client_subject.refresh_access_token(now=now))
        )
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert request_count == 1
    assert len(results) == 2
    assert all(result["ok"] for result in results)
    assert any(result["reused"] for result in results)


def test_postgres_refresh_uses_transaction_advisory_lock(monkeypatch):
    conn = _FakeConn()
    monkeypatch.setattr(client_subject, "is_postgres_runtime", lambda: True)

    assert client_subject._acquire_postgres_singleflight(conn) is True
    assert "pg_advisory_xact_lock" in conn.executions[0][0]
    assert conn.executions[0][1] == ("vkpi_shopify_client_credentials_refresh",)


def test_provider_rejection_is_revoked_and_redacted(monkeypatch):
    now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    row = _formal_row()
    conn = _FakeConn()

    class _Response:
        status_code = 401

        @staticmethod
        def json():
            return {
                "error": "client_secret_1234567890 and provider-private-diagnostics"
            }

    class _Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, *_args, **_kwargs):
            return _Response()

    monkeypatch.setattr(subject, "_load_row", lambda conn=None: dict(row))
    monkeypatch.setattr(client_subject, "get_conn", lambda: conn)
    monkeypatch.setattr(client_subject, "is_postgres_runtime", lambda: False)
    monkeypatch.setattr(client_subject.httpx, "Client", _Client)

    result = client_subject.refresh_access_token(force=True, now=now)

    assert result["ok"] is False
    assert result["status"] == "revoked"
    assert result["reason"] == "provider_rejected_credentials"
    assert "client_secret_1234567890" not in repr(result)
    assert "provider-private-diagnostics" not in repr(result)
    assert conn.executions[-1][1][0] == "revoked"
