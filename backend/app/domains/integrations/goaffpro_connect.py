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
_AFFILIATE_FIELDS = "id,name,email,ref_code,coupon,status,commission,total_sales,total_orders,total_clicks,balance,signup_date,phone"
# 实测:/admin/orders 返回 {error};GOAFFPRO 销售端点是 /admin/sales。
_SALE_FIELDS = "id,affiliate_id,order_id,number,total,commission,currency,status,date,coupon,ref_code"

# GoAffPro 销售确认态口径:只算已确认/已批准/已付/完成的单,排除退款/取消/拒绝/待定/空。
# 与 routers/vkpi_goaffpro.py 的 _CONFIRMED_SALE_STATUSES 同白名单;GMV/佣金 SUM 只计确认态,
# 且按币种分组绝不把 EUR cents 加进 USD。真 key 到后按 GoAffPro 实际 status 值校准(待 key 校准)。
_CONFIRMED_SALE_STATUSES = frozenset({"approved", "paid", "confirmed", "completed"})


def _is_confirmed_sale(status: Any) -> bool:
    """该销售是否落在确认态白名单(大小写/空白不敏感)。空/pending/refund/cancelled → False。"""
    return str(status or "").strip().lower() in _CONFIRMED_SALE_STATUSES


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
        CREATE TABLE IF NOT EXISTS vkpi_goaffpro_kol_metrics (
            affiliate_id TEXT PRIMARY KEY,
            kol_pool_id INTEGER,
            clicks INTEGER NOT NULL DEFAULT 0,
            orders INTEGER NOT NULL DEFAULT 0,
            gmv_cents INTEGER NOT NULL DEFAULT 0,
            commission_cents INTEGER NOT NULL DEFAULT 0,
            commission_rate TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT '',
            currency TEXT NOT NULL DEFAULT '',
            partial INTEGER NOT NULL DEFAULT 0,
            synced_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_goaffpro_metrics_kol
            ON vkpi_goaffpro_kol_metrics(kol_pool_id);
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
# 行为不变搬到 goaffpro_connect_http.py，这里 re-export 兜住所有调用点(含
# goaffpro_connect_affiliates 对 _get/_post/_patch 的 lazy import)。
from app.domains.integrations.goaffpro_connect_http import (  # noqa: E402
    _admin_headers,
    _get,
    _patch,
    _post,
)


def list_affiliates(limit: int | None = None, offset: int | None = None, *, fetch_all: bool = False) -> dict[str, Any]:
    """List affiliates (GET /admin/affiliates). creds-ready: no token -> not_configured.

    【待 key 校准】分页参数名(limit/offset vs page/per_page)与响应包裹键待真 key 锁定。
    fetch_all=True(且未显式 offset)→ 循环翻页拉全量 affiliate;否则单页(API 端点手动翻页)。
    返回 {ok, affiliates?, count?, total?, partial?, raw?, reason?/error?}。绝不抛、绝不烧 LLM。
    """
    if fetch_all and offset is None:
        page = _paginate_rows("admin/affiliates", {"fields": _AFFILIATE_FIELDS}, ("affiliates", "data", "results"))
        mapped = [_map_affiliate(r) for r in page.get("rows") or []]
        out: dict[str, Any] = {"ok": bool(page.get("ok")), "affiliates": mapped, "count": len(mapped), "total": page.get("total")}
        if page.get("partial"):
            out["partial"] = True
        if page.get("error"):
            out["error"] = page["error"]
        return out
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


def list_orders(limit: int | None = None, offset: int | None = None, *, fetch_all: bool = False) -> dict[str, Any]:
    """List affiliate orders (GET /admin/orders). creds-ready: no token -> not_configured.

    【待 key 校准】分页参数名与响应包裹键待真 key 锁定。
    fetch_all=True(且未显式 offset)→ 循环翻页拉**全量**(防只取一页漏账);否则单页(API 端点手动翻页)。
    返回 {ok, orders?, count?, total?, partial?, reason?/error?}。绝不抛、绝不烧 LLM。
    """
    if fetch_all and offset is None:
        page = _paginate_rows("admin/orders", {"fields": _SALE_FIELDS}, ("orders", "sales", "data", "results"))
        mapped = [_map_order(r) for r in page.get("rows") or []]
        out: dict[str, Any] = {"ok": bool(page.get("ok")), "orders": mapped, "count": len(mapped), "total": page.get("total")}
        if page.get("partial"):
            out["partial"] = True
        if page.get("error"):
            out["error"] = page["error"]
        return out
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
    se = _soft_error(data)
    if se:
        return {"ok": False, "error": se, "raw": data, "orders": [], "count": 0}
    rows = _extract_list(data, "orders", "sales", "data", "results")
    mapped = [_map_order(r) for r in rows]
    total = data.get("total_results") if isinstance(data, dict) else None
    return {"ok": True, "orders": mapped, "count": len(mapped), "total": total}


_TRAFFIC_FIELDS = "id,affiliate_id,created_at,landing_page,referrer"

_LIST_KEYS = ("affiliates", "orders", "sales", "traffic", "clicks", "products", "data", "results")


def _paginate_rows(
    path: str,
    base_params: dict[str, Any],
    list_keys: tuple[str, ...],
    *,
    page_limit: int = _DEFAULT_PAGE_LIMIT,
    max_pages: int = 2000,
) -> dict[str, Any]:
    """循环翻页拉全量(offset 递增直到本页 < page_limit)→ 返回 {ok, rows, total, partial, error}。

    防漏账核心:GOAFFPRO 单页上限 _DEFAULT_PAGE_LIMIT(100),过去只取一页 → 超出永久丢、佣金少算。
    契约:ok = 至少首页成功(有可用数据);partial = 中途某页失败(已拉到的 rows 照返,不静默截断
    冒充全量);首页即失败 → ok=False、rows=[]。绝不抛、绝不烧 LLM。
    """
    rows: list[dict[str, Any]] = []
    total: int | None = None
    offset = 0
    ok = False
    partial = False
    error = ""
    for _ in range(max(1, int(max_pages))):
        params = {**base_params, "limit": page_limit, "offset": offset}
        result = _get(path, params)
        if not result.get("ok"):
            error = str(result.get("error") or result.get("reason") or "page fetch failed")
            partial = True
            break
        data = result.get("data")
        se = _soft_error(data)
        if se:
            error = se
            partial = True
            break
        ok = True
        if total is None and isinstance(data, dict):
            total = data.get("total_results")
        page = _extract_list(data, *list_keys)
        rows.extend(page)
        if len(page) < page_limit:
            break
        offset += page_limit
    return {"ok": ok, "rows": rows, "total": total, "partial": partial, "error": error}


def list_traffic(
    affiliate_id: str | int | None = None,
    limit: int | None = None,
    offset: int | None = None,
    *,
    fetch_all: bool = False,
) -> dict[str, Any]:
    """GET /admin/traffic(点击日志,fields 必填)。可按 affiliate_id 过滤(实测有效)。

    fetch_all=True(且未显式 offset)→ 循环翻页拉全量点击行;否则单页。
    返回 {ok, clicks?, count?, total?, partial?, reason?/error?}。total = total_results(全量点击数)。绝不抛。
    """
    if fetch_all and offset is None:
        bp: dict[str, Any] = {"fields": _TRAFFIC_FIELDS}
        if affiliate_id:
            bp["affiliate_id"] = str(affiliate_id)
        page = _paginate_rows("admin/traffic", bp, ("traffic", "clicks", "data", "results"))
        rows = page.get("rows") or []
        out: dict[str, Any] = {"ok": bool(page.get("ok")), "clicks": rows, "count": len(rows), "total": page.get("total")}
        if page.get("partial"):
            out["partial"] = True
        if page.get("error"):
            out["error"] = page["error"]
        return out
    params: dict[str, Any] = {"fields": _TRAFFIC_FIELDS}
    if affiliate_id:
        params["affiliate_id"] = str(affiliate_id)
    params["limit"] = int(limit) if limit else _DEFAULT_PAGE_LIMIT
    if offset:
        params["offset"] = int(offset)
    result = _get("admin/traffic", params)
    if not result.get("ok"):
        return result
    data = result.get("data")
    se = _soft_error(data)
    if se:
        return {"ok": False, "error": se, "raw": data, "clicks": [], "count": 0}
    rows = _extract_list(data, "traffic", "clicks", "data", "results")
    total = data.get("total_results") if isinstance(data, dict) else None
    return {"ok": True, "clicks": rows, "count": len(rows), "total": total}


def affiliate_attribution(affiliate_id: str | int) -> dict[str, Any]:
    """单 affiliate 的归因汇总:点击数 + 订单数 + GMV + 佣金(实时查 traffic + orders)。

    点击数取 traffic 的 total_results(便宜,limit=1);订单/GMV/佣金只对**确认态**订单求和
    (_is_confirmed_sale:approved/paid/confirmed/completed,排除 refund/cancelled/pending/空),
    且按币种分组只上报主币种(无 FX 表,绝不跨币混加)。
    返回 {ok, clicks, orders, gmv_cents, commission_cents, currency, by_currency, mixed_currency,
    partial?, error?}。orders/gmv/commission 均为主币种确认态口径。绝不抛。
    任一子查询失败 → partial=True + error(让调用方/前端能区分「真零」与「查询失败」,不污染汇总)。
    """
    aid = str(affiliate_id or "").strip()
    base = {
        "ok": False,
        "clicks": 0,
        "orders": 0,
        "gmv_cents": 0,
        "commission_cents": 0,
        "currency": "",
        "by_currency": [],
        "mixed_currency": False,
    }
    if not aid:
        return {**base, "reason": "missing_id"}
    tr = list_traffic(affiliate_id=aid, limit=1)
    tr_ok = bool(tr.get("ok"))
    clicks = int(tr.get("total") if tr_ok and tr.get("total") is not None else (tr.get("count") or 0))
    # 订单/GMV/佣金:循环翻页拉该 affiliate **全量**订单(过去只取一页 limit=250 → 订单超 250 漏算佣金)。
    op = _paginate_rows("admin/orders", {"fields": _SALE_FIELDS, "affiliate_id": aid}, ("orders", "sales", "data", "results"))
    od_ok = bool(op.get("ok"))
    orders_list = op.get("rows") or []
    # 只算确认态订单(排除 refund/cancelled/declined/pending/空),并按币种分组——无 FX 表,绝不把
    # EUR cents 加进 USD。取主币种(GMV 最大)上报 gmv/commission/orders,by_currency 留全量供审计,
    # mixed_currency 标记多币种。之前无 status 过滤 + currency 取首单 → 退款/待定单虚增 GMV、跨币混加。
    by_currency: dict[str, dict[str, int]] = {}
    for o in orders_list:
        if not _is_confirmed_sale(o.get("status")):
            continue
        cur = str(o.get("currency") or "").strip().upper() or "UNKNOWN"
        bucket = by_currency.setdefault(cur, {"gmv_cents": 0, "commission_cents": 0, "orders": 0})
        bucket["gmv_cents"] += to_cents(o.get("total") or o.get("order_total"))
        bucket["commission_cents"] += to_cents(o.get("commission"))
        bucket["orders"] += 1
    if by_currency:
        primary_cur = max(by_currency.items(), key=lambda kv: (kv[1]["gmv_cents"], kv[1]["orders"]))[0]
        pb = by_currency[primary_cur]
        gmv = pb["gmv_cents"]
        commission = pb["commission_cents"]
        confirmed_orders = pb["orders"]
        currency = "" if primary_cur == "UNKNOWN" else primary_cur
    else:
        gmv = 0
        commission = 0
        confirmed_orders = 0
        currency = ""
    by_currency_list = [
        {"currency": ("" if k == "UNKNOWN" else k), **v}
        for k, v in sorted(by_currency.items(), key=lambda kv: (-kv[1]["gmv_cents"], kv[0]))
    ]
    partial = (not tr_ok) or (not od_ok) or bool(op.get("partial"))
    out = {
        "ok": not partial,
        "clicks": clicks,
        "orders": confirmed_orders,
        "gmv_cents": gmv,
        "commission_cents": commission,
        "currency": currency,
        "by_currency": by_currency_list,
        "mixed_currency": len(by_currency) > 1,
        "partial": partial,
    }
    if partial:
        out["error"] = op.get("error") or tr.get("error") or "goaffpro query failed"
    return out


def sync_kol_metrics(limit: int | None = None) -> dict[str, Any]:
    """刷新所有已建链 KOL 的 GOAFFPRO 指标快照 → vkpi_goaffpro_kol_metrics(性能落库)。

    每个 affiliate 实时查 traffic+orders(affiliate_attribution)+ affiliate(佣金/状态),
    upsert 进缓存表。给定时任务 + 手动「刷新」调用;summary 读这张表秒出,不再逐 KOL 打 GOAFFPRO。
    返回 {ok, synced, errors, synced_at}。绝不抛。no creds → {ok:False, reason}。
    """
    if connection_status().get("status") == "not_configured":
        return {"ok": False, "reason": "not_configured", "synced": 0, "errors": 0}
    ensure_goaffpro_links_schema()
    conn = get_conn()
    sql = "SELECT kol_pool_id, affiliate_id FROM vkpi_goaffpro_kol_links WHERE COALESCE(affiliate_id,'') <> ''"
    rows = conn.execute(sql + (" LIMIT ?" if limit else ""), ((int(limit),) if limit else ())).fetchall()
    synced = 0
    errors = 0
    now = _utcnow()
    for r in rows:
        d = dict(r)
        aid = str(d.get("affiliate_id") or "").strip()
        if not aid:
            continue
        attr = affiliate_attribution(aid)
        aff = get_affiliate(aid)
        # partial 列是 BOOLEAN(迁移164):Postgres 严格,传 int 1/0 → DatatypeMismatch;
        # 用真 bool(SQLite 也兼容)。这是之前 vkpi_goaffpro_kol_metrics 一直空的真因(此 job 在 PG 上必崩)。
        partial = bool(attr.get("partial"))
        if partial:
            errors += 1
        conn.execute(
            """
            INSERT INTO vkpi_goaffpro_kol_metrics
                (affiliate_id, kol_pool_id, clicks, orders, gmv_cents, commission_cents,
                 commission_rate, status, currency, partial, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (affiliate_id) DO UPDATE SET
                kol_pool_id = excluded.kol_pool_id,
                clicks = excluded.clicks,
                orders = excluded.orders,
                gmv_cents = excluded.gmv_cents,
                commission_cents = excluded.commission_cents,
                commission_rate = excluded.commission_rate,
                status = excluded.status,
                currency = excluded.currency,
                partial = excluded.partial,
                synced_at = excluded.synced_at
            """,
            (
                aid,
                d.get("kol_pool_id"),
                int(attr.get("clicks") or 0),
                int(attr.get("orders") or 0),
                int(attr.get("gmv_cents") or 0),
                int(attr.get("commission_cents") or 0),
                str(aff.get("commission_rate") or "") if aff.get("ok") else "",
                str(aff.get("status") or "") if aff.get("ok") else "",
                str(attr.get("currency") or ""),
                partial,
                now,
            ),
        )
        synced += 1
    conn.commit()
    return {"ok": True, "synced": synced, "errors": errors, "synced_at": now}


# --- D2 写侧 + 纯映射/拼链/产品解析簇:行为不变搬到 goaffpro_connect_affiliates.py，
# 这里 re-export 兜住所有调用点(含本文件保留函数对 _map_*/_extract_list/_soft_error/
# to_cents/get_affiliate/coupon_for/commission_label 的内部引用)。下划线私有名显式列出。
from app.domains.integrations.goaffpro_connect_affiliates import (  # noqa: E402
    _default_store_url,
    _extract_affiliate,
    _extract_list,
    _fmt_num,
    _map_affiliate,
    _map_order,
    _norm_match,
    _norm_token_set,
    _read_ref_code,
    _soft_error,
    commission_label,
    coupon_for,
    create_affiliate,
    find_product_handle,
    get_affiliate,
    list_products,
    product_referral_link,
    referral_link,
    resolve_affiliate,
    search_affiliate,
    to_cents,
    update_affiliate_commission,
    update_affiliate_coupon,
)


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
    "update_affiliate_commission",
    "update_affiliate_coupon",
    "commission_label",
    "referral_link",
    "coupon_for",
    "to_cents",
    "list_traffic",
    "affiliate_attribution",
    "sync_kol_metrics",
    "list_products",
    "find_product_handle",
    "product_referral_link",
    "sync_stub",
]
