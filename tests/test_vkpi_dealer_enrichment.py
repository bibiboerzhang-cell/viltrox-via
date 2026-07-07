"""C3 Dealer 零售增强单测:CRUD 容错(hermetic)+ dealer_targets 有/无 enrichment 两态。

hermetic 路径全部经 monkeypatch 注入假连接(迁移 226 可能尚未 apply,表缺席正是
诚实态——record 回 table_missing、读回 [] / None,全程不抛)。融合评分两态用假
dealer 行 + 显式 enrichment_map / monkeypatch 验证:有增强融合改分,无增强逐字节 v0。
末尾附一条 pg-marked 真库往返(record→get→list→UPSERT COALESCE),缺库自动跳过。
"""
from __future__ import annotations

import json

import pytest

from app.domains.channel import dealer_enrichment, dealer_scoring


# ── 假连接(照抄 signal_ledger 测试口径) ────────────────────────────


class _FakeCursor:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def fetchall(self) -> list[dict]:
        return self._rows

    def fetchone(self) -> dict | None:
        return self._rows[0] if self._rows else None


class _FakeConn:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def execute(self, sql: str, params: tuple = ()) -> _FakeCursor:
        return _FakeCursor(self._rows)

    def commit(self) -> None:
        return None


class _CaptureConn:
    """记录每次 execute 的 (sql, params);fetchone 回给定行(验参数透传用)。"""

    def __init__(self, rows: list[dict]):
        self._rows = rows
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql: str, params: tuple = ()):
        self.calls.append((sql, params))
        return _FakeCursor(self._rows)

    def commit(self) -> None:
        return None


def _patch_db(monkeypatch, *, exists: bool, rows: list[dict] | None = None) -> None:
    import app.db.connection as connection

    monkeypatch.setattr(connection, "table_exists", lambda name: exists)
    monkeypatch.setattr(connection, "get_conn", lambda: _FakeConn(rows or []))


# ── record_enrichment:参数校验 + 表缺席容错 ─────────────────────────


def test_record_invalid_dealer_id_no_db():
    for bad in (0, -1, "abc", None):
        out = dealer_enrichment.record_enrichment(bad, market="US")
        assert out["ok"] is False
        assert out["reason"] == "invalid_dealer_id"


def test_record_table_missing(monkeypatch):
    _patch_db(monkeypatch, exists=False)
    out = dealer_enrichment.record_enrichment(11, market="US")
    assert out == {"ok": False, "id": None, "upserted": False, "reason": "table_missing"}


def test_record_threads_params_and_dumps_family_fit(monkeypatch):
    import app.db.connection as connection

    conn = _CaptureConn([{"id": 7}])
    monkeypatch.setattr(connection, "table_exists", lambda name: True)
    monkeypatch.setattr(connection, "get_conn", lambda: conn)

    out = dealer_enrichment.record_enrichment(
        7, market="US", region="North America",
        product_family_fit={"lens": 0.9}, conversion_proxy=0.8,
        response_rate="0.6", estimated_reach=1200,
    )
    assert out["ok"] is True
    assert out["id"] == 7
    assert out["upserted"] is True  # existing SELECT 命中 → 视为 upsert
    insert_sql, insert_params = conn.calls[-1]
    assert "ON CONFLICT (organization_id, dealer_id)" in insert_sql
    assert dealer_enrichment.DEFAULT_ORG in insert_params
    assert 7 in insert_params
    assert json.dumps({"lens": 0.9}, ensure_ascii=False) in insert_params
    assert 0.8 in insert_params
    assert 0.6 in insert_params  # 字符串 "0.6" 宽容成 float


def test_record_unknown_kwargs_debug_only(monkeypatch):
    import app.db.connection as connection

    conn = _CaptureConn([])  # existing 查空 → upserted False
    monkeypatch.setattr(connection, "table_exists", lambda name: True)
    monkeypatch.setattr(connection, "get_conn", lambda: conn)
    out = dealer_enrichment.record_enrichment(3, bogus_col="x", market="US")
    assert out["ok"] is True
    assert out["upserted"] is False
    _, params = conn.calls[-1]
    assert "x" not in params  # 未知列不进 SQL 参数


# ── get_enrichment / list_enriched / get_enrichment_map ──────────────


def test_get_enrichment_table_missing(monkeypatch):
    _patch_db(monkeypatch, exists=False)
    assert dealer_enrichment.get_enrichment(5) is None


def test_get_enrichment_ready_normalizes(monkeypatch):
    row = {
        "id": 1, "organization_id": "viltrox", "dealer_id": 5,
        "market": "US", "region": "North America", "city": "NYC",
        # jsonb 读回可能是 JSON 串 → _family_fit_map 归一 dict
        "product_family_fit": '{"lens": 0.9, "cine": 0.5}',
        "channel_type": "retail", "response_rate": "0.6",
        "conversion_proxy": 0.8, "estimated_reach": 1200,
        "updated_at": "2026-07-06T12:00:00Z",
    }
    _patch_db(monkeypatch, exists=True, rows=[row])
    got = dealer_enrichment.get_enrichment(5)
    assert got is not None
    assert got["dealer_id"] == 5
    assert got["product_family_fit"] == {"lens": 0.9, "cine": 0.5}
    assert got["response_rate"] == 0.6
    assert got["updated_at"] == "2026-07-06T12:00:00+00:00"


