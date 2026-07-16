from __future__ import annotations

from app.domains.commerce import shopify_connect as subject


class _FakeConn:
    def __init__(self) -> None:
        self.executions: list[tuple[str, tuple]] = []
        self.commits = 0

    def execute(self, sql: str, params: tuple = ()):
        self.executions.append((sql, params))
        return self

    def commit(self) -> None:
        self.commits += 1


def test_save_accepts_existing_ui_domain_alias_and_never_echoes_secret(monkeypatch):
    conn = _FakeConn()
    monkeypatch.setattr(subject, "_load_row", lambda: {})
    monkeypatch.setattr(subject, "get_conn", lambda: conn)

    result = subject.save_credentials(
        {
            "store_domain": "https://Demo.MyShopify.com/admin/",
            "access_token": "shpat_super_secret",
            "webhook_secret": "webhook_super_secret",
        }
    )

    assert result == {
        "ok": True,
        "shop_domain": "demo.myshopify.com",
        "token_configured": True,
        "webhook_secret_configured": True,
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
