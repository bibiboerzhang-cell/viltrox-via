"""附录 A 第 12 段·承接层就绪度(独立模块,防 gtm_plan_preview 撞千行卫兵)。
纯读 checklist;承接弱 → thesis_advisory 提示「先补承接再放大流量」(Reddit 4.4:PR 不等于 sales)。
红线:零写库零 LLM;不知道就 unknown,绝不装知道。"""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn


def _text(value: Any, limit: int) -> str:
    return str(value or "")[:limit]


def build_conversion_readiness(sku: str, product: dict[str, Any] | None) -> dict[str, Any]:
    """附录 A 第 12 段·承接层就绪度(Reddit 4.4 信号:PR 不等于 sales,承接先接住)。
    纯读 checklist:每项 ready/missing/unknown,绝不装知道;承接弱 → thesis_advisory
    提示「先补承接再放大流量」。本地无 Shopify 订单/页面数据时诚实 unknown。"""
    conn = get_conn()
    items: list[dict[str, Any]] = []

    def _item(key: str, label: str, status: str, note: str) -> None:
        items.append({"key": key, "label": label, "status": status, "note": _text(note, 160)})

    # 1) 产品评价基础:B&H 评论表该 SKU 有无口碑数据(表在但可能空——诚实)。
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM vkpi_bh_reviews WHERE UPPER(COALESCE(sku,'')) = ?",
            (str(sku).upper(),),
        ).fetchone()
        n_reviews = int(dict(row or {}).get("n") or 0)
        _item("reviews", "产品页 review 基础", "ready" if n_reviews > 0 else "missing",
              f"库内口碑样本 {n_reviews} 条" if n_reviews else "B&H 口碑表 0 条,产品页 review 数需人工核;弱评价先补 seeding")
    except Exception:
        _item("reviews", "产品页 review 基础", "unknown", "口碑表不可读,人工核产品页 review 数")

    # 2) 短链归因就绪:viltroxvia.com 短链体系为既有架构,标 ready(域名级),单链需 GTM-3 生成。
    _item("shortlink", "归因短链就绪", "ready", "viltroxvia.com 短链+webhook 架构已在;该 SKU 专属链/佣金码待 GTM-3 生成")

    # 3) 订单承接数据:本地库无订单即诚实 unknown(线上有 webhook 流)。
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM vkpi_shopify_orders").fetchone()
        n_orders = int(dict(row or {}).get("n") or 0)
        _item("orders_flow", "订单归因流", "ready" if n_orders > 0 else "unknown",
              f"订单快照 {n_orders} 条" if n_orders else "本地库 0 订单(线上 webhook 有流);上云后自动转 ready")
    except Exception:
        _item("orders_flow", "订单归因流", "unknown", "订单表不可读")

    # 4) 落地页要素(样片/FAQ/价格库存可见性):系统无页面抓取数据 → 诚实人工 checklist。
    _item("landing_page", "落地页样片/FAQ", "unknown", "系统未抓页面;上线前人工核:样片、FAQ、兼容卡口说明齐")
    _item("purchase_path", "价格/库存/购买路径", "unknown", "人工核:价格清晰、库存显示、结账步骤≤3;Firecrawl 接入后可自动核")

    missing = sum(1 for i in items if i["status"] == "missing")
    unknown = sum(1 for i in items if i["status"] == "unknown")
    weak = missing >= 1 or unknown >= 3
    return {
        "status": "ready",
        "items": items,
        "overall": "weak" if weak else ("partial" if unknown else "ready"),
        "thesis_advisory": (
            "承接层未就绪信号存在:先补 review/落地页要素,再放大付费与达人流量(PR≠sales)。"
            if weak else "承接层无已知硬伤;单链与佣金码在 materialize 时生成。"
        ),
        "basis": "库内只读 checklist(口碑表/短链架构/订单流)+人工核清单;页面级自动核待 Firecrawl(GTM-2+)。",
    }
