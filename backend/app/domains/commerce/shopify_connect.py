"""Shopify connect — encrypted credentials, connection truth and webhooks.

目标:无真 creds 也能跑通代码(单测用 mock,真 creds 一填即用)。
- Organization apps use migration 267's Client Credentials fields. Legacy
  access-token credentials remain an explicit compatibility mode.
- access_token/client_secret/webhook_secret plaintext never reaches storage,
  logs, or a response.
- 加密密钥派生与 channels/common._fernet() 同一组 env(VKPI_CHANNELS_ENCRYPTION_KEY|JWT_SECRET|
  APP_SECRET|fallback),保证同密钥;本文件自带 _fernet/_encrypt/_decrypt(channels 模块无 _decrypt)。
- connection_status():表无行 → fallback 读 os.environ(向后兼容旧 SHOPIFY_* env);env+DB 都空 → not_configured。
- register_webhooks():无 creds → {ok:False, reason:'not_configured'},绝不抛、绝不烧 LLM。

DB 全走 get_conn() + '?' 占位 + conn.commit();SQL 禁裸 %。
SQLite 运行时自建表(is_postgres_runtime() 短路);Postgres 走 migrations 145/267。
与 KOL 评分域物理隔离:无 viltrox_fit_score / rule_v0 触点。
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app.db.connection import get_conn, is_postgres_runtime
from app.domains.projects.workflow import staff_id as resolve_staff_id

_DEFAULT_API_VERSION = "2026-04"  # 对齐 viltrox.com 店应用声明的 API 版本(同事可在连接表单覆盖)
_CREDS_SINGLETON_ID = 1
_VALID_STATUS = {"pending", "connected", "error", "revoked"}
_SCHEMA_READY = False
_SHOPIFY_DOMAIN_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.myshopify\.com$"
)
_SHOPIFY_API_VERSION_RE = re.compile(r"^20\d{2}-(?:01|04|07|10)$")
_SHOPIFY_DOMAIN_ERROR = (
    "shop_domain must be a canonical *.myshopify.com hostname without "
    "scheme, path, port, userinfo, query, or fragment"
)


class ShopifyEncryptionKeyUnavailable(RuntimeError):
    """Production secret persistence was attempted without explicit key material."""


def _is_production_environment() -> bool:
    mode = str(os.environ.get("ENVIRONMENT") or os.environ.get("APP_ENV") or "").strip().lower()
    legacy_flag = str(os.environ.get("V2_PRODUCTION_MODE") or "").strip().lower()
    return mode in {"prod", "production"} or legacy_flag in {"1", "true", "yes", "on"}


def require_credentials_encryption_ready() -> None:
    """Fail closed before a production credential write or provider cutover.

    Development and tests retain the historical deterministic key so existing
    local fixtures remain readable. Production must supply at least one of the
    documented explicit secret sources; it may never fall back to that key.
    """

    if any(
        str(os.environ.get(name) or "").strip()
        for name in ("VKPI_CHANNELS_ENCRYPTION_KEY", "JWT_SECRET", "APP_SECRET")
    ):
        return
    if _is_production_environment():
        raise ShopifyEncryptionKeyUnavailable(
            "production Shopify credential encryption key is not configured"
        )


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fernet() -> Fernet:
    # Same key derivation as channels/common._fernet() so secrets round-trip across domains.
    require_credentials_encryption_ready()
    key = (
        os.environ.get("VKPI_CHANNELS_ENCRYPTION_KEY")
        or os.environ.get("JWT_SECRET")
        or os.environ.get("APP_SECRET")
        or "vkpi-local-dev-key"
    )
    raw = hashlib.sha256(key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(raw))


def _encrypt(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    return _fernet().encrypt(text.encode("utf-8")).decode("utf-8")


def _decrypt(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    try:
        return _fernet().decrypt(text.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ShopifyEncryptionKeyUnavailable, ValueError, TypeError):
        # Never leak the ciphertext or raise into a request path; an unreadable
        # secret behaves as not-configured rather than crashing creds-ready flows.
        return ""


def _mask(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    return f"{text[:4]}...{text[-4:]}" if len(text) > 8 else "****"


def _norm_domain(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text or not _SHOPIFY_DOMAIN_RE.fullmatch(text):
        return ""
    return text


def _require_shop_domain(value: Any) -> str:
    domain = _norm_domain(value)
    if not domain:
        raise ValueError(_SHOPIFY_DOMAIN_ERROR)
    return domain


def _require_api_version(value: Any) -> str:
    version = str(value or "").strip() or _DEFAULT_API_VERSION
    if not _SHOPIFY_API_VERSION_RE.fullmatch(version):
        raise ValueError("api_version must be a dated Shopify version such as 2026-04")
    return version


def _actor(staff: dict[str, Any] | None) -> int | None:
    try:
        return resolve_staff_id(staff) or None
    except Exception:
        return None


def ensure_shopify_creds_schema() -> None:
    """SQLite-only runtime guard mirroring schema_reconciliation; Postgres uses migration 144."""
    global _SCHEMA_READY
    if _SCHEMA_READY or is_postgres_runtime():
        return
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS vkpi_shopify_credentials (
            id INTEGER PRIMARY KEY,
            shop_domain TEXT NOT NULL DEFAULT '',
            access_token_encrypted TEXT NOT NULL DEFAULT '',
            webhook_secret_encrypted TEXT NOT NULL DEFAULT '',
            auth_mode TEXT NOT NULL DEFAULT 'legacy_access_token',
            client_id TEXT NOT NULL DEFAULT '',
            client_secret_encrypted TEXT NOT NULL DEFAULT '',
            access_token_expires_at TEXT,
            granted_scopes TEXT NOT NULL DEFAULT '',
            last_refresh_at TEXT,
            revoked_at TEXT,
            refresh_lease_owner TEXT,
            refresh_lease_expires_at TEXT,
            refresh_retry_after TEXT,
            refresh_failure_count INTEGER NOT NULL DEFAULT 0,
            api_version TEXT NOT NULL DEFAULT '2024-10',
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','connected','error','revoked')),
            connected_at TEXT,
            updated_at TEXT NOT NULL,
            updated_by_staff_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS vkpi_event_discount_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL,
            discount_code TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            created_by_staff_id INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_event_discount_codes_event
            ON vkpi_event_discount_codes(event_id);
        """
    )
    existing_columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(vkpi_shopify_credentials)").fetchall()
    }
    sqlite_additions = {
        "auth_mode": "TEXT NOT NULL DEFAULT 'legacy_access_token'",
        "client_id": "TEXT NOT NULL DEFAULT ''",
        "client_secret_encrypted": "TEXT NOT NULL DEFAULT ''",
        "access_token_expires_at": "TEXT",
        "granted_scopes": "TEXT NOT NULL DEFAULT ''",
        "last_refresh_at": "TEXT",
        "revoked_at": "TEXT",
        "refresh_lease_owner": "TEXT",
        "refresh_lease_expires_at": "TEXT",
        "refresh_retry_after": "TEXT",
        "refresh_failure_count": "INTEGER NOT NULL DEFAULT 0",
    }
    for column, definition in sqlite_additions.items():
        if column not in existing_columns:
            conn.execute(f"ALTER TABLE vkpi_shopify_credentials ADD COLUMN {column} {definition}")
    conn.commit()
    _SCHEMA_READY = True


