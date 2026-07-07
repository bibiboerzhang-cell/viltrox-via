"""B4 · 客户库挖 KOL(customer mining)—— 「你的买家里的创作者」对标最值单点。

mine_customers() -> dict:
  1. 侦察本地 Shopify / GOAFFPRO 订单与客户真落点(表在不在、几行、几行带联系方式):
       vkpi_shopify_orders(138,customer_email 列)/ orders(015,customer_email 列)/
       vkpi_shopify_order_snapshots(034,raw_payload_json 里的 email/customer)/
       vkpi_goaffpro_sales(163,无客户明文列,只清点)/ parties+identity_links(010,PII 哈希化,
       无明文可撞,只清点说明)。
  2. 有客户行:邮箱与 vkpi_kol_pool.email 精确撞库(大小写归一)+ 名字模糊匹配
       (difflib 纯 Python,零 SQL LIKE)出「你的买家里的创作者」候选。
  3. 本地无客户数据:诚实 empty + ready_when 说明哪条数据链接通后本功能自动生效。

撞库核心 match_customers_to_pool 是纯函数(无 DB 依赖),假 fixture 单测可直接证明可用。

合规红线:输出一律脱敏(邮箱 e***@d***,走 pool_common._mask_email 同款口径),
真值联系方式仅经既有 contact_reveal 门控(二次确认 + 审计)查看,本模块绝不裸露明文;
只读侦察,绝不写任何表;零 LLM;绝不读写 viltrox_fit_score、绝不碰 rule_v0。
compat:SQL 占位符 ?;SQL 零字面 percent(不用 LIKE,词表/模糊匹配全拉回 Python 做)。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

_POOL = "vkpi_kol_pool"

# 名字模糊匹配阈值(SequenceMatcher ratio):偏保守,宁缺勿滥(候选仍需人工确认)。
NAME_FUZZY_THRESHOLD = 0.86

# 侦察的客户数据源(表名 → 角色说明);只读清点,不在此写死列名(逐表单独取)。
_CUSTOMER_SOURCES: tuple[tuple[str, str], ...] = (
    ("vkpi_shopify_orders", "Shopify ingest 订单账本(customer_email 明文列)"),
    ("orders", "V5 commerce 归一化订单(customer_email 明文列)"),
    ("vkpi_shopify_order_snapshots", "Shopify webhook 快照(raw_payload_json 内 email/customer)"),
    ("vkpi_goaffpro_sales", "GOAFFPRO 销售快照(无客户明文列,仅清点)"),
    ("parties", "统一客户层(PII 经 identity_links 哈希化,无明文可撞,仅清点)"),
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_rollback(conn: Any) -> None:
    try:
        conn.rollback()
    except Exception:  # noqa: BLE001
        logger.debug("回滚失败(best-effort)", exc_info=True)


def _loads(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (str, bytes)):
        try:
            return json.loads(value)
        except Exception:
            return None
    return None


def _mask_email(value: Any) -> str:
    """脱敏口径与 pool_common._mask_email 一致(懒 import 复用,失败用同规则本地兜底)。"""
    try:
        from app.domains.kol.pool_common import _mask_email as pool_mask

        return pool_mask(value)
    except Exception:  # noqa: BLE001 — 兜底同款规则,绝不裸露明文
        text = str(value or "").strip()
        if not text or "@" not in text:
            return text
        local, _, domain = text.partition("@")
        return f"{(local[0] + '***') if local else '***'}@{(domain[0] + '***') if domain else '***'}"


def _norm_email(value: Any) -> str:
    return str(value or "").strip().lower()


def _norm_name(value: Any) -> str:
    """名字归一:小写、去首尾空白、压缩连续空白(纯 Python,零 SQL)。"""
    return " ".join(str(value or "").strip().lower().split())


# ── 撞库核心(纯函数,fixture 单测直接喂)────────────────────────────


def match_customers_to_pool(
    customers: list[dict[str, Any]],
    pool_rows: list[dict[str, Any]],
    *,
    name_threshold: float = NAME_FUZZY_THRESHOLD,
) -> list[dict[str, Any]]:
    """客户 × KOL 池撞库:邮箱精确(高置信)+ 名字模糊(需人工确认)。

    customers: [{email, name, source_table, source_row_id}];
    pool_rows: [{id, platform, handle, display_name, email}]。
    返回候选列表(邮箱一律脱敏输出),按置信排序:email_exact 先、name_fuzzy 按分降序。
    去重口径:同一 (kol_pool_id, source_table, source_row_id) 只出一条,精确压过模糊。
    """
    exact: list[dict[str, Any]] = []
    fuzzy: list[dict[str, Any]] = []
    seen: set[tuple[int, str, Any]] = set()

    pool_by_email: dict[str, list[dict[str, Any]]] = {}
    for p in pool_rows:
        em = _norm_email(p.get("email"))
        if em:
            pool_by_email.setdefault(em, []).append(p)

    def _candidate(p: dict[str, Any], c: dict[str, Any], basis: str, score: float | None) -> dict[str, Any]:
        return {
            "kol_pool_id": int(p.get("id") or 0),
            "platform": str(p.get("platform") or ""),
            "handle": str(p.get("handle") or ""),
            "display_name": str(p.get("display_name") or ""),
            "matched_by": basis,
            "score": round(float(score), 4) if score is not None else None,
            "confidence": "high" if basis == "email_exact" else "needs_review",
            "customer_email_masked": _mask_email(c.get("email")),
            "customer_name": str(c.get("name") or ""),
            "customer_source": {
                "table": str(c.get("source_table") or ""),
                "row_id": c.get("source_row_id"),
            },
            "note": (
                "邮箱精确命中:该买家邮箱与 KOL 池档案邮箱一致(真值经 contact_reveal 门控查看)"
                if basis == "email_exact"
                else "名字模糊命中:需人工确认是否同一人(不自动认定)"
            ),
        }

    for c in customers:
        c_email = _norm_email(c.get("email"))
        c_name = _norm_name(c.get("name"))

        # ① 邮箱精确撞库(高置信)。
        if c_email:
            for p in pool_by_email.get(c_email, []):
                key = (int(p.get("id") or 0), str(c.get("source_table") or ""), c.get("source_row_id"))
                if key in seen:
                    continue
                seen.add(key)
                exact.append(_candidate(p, c, "email_exact", None))

        # ② 名字模糊匹配(display_name / handle,纯 Python difflib)。
        if not c_name:
            continue
        for p in pool_rows:
            key = (int(p.get("id") or 0), str(c.get("source_table") or ""), c.get("source_row_id"))
            if key in seen:
                continue
            best = 0.0
            for field in ("display_name", "handle"):
                target = _norm_name(p.get(field))
                if not target:
                    continue
                ratio = SequenceMatcher(None, c_name, target).ratio()
                if ratio > best:
                    best = ratio
            if best >= float(name_threshold):
                seen.add(key)
                fuzzy.append(_candidate(p, c, "name_fuzzy", best))

    fuzzy.sort(key=lambda x: -(x.get("score") or 0.0))
    return exact + fuzzy


# ── 数据源侦察(只读清点)────────────────────────────────────────────


def _count(conn: Any, sql: str, params: tuple = ()) -> int | None:
    try:
        row = conn.execute(sql, params).fetchone()
        return int(dict(row).get("n") or 0) if row else 0
    except Exception:
        _safe_rollback(conn)
        return None


def _scan_sources() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """侦察各客户数据源真落点。返回 (sources 报告, 客户行列表[email/name/来源])。"""
    from app.db.connection import get_conn, table_exists

    conn = get_conn()
    sources: list[dict[str, Any]] = []
    customers: list[dict[str, Any]] = []

    for table, role in _CUSTOMER_SOURCES:
        info: dict[str, Any] = {"table": table, "role": role, "exists": table_exists(table)}
        if not info["exists"]:
            info["rows"] = 0
            info["rows_with_contact"] = 0
            sources.append(info)
            continue
        info["rows"] = _count(conn, f"SELECT COUNT(*) AS n FROM {table}")

        if table == "vkpi_shopify_orders":
            info["rows_with_contact"] = _count(
                conn, f"SELECT COUNT(*) AS n FROM {table} WHERE COALESCE(customer_email,'') <> ''"
            )
            try:
                rows = conn.execute(
                    f"SELECT id, customer_email FROM {table} "
                    "WHERE COALESCE(customer_email,'') <> '' ORDER BY id DESC LIMIT ?",
                    (500,),
                ).fetchall()
                for r in rows:
                    d = dict(r)
                    customers.append(
                        {"email": d.get("customer_email"), "name": "", "source_table": table, "source_row_id": d.get("id")}
                    )
            except Exception:
                _safe_rollback(conn)
                info["scan_error"] = "customer_email 读取失败"
        elif table == "orders":
            info["rows_with_contact"] = _count(
                conn, f"SELECT COUNT(*) AS n FROM {table} WHERE COALESCE(customer_email,'') <> ''"
            )
            try:
                rows = conn.execute(
                    f"SELECT id, customer_email FROM {table} "
                    "WHERE COALESCE(customer_email,'') <> '' ORDER BY id DESC LIMIT ?",
                    (500,),
                ).fetchall()
                for r in rows:
                    d = dict(r)
                    customers.append(
                        {"email": d.get("customer_email"), "name": "", "source_table": table, "source_row_id": d.get("id")}
                    )
            except Exception:
                _safe_rollback(conn)
                info["scan_error"] = "customer_email 读取失败"
        elif table == "vkpi_shopify_order_snapshots":
            # 快照客户字段藏在 raw_payload_json:拉回 Python 解析(零 SQL 词表/LIKE)。
            found = 0
            try:
                rows = conn.execute(
                    f"SELECT id, raw_payload_json FROM {table} ORDER BY id DESC LIMIT ?",
                    (500,),
                ).fetchall()
                for r in rows:
                    d = dict(r)
                    raw = _loads(d.get("raw_payload_json"))
                    raw = raw if isinstance(raw, dict) else {}
                    cust = raw.get("customer") if isinstance(raw.get("customer"), dict) else {}
                    email = str(raw.get("email") or cust.get("email") or "").strip()
                    name = " ".join(
                        str(cust.get(k) or "").strip() for k in ("first_name", "last_name")
                    ).strip()
                    if email or name:
                        found += 1
                        customers.append(
                            {"email": email, "name": name, "source_table": table, "source_row_id": d.get("id")}
                        )
                info["rows_with_contact"] = found
            except Exception:
                _safe_rollback(conn)
                info["rows_with_contact"] = None
                info["scan_error"] = "raw_payload_json 解析失败"
        elif table == "parties":
            # PII 哈希化(identity_links),无明文邮箱可撞;只清点 is_customer 供诚实说明。
            info["rows_with_contact"] = 0
            info["customer_rows"] = _count(conn, f"SELECT COUNT(*) AS n FROM {table} WHERE is_customer = TRUE")
            info["note"] = "party 层 PII 哈希化存储,无明文可撞库(合规设计,非缺数)"
        else:  # vkpi_goaffpro_sales:无客户明文列。
            info["rows_with_contact"] = 0
            info["note"] = "销售快照无客户明文列(金额/佣金归因用),不参与撞库"
        sources.append(info)

    return sources, customers


def _load_pool_rows(limit: int = 5000) -> list[dict[str, Any]]:
    """KOL 池撞库对照面:id/platform/handle/display_name/email(只读;email 仅内存比对,
    绝不进输出——输出侧一律脱敏)。"""
    from app.db.connection import get_conn, table_exists

    if not table_exists(_POOL):
        return []
    conn = get_conn()
    try:
        rows = conn.execute(
            f"SELECT id, platform, handle, display_name, email FROM {_POOL} ORDER BY id LIMIT ?",
            (max(1, min(int(limit or 5000), 20000)),),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        _safe_rollback(conn)
        logger.warning("customer_mining.load_pool_failed", exc_info=True)
        return []


# ── 对外契约 ─────────────────────────────────────────────────────────


def mine_customers() -> dict[str, Any]:
    """侦察客户数据源 → 撞库出「你的买家里的创作者」候选;无客户数据诚实 empty + ready_when。"""
    sources, customers = _scan_sources()

    # 客户侧去重(同邮箱多单只留最新来源;无邮箱有名字的保留做模糊)。
    dedup: list[dict[str, Any]] = []
    seen_emails: set[str] = set()
    for c in customers:
        em = _norm_email(c.get("email"))
        if em:
            if em in seen_emails:
                continue
            seen_emails.add(em)
        elif not _norm_name(c.get("name")):
            continue  # 邮箱名字全空:无可撞信息
        dedup.append(c)

    base = {
        "sources": sources,
        "customers_scanned": len(customers),
        "customers_usable": len(dedup),
        "match_rules": {
            "email_exact": "邮箱大小写归一后与 vkpi_kol_pool.email 精确相等 → 高置信",
            "name_fuzzy": f"difflib 相似度 >= {NAME_FUZZY_THRESHOLD}(display_name/handle)→ 需人工确认",
        },
        "compliance_note": (
            "输出邮箱一律脱敏(e***@d***);真值联系方式仅经既有 contact_reveal 门控"
            "(二次确认 + 敏感访问审计)查看;本模块只读侦察,零写库零 LLM。"
        ),
        "generated_at": _now_iso(),
    }

    if not dedup:
        return {
            **base,
            "status": "empty",
            "reason": (
                "本地无客户数据可撞:vkpi_shopify_orders / orders 均无带邮箱订单行,"
                "webhook 快照(冒烟造数)不含客户字段,GOAFFPRO 销售无客户明文列,"
                "party 层 PII 哈希化(合规设计)。诚实空,不臆造候选。"
            ),
            "ready_when": (
                "以下任一数据链接通后本功能自动生效,零代码改动:"
                "① Shopify 订单 ingest(POST /api/admin/vkpi/shopify/orders)写入带 customer_email 的真订单;"
                "② 线上 webhook 快照落真单(raw_payload_json 带 email/customer);"
                "③ 015 orders 表被 platform_ingest 灌入真订单。"
                "届时 GET /api/admin/vkpi/discovery/customer-miners 直接产出「买家里的创作者」候选。"
            ),
            "candidates": [],
        }

    pool_rows = _load_pool_rows()
    if not pool_rows:
        return {
            **base,
            "status": "empty",
            "reason": "KOL 池为空或不可读,无对照面可撞",
            "candidates": [],
        }

    try:
        candidates = match_customers_to_pool(dedup, pool_rows)
    except Exception as exc:  # noqa: BLE001 — 撞库异常诚实回原因,不 500
        logger.warning("customer_mining.match_failed: %s", exc, exc_info=True)
        return {**base, "status": "error", "reason": str(exc)[:300], "candidates": []}

    return {
        **base,
        "status": "ready" if candidates else "no_match",
        "reason": "" if candidates else "客户数据与 KOL 池撞库零命中(诚实:你的买家里暂未发现池内创作者)",
        "pool_rows_compared": len(pool_rows),
        "candidates": candidates[:100],
        "candidate_count": len(candidates),
    }