def test_list_enriched_table_missing(monkeypatch):
    _patch_db(monkeypatch, exists=False)
    assert dealer_enrichment.list_enriched(market="US") == []


def test_list_enriched_family_filter_python_side(monkeypatch):
    rows = [
        {"id": 1, "dealer_id": 1, "product_family_fit": '{"lens": 0.9}',
         "updated_at": "2026-07-06T00:00:00Z"},
        {"id": 2, "dealer_id": 2, "product_family_fit": '{"lighting": 0.4}',
         "updated_at": "2026-07-05T00:00:00Z"},
    ]
    _patch_db(monkeypatch, exists=True, rows=rows)
    only_lens = dealer_enrichment.list_enriched(family="lens")
    assert [r["dealer_id"] for r in only_lens] == [1]
    # 无 family 过滤 → 两行都在
    both = dealer_enrichment.list_enriched()
    assert {r["dealer_id"] for r in both} == {1, 2}


def test_get_enrichment_map_table_missing(monkeypatch):
    _patch_db(monkeypatch, exists=False)
    assert dealer_enrichment.get_enrichment_map([1, 2]) == {}


def test_get_enrichment_map_empty_ids_short_circuit(monkeypatch):
    # 传入空 id 列表(区别于 None)→ 直接 {},不碰 DB。
    _patch_db(monkeypatch, exists=True, rows=[{"id": 1, "dealer_id": 1}])
    assert dealer_enrichment.get_enrichment_map([]) == {}


def test_get_enrichment_map_indexes_by_dealer_id(monkeypatch):
    rows = [
        {"id": 10, "dealer_id": 1, "product_family_fit": "{}", "market": "US"},
        {"id": 11, "dealer_id": 2, "product_family_fit": "{}", "market": "JP"},
    ]
    _patch_db(monkeypatch, exists=True, rows=rows)
    m = dealer_enrichment.get_enrichment_map([1, 2])
    assert set(m) == {1, 2}
    assert m[2]["market"] == "JP"


# ── _enrichment_component:已知子信号加权均值 / 全缺不参与 ─────────────


def test_enrichment_component_none_is_unknown():
    v, known, _basis, br = dealer_scoring._enrichment_component(None, "lens")
    assert known is False
    assert br == {}


def test_enrichment_component_no_usable_signal_unknown():
    # 增强行存在但无任何可用子信号(family 未命中 + 数值缺 + region 未收录)
    enr = {"product_family_fit": {"cine": 0.5}, "region": "atlantis"}
    v, known, _basis, br = dealer_scoring._enrichment_component(enr, "lens")
    assert known is False
    assert br == {}


def test_enrichment_component_weighted_mean_known_only():
    enr = {
        "product_family_fit": {"lens": 0.9},
        "conversion_proxy": 0.8, "response_rate": 0.6, "region": "North America",
    }
    v, known, _basis, br = dealer_scoring._enrichment_component(enr, "lens")
    assert known is True
    # 0.40*0.9 + 0.30*0.8 + 0.15*0.6 + 0.15*1.0 = 0.84(wsum=1.0)
    assert round(v, 3) == 0.84
    assert br == {"family_fit": 0.9, "conversion_proxy": 0.8,
                  "response_rate": 0.6, "region": 1.0}


def test_enrichment_component_partial_renormalizes():
    # 只有 conversion_proxy 已知 → 该子分独占分母 → 值即 conversion_proxy
    enr = {"conversion_proxy": 0.7}
    v, known, _basis, br = dealer_scoring._enrichment_component(enr, None)
    assert known is True
    assert round(v, 3) == 0.7
    assert br == {"conversion_proxy": 0.7}


# ── dealer_fit:无 enrichment 逐字节 v0,有 enrichment 融合改分 ────────


_DEALER = {"id": 1, "name": "Cam Store", "city": "LA", "state": "CA", "source": "seed"}


def test_dealer_fit_no_enrichment_is_v0(monkeypatch):
    monkeypatch.setattr(dealer_scoring, "_resolve_product", lambda sku: None)
    out = dealer_scoring.dealer_fit("SKU-X", dealers=[dict(_DEALER)], enrichment_map={})
    row = out["dealers"][0]
    # state=CA geo=1.0, category/tier 中性 0.5 → 100*(0.5*1 + 0.35*0.5 + 0.15*0.5)=75.0
    assert row["dealer_fit_score"] == 75.0
    assert "enrichment" not in row["components"]
    assert "enriched" not in row
    assert "base_fit_score" not in row
    assert "enriched_count" not in out


