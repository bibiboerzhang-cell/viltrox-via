"""Shopify connect — creds 加密存 + 连接状态 + webhook 自动注册(creds-ready)。

目标:无真 creds 也能跑通代码(单测用 mock,真 creds 一填即用)。
- creds(shop_domain/access_token/webhook_secret)以 Fernet 密文落库 vkpi_shopify_credentials;
  明文 access_token/webhook_secret 绝不落库、绝不进日志、绝不进 response(只 _mask)。
- 加密密钥派生与 channels/common._fernet() 同一组 env(VKPI_CHANNELS_ENCRYPTION_KEY|JWT_SECRET|
  APP_SECRET|fallback),保证同密钥;本文件自带 _fernet/_encrypt/_decrypt(channels 模块无 _decrypt)。
- connection_status():表无行 → fallback 读 os.environ(向后兼容旧 SHOPIFY_* env);env+DB 都空 → not_configured。
- register_webhooks():无 creds → {ok:False, reason:'not_configured'},绝不抛、绝不烧 LLM。

DB 全走 get_conn() + '?' 占位 + conn.commit();SQL 禁裸 %。
SQLite 运行时自建表(is_postgres_runtime() 短路);Postgres 走 migration 144。
与 KOL 评分域物理隔离:无 viltrox_fit_score / rule_v0 触点。
"""
from __future__ import annotations

