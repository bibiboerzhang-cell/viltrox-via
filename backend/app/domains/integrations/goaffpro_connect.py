"""GOAFFPRO connect — Affiliate 接入骨架(D1,无 key 可建)。

用户拍板:先接 GOAFFPRO,后隐藏自建短链。本模块只建 GOAFFPRO 接入骨架 ——
creds 加密落库 + 连接状态 + 薄 REST client(list_affiliates / list_orders 请求骨架)。
**不删/不隐藏任何现有自建 Links**(下一刀做);本文件零触碰评分域。

设计完全镜像 commerce/shopify_connect.py(creds-ready):
- creds(access_token / public_token + 可选 private_token)以 Fernet 密文落库
  vkpi_goaffpro_credentials;明文 token 绝不落库、绝不进日志、绝不进 response(只 _mask)。
- 加密密钥派生与 shopify_connect._fernet() 同一组 env(VKPI_CHANNELS_ENCRYPTION_KEY|
  JWT_SECRET|APP_SECRET|fallback),保证同密钥同 Fernet。
- connection_status():表无行 → fallback 读 os.environ(GOAFFPRO_ACCESS_TOKEN 等);
  env+DB 都空 → not_configured。全程只回 masked,绝不回明文。
- list_affiliates()/list_orders():无 creds → {ok:False, reason:'not_configured'},
  绝不抛、绝不烧 LLM、httpx 直连。

DB 全走 get_conn() + '?' 占位 + conn.commit();SQLite 运行时自建表
(is_postgres_runtime() 短路);Postgres 走 migration 162(随刀提供,集成者应用)。
与 KOL 评分域物理隔离:无 viltrox_fit_score / rule_v0 触点。

【待 key 校准】GOAFFPRO 公开文档当前 gated(api.goaffpro.com/docs/admin 动态加载),
以下据公开资料先设并显式标注「待 key 校准」,拿到真 key 后按 Swagger 实测校准:
- API base:https://api.goaffpro.com/v1
- 鉴权头:X-GOAFFPRO-ACCESS-TOKEN(管理私钥)/ X-GOAFFPRO-PUBLIC-TOKEN(公钥)
- 端点:GET /admin/affiliates、GET /admin/orders
- 响应字段名(affiliate.id/email/...、order.id/total/...)按文档先设,真 key 一到即对账。
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

# 【待 key 校准】公开资料口径,真 key 一到即按 Swagger 实测校准。
_DEFAULT_API_BASE = "https://api.goaffpro.com/v1"
_CREDS_SINGLETON_ID = 1
_VALID_STATUS = {"pending", "connected", "error", "revoked"}
_SCHEMA_READY = False

# 默认分页上限(请求骨架用;真 key 后按文档确认 limit/offset 还是 page/per_page)。
_DEFAULT_PAGE_LIMIT = 100


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fernet() -> Fernet:
    # Same key derivation as commerce/shopify_connect._fernet() so secrets
    # round-trip across the two creds tables under one VKPI key.
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


def _norm_base(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return _DEFAULT_API_BASE
    return text.rstrip("/")


def _actor(staff: dict[str, Any] | None) -> int | None:
    try:
        return resolve_staff_id(staff) or None
    except Exception:
        return None


def ensure_goaffpro_creds_schema() -> None:
    """SQLite-only runtime guard mirroring shopify_connect; Postgres uses migration 162."""
    global _SCHEMA_READY
    if _SCHEMA_READY or is_postgres_runtime():
        return
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS vkpi_goaffpro_credentials (
            id INTEGER PRIMARY KEY,
            api_base TEXT NOT NULL DEFAULT '',
            access_token_encrypted TEXT NOT NULL DEFAULT '',
            public_token_encrypted TEXT NOT NULL DEFAULT '',
            private_token_encrypted TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','connected','error','revoked')),
            connected_at TEXT,
            updated_at TEXT NOT NULL,
            updated_by_staff_id INTEGER
        );
        """
    )
    conn.commit()
    _SCHEMA_READY = True