def test_dealer_fit_with_enrichment_blends_up(monkeypatch):
    monkeypatch.setattr(
        dealer_scoring, "_resolve_product",
        lambda sku: {"sku": sku, "category_main": "lens", "model_name": "m", "mount": "E"},
    )
    enr = {
        "product_family_fit": {"lens": 0.9},
        "conversion_proxy": 0.8, "response_rate": 0.6, "region": "North America",
    }
    out = dealer_scoring.dealer_fit("SKU-X", dealers=[dict(_DEALER)], enrichment_map={1: enr})
    row = out["dealers"][0]
    assert row["enriched"] is True
    assert row["base_fit_score"] == 75.0
    # enrichment=0.84 → final = 0.65*75 + 0.35*84 = 78.15 → 在 base 与 84 之间且 > base
    assert 75.0 < row["dealer_fit_score"] < 84.0
    assert row["components"]["enrichment"]["value"] == 0.84
    assert out["enriched_count"] == 1


# ── dealer_targets:有/无 enrichment 两态(monkeypatch 真库读) ─────────


def _patch_targets(monkeypatch, enrichment_map: dict) -> None:
    monkeypatch.setattr(dealer_scoring, "table_exists", lambda name: True)
    monkeypatch.setattr(
        dealer_scoring, "_resolve_product",
        lambda sku: {"sku": sku, "category_main": "lens", "model_name": "m", "mount": "E"},
    )
    monkeypatch.setattr(dealer_scoring, "_load_real_dealers", lambda limit=500: [dict(_DEALER)])
    monkeypatch.setattr(dealer_scoring, "_load_enrichment_map", lambda ids: enrichment_map)


def test_dealer_targets_without_enrichment_v0(monkeypatch):
    _patch_targets(monkeypatch, {})
    out = dealer_scoring.dealer_targets("SKU-X", limit=10)
    assert out["status"] == "ok"
    assert out["count"] == 1
    target = out["targets"][0]
    assert target["dealer_fit_score"] == 75.0
    assert "enriched" not in target
    assert "enriched_count" not in out


def test_dealer_targets_with_enrichment_blends(monkeypatch):
    enr = {
        "product_family_fit": {"lens": 0.9},
        "conversion_proxy": 0.8, "response_rate": 0.6, "region": "North America",
    }
    _patch_targets(monkeypatch, {1: enr})
    out = dealer_scoring.dealer_targets("SKU-X", limit=10)
    assert out["status"] == "ok"
    target = out["targets"][0]
    assert target["enriched"] is True
    assert target["dealer_fit_score"] > 75.0
    assert out["enriched_count"] == 1


def test_dealer_targets_table_missing_data_missing(monkeypatch):
    monkeypatch.setattr(dealer_scoring, "table_exists", lambda name: False)
    out = dealer_scoring.dealer_targets("SKU-X")
    assert out["status"] == "data_missing"
    assert out["targets"] == []
    assert "ready_when" in out


# ── pg-marked 真库往返(缺库自动跳过) ───────────────────────────────


@pytest.mark.pg
def test_enrichment_real_pg_roundtrip_and_coalesce():
    from app.db.connection import get_conn, table_exists

    if not table_exists("vkpi_dealer_enrichment"):
        pytest.skip("vkpi_dealer_enrichment 未 apply（迁移 226 未运行）")

    did = 990000777  # 本地 vkpi_dealers 0 行,高位 id 不与真行冲突
    conn = get_conn()
    try:
        r1 = dealer_enrichment.record_enrichment(
            did, market="US", region="North America",
            product_family_fit={"lens": 0.9}, conversion_proxy=0.8, response_rate=0.6,
        )
        assert r1["ok"] is True
        assert r1["upserted"] is False  # 首插

        got = dealer_enrichment.get_enrichment(did)
        assert got is not None
        assert got["market"] == "US"
        assert got["product_family_fit"] == {"lens": 0.9}
        assert got["conversion_proxy"] == 0.8

        # 部分重录:不给 market / family_fit → COALESCE + NULLIF 保留旧值,response_rate 刷新
        r2 = dealer_enrichment.record_enrichment(did, response_rate=0.95)
        assert r2["upserted"] is True
        got2 = dealer_enrichment.get_enrichment(did)
        assert got2["market"] == "US"  # COALESCE 保留
        assert got2["product_family_fit"] == {"lens": 0.9}  # NULLIF 空对象保留
        assert got2["response_rate"] == 0.95  # 刷新

        listed = dealer_enrichment.list_enriched(market="US", family="lens")
        assert any(row["dealer_id"] == did for row in listed)
        m = dealer_enrichment.get_enrichment_map([did])
        assert m[did]["market"] == "US"
    finally:
        conn.execute(
            "DELETE FROM vkpi_dealer_enrichment WHERE dealer_id = ?", (did,)
        )
        conn.commit()
