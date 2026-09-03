"""KOL 自助门户(只读·token 隔离)纯函数层。

凭专属 token 让单个 KOL 看「自己的」点击/订单/GMV/被分配项目,绝不泄露别人。

强隔离设计:
  - 每个子查询都强制带 ``WHERE ... kol_pool_id = ?``(或桥接后的 ``kol_id = (SELECT
    linked_main_kol_id ...)``),只绑定那个被 resolve 出来的单一 kol_pool_id,绝不
    接收/回显任何外部 id。
  - 纯 SELECT,零写、零删、零改业务表(issue_token 仅写本域 token 账本)。
  - 绝不读/回 viltrox_fit_score,绝不碰评分域(rule_v0)。

ID 空间:门户键 kol_pool_id(= vkpi_kol_pool.id)。点击/归因表键的是主表 kols.id,
桥是 vkpi_kol_pool.linked_main_kol_id。未桥接(NULL)时点击/sales_attributions 诚实
为空,绝不编造。

DB 访问全部走 conn.execute(sql, params) 的 ``?`` 占位符(sqlite 风格,运行时翻译为
Postgres),与 my_kol_aggregate.py 一致。
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from app.domains import business_truth

# S-08:门户 token 入库只存 sha256 摘要;默认 90 天过期(env VKPI_PORTAL_TOKEN_TTL_DAYS 可调)。
_TOKEN_DIGEST_PREFIX = "sha256$"
_DEFAULT_PORTAL_TOKEN_TTL_DAYS = 90


def _json(value: Any, default: Any) -> Any:
    """psycopg 可能把 jsonb 交回 str 或已解析对象;防御式解析(同 my_kol_aggregate)。"""
    if value is None or value == "":
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


# ─────────────────────────────────────────────────────────────────────────────
# Token 发放 / 解析
# ─────────────────────────────────────────────────────────────────────────────
def token_digest(raw: str) -> str:
    """门户 token 的入库形态:``sha256$<hex>``。原文只在签发瞬间回给调用方。"""
    return _TOKEN_DIGEST_PREFIX + hashlib.sha256(str(raw or "").encode("utf-8")).hexdigest()


def _token_lookup_values(raw: str) -> tuple[str, str]:
    """``token IN (?, ?)`` 的两个候选:摘要 + 原文(兼容切换前的明文行)。

    提交值本身是 ``sha256$`` 形态时不走原文分支——库泄后拿摘要当 token 不得过闸。
    """
    text = str(raw or "").strip()
    digest = token_digest(text)
    return digest, (digest if text.startswith(_TOKEN_DIGEST_PREFIX) else text)


def portal_token_ttl_days() -> int:
    """门户 token 有效期(天);env 非法 / 非正数时回落默认 90。"""
    raw = os.getenv("VKPI_PORTAL_TOKEN_TTL_DAYS", "").strip()
    try:
        days = int(raw) if raw else _DEFAULT_PORTAL_TOKEN_TTL_DAYS
    except ValueError:
        days = _DEFAULT_PORTAL_TOKEN_TTL_DAYS
    return days if days > 0 else _DEFAULT_PORTAL_TOKEN_TTL_DAYS


def _as_utc(value: Any) -> datetime | None:
    """TIMESTAMPTZ(psycopg datetime)或 ISO 字符串(sqlite)→ aware UTC datetime;解析不了 → None。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip().replace("Z", "+00:00")
    if " " in text and "T" not in text:
        text = text.replace(" ", "T", 1)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _token_row_expired(row: dict[str, Any], *, now: datetime) -> bool:
    """有 expires_at 按它判;没有(迁移 308 前的老行)按 created_at + 默认 TTL 判;都解析不了视为过期。"""
    expires_at = _as_utc(row.get("expires_at"))
    if expires_at is None:
        created_at = _as_utc(row.get("created_at"))
        if created_at is None:
            return True
        expires_at = created_at + timedelta(days=_DEFAULT_PORTAL_TOKEN_TTL_DAYS)
    return expires_at <= now