def _load_row(conn: Any | None = None) -> dict[str, Any]:
    ensure_shopify_creds_schema()
    row = (conn or get_conn()).execute(
        "SELECT * FROM vkpi_shopify_credentials WHERE id=?",
        (_CREDS_SINGLETON_ID,),
    ).fetchone()
    return dict(row) if row else {}


def _scope_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        raw = value
    else:
        raw = str(value or "").replace(" ", ",").split(",")
    return sorted({str(scope).strip() for scope in raw if str(scope).strip()})


def _credentials_from_row(row: dict[str, Any]) -> dict[str, Any]:
    auth_mode = str(row.get("auth_mode") or "legacy_access_token").strip().lower()
    if auth_mode not in {"client_credentials", "legacy_access_token"}:
        auth_mode = "legacy_access_token"
    client_secret = _decrypt(str(row.get("client_secret_encrypted") or ""))
    webhook_secret = _decrypt(str(row.get("webhook_secret_encrypted") or ""))
    if auth_mode == "client_credentials" and client_secret:
        webhook_secret = client_secret
    return {
        "shop_domain": _norm_domain(row.get("shop_domain")),
        "auth_mode": auth_mode,
        "client_id": str(row.get("client_id") or "").strip(),
        "client_secret": client_secret,
        "access_token": _decrypt(str(row.get("access_token_encrypted") or "")),
        "webhook_secret": webhook_secret,
        "access_token_expires_at": row.get("access_token_expires_at"),
        "granted_scopes": _scope_list(row.get("granted_scopes")),
        "last_refresh_at": row.get("last_refresh_at"),
        "revoked_at": row.get("revoked_at"),
        "refresh_lease_owner": str(row.get("refresh_lease_owner") or ""),
        "refresh_lease_expires_at": row.get("refresh_lease_expires_at"),
        "refresh_retry_after": row.get("refresh_retry_after"),
        "refresh_failure_count": int(row.get("refresh_failure_count") or 0),
        "updated_at": row.get("updated_at"),
        "api_version": str(row.get("api_version") or _DEFAULT_API_VERSION),
        "status": str(row.get("status") or "pending"),
        "connected_at": row.get("connected_at"),
        "source": "db",
    }


