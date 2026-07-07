"""CB3 独立站/Shopify 承接建议 —— Conversion Readiness Actions(纯读)。

Reddit 信号洞察:PR ≠ sales。曝光(公关/KOL)打出去后,若「承接层」没接住,
流量白流。本模块对给定 SKU 把承接层拆成 4 项可勾选的就绪度检查:
  ① 短链就绪(short_link)   —— KOL 可追踪短链能否铸造
  ② 落地页 + 样片 + FAQ(landing_page) —— 落地页 URL / 样片 / FAQ
  ③ 佣金码(commission_code) —— 是否已铸 KOL 佣金/优惠券码
  ④ 购买路径(purchase_path) —— Shopify 购买路径是否搭好并经真实订单验证
每项独立判 ready|missing|unknown,并带 basis(依据)可审计,绝不装知道。

数据来源(全只读、零副作用、零 LLM、零采集):
- 产品页信息:sku_performance.resolve_sku(sku) → vkpi_products(product_url 等)。
- Shopify 连接 + 真实订单:shopify_connect.connection_status() + COUNT(vkpi_shopify_orders)。
- 短链/佣金引擎:goaffpro_connect.connection_status() + vkpi_goaffpro_kol_links(coupon/link 数)。

诚实态铁律:
- SKU 未命中 vkpi_products → resolved=False + 各项 unknown + note,checklist 结构照常就位。
- 本地 0 Shopify 订单 → shopify.status='data_missing' + note;购买路径判 unknown(路径已搭
  但未经真实订单验证),绝不编数。
- COUNT 因表缺失读不到 → None(unknown),与「表在但为 0」(data_missing)显式区分。
- 每个状态/数字都带 basis。

红线:纯展示只读、零写库;绝不触碰 viltrox_fit_score / rule_v0;
SQL 无字面 %(词表在 Python 侧;本模块无 LIKE 需求)。
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_conn


READY = "ready"
MISSING = "missing"
UNKNOWN = "unknown"


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if hasattr(row, "get"):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:
        return default


def _count(sql: str) -> int | None:
    """Guarded COUNT(*) — None 表示表不可读(unknown),0 表示表在但空(data_missing)。

    纯读;任何异常(表缺失 / 权限 / 序列化)吞掉回 None,绝不抛穿承接建议。
    """
    try:
        conn = get_conn()
        row = conn.execute(sql).fetchone()
    except Exception:
        return None
    val = _row_get(row, "n", None)
    if val is None:
        return 0
    try:
        return int(val)
    except Exception:
        return 0


def _public_base_url() -> str:
    """短链跳转域(viltroxvia.com 一类):读 env,绝不写。"""
    for key in ("PUBLIC_BASE_URL", "APP_BASE_URL", "WEBHOOK_BASE_URL", "SHOPIFY_WEBHOOK_BASE_URL"):
        val = str(os.environ.get(key) or "").strip()
        if val:
            return val
    return ""


# --- 侦察层:先摸清 Shopify 连接 + 短链/佣金引擎的真实状态 -------------------

def _shopify_readiness() -> dict[str, Any]:
    """Shopify 连接状态 + 真实订单数(购买路径证据)。本地 0 单诚实 data_missing。"""
    conn_status = "not_configured"
    shop_domain = ""
    try:
        from app.domains.commerce import shopify_connect

        status = shopify_connect.connection_status()
        conn_status = str(status.get("status") or "not_configured")
        shop_domain = str(status.get("shop_domain") or "")
    except Exception:
        pass

    order_count = _count("SELECT COUNT(*) AS n FROM vkpi_shopify_orders")
    if order_count is None:
        data_status = "data_missing"
        note = "vkpi_shopify_orders 表不可读,真实订单数未知。"
        basis = "table vkpi_shopify_orders unreadable"
    elif order_count == 0:
        data_status = "data_missing"
        note = "本地 0 条 Shopify 订单(线上另有真实订单);购买路径尚未经真实成交验证。"
        basis = "COUNT(vkpi_shopify_orders)=0"
    else:
        data_status = "ok"
        note = f"已有 {order_count} 条真实 Shopify 订单。"
        basis = f"COUNT(vkpi_shopify_orders)={order_count}"

    return {
        "connection_status": conn_status,
        "shop_domain": shop_domain,
        "order_count": order_count,
        "status": data_status,
        "note": note,
        "basis": basis,
    }


def _affiliate_readiness() -> dict[str, Any]:
    """GOAFFPRO 短链/佣金引擎连接状态 + 已铸 tracking_url / coupon 数。"""
    conn_status = "not_configured"
    try:
        from app.domains.integrations import goaffpro_connect

        status = goaffpro_connect.connection_status()
        conn_status = str(status.get("status") or "not_configured")
    except Exception:
        pass

    coupon_count = _count("SELECT COUNT(*) AS n FROM vkpi_goaffpro_kol_links WHERE coupon <> ''")
    link_count = _count("SELECT COUNT(*) AS n FROM vkpi_goaffpro_kol_links WHERE tracking_url <> ''")

    return {
        "connection_status": conn_status,
        "coupon_count": coupon_count,
        "tracking_link_count": link_count,
        "public_base_url_set": bool(_public_base_url()),
    }


# --- 4 项承接检查(每项 ready|missing|unknown + basis)------------------------

def _item(key: str, label: str, state: str, detail: str, basis: str,
          sub_checks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    out = {"key": key, "label": label, "state": state, "detail": detail, "basis": basis}
    if sub_checks is not None:
        out["sub_checks"] = sub_checks
    return out


def _check_short_link(affiliate: dict[str, Any]) -> dict[str, Any]:
    conn_status = str(affiliate.get("connection_status") or "not_configured")
    link_count = affiliate.get("tracking_link_count")
    public_base = bool(affiliate.get("public_base_url_set"))
    if conn_status == "connected":
        return _item(
            "short_link", "短链就绪", READY,
            "GOAFFPRO 已连接,KOL 可追踪短链可自动铸造。",
            f"goaffpro=connected; tracking_link_count={link_count}",
        )
    if conn_status == "not_configured" and not public_base:
        return _item(
            "short_link", "短链就绪", MISSING,
            "无 GOAFFPRO 连接、无短链跳转域(PUBLIC_BASE_URL 未设);KOL 追踪短链暂无法铸造。",
            "goaffpro=not_configured; public_base_url_set=false",
        )
    return _item(
        "short_link", "短链就绪", UNKNOWN,
        "短链引擎凭据未就绪/未验证,或仅有跳转域;需人工确认。",
        f"goaffpro={conn_status}; public_base_url_set={str(public_base).lower()}",
    )


def _check_landing_page(product_url: str) -> dict[str, Any]:
    has_url = bool(product_url)
    sub_checks = [
        _item(
            "landing_url", "落地页 URL",
            READY if has_url else MISSING,
            product_url if has_url else "vkpi_products 无 product_url。",
            f"vkpi_products.product_url={'present' if has_url else 'empty'}",
        ),
        # 样片/FAQ 是页面内容,无法从库内核验 —— 诚实标 unknown,绝不装知道。
        _item("sample_footage", "样片", UNKNOWN,
              "样片是否上架无法从库内核验,需人工核对落地页。", "not verifiable from DB"),
        _item("faq", "FAQ", UNKNOWN,
              "FAQ 是否齐备无法从库内核验,需人工核对落地页。", "not verifiable from DB"),
    ]
    if has_url:
        return _item(
            "landing_page", "落地页 + 样片 + FAQ", READY,
            "已有 product_url 落地页;样片/FAQ 页面内容无法从库内核验(需人工核对)。",
            "vkpi_products.product_url present",
            sub_checks=sub_checks,
        )
    return _item(
        "landing_page", "落地页 + 样片 + FAQ", MISSING,
        "vkpi_products 无 product_url —— 承接落地页未登记;样片/FAQ 亦无从核验。",
        "vkpi_products.product_url empty",
        sub_checks=sub_checks,
    )


def _check_commission_code(affiliate: dict[str, Any]) -> dict[str, Any]:
    conn_status = str(affiliate.get("connection_status") or "not_configured")
    coupon_count = affiliate.get("coupon_count")
    if isinstance(coupon_count, int) and coupon_count > 0:
        return _item(
            "commission_code", "佣金码", READY,
            f"已铸 {coupon_count} 个 KOL 佣金/优惠券码。",
            f"vkpi_goaffpro_kol_links coupon<>'' count={coupon_count}",
        )
    if conn_status in ("connected", "pending"):
        return _item(
            "commission_code", "佣金码", UNKNOWN,
            "佣金引擎已接但尚未铸造任何优惠券码。",
            f"goaffpro={conn_status}; coupon_count={coupon_count}",
        )
    if conn_status == "not_configured":
        return _item(
            "commission_code", "佣金码", MISSING,
            "无 GOAFFPRO 佣金引擎连接;佣金码暂无法铸造。",
            "goaffpro=not_configured",
        )
    return _item(
        "commission_code", "佣金码", UNKNOWN,
        "佣金码状态无法核验(引擎 error/revoked)。",
        f"goaffpro={conn_status}; coupon_count={coupon_count}",
    )


def _check_purchase_path(shopify: dict[str, Any]) -> dict[str, Any]:
    conn_status = str(shopify.get("connection_status") or "not_configured")
    order_count = shopify.get("order_count")
    basis = str(shopify.get("basis") or "")
    if isinstance(order_count, int) and order_count > 0:
        return _item(
            "purchase_path", "购买路径", READY,
            f"已有 {order_count} 条真实订单,购买路径经真实成交验证。",
            basis,
        )
    if conn_status == "connected":
        return _item(
            "purchase_path", "购买路径", UNKNOWN,
            "Shopify 已连接但本地 0 订单;购买路径已搭,未经真实订单验证(data_missing)。",
            basis or "shopify=connected; order_count=0",
        )
    if conn_status == "not_configured":
        return _item(
            "purchase_path", "购买路径", MISSING,
            "Shopify 未连接;购买路径未搭(本地口径;线上另有接入)。",
            "shopify=not_configured",
        )
    return _item(
        "purchase_path", "购买路径", UNKNOWN,
        "Shopify 连接状态未就绪(pending/error);购买路径待确认。",
        f"shopify={conn_status}",
    )


def _skeleton_checklist(note: str) -> list[dict[str, Any]]:
    """SKU 未命中时的结构就位版:4 项全 unknown,detail 带诚实 note。"""
    labels = [
        ("short_link", "短链就绪"),
        ("landing_page", "落地页 + 样片 + FAQ"),
        ("commission_code", "佣金码"),
        ("purchase_path", "购买路径"),
    ]
    return [_item(k, lab, UNKNOWN, note, "sku not resolved") for k, lab in labels]


def _summarize(checklist: list[dict[str, Any]]) -> dict[str, int]:
    counts = {READY: 0, MISSING: 0, UNKNOWN: 0}
    for it in checklist:
        st = str(it.get("state") or UNKNOWN)
        if st in counts:
            counts[st] += 1
    return {
        "ready": counts[READY],
        "missing": counts[MISSING],
        "unknown": counts[UNKNOWN],
        "total": len(checklist),
    }


def _product_view(product: dict[str, Any]) -> dict[str, Any]:
    return {
        "sku": str(product.get("sku") or ""),
        "model_name": str(product.get("model_name") or ""),
        "marketing_name": str(product.get("marketing_name") or ""),
        "product_url": str(product.get("product_url") or ""),
        "price_usd": product.get("price_usd"),
        "category_main": str(product.get("category_main") or ""),
    }


def indie_site_actions(sku: str) -> dict[str, Any]:
    """独立站/Shopify 承接就绪 checklist(纯读)。见模块 docstring 为口径与诚实态。"""
    sku_clean = str(sku or "").strip()
    if not sku_clean:
        return {
            "status": "invalid",
            "sku": "",
            "resolved": False,
            "note": "sku 参数为空。",
            "generated_at": _utcnow(),
        }

    # 侦察层(与产品解析无关,先摸清承接引擎现状)。
    shopify = _shopify_readiness()
    affiliate = _affiliate_readiness()

    # 产品解析(承接的落点)。
    product: dict[str, Any] | None = None
    try:
        from app.domains.products.sku_performance import resolve_sku

        product = resolve_sku(sku_clean)
    except Exception:
        product = None

    if not product:
        note = f"SKU「{sku_clean}」未在 vkpi_products 命中;承接 checklist 结构就位,各项待人工核验。"
        checklist = _skeleton_checklist("SKU 未解析,无法核验此项。")
        return {
            "status": "not_found",
            "sku": sku_clean,
            "resolved": False,
            "note": note,
            "product": None,
            "shopify": shopify,
            "affiliate": affiliate,
            "checklist": checklist,
            "summary": _summarize(checklist),
            "generated_at": _utcnow(),
        }

    product_url = str(product.get("product_url") or "").strip()
    checklist = [
        _check_short_link(affiliate),
        _check_landing_page(product_url),
        _check_commission_code(affiliate),
        _check_purchase_path(shopify),
    ]

    # 顶层状态:结构永远就位(ok);本地 0 订单时把 data_missing 显式抬到 note,绝不编数。
    top_note = (
        "承接 checklist 已生成。"
        + (" 注:" + shopify["note"] if shopify.get("status") == "data_missing" else "")
    )

    return {
        "status": "ok",
        "sku": sku_clean,
        "resolved": True,
        "note": top_note,
        "product": _product_view(product),
        "shopify": shopify,
        "affiliate": affiliate,
        "checklist": checklist,
        "summary": _summarize(checklist),
        "generated_at": _utcnow(),
    }