def issue_token(conn: Any, kol_pool_id: int, *, created_by_staff_id: int | None = None) -> str:
    """为某 KOL 发放门户 token;库里只存摘要,所以每次发放都是**轮换**:先撤销旧 live token,再插新的。

    原文无法从摘要还原,因此不再「幂等复用」;旧链接立即失效,新链接只在本次返回值里出现一次。
    表上 ``WHERE revoked=FALSE`` 的偏唯一索引仍是并发竞态的安全网(同一 KOL 至多一个 live token)。
    过期时间 = now + :func:`portal_token_ttl_days`(默认 90 天)。
    """
    pool_id = _int(kol_pool_id)
    conn.execute(
        "UPDATE vkpi_kol_portal_tokens SET revoked = TRUE WHERE kol_pool_id = ? AND revoked = FALSE",
        (pool_id,),
    )
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(days=portal_token_ttl_days())).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    conn.execute(
        """
        INSERT INTO vkpi_kol_portal_tokens (kol_pool_id, token, created_by_staff_id, expires_at)
        VALUES (?, ?, ?, ?)
        """,
        (pool_id, token_digest(token), _int(created_by_staff_id) or None, expires_at),
    )
    return token


def resolve_token(conn: Any, token: str) -> int | None:
    """token → kol_pool_id;撤销 / 过期 / 不存在 / 空 → None(门户随即 404,不泄露存在性)。"""
    tok = str(token or "").strip()
    if not tok:
        return None
    row = conn.execute(
        """
        SELECT kol_pool_id, expires_at, created_at
        FROM vkpi_kol_portal_tokens
        WHERE token IN (?, ?) AND revoked = FALSE
        LIMIT 1
        """,
        _token_lookup_values(tok),
    ).fetchone()
    if not row:
        return None
    item = dict(row)
    if _token_row_expired(item, now=datetime.now(timezone.utc)):
        return None
    return _int(item.get("kol_pool_id")) or None


# ─────────────────────────────────────────────────────────────────────────────
# 门户聚合(全部 WHERE kol_pool_id=? 强隔离)
# ─────────────────────────────────────────────────────────────────────────────
def _kol_basic(conn: Any, kol_pool_id: int) -> dict[str, Any] | None:
    """该 KOL 的展示用基础字段。绝不取 viltrox_fit_score;绝不回联系 PII。

    linked_main_kol_id 仅用于内部桥接,不出现在返回里。
    """
    row = conn.execute(
        """
        SELECT id, platform, handle, display_name, avatar_url, profile_url,
               country, language, primary_topic, followers, linked_main_kol_id
        FROM vkpi_kol_pool
        WHERE id = ?
        """,
        (_int(kol_pool_id),),
    ).fetchone()
    if not row:
        return None
    item = dict(row)
    return {
        "kol_pool_id": _int(item.get("id")),
        "platform": str(item.get("platform") or ""),
        "handle": str(item.get("handle") or ""),
        "display_name": str(item.get("display_name") or item.get("handle") or ""),
        "avatar_url": str(item.get("avatar_url") or ""),
        "profile_url": str(item.get("profile_url") or ""),
        "country": str(item.get("country") or ""),
        "language": str(item.get("language") or ""),
        "primary_topic": str(item.get("primary_topic") or ""),
        "followers": _int(item.get("followers")),
    }


