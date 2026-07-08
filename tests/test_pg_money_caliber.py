"""Real-Postgres money-caliber tests — one per money-aggregation function.

金额口径铁律(每个 SUM revenue/GMV/佣金/成本 的函数都必须满足):
  ① 只算确认态:排除 refund / unmatched / cancelled / pending / void 等非确认行。
  ② 不跨币相加:无 FX 表时绝不把 EUR cents 加进 USD;应按 currency 分组后报主币种
     (或多币种置 None + 明细),EUR 绝不并入 USD 头条数。

做法:每个测试在 `pg_compat`(生产 PostgresCompatConnection,teardown 回滚)自己的
事务里插入「确认态 + 非确认态 × USD + EUR 混币」数据,把这条连接注入 get_conn()
(经 `_scoped_conn` ContextVar),调用真业务函数,断言口径。全程不 commit,teardown 回滚,
对真库零污染、可重复跑;PG 不可达 → `pg` marker 自动 skip(不红)。

覆盖(本 session 修过的 + 疑似漏网的):
  fixed(应通过,固化防回归):
    - goaffpro   _aggregate_confirmed_sales
    - shopify    summarize_gmv
    - account_picker _build_attributed_gmv_roi
    - costs      summarize_project
  suspected 漏网(断言正确口径 → 若仍跨币相加则红,暴露 bug):
    - metrics.aggregation      _sum_revenue
    - vkpi_decision_common     _summary
    - decision_dashboard       revenue_trend
"""
from __future__ import annotations

import secrets
from contextlib import contextmanager
from typing import Any, Iterator

import pytest

from app.db import connection as dbc

pytestmark = pytest.mark.pg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
@contextmanager
def _scoped(conn: Any) -> Iterator[None]:
    """Route the production get_conn() at this exact compat connection for the
    duration of a call, so the function-under-test reads our uncommitted test
    rows inside the same rolled-back transaction (real isolation, zero commit)."""
    token = dbc._scoped_conn.set(conn)
    try:
        assert dbc.get_conn() is conn, "scoped-conn injection failed"
        assert dbc.is_postgres_runtime() is True, "expected postgres runtime"
        yield
    finally:
        dbc._scoped_conn.reset(token)


def _ref() -> str:
    return "pgmoney_" + secrets.token_hex(8)


def _new_project(conn: Any) -> int:
    row = conn.execute(
        """
        INSERT INTO vkpi_projects (project_uid, project_name, stage, stage_status)
        VALUES (?, ?, 'discovery', 'active')
        RETURNING id
        """,
        (_ref(), "pgmoney-caliber"),
    ).fetchone()
    return int(row["id"])


def _ins_sale(
    conn: Any,
    *,
    revenue_cents: int,
    currency: str,
    confidence: str,
    project_id: int | None = None,
    staff_id: int | None = None,
    commission_cents: int = 0,
) -> None:
    conn.execute(
        """
        INSERT INTO vkpi_sales_attributions
            (source_platform, source_ref, project_id, staff_id,
             revenue_cents, commission_cents, currency, confidence, occurred_at)
        VALUES ('shopify', ?, ?, ?, ?, ?, ?, ?, NOW())
        """,
        (_ref(), project_id, staff_id, revenue_cents, commission_cents, currency, confidence),
    )


def _ins_cost(
    conn: Any, *, project_id: int, amount_cents: int, currency: str, status: str
) -> None:
    conn.execute(
        """
        INSERT INTO vkpi_cost_ledger (project_id, cost_type, amount_cents, currency, status)
        VALUES (?, 'other', ?, ?, ?)
        """,
        (project_id, amount_cents, currency, status),
    )


def _ins_order(
    conn: Any, *, total_price_cents: int, currency: str, financial_status: str, discount_code: str
) -> None:
    conn.execute(
        """
        INSERT INTO vkpi_shopify_orders
            (shop_domain, order_id, total_price_cents, currency, financial_status,
             discount_code, ordered_at, ingested_at)
        VALUES ('pgmoney.myshopify.com', ?, ?, ?, ?, ?, NOW(), NOW())
        """,
        (_ref(), total_price_cents, currency, financial_status, discount_code),
    )


