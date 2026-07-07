"""F1 件⑤ golden:roster_optimizer 缺地理候选按 KOL 独立 unknown 桶,不互相折减。

fake conn 喂固定行,断言:
- 两个 country 为空的同平台候选不再共享 UNKNOWN 覆盖单元:第二人的边际触达
  = 全额 base_reach(修复前按 geo_proxy 同平台 30 个点互扣);
- 展示层统一折回 UNKNOWN:by_geo / geo_top / top_new_units 不泄漏 unknown:{id};
- payload 键结构不变(selected/dropped_overlap/coverage/basis/confidence/budget)。
"""
from app.domains.kol import roster_optimizer as ro


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        for key, rows in self.routes.items():
            if key in sql:
                return _Result(rows)
        return _Result([])


def _pool_row(kid, followers, country=""):
    return {
        "id": kid, "platform": "youtube", "handle": f"kol{kid}",
        "display_name": f"KOL {kid}", "followers": followers, "avg_views": None,
        "country": country, "audience_estimated_json": None,
    }


def _run(monkeypatch, pool_rows, ids, max_size=3):
    conn = _Conn({"FROM vkpi_kol_pool": pool_rows})
    monkeypatch.setattr("app.db.connection.get_conn", lambda: conn)
    # 报价段(件B)与本修复无关,打桩为空态,防 fake conn 干扰
    monkeypatch.setattr(
        "app.domains.kol.rate_card.estimate_rate",
        lambda kid: {"status": "empty", "reason": "stubbed in test"},
        raising=False,
    )
    return ro.optimize_roster(ids, max_size=max_size)


def test_unknown_geo_candidates_do_not_discount_each_other(monkeypatch):
    # 两个缺地理 + 同平台:修复前第二人被按 UNKNOWN 同桶折减 30 个点
    payload = _run(monkeypatch, [_pool_row(1, 1000), _pool_row(2, 500)], [1, 2], max_size=2)

    assert payload["status"] == "ok"
    selected = {e["kol_pool_id"]: e for e in payload["selected"]}
    assert set(selected) == {1, 2}
    # 边际 = 全额 base(不互相折减)
    assert selected[1]["marginal_reach"] == 1000.0
    assert selected[2]["marginal_reach"] == 500.0
    assert selected[2]["marginal_reach"] == selected[2]["base_reach"]
    # 总去重触达 = 两人加和,dedup_saved 为 0
    assert payload["coverage"]["total_dedup_reach"] == 1500.0
    assert payload["coverage"]["dedup_saved_pct"] == 0.0


def test_unknown_buckets_display_normalized(monkeypatch):
    payload = _run(
        monkeypatch,
        [_pool_row(1, 1000), _pool_row(2, 500), _pool_row(3, 800, country="美国")],
        [1, 2, 3],
    )

    by_geo = payload["coverage"]["by_geo"]
    buckets = [g["bucket"] for g in by_geo]
    # 展示层折回 UNKNOWN(聚合两人),不泄漏 unknown:{kol_id}
    assert "UNKNOWN" in buckets
    assert all(":" not in b for b in buckets)
    unknown_entry = next(g for g in by_geo if g["bucket"] == "UNKNOWN")
    assert unknown_entry["reach"] == 1500.0
    assert "US" in buckets
    # unknown_geo_pct 吃聚合后的 UNKNOWN 桶
    assert payload["coverage"]["unknown_geo_pct"] == unknown_entry["pct"]

    for entry in payload["selected"]:
        for bucket, _pct in entry["geo_top"]:
            assert ":" not in str(bucket)
        assert "unknown:" not in entry["reason"]

    # 键结构不变(strategy_sim / launch_assembly 在吃)
    for key in ("status", "method", "max_size", "selected", "dropped_overlap",
                "coverage", "basis", "confidence", "note", "budget"):
        assert key in payload
    for key in ("total_dedup_reach", "raw_reach_sum", "dedup_saved_pct",
                "by_geo", "by_platform", "unknown_geo_pct"):
        assert key in payload["coverage"]
    entry = payload["selected"][0]
    for key in ("rank", "kol_pool_id", "handle", "display_name", "platform",
                "marginal_reach", "base_reach", "reach_basis", "geo_source", "geo_top", "reason"):
        assert key in entry


def test_known_geo_same_platform_still_discounts(monkeypatch):
    # 回归护栏:真同地理(都是美国)+ 同平台仍按 geo_proxy 折减,修复只豁免 unknown
    payload = _run(
        monkeypatch,
        [_pool_row(1, 1000, country="美国"), _pool_row(2, 500, country="美国")],
        [1, 2],
        max_size=2,
    )
    selected = {e["kol_pool_id"]: e for e in payload["selected"]}
    # 第二人被折减:同平台同地理 proxy 0.30 → 500 × 0.7 = 350
    assert selected[2]["marginal_reach"] == 350.0
    assert payload["coverage"]["total_dedup_reach"] == 1350.0