def save_credentials(body: dict[str, Any], staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """Encrypt + upsert Shopify creds (singleton id=1). Returns masked-only view.

    Never logs or returns the plaintext token/secret. Raises ValueError when
    shop_domain is missing or no secret material is supplied.
    """
    body = body or {}
    # ``store_domain`` was used by the first Shopify Hub form.  Keep accepting
    # it at the boundary while standardising new clients on ``shop_domain`` so
    # an authorization submitted through the existing UI does not fail with a
    # misleading "shop_domain is required" response.
    shop_domain = _require_shop_domain(
        body.get("shop_domain")
        or body.get("shopDomain")
        or body.get("store_domain")
        or body.get("storeDomain")
        or body.get("shop")
    )
    access_token = str(body.get("access_token") or body.get("accessToken") or "").strip()
    webhook_secret = str(body.get("webhook_secret") or body.get("webhookSecret") or "").strip()
    api_version = _require_api_version(body.get("api_version") or body.get("apiVersion"))
    if not access_token and not webhook_secret:
        raise ValueError("access_token or webhook_secret is required")

    existing = _load_row()
    existing_mode = str(existing.get("auth_mode") or "legacy_access_token").strip().lower()
    if existing_mode != "legacy_access_token" and not access_token:
        raise ValueError("access_token is required when switching to legacy_access_token mode")
    # Preserve previously stored secrets when a field is omitted on update.
    token_enc = (
        _encrypt(access_token)
        if access_token
        else str(existing.get("access_token_encrypted") or "")
    )
    secret_enc = (
        _encrypt(webhook_secret)
        if webhook_secret
        else str(existing.get("webhook_secret_encrypted") or "")
        if existing_mode == "legacy_access_token"
        else ""
    )
    # Credential presence is configuration, not proof that Shopify accepted it.
    status = "pending"
    now = _utcnow()
    # Do not manufacture a connection timestamp merely because a token was
    # saved.  A later real provider success may populate this field.
    # Any credential write invalidates the previous provider proof.  A token
    # replacement must never inherit an old ``connected_at`` receipt.
    connected_at = None
    actor = _actor(staff)

    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_shopify_credentials
            (id, shop_domain, access_token_encrypted, webhook_secret_encrypted,
             api_version, status, connected_at, updated_at, updated_by_staff_id,
             auth_mode, client_id, client_secret_encrypted, access_token_expires_at,
             granted_scopes, last_refresh_at, revoked_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            shop_domain=excluded.shop_domain,
            access_token_encrypted=excluded.access_token_encrypted,
            webhook_secret_encrypted=excluded.webhook_secret_encrypted,
            api_version=excluded.api_version,
            status=excluded.status,
            connected_at=excluded.connected_at,
            updated_at=excluded.updated_at,
            updated_by_staff_id=excluded.updated_by_staff_id,
            auth_mode=excluded.auth_mode,
            client_id=excluded.client_id,
            client_secret_encrypted=excluded.client_secret_encrypted,
            access_token_expires_at=excluded.access_token_expires_at,
            granted_scopes=excluded.granted_scopes,
            last_refresh_at=excluded.last_refresh_at,
            revoked_at=excluded.revoked_at,
            refresh_lease_owner=NULL, refresh_lease_expires_at=NULL,
            refresh_retry_after=NULL, refresh_failure_count=0
        """,
        (
            _CREDS_SINGLETON_ID,
            shop_domain,
            token_enc,
            secret_enc,
            api_version,
            status,
            connected_at,
            now,
            actor,
            "legacy_access_token",
            "",
            "",
            None,
            "",
            None,
            None,
        ),
    )
    conn.commit()
    return {
        "ok": True,
        "shop_domain": shop_domain,
        "token_configured": bool(token_enc),
        "webhook_secret_configured": bool(secret_enc),
        "auth_mode": "legacy_access_token",
        "api_version": api_version,
        "status": status,
        "source": "db",
    }


def get_credentials() -> dict[str, Any]:
    """Internal-only: returns DECRYPTED token/secret for in-process Admin API calls.

    NEVER serialize this into an HTTP response or a log line. Falls back to the
    legacy SHOPIFY_* environment variables when no DB row exists.
    """
    row = _load_row()
    if row and any(
        row.get(field)
        for field in (
            "access_token_encrypted",
            "webhook_secret_encrypted",
            "client_id",
            "client_secret_encrypted",
        )
    ):
        return _credentials_from_row(row)
    env_domain = _norm_domain(os.environ.get("SHOPIFY_SHOP_DOMAIN"))
    env_token = str(
        os.environ.get("SHOPIFY_ADMIN_ACCESS_TOKEN") or os.environ.get("SHOPIFY_API_ACCESS_TOKEN") or ""
    ).strip()
    env_secret = str(os.environ.get("SHOPIFY_WEBHOOK_SECRET") or "").strip()
    if env_domain or env_token or env_secret:
        return {
            "shop_domain": env_domain,
            "auth_mode": "legacy_access_token",
            "client_id": "",
            "client_secret": "",
            "access_token": env_token,
            "webhook_secret": env_secret,
            "access_token_expires_at": None,
            "granted_scopes": [],
            "last_refresh_at": None,
            "revoked_at": None,
            "api_version": str(os.environ.get("SHOPIFY_API_VERSION") or _DEFAULT_API_VERSION).strip() or _DEFAULT_API_VERSION,
            "status": "pending",
            "connected_at": None,
            "source": "env",
        }
    return {
        "shop_domain": "",
        "auth_mode": "legacy_access_token",
        "client_id": "",
        "client_secret": "",
        "access_token": "",
        "webhook_secret": "",
        "access_token_expires_at": None,
        "granted_scopes": [],
        "last_refresh_at": None,
        "revoked_at": None,
        "refresh_lease_owner": "",
        "refresh_lease_expires_at": None,
        "refresh_retry_after": None,
        "refresh_failure_count": 0,
        "api_version": _DEFAULT_API_VERSION,
        "status": "pending",
        "connected_at": None,
        "source": "none",
    }


def connection_status() -> dict[str, Any]:
    """Masked, response-safe connection status. Never returns a plaintext token."""
    creds = get_credentials()
    from app.domains.commerce import shopify_client_credentials

    token = creds.get("access_token") or ""
    secret = creds.get("webhook_secret") or ""
    domain = creds.get("shop_domain") or ""
    auth_mode = str(creds.get("auth_mode") or "legacy_access_token")
    client_id = str(creds.get("client_id") or "")
    client_secret = str(creds.get("client_secret") or "")
    source = creds.get("source") or "none"
    token_fresh = shopify_client_credentials.token_is_fresh(creds)
    configured = (
        bool(domain and client_id and client_secret)
        if auth_mode == "client_credentials"
        else bool(domain and token)
    )
    persisted_status = str(creds.get("status") or "pending").strip().lower()
    if source == "none":
        status = "not_configured"
    elif persisted_status in {"error", "revoked"}:
        status = persisted_status
    elif configured and token_fresh and persisted_status == "connected":
        status = "connected"
    elif configured and token_fresh:
        status = "configured"
    elif configured:
        status = "pending"
    else:
        status = persisted_status if persisted_status in _VALID_STATUS else "pending"
    return {
        "shop_domain": domain,
        "auth_mode": auth_mode,
        "client_id_configured": bool(client_id),
        "client_secret_configured": bool(client_secret),
        "token_configured": bool(token),
        "token_fresh": token_fresh,
        "refresh_required": auth_mode == "client_credentials" and not token_fresh,
        "access_token_expires_at": shopify_client_credentials.iso_timestamp(
            creds.get("access_token_expires_at")
        ),
        "granted_scopes": list(creds.get("granted_scopes") or []),
        "last_refresh_at": shopify_client_credentials.iso_timestamp(creds.get("last_refresh_at")),
        "revoked_at": shopify_client_credentials.iso_timestamp(creds.get("revoked_at")),
        "webhook_secret_configured": bool(secret),
        "api_version": creds.get("api_version") or _DEFAULT_API_VERSION,
        "status": status,
        "last_verified_at": creds.get("connected_at"),
        "source": source,
    }


_SHOP_PROBE_QUERY = """
query VkpiShopifyConnectionProbe {
  shop { id name myshopifyDomain }
}
""".strip()


def _admin_credentials() -> dict[str, Any]:
    from app.domains.commerce import shopify_client_credentials

    return shopify_client_credentials.credentials_with_fresh_token()


def _persist_probe_state(status: str, *, connected_at: str | None = None) -> None:
    """Persist only a coarse provider state; never provider text or secrets."""

    if status not in _VALID_STATUS:
        status = "error"
    ensure_shopify_creds_schema()
    row = _load_row()
    if not row:
        # Environment-backed credentials stay environment-backed.  Do not copy
        # secret material into the database merely to store a probe receipt.
        return
    conn = get_conn()
    conn.execute(
        """
        UPDATE vkpi_shopify_credentials
        SET status=?, connected_at=?, updated_at=?
        WHERE id=?
        """,
        (status, connected_at if status == "connected" else None, _utcnow(), _CREDS_SINGLETON_ID),
    )
    conn.commit()


def _probe_credentials(
    creds: dict[str, Any],
    request=None,
) -> dict[str, Any]:
    """Probe an explicit candidate without reading or mutating the singleton."""

    domain = _norm_domain(creds.get("shop_domain"))
    token = str(creds.get("access_token") or "").strip()
    if not domain or not token:
        return {"ok": False, "status": "not_configured", "reason": "not_configured"}
    result = (
        request(_SHOP_PROBE_QUERY)
        if request is not None
        else _post_graphql_with_credentials(creds, _SHOP_PROBE_QUERY)
    )
    if not result.get("ok"):
        status_code = int(result.get("status_code") or 0)
        revoked = status_code in {401, 403}
        return {
            "ok": False,
            "status": "revoked" if revoked else "error",
            "reason": (
                "provider_rejected_credentials"
                if revoked
                else "provider_graphql_error"
                if result.get("errors")
                else "provider_unreachable"
            ),
            "shop_domain": domain,
        }

    shop = ((result.get("data") or {}).get("shop")) if isinstance(result.get("data"), dict) else None
    returned_domain = _norm_domain(shop.get("myshopifyDomain")) if isinstance(shop, dict) else ""
    if (
        not isinstance(shop, dict)
        or not str(shop.get("id") or "").strip()
        or not returned_domain
        or returned_domain != domain
    ):
        return {
            "ok": False,
            "status": "error",
            "reason": "provider_probe_payload_invalid",
            "shop_domain": domain,
        }
    return {
        "ok": True,
        "status": "connected",
        "shop_domain": domain,
        "verified_at": _utcnow(),
        "shop": {
            "id": str(shop.get("id") or ""),
            "name": str(shop.get("name") or ""),
            "myshopify_domain": returned_domain,
        },
    }


def probe_connection() -> dict[str, Any]:
    """Run an explicit, read-only Admin API canary and store its coarse receipt.

    This function is only called by the admin-gated probe endpoint.  Merely
    saving credentials never calls Shopify and never becomes ``connected``.
    Provider response bodies are deliberately not returned or persisted.
    """

    creds = _admin_credentials()
    domain = _norm_domain(creds.get("shop_domain"))
    token = str(creds.get("access_token") or "").strip()
    if not domain or not token:
        reason = str(creds.get("token_refresh_reason") or "not_configured")
        return {
            "ok": False,
            "status": "error" if reason != "not_configured" else "not_configured",
            "reason": reason,
        }

    result = _probe_credentials(creds, request=post_graphql)
    if result.get("ok"):
        _persist_probe_state("connected", connected_at=str(result.get("verified_at") or _utcnow()))
    else:
        _persist_probe_state(str(result.get("status") or "error"))
    return result


def _admin_endpoint(creds: dict[str, Any] | None = None) -> str:
    creds = creds or get_credentials()
    domain = _require_shop_domain(creds.get("shop_domain"))
    api_version = _require_api_version(creds.get("api_version"))
    return f"https://{domain}/admin/api/{api_version}/graphql.json"


def _admin_headers(token: str) -> dict[str, str]:
    return {"X-Shopify-Access-Token": str(token or ""), "Content-Type": "application/json"}


def _public_base_url() -> str:
    base = str(
        os.environ.get("PUBLIC_BASE_URL")
        or os.environ.get("APP_BASE_URL")
        or os.environ.get("WEBHOOK_BASE_URL")
        or os.environ.get("SHOPIFY_WEBHOOK_BASE_URL")
        or ""
    ).strip()
    return base.rstrip("/")


def _post_graphql_with_credentials(
    creds: dict[str, Any],
    query: str,
    variables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Single sanitized GraphQL call using an explicit candidate credential set."""

    token = creds.get("access_token") or ""
    domain = _norm_domain(creds.get("shop_domain"))
    if not domain or not token:
        return {
            "ok": False,
            "reason": str(creds.get("token_refresh_reason") or "not_configured"),
        }
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.post(
                _admin_endpoint(creds),
                headers=_admin_headers(token),
                json={"query": query, "variables": variables or {}},
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        return {"ok": False, "error": f"http {exc.response.status_code}", "status_code": exc.response.status_code}
    except (httpx.HTTPError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    errors = data.get("errors") if isinstance(data, dict) else None
    if errors:
        return {"ok": False, "errors": errors}
    return {"ok": True, "data": data.get("data") if isinstance(data, dict) else None}


def post_graphql(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    """Single GraphQL Admin API call. no creds -> not_configured, never raises."""

    return _post_graphql_with_credentials(_admin_credentials(), query, variables)


_WEBHOOK_TOPICS = (
    ("ORDERS_CREATE", "/api/vkpi/webhooks/shopify/orders"),
    ("ORDERS_UPDATED", "/api/vkpi/webhooks/shopify/orders"),
    ("REFUNDS_CREATE", "/api/vkpi/webhooks/shopify/refunds"),
)

_WEBHOOK_MUTATION = """
mutation webhookSubscriptionCreate($topic: WebhookSubscriptionTopic!, $sub: WebhookSubscriptionInput!) {
  webhookSubscriptionCreate(topic: $topic, webhookSubscription: $sub) {
    webhookSubscription { id }
    userErrors { field message }
  }
}
""".strip()

_WEBHOOK_DELETE_MUTATION = """
mutation webhookSubscriptionDelete($id: ID!) {
  webhookSubscriptionDelete(id: $id) {
    deletedWebhookSubscriptionId
    userErrors { field message }
  }
}
""".strip()


def _cleanup_webhooks(
    creds: dict[str, Any],
    registered: list[dict[str, Any]],
) -> dict[str, Any]:
    attempted = [str(item.get("id") or "") for item in registered if item.get("id")]
    deleted: list[str] = []
    failed = 0
    for subscription_id in attempted:
        result = _post_graphql_with_credentials(
            creds,
            _WEBHOOK_DELETE_MUTATION,
            {"id": subscription_id},
        )
        payload = ((result.get("data") or {}).get("webhookSubscriptionDelete")) or {}
        if (
            result.get("ok")
            and not (payload.get("userErrors") or [])
            and str(payload.get("deletedWebhookSubscriptionId") or "") == subscription_id
        ):
            deleted.append(subscription_id)
        else:
            failed += 1
    return {"attempted": len(attempted), "deleted": len(deleted), "failed": failed}


def _register_webhooks_with_credentials(creds: dict[str, Any]) -> dict[str, Any]:
    """Register all required subscriptions for an explicit candidate.

    On a partial failure, best-effort deletion is attempted for subscriptions
    created by this call only. Existing subscriptions are never touched.
    """

    token = creds.get("access_token") or ""
    domain = _norm_domain(creds.get("shop_domain"))
    if not domain or not token:
        return {"ok": False, "reason": "not_configured", "registered": []}
    base = _public_base_url()
    if not base:
        return {"ok": False, "reason": "public_base_url_missing", "registered": []}

    registered: list[dict[str, Any]] = []
    errors: list[Any] = []
    for topic, path in _WEBHOOK_TOPICS:
        callback_url = f"{base}{path}"
        result = _post_graphql_with_credentials(
            creds,
            _WEBHOOK_MUTATION,
            {"topic": topic, "sub": {"callbackUrl": callback_url, "format": "JSON"}},
        )
        if not result.get("ok"):
            errors.append({"topic": topic, "reason": result.get("reason") or "provider_webhook_error"})
            continue
        payload = ((result.get("data") or {}).get("webhookSubscriptionCreate")) or {}
        user_errors = payload.get("userErrors") or []
        sub = payload.get("webhookSubscription") or {}
        if user_errors or not sub.get("id"):
            errors.append({"topic": topic, "reason": "provider_webhook_rejected"})
            continue
        registered.append({"topic": topic, "callbackUrl": callback_url, "id": sub.get("id")})
    cleanup = {"attempted": 0, "deleted": 0, "failed": 0}
    if errors and registered:
        cleanup = _cleanup_webhooks(creds, registered)
    return {
        "ok": len(registered) == len(_WEBHOOK_TOPICS) and not errors,
        "registered": registered,
        "registered_count": len(registered),
        "required_count": len(_WEBHOOK_TOPICS),
        "errors": errors,
        "cleanup": cleanup,
    }


def register_webhooks() -> dict[str, Any]:
    """Register ORDERS_CREATE/ORDERS_UPDATED/REFUNDS_CREATE webhooks via Admin GraphQL.

    creds-ready: no creds or no PUBLIC_BASE_URL -> {ok:False, reason:...}; never raises.
    """
    creds = _admin_credentials()
    result = _register_webhooks_with_credentials(creds)
    if not result.get("ok") and not result.get("reason"):
        result["reason"] = "provider_webhook_error"
    return result


__all__ = [
    "ShopifyEncryptionKeyUnavailable",
    "ensure_shopify_creds_schema",
    "save_credentials",
    "get_credentials",
    "connection_status",
    "probe_connection",
    "register_webhooks",
    "post_graphql",
    "require_credentials_encryption_ready",
]
