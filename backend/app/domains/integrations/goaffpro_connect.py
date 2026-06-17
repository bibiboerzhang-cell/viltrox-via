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
_LINKS_SCHEMA_READY = False

# 默认分页上限(请求骨架用;真 key 后按文档确认 limit/offset 还是 page/per_page)。
_DEFAULT_PAGE_LIMIT = 100

# 2026-06-17 实测校准:GET /admin/affiliates 不带 fields 返回的 affiliate 对象为空 {},
# 必须用 fields= 逗号分隔列名才回真字段(GOAFFPRO 列选约定)。下列字段按 GOAFFPRO 约定先设,
# 真字段以响应里 _raw_keys 实测对照后微调(ref_code/coupon 是 KOL↔affiliate 配对键)。
_AFFILIATE_FIELDS = "id,name,email,ref_code,coupon,status,total_sales,total_orders,total_clicks,balance,signup_date,phone"
# 实测:/admin/orders 返回 {error};GOAFFPRO 销售端点是 /admin/sales。
_SALE_FIELDS = "id,affiliate_id,order_id,number,total,commission,currency,status,date,coupon,ref_code"


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


def ensure_goaffpro_links_schema() -> None:
    """SQLite-only runtime guard for D2 映射/销售表; Postgres uses migration 163.

    与 ensure_goaffpro_creds_schema 同语义:Postgres 短路(走迁移),SQLite 自建同构表
    (BIGSERIAL→INTEGER PK AUTOINCREMENT,TIMESTAMPTZ→TEXT,UNIQUE/索引保持)。
    """
    global _LINKS_SCHEMA_READY
    if _LINKS_SCHEMA_READY or is_postgres_runtime():
        return
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS vkpi_goaffpro_kol_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kol_pool_id INTEGER NOT NULL UNIQUE,
            affiliate_id TEXT NOT NULL DEFAULT '',
            ref_code TEXT NOT NULL DEFAULT '',
            tracking_url TEXT NOT NULL DEFAULT '',
            coupon TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_goaffpro_kol_links_affiliate
            ON vkpi_goaffpro_kol_links(affiliate_id);
        CREATE TABLE IF NOT EXISTS vkpi_goaffpro_sales (
            sale_id TEXT PRIMARY KEY,
            affiliate_id TEXT NOT NULL DEFAULT '',
            kol_pool_id INTEGER,
            total_cents INTEGER NOT NULL DEFAULT 0,
            commission_cents INTEGER NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT '',
            occurred_at TEXT,
            synced_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_goaffpro_sales_affiliate
            ON vkpi_goaffpro_sales(affiliate_id);
        CREATE INDEX IF NOT EXISTS idx_vkpi_goaffpro_sales_kol
            ON vkpi_goaffpro_sales(kol_pool_id);
        """
    )
    conn.commit()
    _LINKS_SCHEMA_READY = True


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


def _post(path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Single POST against GOAFFPRO Admin API. creds-ready: no token -> not_configured.

    Returns {ok, data?, status_code?, reason?, error?}. Never burns an LLM, never raises;
    httpx direct. On HTTP error still tries to surface the JSON body (GOAFFPRO error msg)
    so callers can透出 raw 给校准。
    """
    creds = get_credentials()
    token = creds.get("access_token") or ""
    if not token:
        return {"ok": False, "reason": "not_configured"}
    base = _norm_base(creds.get("api_base"))
    url = f"{base}/{str(path or '').lstrip('/')}"
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.post(url, headers=_admin_headers(creds), json=body or {})
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        body_text: Any = None
        try:
            body_text = exc.response.json()
        except Exception:  # noqa: BLE001 — body may be non-JSON; keep it as text
            try:
                body_text = exc.response.text
            except Exception:  # noqa: BLE001
                body_text = None
        return {
            "ok": False,
            "error": f"http {exc.response.status_code}",
            "status_code": exc.response.status_code,
            "raw": body_text,
        }
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
    params: dict[str, Any] = {"fields": _AFFILIATE_FIELDS}
    params["limit"] = int(limit) if limit else _DEFAULT_PAGE_LIMIT
    if offset:
        params["offset"] = int(offset)
    result = _get("admin/affiliates", params)
    if not result.get("ok"):
        return result
    data = result.get("data")
    rows = _extract_list(data, "affiliates", "data", "results")
    mapped = [_map_affiliate(r) for r in rows]
    total = data.get("total_results") if isinstance(data, dict) else None
    return {"ok": True, "affiliates": mapped, "count": len(mapped), "total": total}


def list_orders(limit: int | None = None, offset: int | None = None) -> dict[str, Any]:
    """List affiliate orders (GET /admin/orders). creds-ready: no token -> not_configured.

    【待 key 校准】分页参数名与响应包裹键待真 key 锁定。
    返回 {ok, orders?, count?, reason?/error?}。绝不抛、绝不烧 LLM。
    """
    params: dict[str, Any] = {"fields": _SALE_FIELDS}
    params["limit"] = int(limit) if limit else _DEFAULT_PAGE_LIMIT
    if offset:
        params["offset"] = int(offset)
    # 端点修正(Swagger 93 端点实证):销售/转化是 GET /admin/orders;**没有 /admin/sales**。
    # 之前 /admin/orders 返 {error} 是漏了必填 fields=(与 affiliates 同坑),不是路径错。
    result = _get("admin/orders", params)
    if not result.get("ok"):
        return result
    data = result.get("data")
    rows = _extract_list(data, "orders", "sales", "data", "results")
    mapped = [_map_order(r) for r in rows]
    total = data.get("total_results") if isinstance(data, dict) else None
    return {"ok": True, "orders": mapped, "count": len(mapped), "total": total}


# --- D2 写侧:一键给 KOL 建 affiliate + 拼追踪链 + 优惠码 ----------------------

# 【待 key 校准】create_affiliate body 字段:GOAFFPRO 标准 affiliate 对象按公开资料用
# {name, email};真 key 一到即对 POST /admin/affiliates 的 Swagger 实测校准必填/可选字段名。
def _default_store_url() -> str:
    """追踪链拼接用的店铺根 URL,读 env GOAFFPRO_STORE_URL,缺省 https://www.viltrox.com。"""
    return _norm_base(os.environ.get("GOAFFPRO_STORE_URL") or "https://www.viltrox.com")


def _extract_affiliate(data: Any) -> dict[str, Any]:
    """从 create 响应里捞出 affiliate 对象 —— 可能是裸对象,或 {affiliate:{...}}/{data:{...}}。
    宽容提取,真 key 后用返回的 raw 锁定真实包裹键。"""
    if isinstance(data, dict):
        for k in ("affiliate", "data", "result"):
            v = data.get(k)
            if isinstance(v, dict):
                return v
        return data
    return {}


def _read_ref_code(affiliate_raw: dict[str, Any]) -> str:
    """读 affiliate 的推荐码 —— GOAFFPRO 字段名未定,宽容多别名。真 key 后锁定。"""
    raw = affiliate_raw or {}
    for k in ("ref_code", "referral_code", "refcode", "coupon", "id"):
        v = raw.get(k)
        if v not in (None, ""):
            return str(v)
    return ""


def create_affiliate(name: str, email: str | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """POST /admin/affiliates —— 给 KOL 在 GOAFFPRO 建 affiliate(KOL 零注册)。

    防御式:绝不抛、绝不烧 LLM。no creds -> {ok:False, reason:'not_configured'}。
    返回 {ok, affiliate(原始对象), ref_code, error?, status_code?, raw?(GOAFFPRO 原始响应)}。
    【待 key 校准】body 字段按 GOAFFPRO 标准 {name, email} 先设;ref_code/coupon/id 真实字段名
    以响应里 raw / affiliate 对照后锁定。
    """
    payload: dict[str, Any] = {"name": str(name or "").strip()}
    em = str(email or "").strip()
    if em:
        payload["email"] = em
    if isinstance(extra, dict):
        for k, v in extra.items():
            if k not in payload:
                payload[k] = v
    result = _post("admin/affiliates", payload)
    if not result.get("ok"):
        # 透出 GOAFFPRO 原始响应(raw)便于校准必填字段。
        out = {"ok": False, "affiliate": {}, "ref_code": ""}
        if result.get("reason"):
            out["reason"] = result["reason"]
        if result.get("error"):
            out["error"] = result["error"]
        if result.get("status_code") is not None:
            out["status_code"] = result["status_code"]
        if result.get("raw") is not None:
            out["raw"] = result["raw"]
        return out
    data = result.get("data")
    # GOAFFPRO 软失败:HTTP 200 但 body 带 error/errors/message 且无 affiliate 主体(像 /admin/orders
    # 那套)→ 当作失败,把真因透出(此前把 200 当成功 → 读不到 id → 报 resolve failed 吞了真因)。
    if isinstance(data, dict):
        soft_err = data.get("error") or data.get("errors") or data.get("message")
        has_body = bool(data.get("id") or data.get("affiliate") or data.get("data") or data.get("ref_code"))
        if soft_err and not has_body:
            return {"ok": False, "affiliate": {}, "ref_code": "", "error": str(soft_err), "raw": data}
    affiliate_raw = _extract_affiliate(data)
    ref_code = _read_ref_code(affiliate_raw)
    return {
        "ok": True,
        "affiliate": affiliate_raw,
        "ref_code": ref_code,
        "raw": data,  # 真 key 后用它对照真实字段名(ref_code/coupon/link),然后删
    }


def referral_link(affiliate_raw: dict[str, Any] | None, ref_code: str | None = None) -> str:
    """拼追踪链:优先读 affiliate 里现成的 referral_link/link;没有则 {store}/?ref={ref_code}。

    store 读 env GOAFFPRO_STORE_URL(缺省 https://www.viltrox.com)。
    【待 key 校准】affiliate 里现成链接的字段名(referral_link/link/url)真 key 后锁定。
    """
    raw = affiliate_raw or {}
    code = str(ref_code or _read_ref_code(raw) or "").strip()
    store = _default_store_url()
    # 2026-06-17 修:此前取第一个非空 referral_link/link/url 字段,但 GOAFFPRO 返回的
    # url/link 常是「光店铺首页(无 ?ref=)」→ 拼出来追不到 KOL。改为:只有当现成链接
    # 真带追踪参数(含 'ref=' 或包含该 affiliate 的 code)才用它,否则一律用 code 拼
    # {store}/?ref={code}(GOAFFPRO 标准追踪参数)。
    for k in ("referral_link", "referral_url", "share_link", "link", "url"):
        v = str(raw.get(k) or "").strip()
        if not v:
            continue
        low = v.lower()
        if "ref=" in low or "/ref/" in low or (code and code.lower() in low):
            return v
    if not code:
        # 连追踪码都没读到(GOAFFPRO 字段名待校准)→ 退店铺首页,诚实:此时追踪不了。
        return store
    return f"{store}/?ref={code}"


def to_cents(value: Any) -> int:
    """金额→整数分(避免浮点)。GOAFFPRO total/commission 通常是十进制元;
    宽容解析(str/float/int/None);解析失败 → 0,绝不抛。
    【待 key 校准】若真 key 返回已是整数分,这里的 *100 需按响应口径调整。"""
    if value in (None, ""):
        return 0
    try:
        return int(round(float(value) * 100))
    except (TypeError, ValueError):
        return 0


def coupon_for(affiliate_raw: dict[str, Any] | None) -> str:
    """读 affiliate 的优惠码 —— 返回码字符串,无则 ''。

    实测(2026-06-17 活 API):GOAFFPRO 的 coupon 是**对象** `{'code':'HUNGKAI', ...}`,
    不是字符串;另有 `coupons` 数组。这里都兼容:dict 取 .code,list 取首个 .code。
    """
    raw = affiliate_raw or {}
    for k in ("coupon", "coupon_code", "discount_code", "promo_code"):
        v = raw.get(k)
        if isinstance(v, dict):
            c = v.get("code") or v.get("coupon") or ""
            if c:
                return str(c)
        elif v not in (None, ""):
            return str(v)
    cs = raw.get("coupons")
    if isinstance(cs, list) and cs:
        first = cs[0]
        return str(first.get("code") or "") if isinstance(first, dict) else str(first or "")
    return ""


def _norm_match(value: Any) -> str:
    """归一化用于宽松匹配(小写 + 仅字母数字),比对 name/email 命中。"""
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def search_affiliate(in_field: str, keyword: str) -> list[dict[str, Any]]:
    """GET /admin/affiliates/search?in=&keyword=&fields= → 返回匹配行(raw)。

    in_field ∈ name/email/...;实测 search 命中 0 时返回空列表(非报错)。绝不抛。
    """
    kw = str(keyword or "").strip()
    if not kw:
        return []
    result = _get("admin/affiliates/search", {"in": in_field, "keyword": kw, "fields": _AFFILIATE_FIELDS})
    if not result.get("ok"):
        return []
    return _extract_list(result.get("data"), "affiliates", "data", "results")


def resolve_affiliate(name: str, email: str | None = None, create: bool = False) -> dict[str, Any]:
    """确保 KOL 有 GOAFFPRO affiliate:先按 email 搜 → 按 name 搜 → (create=True 才)建+审批,
    然后 ?id= 回查拿 ref_code。返回 {ok, affiliate_id, ref_code, coupon, status, affiliate, created, reason?}。

    为什么 search-first(2026-06-17 实测根因):早期建链可能 create 失败/没建,留下**空
    affiliate_id 的废映射**,单靠 affiliate_id 回查补不上(实测 jianbozhang_v 在 GOAFFPRO
    搜 0 命中却有废映射)。search 能找回已存在的,找不到再建,幂等防重复建号。绝不抛。
    """
    nm = str(name or "").strip()
    em = str(email or "").strip()
    hit: dict[str, Any] = {}
    if em:
        for r in search_affiliate("email", em):
            if _norm_match(r.get("email")) == _norm_match(em):
                hit = r
                break
    if not hit and nm:
        for r in search_affiliate("name", nm):
            if _norm_match(r.get("name")) == _norm_match(nm):
                hit = r
                break
    created_flag = False
    if not hit:
        if not create:
            return {"ok": False, "reason": "not_found", "affiliate_id": "", "ref_code": "", "coupon": "", "status": ""}
        if not nm:
            return {"ok": False, "reason": "no_name", "affiliate_id": "", "ref_code": "", "coupon": "", "status": ""}
        cr = create_affiliate(nm, em or None, extra={"status": "approved"})
        if not cr.get("ok"):
            return {
                "ok": False,
                "reason": cr.get("reason") or "create_failed",
                "error": cr.get("error"),
                "status_code": cr.get("status_code"),
                "raw": cr.get("raw"),
                "affiliate_id": "",
                "ref_code": "",
                "coupon": "",
                "status": "",
            }
        created_flag = True
        hit = cr.get("affiliate") or {}
        # 建完响应可能不含 id/ref_code → 回搜一次锁定真号。
        if not str(hit.get("id") or "").strip():
            for r in (search_affiliate("email", em) if em else []) + (search_affiliate("name", nm) if nm else []):
                if (em and _norm_match(r.get("email")) == _norm_match(em)) or (nm and _norm_match(r.get("name")) == _norm_match(nm)):
                    hit = r
                    break
    aid = str(hit.get("id") or "").strip()
    ref_code = _read_ref_code(hit)
    coupon = coupon_for(hit)
    status = str(hit.get("status") or "")
    # ref_code 还空但有 id → ?id= 回查(建完当下响应常不含 ref_code,实测回查必有)。
    if aid and not ref_code:
        got = get_affiliate(aid)
        if got.get("ok"):
            ref_code = got.get("ref_code") or ref_code
            coupon = coupon or got.get("coupon") or ""
            status = status or got.get("status") or ""
            if got.get("affiliate"):
                hit = got["affiliate"]
    ok = bool(aid and ref_code)
    out = {
        "ok": ok,
        "affiliate_id": aid,
        "ref_code": ref_code,
        "coupon": coupon,
        "status": status,
        "affiliate": hit,
        "created": created_flag,
    }
    if not ok:
        # 永不再吞真因:明确标出卡在哪一步,并把 affiliate 原始体透出便于排查。
        out["reason"] = "no_affiliate_id" if not aid else "no_ref_code"
        out["raw"] = hit
    return out


def get_affiliate(affiliate_id: str | int) -> dict[str, Any]:
    """回查单个 affiliate,拿分配好的 ref_code/coupon/status。

    为什么需要:GOAFFPRO 新建 affiliate 的 POST 响应当下常不返回 ref_code(要它分配后才有),
    若建完立刻拼链 ref_code 为空 → 出光店铺链(追不到 KOL)。建后回查即可拿到真码
    (实测 2421 个 affiliate 含 Pending Approval 的都带 ref_code,故回查必有)。

    端点修正(Swagger 93 端点实证):GOAFFPRO **没有 GET /admin/affiliates/{id}**
    (该路径只有 PATCH/DELETE)→ 取单条必须用列表端点按 id 过滤 + fields。
    返回 {ok, affiliate, ref_code, coupon, status, reason?/error?}。绝不抛、绝不烧 LLM。
    """
    aid = str(affiliate_id or "").strip()
    if not aid:
        return {"ok": False, "reason": "missing_id"}
    # GET /admin/affiliates?id={aid}&fields=... —— 必带 fields 否则返空对象。
    result = _get("admin/affiliates", {"id": aid, "fields": _AFFILIATE_FIELDS})
    if not result.get("ok"):
        return result
    rows = _extract_list(result.get("data"), "affiliates", "data", "results")
    obj: dict[str, Any] = {}
    for r in rows:
        if str((r or {}).get("id") or "") == aid:
            obj = r
            break
    if not obj and rows:
        obj = rows[0]
    return {
        "ok": True,
        "affiliate": obj,
        "ref_code": _read_ref_code(obj),
        "coupon": coupon_for(obj),
        "status": str((obj or {}).get("status") or ""),
        "raw": result.get("data"),
    }


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
    "ensure_goaffpro_links_schema",
    "save_credentials",
    "get_credentials",
    "connection_status",
    "list_affiliates",
    "list_orders",
    "create_affiliate",
    "get_affiliate",
    "search_affiliate",
    "resolve_affiliate",
    "referral_link",
    "coupon_for",
    "to_cents",
    "sync_stub",
]
