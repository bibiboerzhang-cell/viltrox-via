"""特征快照 v2:≤20 派生强特征、缺料 None、数值向量有界、非空率统计、v1 快照仍可读回。"""
from __future__ import annotations

import json
from typing import Any

from app.domains.recommendations import feature_store, feature_store_derived as fd


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Conn:
    """按表名路由;``missing`` 里的表抛错(模拟列缺/表缺),验证回滚 + None。"""

    def __init__(self, *, missing: set[str] | None = None):
        self.missing = missing or set()
        self.rollbacks = 0

    def execute(self, sql: str, params: tuple = ()):
        for name in self.missing:
            if name in sql:
                raise RuntimeError(f"relation {name} broken")
        if "vkpi_kol_profile_deep" in sql:
            return _Cursor([{"dimensions_11_json": json.dumps({
                "overall_score": 71, "block2_performance": {"engagement_quality_score": 64},
                "block3_business": {"cooperation_history_score": 20, "competitor_risk_score": 35},
            })}])
        if "vkpi_kol_llm_deep_analysis_results" in sql:
            return _Cursor([
                {"llm_dimensions_11": json.dumps({"scores": {"content_quality_score": {"score": 88}, "marketing_value_score": {"score": 80},
                                                             "product_proof_score": {"score": 70}, "viewer_heart_score": {"score": 60},
                                                             "channel_value_score": {"score": 10}},
                                                  "emotion_tags_v1": {"valence": "positive"}})},
                {"llm_dimensions_11": json.dumps({"scores": {"content_quality_score": {"score": 68}, "marketing_value_score": {"score": 60}},
                                                  "emotion_tags_v1": {"valence": "negative"}})},
            ])
        if "vkpi_kol_lens_evidence" in sql:
            return _Cursor([{"n": 3}])
        if "vkpi_kol_rates" in sql:
            return _Cursor([{"amount_usd": 500}, {"amount_usd": 900}, {"amount_usd": 0}])
        if "vkpi_kol_cooperation_events" in sql:
            return _Cursor([{"n": 2}])
        if "vkpi_kol_video_product_links" in sql:
            return _Cursor([{"n": 4}])
        if "FROM vkpi_kol_pool WHERE id=?" in sql:
            return _Cursor([{"real_er": 0.034, "suspect_inflation": 1,
                             "audience_estimated_json": json.dumps({"top_countries": [{"code": "US", "pct": 71.7}], "confidence": 0.76})}])
        return _Cursor([])

    def rollback(self):
        self.rollbacks += 1


def _wire(monkeypatch, conn):
    monkeypatch.setattr(fd, "get_conn", lambda: conn)
    monkeypatch.setattr(fd, "table_exists", lambda name: True)


def test_keys_bounded_and_schema_version_bumped() -> None:
    assert len(fd.DERIVED_FEATURE_KEYS) <= 20 and len(set(fd.DERIVED_FEATURE_KEYS)) == len(fd.DERIVED_FEATURE_KEYS)
    assert all(k.startswith("d_") for k in fd.DERIVED_FEATURE_KEYS)
    assert feature_store.FEATURE_SNAPSHOT_SCHEMA_VERSION.endswith("_v2")
    assert "vkpi_kol_feature_snapshot_v1" in feature_store._COMPATIBLE_SCHEMA_VERSIONS
    names = feature_store.list_feature_names()
    assert "derived.d_real_er" in names and "followers" in names
    assert len(feature_store.list_feature_names(include_derived=False)) == 12


def test_derived_features_from_all_sources(monkeypatch) -> None:
    conn = _Conn()
    _wire(monkeypatch, conn)
    out = fd.derived_features(5)
    assert set(out) == set(fd.DERIVED_FEATURE_KEYS)
    assert out["d_rule11_overall"] == 71 and out["d_rule11_competitor_risk"] == 35
    assert out["d_has_final_v1"] == 1.0 and out["d_final_v1_videos"] == 2.0
    assert out["d_final_v1_content_quality"] == 78.0 and out["d_final_v1_channel_value"] == 10.0  # 缺分只算有分的视频
    assert out["d_emotion_positive_share"] == 0.5
    assert out["d_lens_family_count"] == 3.0 and out["d_rate_median_usd"] == 700.0
    assert out["d_cooperation_events"] == 2.0 and out["d_video_product_links"] == 4.0
    assert out["d_real_er"] == 0.034 and out["d_suspect_inflation"] == 1.0
    assert out["d_audience_top_country_pct"] == 71.7 and out["d_audience_geo_confidence"] == 0.76
    assert all(v is not None for v in out.values())


def test_missing_sources_are_none_not_zero(monkeypatch) -> None:
    conn = _Conn(missing={"vkpi_kol_profile_deep", "vkpi_kol_lens_evidence", "vkpi_kol_rates"})
    _wire(monkeypatch, conn)
    out = fd.derived_features(5, pool_row={"real_er": None, "suspect_inflation": None, "audience_estimated_json": "{}"})
    assert out["d_rule11_overall"] is None and out["d_lens_family_count"] is None and out["d_rate_median_usd"] is None
    assert out["d_real_er"] is None and out["d_suspect_inflation"] is None and out["d_audience_geo_confidence"] is None
    assert out["d_has_final_v1"] == 1.0  # 其余来源照常
    assert conn.rollbacks == 3
    assert fd.derived_features(0) == {k: None for k in fd.DERIVED_FEATURE_KEYS}