def _load_row() -> dict[str, Any]:
    ensure_goaffpro_creds_schema()
    row = get_conn().execute(
        """
        SELECT id, api_base, access_token_encrypted, public_token_encrypted,
               private_token_encrypted, status, connected_at, updated_at, updated_by_staff_id
        FROM vkpi_goaffpro_credentials
        WHERE id=?
        """,
        (_CREDS_SINGLETON_ID,),
    ).fetchone()
    return dict(row) if row else {}


def save_credentials(body: dict[str, Any], staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """Encrypt + upsert GOAFFPRO creds (singleton id=1). Returns masked-only view.

    body 接受 snake / camel 双写:
      access_token / accessToken  -> X-GOAFFPRO-ACCESS-TOKEN(管理私钥,主鉴权)
      public_token / publicToken  -> X-GOAFFPRO-PUBLIC-TOKEN(公钥,可选)
      private_token / privateToken -> 预留(部分自定义集成区分公私钥;可选)
      api_base / apiBase          -> 覆盖默认 https://api.goaffpro.com/v1

    Never logs or returns plaintext tokens. Raises ValueError when no token supplied.
    更新时省略某字段 → 保留库内旧密文(与 shopify_connect.save_credentials 同语义)。
    """
    body = body or {}
    api_base = _norm_base(body.get("api_base") or body.get("apiBase"))
    access_token = str(body.get("access_token") or body.get("accessToken") or "").strip()
    public_token = str(body.get("public_token") or body.get("publicToken") or "").strip()
    private_token = str(body.get("private_token") or body.get("privateToken") or "").strip()
    if not access_token and not public_token and not private_token:
        raise ValueError("access_token or public_token is required")

    existing = _load_row()
    token_enc = _encrypt(access_token) if access_token else str(existing.get("access_token_encrypted") or "")
    public_enc = _encrypt(public_token) if public_token else str(existing.get("public_token_encrypted") or "")
    private_enc = _encrypt(private_token) if private_token else str(existing.get("private_token_encrypted") or "")
    # 主鉴权键 = access_token;有它即视为 connected。
    status = "connected" if token_enc else "pending"
    now = _utcnow()
    connected_at = now if token_enc else (existing.get("connected_at") or None)
    actor = _actor(staff)

    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_goaffpro_credentials
            (id, api_base, access_token_encrypted, public_token_encrypted,
             private_token_encrypted, status, connected_at, updated_at, updated_by_staff_id)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            api_base=excluded.api_base,
            access_token_encrypted=excluded.access_token_encrypted,
            public_token_encrypted=excluded.public_token_encrypted,
            private_token_encrypted=excluded.private_token_encrypted,
            status=excluded.status,
            connected_at=COALESCE(vkpi_goaffpro_credentials.connected_at, excluded.connected_at),
            updated_at=excluded.updated_at,
            updated_by_staff_id=excluded.updated_by_staff_id
        """,
        (
            _CREDS_SINGLETON_ID,
            api_base,
            token_enc,
            public_enc,
            private_enc,
            status,
            connected_at,
            now,
            actor,
        ),
    )
    conn.commit()
    return {
        "ok": True,
        "api_base": api_base,
        "token": _mask(access_token) if access_token else (_mask(_decrypt(token_enc)) if token_enc else ""),
        "access_token_configured": bool(token_enc),
        "public_token_configured": bool(public_enc),
        "private_token_configured": bool(private_enc),
        "status": status,
        "source": "db",
    }


def get_credentials() -> dict[str, Any]:
    """Internal-only: returns DECRYPTED tokens for in-process GOAFFPRO API calls.

    NEVER serialize this into an HTTP response or a log line. Falls back to env
    GOAFFPRO_* when no DB row exists.
    """
    row = _load_row()
    if row and (
        row.get("access_token_encrypted")
        or row.get("public_token_encrypted")
        or row.get("private_token_encrypted")
    ):
        return {
            "api_base": _norm_base(row.get("api_base")),
            "access_token": _decrypt(str(row.get("access_token_encrypted") or "")),
            "public_token": _decrypt(str(row.get("public_token_encrypted") or "")),
            "private_token": _decrypt(str(row.get("private_token_encrypted") or "")),
            "status": str(row.get("status") or "pending"),
            "source": "db",
        }
    env_token = str(
        os.environ.get("GOAFFPRO_ACCESS_TOKEN") or os.environ.get("GOAFFPRO_API_ACCESS_TOKEN") or ""
    ).strip()
    env_public = str(os.environ.get("GOAFFPRO_PUBLIC_TOKEN") or "").strip()
    env_private = str(os.environ.get("GOAFFPRO_PRIVATE_TOKEN") or "").strip()
    env_base = _norm_base(os.environ.get("GOAFFPRO_API_BASE"))
    if env_token or env_public or env_private:
        return {
            "api_base": env_base,
            "access_token": env_token,
            "public_token": env_public,
            "private_token": env_private,
            "status": "connected" if env_token else "pending",
            "source": "env",
        }
    return {
        "api_base": _DEFAULT_API_BASE,
        "access_token": "",
        "public_token": "",
        "private_token": "",
        "status": "pending",
        "source": "none",
    }


def connection_status() -> dict[str, Any]:
    """Masked, response-safe connection status. Never returns a plaintext token."""
    creds = get_credentials()
    token = creds.get("access_token") or ""
    public = creds.get("public_token") or ""
    private = creds.get("private_token") or ""
    source = creds.get("source") or "none"
    configured = bool(token)
    if source == "none":
        status = "not_configured"
    elif configured:
        status = "connected"
    else:
        status = str(creds.get("status") or "pending")
    return {
        "api_base": creds.get("api_base") or _DEFAULT_API_BASE,
        "token": _mask(token),
        "access_token_configured": bool(token),
        "public_token_configured": bool(public),
        "private_token_configured": bool(private),
        "status": status,
        "source": source,
    }


# --- REST client(薄封装,httpx 直连;无 creds -> not_configured,绝不抛)----------

def _admin_headers(creds: dict[str, Any]) -> dict[str, str]:
    """【待 key 校准】鉴权头按公开资料先设:
    X-GOAFFPRO-ACCESS-TOKEN(管理私钥,主)/ X-GOAFFPRO-PUBLIC-TOKEN(公钥,辅)。
    真 key 一到即对 Swagger 校准 header 名大小写与是否双发。
    """
    headers: dict[str, str] = {"Content-Type": "application/json"}
    token = str(creds.get("access_token") or "")
    public = str(creds.get("public_token") or "")
    if token:
        headers["X-GOAFFPRO-ACCESS-TOKEN"] = token
    if public:
        headers["X-GOAFFPRO-PUBLIC-TOKEN"] = public
    return headers


def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Single GET against GOAFFPRO Admin API. creds-ready: no token -> not_configured.

    Returns {ok, data?, status_code?, reason?, error?}. Never burns an LLM; httpx direct.
    """
    creds = get_credentials()
    token = creds.get("access_token") or ""
    if not token:
        return {"ok": False, "reason": "not_configured"}
    base = _norm_base(creds.get("api_base"))
    url = f"{base}/{str(path or '').lstrip('/')}"
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.get(url, headers=_admin_headers(creds), params=params or {})
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        return {"ok": False, "error": f"http {exc.response.status_code}", "status_code": exc.response.status_code}
    except (httpx.HTTPError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "data": data}


def _map_affiliate(raw: dict[str, Any]) -> dict[str, Any]:
    """【待 key 校准】affiliate 字段映射 —— 按公开资料先设,真 key 一到即对账。
    GOAFFPRO 公开端点 GET /admin/affiliates,以下字段名为占位映射(id/name/email 较稳,
    其余 phone/referral_code/total_* 真 key 后校准)。
    """
    raw = raw or {}
    return {
        "id": raw.get("id"),
        "name": raw.get("name") or raw.get("full_name") or "",
        "email": raw.get("email") or "",
        "referral_code": raw.get("ref_code") or raw.get("referral_code") or "",
        "status": raw.get("status") or "",
        # 待 key 校准:佣金/累计销售字段名按 Swagger 实测对账。
        "total_sales": raw.get("total_sales"),
        "total_commissions": raw.get("total_commissions"),
        "_raw_keys": sorted(raw.keys()),  # 真 key 后用它对照真实字段名,然后删
    }


def _map_order(raw: dict[str, Any]) -> dict[str, Any]:
    """【待 key 校准】order 字段映射 —— 按公开资料先设,真 key 一到即对账。
    GOAFFPRO 公开端点 GET /admin/orders,以下字段名为占位映射。
    """
    raw = raw or {}
    return {
        "id": raw.get("id") or raw.get("order_id"),
        "affiliate_id": raw.get("affiliate_id") or raw.get("ref_id"),
        "total": raw.get("total") or raw.get("order_total"),
        "currency": raw.get("currency") or "",
        "commission": raw.get("commission"),
        "status": raw.get("status") or "",
        "created_at": raw.get("created_at") or raw.get("date") or "",
        "_raw_keys": sorted(raw.keys()),  # 真 key 后用它对照真实字段名,然后删
    }


def _extract_list(data: Any, *keys: str) -> list[dict[str, Any]]:
    """【待 key 校准】GOAFFPRO 列表响应外层包裹名未定(可能是裸数组或 {affiliates:[...]}/
    {orders:[...]}/{data:[...]})。先做宽容提取,真 key 后锁定真实包裹键。
    """
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for k in keys:
            v = data.get(k)
            if isinstance(v, list):
                return [r for r in v if isinstance(r, dict)]
    return []


def list_affiliates(limit: int | None = None, offset: int | None = None) -> dict[str, Any]:
    """List affiliates (GET /admin/affiliates). creds-ready: no token -> not_configured.

    【待 key 校准】分页参数名(limit/offset vs page/per_page)与响应包裹键待真 key 锁定。
    返回 {ok, affiliates?, count?, raw?, reason?/error?}。绝不抛、绝不烧 LLM。
    """
    params: dict[str, Any] = {}
    params["limit"] = int(limit) if limit else _DEFAULT_PAGE_LIMIT
    if offset:
        params["offset"] = int(offset)
    result = _get("admin/affiliates", params)
    if not result.get("ok"):
        return result
    rows = _extract_list(result.get("data"), "affiliates", "data", "results")
    mapped = [_map_affiliate(r) for r in rows]
    return {"ok": True, "affiliates": mapped, "count": len(mapped)}


def list_orders(limit: int | None = None, offset: int | None = None) -> dict[str, Any]:
    """List affiliate orders (GET /admin/orders). creds-ready: no token -> not_configured.

    【待 key 校准】分页参数名与响应包裹键待真 key 锁定。
    返回 {ok, orders?, count?, reason?/error?}。绝不抛、绝不烧 LLM。
    """
    params: dict[str, Any] = {}
    params["limit"] = int(limit) if limit else _DEFAULT_PAGE_LIMIT
    if offset:
        params["offset"] = int(offset)
    result = _get("admin/orders", params)
    if not result.get("ok"):
        return result
    rows = _extract_list(result.get("data"), "orders", "data", "results")
    mapped = [_map_order(r) for r in rows]
    return {"ok": True, "orders": mapped, "count": len(mapped)}


def sync_stub() -> dict[str, Any]:
    """手动 sync stub(D1 骨架)—— 只拉一页 affiliates + orders 探活,不落库、不归因。

    本刀只建骨架:落库/归因/折扣码映射是后续刀(对齐 revenue/attribution 落账模式)。
    no creds -> {ok:False, reason:'not_configured'}。绝不抛、绝不烧 LLM。
    """
    status = connection_status()
    if status.get("status") == "not_configured":
        return {"ok": False, "reason": "not_configured", "connection": status}
    affiliates = list_affiliates(limit=1)
    orders = list_orders(limit=1)
    return {
        "ok": bool(affiliates.get("ok") and orders.get("ok")),
        "connection": status,
        "affiliates_probe": affiliates,
        "orders_probe": orders,
        "note": "D1 stub: probe-only, no persistence/attribution yet (next cut). 字段映射待 key 校准。",
    }


__all__ = [
    "ensure_goaffpro_creds_schema",
    "save_credentials",
    "get_credentials",
    "connection_status",
    "list_affiliates",
    "list_orders",
    "sync_stub",
]