import base64
import hashlib
import os
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


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fernet() -> Fernet:
    # Same key derivation as channels/common._fernet() so secrets round-trip across domains.
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
    except (InvalidToken, ValueError, TypeError):
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
    if not text:
        return ""
    for prefix in ("https://", "http://"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text.strip("/").split("/")[0]


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
    conn.commit()
    _SCHEMA_READY = True


def _load_row() -> dict[str, Any]:
    ensure_shopify_creds_schema()
    row = get_conn().execute(
        """
        SELECT id, shop_domain, access_token_encrypted, webhook_secret_encrypted,
               api_version, status, connected_at, updated_at, updated_by_staff_id
        FROM vkpi_shopify_credentials
        WHERE id=?
        """,
        (_CREDS_SINGLETON_ID,),
    ).fetchone()
    return dict(row) if row else {}


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
    shop_domain = _norm_domain(
        body.get("shop_domain")
        or body.get("shopDomain")
        or body.get("store_domain")
        or body.get("storeDomain")
        or body.get("shop")
    )
    if not shop_domain:
        raise ValueError("shop_domain is required")
    access_token = str(body.get("access_token") or body.get("accessToken") or "").strip()
    webhook_secret = str(body.get("webhook_secret") or body.get("webhookSecret") or "").strip()
    api_version = str(body.get("api_version") or body.get("apiVersion") or "").strip() or _DEFAULT_API_VERSION
    if not access_token and not webhook_secret:
        raise ValueError("access_token or webhook_secret is required")

    existing = _load_row()
    # Preserve previously stored secrets when a field is omitted on update.
    token_enc = _encrypt(access_token) if access_token else str(existing.get("access_token_encrypted") or "")
    secret_enc = _encrypt(webhook_secret) if webhook_secret else str(existing.get("webhook_secret_encrypted") or "")
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
             api_version, status, connected_at, updated_at, updated_by_staff_id)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            shop_domain=excluded.shop_domain,
            access_token_encrypted=excluded.access_token_encrypted,
            webhook_secret_encrypted=excluded.webhook_secret_encrypted,
            api_version=excluded.api_version,
            status=excluded.status,
            connected_at=excluded.connected_at,
            updated_at=excluded.updated_at,
            updated_by_staff_id=excluded.updated_by_staff_id
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
        ),
    )
    conn.commit()
    return {
        "ok": True,
        "shop_domain": shop_domain,
        "token_configured": bool(token_enc),
        "webhook_secret_configured": bool(secret_enc),
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
    if row and (row.get("access_token_encrypted") or row.get("webhook_secret_encrypted")):
        return {
            "shop_domain": str(row.get("shop_domain") or ""),
            "access_token": _decrypt(str(row.get("access_token_encrypted") or "")),
            "webhook_secret": _decrypt(str(row.get("webhook_secret_encrypted") or "")),
            "api_version": str(row.get("api_version") or _DEFAULT_API_VERSION),
            "status": str(row.get("status") or "pending"),
            "connected_at": row.get("connected_at"),
            "source": "db",
        }
    env_domain = _norm_domain(os.environ.get("SHOPIFY_SHOP_DOMAIN"))
    env_token = str(
        os.environ.get("SHOPIFY_ADMIN_ACCESS_TOKEN") or os.environ.get("SHOPIFY_API_ACCESS_TOKEN") or ""
    ).strip()
    env_secret = str(os.environ.get("SHOPIFY_WEBHOOK_SECRET") or "").strip()
    if env_domain or env_token or env_secret:
        return {
            "shop_domain": env_domain,
            "access_token": env_token,
            "webhook_secret": env_secret,
            "api_version": str(os.environ.get("SHOPIFY_API_VERSION") or _DEFAULT_API_VERSION).strip() or _DEFAULT_API_VERSION,
            "status": "pending",
            "connected_at": None,
            "source": "env",
        }
    return {
        "shop_domain": "",
        "access_token": "",
        "webhook_secret": "",
        "api_version": _DEFAULT_API_VERSION,
        "status": "pending",
        "connected_at": None,
        "source": "none",
    }


def connection_status() -> dict[str, Any]:
    """Masked, response-safe connection status. Never returns a plaintext token."""
    creds = get_credentials()
    token = creds.get("access_token") or ""
    secret = creds.get("webhook_secret") or ""
    domain = creds.get("shop_domain") or ""
    source = creds.get("source") or "none"
    configured = bool(domain and token)
    persisted_status = str(creds.get("status") or "pending").strip().lower()
    if source == "none":
        status = "not_configured"
    elif configured and persisted_status in {"connected", "error", "revoked"}:
        status = persisted_status
    elif configured:
        status = "configured"
    else:
        status = persisted_status if persisted_status in _VALID_STATUS else "pending"
    return {
        "shop_domain": domain,
        "token_configured": bool(token),
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


def probe_connection() -> dict[str, Any]:
    """Run an explicit, read-only Admin API canary and store its coarse receipt.

    This function is only called by the admin-gated probe endpoint.  Merely
    saving credentials never calls Shopify and never becomes ``connected``.
    Provider response bodies are deliberately not returned or persisted.
    """

    creds = get_credentials()
    domain = _norm_domain(creds.get("shop_domain"))
    token = str(creds.get("access_token") or "").strip()
    if not domain or not token:
        return {"ok": False, "status": "not_configured", "reason": "not_configured"}

    result = post_graphql(_SHOP_PROBE_QUERY)
    if not result.get("ok"):
        status_code = int(result.get("status_code") or 0)
        revoked = status_code in {401, 403}
        state = "revoked" if revoked else "error"
        _persist_probe_state(state)
        reason = (
            "provider_rejected_credentials"
            if revoked
            else "provider_graphql_error"
            if result.get("errors")
            else "provider_unreachable"
        )
        return {"ok": False, "status": state, "reason": reason, "shop_domain": domain}

    shop = ((result.get("data") or {}).get("shop")) if isinstance(result.get("data"), dict) else None
    if not isinstance(shop, dict) or not str(shop.get("id") or "").strip():
        _persist_probe_state("error")
        return {
            "ok": False,
            "status": "error",
            "reason": "provider_probe_payload_invalid",
            "shop_domain": domain,
        }

    verified_at = _utcnow()
    _persist_probe_state("connected", connected_at=verified_at)
    return {
        "ok": True,
        "status": "connected",
        "shop_domain": domain,
        "verified_at": verified_at,
        "shop": {
            "id": str(shop.get("id") or ""),
            "name": str(shop.get("name") or ""),
            "myshopify_domain": _norm_domain(shop.get("myshopifyDomain") or domain),
        },
    }


def _admin_endpoint(creds: dict[str, Any] | None = None) -> str:
    creds = creds or get_credentials()
    domain = _norm_domain(creds.get("shop_domain"))
    api_version = str(creds.get("api_version") or _DEFAULT_API_VERSION).strip() or _DEFAULT_API_VERSION
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


def post_graphql(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    """Single GraphQL Admin API call. creds-ready: no creds -> not_configured, never raises.

    Returns {ok, data?, errors?, reason?, error?}. Never burns an LLM; httpx direct.
    """
    creds = get_credentials()
    token = creds.get("access_token") or ""
    domain = _norm_domain(creds.get("shop_domain"))
    if not domain or not token:
        return {"ok": False, "reason": "not_configured"}
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


def register_webhooks() -> dict[str, Any]:
    """Register ORDERS_CREATE/ORDERS_UPDATED/REFUNDS_CREATE webhooks via Admin GraphQL.

    creds-ready: no creds or no PUBLIC_BASE_URL -> {ok:False, reason:...}; never raises.
    """
    creds = get_credentials()
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
        result = post_graphql(
            _WEBHOOK_MUTATION,
            {"topic": topic, "sub": {"callbackUrl": callback_url, "format": "JSON"}},
        )
        if not result.get("ok"):
            errors.append({"topic": topic, "error": result.get("error") or result.get("errors") or result.get("reason")})
            continue
        payload = ((result.get("data") or {}).get("webhookSubscriptionCreate")) or {}
        user_errors = payload.get("userErrors") or []
        sub = payload.get("webhookSubscription") or {}
        if user_errors:
            errors.append({"topic": topic, "userErrors": user_errors})
            continue
        registered.append({"topic": topic, "callbackUrl": callback_url, "id": sub.get("id")})
    return {"ok": bool(registered) and not errors, "registered": registered, "errors": errors}


__all__ = [
    "ensure_shopify_creds_schema",
    "save_credentials",
    "get_credentials",
    "connection_status",
    "probe_connection",
    "register_webhooks",
    "post_graphql",
]