def test_numeric_vector_is_bounded_and_ordered(monkeypatch) -> None:
    conn = _Conn()
    _wire(monkeypatch, conn)
    vec = fd.derived_numeric_vector(fd.derived_features(5))
    assert list(vec) == list(fd.DERIVED_FEATURE_KEYS)
    assert vec["d_rule11_overall"] == 0.71 and vec["d_audience_top_country_pct"] == 0.717
    assert 0.0 <= vec["d_rate_median_usd"] <= 1.0 and vec["d_final_v1_videos"] > 0
    assert fd.derived_numeric_vector(None)["d_real_er"] == 0.0


def test_feature_coverage_reports_nonnull_rates(monkeypatch) -> None:
    conn = _Conn(missing={"vkpi_kol_rates"})
    _wire(monkeypatch, conn)
    out = fd.feature_coverage(kol_pool_ids=[1, 2, 3])
    assert out["status"] == "ok" and out["sample_n"] == 3 and out["feature_count"] == len(fd.DERIVED_FEATURE_KEYS)
    assert out["nonnull_rate"]["d_rate_median_usd"] == 0.0 and out["nonnull_rate"]["d_real_er"] == 1.0
    assert "d_rate_median_usd" not in out["eligible_features"] and "d_real_er" in out["eligible_features"]
    assert fd.feature_coverage(kol_pool_ids=[], sample_limit=0)["status"] == "empty"


def test_snapshot_features_attaches_derived(monkeypatch) -> None:
    class _SnapConn(_Conn):
        def execute(self, sql: str, params: tuple = ()):
            if "FROM vkpi_kol_pool WHERE id=?" in sql and "real_er" not in sql:
                return _Cursor([{"id": 5, "platform": "youtube", "handle": "h", "followers": 10, "posts_count": 1, "avg_views": 5,
                                 "avg_likes": 1, "avg_comments": 0, "engagement_rate": 0.1, "primary_topic": "t", "sync_status": "ok",
                                 "source_type": "x", "real_er": 0.02, "suspect_inflation": 0,
                                 "audience_estimated_json": json.dumps({"confidence": 0.5})}])
            return super().execute(sql, params)

    conn = _SnapConn()
    monkeypatch.setattr(feature_store, "get_conn", lambda: conn)
    monkeypatch.setattr(feature_store, "ensure_vkpi_product_industry_schema", lambda: None)
    _wire(monkeypatch, conn)
    snap = feature_store.snapshot_features(kol_pool_id=5)
    assert snap["feature_schema_version"] == feature_store.FEATURE_SNAPSHOT_SCHEMA_VERSION
    assert snap["derived_feature_version"] == fd.DERIVED_FEATURE_VERSION
    assert snap["derived"]["d_real_er"] == 0.02 and snap["derived"]["d_audience_geo_confidence"] == 0.5
    assert snap["followers"] == 10  # v1 浅特征原样保留


def test_get_features_at_time_accepts_v1_and_v2_snapshots(monkeypatch) -> None:
    base = {"platform": "youtube", "followers": 1, "posts_count": 1, "avg_views": 1, "avg_likes": 1, "avg_comments": 1,
            "engagement_rate": 0.1, "primary_topic": "t", "sync_status": "ok", "kol_pool_id": 5}
    rows = [
        {"id": 2, "launch_id": None, "created_at": "2026-08-02T00:00:00Z",
         "feature_snapshot_json": json.dumps({**base, "feature_schema_version": "vkpi_kol_feature_snapshot_v2", "snapshot_at": "2026-08-02T00:00:00Z",
                                              "derived": {"d_real_er": 0.1}, "derived_feature_version": fd.DERIVED_FEATURE_VERSION})},
        {"id": 1, "launch_id": None, "created_at": "2026-08-01T00:00:00Z",
         "feature_snapshot_json": json.dumps({**base, "feature_schema_version": "vkpi_kol_feature_snapshot_v1", "snapshot_at": "2026-08-01T00:00:00Z"})},
    ]

    class _HistConn:
        def execute(self, sql: str, params: tuple = ()):
            return _Cursor(rows)

    monkeypatch.setattr(feature_store, "get_conn", lambda: _HistConn())
    newest = feature_store.get_features_at_time(5, "2026-08-03T00:00:00Z")
    assert newest["_point_in_time"]["source_row_id"] == 2 and newest["derived"] == {"d_real_er": 0.1}
    older = feature_store.get_features_at_time(5, "2026-08-01T12:00:00Z")
    assert older["_point_in_time"]["source_row_id"] == 1 and older["_point_in_time"]["feature_schema_version"] == "vkpi_kol_feature_snapshot_v1"
    assert "derived" not in older