def _projects(conn: Any, kol_pool_id: int) -> list[dict[str, Any]]:
    """该 KOL 被分配的项目(直键 kol_pool_id,无需桥接)。"""
    rows = conn.execute(
        """
        SELECT a.project_id, a.stage, a.stage_status, a.updated_at,
               p.project_name, p.product_sku, p.product_name, p.platform
        FROM vkpi_project_kol_assignments a
        JOIN vkpi_projects p ON p.id = a.project_id
        WHERE a.kol_pool_id = ?
        ORDER BY a.updated_at DESC, a.project_id DESC
        """,
        (_int(kol_pool_id),),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        items.append(
            {
                "project_id": _int(row.get("project_id")),
                "project_name": str(row.get("project_name") or ""),
                "product_sku": str(row.get("product_sku") or ""),
                "product_name": str(row.get("product_name") or ""),
                "platform": str(row.get("platform") or ""),
                "stage": str(row.get("stage") or ""),
                "stage_status": str(row.get("stage_status") or ""),
            }
        )
    return items


def _gmv(conn: Any, kol_pool_id: int) -> dict[str, Any]:
    """归因 GMV/订单(主源:vkpi_shopify_orders.attributed_kol_pool_id,直键 kol_pool_id)。"""
    row = conn.execute(
        """
        SELECT COUNT(*) AS orders_count,
               COALESCE(SUM(total_price_cents), 0) AS gmv_cents,
               MIN(currency) AS currency
        FROM vkpi_shopify_orders
        WHERE attributed_kol_pool_id = ?
          AND LOWER(COALESCE(financial_status,'')) IN ('paid','partially_paid','partially_refunded')
        """,
        (_int(kol_pool_id),),
    ).fetchone()
    item = dict(row) if row else {}
    return {
        "orders_count": _int(item.get("orders_count")),
        "gmv_cents": _int(item.get("gmv_cents")),
        "currency": str(item.get("currency") or "USD"),
    }


def _clicks(conn: Any, main_kol_id: int | None) -> dict[str, Any]:
    """短链点击(桥接源:vkpi_links.kol_id = linked_main_kol_id)。未桥接 → 诚实零。"""
    if not main_kol_id:
        return {"total": 0, "valid": 0}
    row = conn.execute(
        """
        SELECT COALESCE(SUM(click_count), 0) AS total,
               COALESCE(SUM(valid_click_count), 0) AS valid
        FROM vkpi_links
        WHERE kol_id = ?
        """,
        (_int(main_kol_id),),
    ).fetchone()
    item = dict(row) if row else {}
    return {"total": _int(item.get("total")), "valid": _int(item.get("valid"))}


def _attributions(conn: Any, main_kol_id: int | None) -> list[dict[str, Any]]:
    """次级 GMV 源:vkpi_sales_attributions(桥接 kols.id)。未桥接 → 空列表。"""
    if not main_kol_id:
        return []
    rows = conn.execute(
        f"""
        SELECT revenue_cents, commission_cents, currency, occurred_at
        FROM vkpi_sales_attributions
        WHERE kol_id = ?
          AND {business_truth.verified_shopify_attribution_sql()}
        ORDER BY occurred_at DESC NULLS LAST, id DESC
        LIMIT 100
        """,
        (_int(main_kol_id),),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        items.append(
            {
                "revenue_cents": _int(row.get("revenue_cents")),
                "commission_cents": _int(row.get("commission_cents")),
                "currency": str(row.get("currency") or "USD"),
                "occurred_at": row.get("occurred_at"),
            }
        )
    return items


def get_portal_data(conn: Any, kol_pool_id: int) -> dict[str, Any] | None:
    """聚合「该 KOL 自己的」门户数据。强隔离:只绑定单一 resolve 出来的 kol_pool_id。

    返回 None 表示 pool 行不存在(调用方据此 404,不泄露存在性)。点击/归因经
    linked_main_kol_id 桥接;桥为 NULL 时诚实返回零,绝不编造别人的数。
    """
    pool_id = _int(kol_pool_id)
    basic = _kol_basic(conn, pool_id)
    if basic is None:
        return None

    # 内部桥接 id —— 仅用于点击/归因查询,绝不回显给前端。
    main_kol_id = conn.execute(
        "SELECT linked_main_kol_id FROM vkpi_kol_pool WHERE id = ?",
        (pool_id,),
    ).fetchone()
    main_id = _int(dict(main_kol_id).get("linked_main_kol_id")) if main_kol_id else 0
    main_id = main_id or None

    return {
        "kol": basic,
        "projects": _projects(conn, pool_id),
        "gmv": _gmv(conn, pool_id),
        "clicks": _clicks(conn, main_id),
        "attributions": _attributions(conn, main_id),
    }