def _ins_goaffpro(
    conn: Any, *, kol_pool_id: int, total_cents: int, currency: str, status: str
) -> None:
    conn.execute(
        """
        INSERT INTO vkpi_goaffpro_sales
            (sale_id, affiliate_id, kol_pool_id, total_cents, commission_cents, currency, status)
        VALUES (?, 'aff-pgmoney', ?, ?, ?, ?, ?)
        """,
        (_ref(), kol_pool_id, total_cents, total_cents // 10, currency, status),
    )


# ---------------------------------------------------------------------------
# FIXED FUNCTIONS — these already group by currency; tests lock the fix in.
# ---------------------------------------------------------------------------
def test_goaffpro_attribution_confirmed_and_no_cross_currency(pg_compat: Any) -> None:
    """goaffpro _aggregate_confirmed_sales:确认态白名单 + 按币种分组取主币种,EUR 不进 USD。"""
    from app.api.routers.vkpi_goaffpro import _aggregate_confirmed_sales

    kid = 900_000_000 + secrets.randbelow(50_000_000)  # unique, unused kol_pool_id (no FK)
    _ins_goaffpro(pg_compat, kol_pool_id=kid, total_cents=10_000, currency="USD", status="approved")
    _ins_goaffpro(pg_compat, kol_pool_id=kid, total_cents=20_000, currency="USD", status="paid")
    _ins_goaffpro(pg_compat, kol_pool_id=kid, total_cents=5_000, currency="EUR", status="confirmed")
    # non-confirmed rows that must be excluded from every figure:
    _ins_goaffpro(pg_compat, kol_pool_id=kid, total_cents=99_999, currency="USD", status="refund")
    _ins_goaffpro(pg_compat, kol_pool_id=kid, total_cents=88_888, currency="USD", status="pending")

    agg = _aggregate_confirmed_sales(pg_compat, "kol_pool_id = ?", (kid,))

    by_cur = {b["currency"]: b for b in agg["by_currency"]}
    # ① confirmed-only: the USD bucket is exactly the two confirmed USD sales (30000),
    #    refund(99999) + pending(88888) excluded.
    assert by_cur["USD"]["total_cents"] == 30_000, agg
    assert by_cur["USD"]["sales_count"] == 2, agg
    # ② no cross-currency: dominant scalar == USD only; EUR carried in breakdown, never added.
    assert agg["total_cents"] == 30_000, agg
    assert agg["currency"] == "USD", agg
    assert agg["mixed_currency"] is True, agg
    assert by_cur["EUR"]["total_cents"] == 5_000, agg


def test_shopify_summarize_gmv_no_cross_currency(pg_compat: Any) -> None:
    """shopify summarize_gmv:订单账本按 currency 分组取主币种,EUR cents 不并入 USD。"""
    from app.domains.commerce import shopify_orders

    code = "PGMONEY_" + secrets.token_hex(4)
    _ins_order(pg_compat, total_price_cents=10_000, currency="USD", financial_status="paid", discount_code=code)
    _ins_order(pg_compat, total_price_cents=20_000, currency="USD", financial_status="paid", discount_code=code)
    _ins_order(pg_compat, total_price_cents=5_000, currency="EUR", financial_status="paid", discount_code=code)

    with _scoped(pg_compat):
        out = shopify_orders.summarize_gmv(discount_codes=[code])

    # ② dominant currency only (USD 30000); EUR (5000) never folded into the headline.
    assert out["gmv_cents"] == 30_000, out
    assert out["currency"] == "USD", out
    assert out["mixed_currency"] is True, out
    assert out["order_ledger_gmv_cents"] == 30_000, out
    assert out["gmv_source"].startswith("vkpi_shopify_orders"), out


def test_account_picker_gmv_confirmed_and_no_cross_currency(pg_compat: Any) -> None:
    """account_picker _build_attributed_gmv_roi:confidence='confirmed' + 按币种分组,EUR 不进 USD。

    程序级(不可按账户 scope),故以事务内基线增量断言:注入前先量当前 USD 确认额,
    断言函数只在其上加了我们的 USD 确认额,EUR 与非确认态都不进头条 GMV。
    """
    from app.domains.dashboard.account_picker import _build_attributed_gmv_roi

    base_usd = int(
        pg_compat.execute(
            """
            SELECT COALESCE(SUM(revenue_cents), 0) AS s
            FROM vkpi_sales_attributions
            WHERE source_platform = 'shopify' AND confidence = 'confirmed'
              AND COALESCE(NULLIF(currency, ''), 'USD') = 'USD'
            """
        ).fetchone()["s"]
    )

    _ins_sale(pg_compat, revenue_cents=100_000, currency="USD", confidence="confirmed")
    _ins_sale(pg_compat, revenue_cents=50_000, currency="EUR", confidence="confirmed")
    # non-confirmed noise that must be excluded:
    _ins_sale(pg_compat, revenue_cents=999_999, currency="USD", confidence="unmatched")
    _ins_sale(pg_compat, revenue_cents=888_888, currency="USD", confidence="refund")

    with _scoped(pg_compat):
        out = _build_attributed_gmv_roi()

    # ① + ②: exactly the confirmed USD delta; EUR + unmatched + refund all excluded.
    assert out["attributed_gmv_cents"] == base_usd + 100_000, out
    assert out["gmv_currency"] == "USD", out
    assert out["gmv_mixed_currency"] is True, out


def test_costs_summarize_project_void_and_no_cross_currency(pg_compat: Any) -> None:
    """costs summarize_project:只算 status='actual'(排除 void/estimate/pending),按币种分组;
    多币种置 total_cost_cents=None + totals_by_currency 明细,绝不跨币直加。"""
    from app.domains.costs.ledger import summarize_project

    pid = _new_project(pg_compat)
    _ins_cost(pg_compat, project_id=pid, amount_cents=10_000, currency="USD", status="actual")
    _ins_cost(pg_compat, project_id=pid, amount_cents=20_000, currency="USD", status="actual")
    _ins_cost(pg_compat, project_id=pid, amount_cents=5_000, currency="EUR", status="actual")
    # non-actual rows that must be excluded:
    _ins_cost(pg_compat, project_id=pid, amount_cents=99_999, currency="USD", status="void")
    _ins_cost(pg_compat, project_id=pid, amount_cents=88_888, currency="USD", status="estimate")
    _ins_cost(pg_compat, project_id=pid, amount_cents=77_777, currency="USD", status="pending")

    with _scoped(pg_compat):
        out = summarize_project(pid)

    # ① actual-only: void/estimate/pending excluded → USD actual = 30000, EUR actual = 5000.
    assert out["totals_by_currency"] == {"USD": 30_000, "EUR": 5_000}, out
    # ② mixed currency: no scalar cross-currency sum; total None + mixed flag.
    assert out["mixed_currency"] is True, out
    assert out["total_cost_cents"] is None, out
    assert out["currency"] is None, out


# ---------------------------------------------------------------------------
# SUSPECTED 漏网 — assert the CORRECT caliber; red = cross-currency bug exposed.
# Each: confirmed-only half must hold (locks that half); the currency assert is
# the one that surfaces "EUR added into USD".
# ---------------------------------------------------------------------------
def test_metrics_sum_revenue_no_cross_currency(pg_compat: Any) -> None:
    """metrics.aggregation._sum_revenue:确认态过滤对,但无 GROUP BY currency → 疑跨币直加。

    正确口径:主币种(USD)确认额 10000。若返回 15000 说明把 EUR(5000)加进了 USD。
    """
    from app.domains.metrics import aggregation as magg

    pid = _new_project(pg_compat)
    _ins_sale(pg_compat, revenue_cents=10_000, currency="USD", confidence="confirmed", project_id=pid)
    _ins_sale(pg_compat, revenue_cents=5_000, currency="EUR", confidence="confirmed", project_id=pid)
    _ins_sale(pg_compat, revenue_cents=999_999, currency="USD", confidence="unmatched", project_id=pid)
    _ins_sale(pg_compat, revenue_cents=888_888, currency="USD", confidence="refund", project_id=pid)

    with _scoped(pg_compat):
        out = magg._sum_revenue("AND project_id = ?", [pid])

    rev = int(out["revenue_cents"])
    # ① confirmed-only holds (unmatched/refund excluded): must be well under the noise.
    assert rev < 500_000, f"non-confirmed leaked into revenue: {out}"
    # ② no cross-currency: EUR must NOT be added onto USD.
    assert rev == 10_000, f"cross-currency: EUR folded into USD revenue, got {rev} (expected 10000)"


def test_decision_common_summary_no_cross_currency(pg_compat: Any) -> None:
    """vkpi_decision_common._summary:确认态过滤对,但 revenue SUM 无 currency 分组 → 疑跨币直加。

    以真实 staff_id=7676 scope(现存 sales 全 staff_id NULL,该 staff 零现存行 → 只见我们注入的)。
    正确口径:USD 确认额 10000;若 15000 说明 EUR 混进 USD。
    """
    from app.shared.vkpi_decision_common import _summary

    staff_id = 7676  # real staff row; has zero existing sales/cost rows
    _ins_sale(pg_compat, revenue_cents=10_000, currency="USD", confidence="confirmed", staff_id=staff_id)
    _ins_sale(pg_compat, revenue_cents=5_000, currency="EUR", confidence="confirmed", staff_id=staff_id)
    _ins_sale(pg_compat, revenue_cents=777_777, currency="USD", confidence="unmatched", staff_id=staff_id)

    with _scoped(pg_compat):
        out = _summary(pg_compat, staff_id=staff_id)

    rev = int(out["revenue_cents"])
    assert rev < 500_000, f"non-confirmed leaked into summary revenue: {rev}"
    assert rev == 10_000, f"cross-currency: EUR folded into USD summary revenue, got {rev} (expected 10000)"


def test_decision_dashboard_revenue_trend_no_cross_currency(pg_compat: Any) -> None:
    """decision_dashboard.revenue_trend:每日 gmv=SUM(confirmed revenue),无 currency 分组 → 疑跨币直加。

    正确口径:窗口内 USD 确认额 10000;若 15000 说明把 EUR 当日额并进了 USD gmv。
    """
    from app.domains.dashboard import decision_dashboard as dd

    staff_id = 7676
    _ins_sale(pg_compat, revenue_cents=10_000, currency="USD", confidence="confirmed", staff_id=staff_id)
    _ins_sale(pg_compat, revenue_cents=5_000, currency="EUR", confidence="confirmed", staff_id=staff_id)
    _ins_sale(pg_compat, revenue_cents=666_666, currency="USD", confidence="cancelled", staff_id=staff_id)

    with _scoped(pg_compat):
        trend = dd.revenue_trend(window_days=7, staff_id=staff_id)

    total_gmv = sum(int(r["gmv_cents"]) for r in trend["rows"])
    assert total_gmv < 500_000, f"non-confirmed leaked into trend gmv: {total_gmv}"
    assert total_gmv == 10_000, (
        f"cross-currency: EUR folded into USD trend gmv, got {total_gmv} (expected 10000)"
    )
