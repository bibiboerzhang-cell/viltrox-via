from __future__ import annotations

from app.db.connection import PostgresCompatCursor
from app.domains.kol import performance_forecast, rate_card
from app.domains.market import strategy_sim
from app.domains.market_brain import gtm_candidate_batch
from app.domains.products import sku_performance, sku_performance_aggregate_rows


class _RawCursor:
    def __init__(self) -> None:
        self.description_reads = 0

    @property
    def description(self):
        self.description_reads += 1
        return [("id",), ("label",)]

    def fetchall(self):
        return [(1, "first"), (2, "second"), (3, "third")]


def test_postgres_compat_fetchall_resolves_projection_once() -> None:
    raw = _RawCursor()
    rows = PostgresCompatCursor(raw).fetchall()

    assert raw.description_reads == 1
    assert [(row["id"], row["label"]) for row in rows] == [
        (1, "first"),
        (2, "second"),
        (3, "third"),
    ]


def _evidence(evidence_id: int, title: str, product_presence: str = "") -> dict:
    return {
        "evidence_id": evidence_id,
        "kol_pool_id": evidence_id + 100,
        "title": title,
        "content_url": f"https://example.test/{evidence_id}",
        "platform": "youtube",
        "posted_at": "2026-07-01",
        "publish_date": "2026-07-01",
        "view_count": evidence_id * 100,
        "like_count": evidence_id * 10,
        "comment_count": evidence_id,
        "handle": f"creator-{evidence_id}",
        "display_name": f"Creator {evidence_id}",
        "kol_platform": "youtube",
        "product_presence": product_presence,
        "brand_exposure": "",
        "content_summary": "",
        "marketing_value_raw": None,
    }


def test_multi_sku_aggregate_projection_matches_single_sku_reader(monkeypatch) -> None:
    products = {
        "sku-50": {"sku": "sku-50", "model_name": "Viltrox 50mm"},
        "sku-85": {"sku": "sku-85", "model_name": "Viltrox 85mm"},
    }
    aliases = {
        "sku-50": [{"alias": "50mm", "alias_norm": "50mm", "confidence": 1.0}],
        "sku-85": [{"alias": "85mm", "alias_norm": "85mm", "confidence": 1.0}],
    }
    deep_rows = [
        _evidence(1, "50mm review", '{"products":["50mm"]}'),
        _evidence(2, "85mm review", '{"products":["85mm"]}'),
    ]
    title_rows = [
        _evidence(1, "50mm review"),  # deep row wins and is de-duplicated
        _evidence(2, "85mm review"),
        _evidence(3, "street test with 50mm"),
        _evidence(4, "portrait test with 85mm"),
    ]
    calls = {"deep": 0, "title": 0}

    monkeypatch.setattr(sku_performance, "resolve_sku", lambda sku: products.get(sku))
    monkeypatch.setattr(
        sku_performance,
        "_aliases_for",
        lambda product: aliases[str(product["sku"])],
    )
    monkeypatch.setattr("app.db.connection.get_conn", lambda: object())

    def load_deep(_conn):
        calls["deep"] += 1
        return [dict(row) for row in deep_rows]

    def load_titles(_conn):
        calls["title"] += 1
        return [dict(row) for row in title_rows]

    monkeypatch.setattr(sku_performance, "_deep_rows", load_deep)
    monkeypatch.setattr(sku_performance, "_title_rows", load_titles)
    monkeypatch.setattr(sku_performance_aggregate_rows, "load_deep_rows", load_deep)
    monkeypatch.setattr(sku_performance_aggregate_rows, "load_title_rows", load_titles)
    monkeypatch.setattr(sku_performance, "_content_fit_matches", lambda _conn, _sku: [])

    expected = {
        sku: sku_performance._content_performance(product, aliases[sku])["aggregate"]
        for sku, product in products.items()
    }
    calls.update(deep=0, title=0)

    actual = sku_performance.sku_content_aggregate_briefs(list(products))

    assert actual == expected
    assert calls == {"deep": 1, "title": 1}


def test_multi_sku_deep_projection_normalizes_each_field_once(monkeypatch) -> None:
    products = {
        "sku-50": {"sku": "sku-50", "model_name": "Viltrox 50mm"},
        "sku-85": {"sku": "sku-85", "model_name": "Viltrox 85mm"},
    }
    aliases = {
        "sku-50": [{"alias": "50mm", "alias_norm": "50mm", "confidence": 1.0}],
        "sku-85": [{"alias": "85mm", "alias_norm": "85mm", "confidence": 1.0}],
    }
    deep = _evidence(10, "unrelated title", '{"notes":"unrelated presence"}')
    deep.update(brand_exposure="unrelated brand", content_summary="unrelated summary")
    normalized_values: list[str] = []
    real_norm = sku_performance._norm

    monkeypatch.setattr(sku_performance, "resolve_sku", lambda sku: products.get(sku))
    monkeypatch.setattr(sku_performance, "_aliases_for", lambda product: aliases[str(product["sku"])])
    monkeypatch.setattr("app.db.connection.get_conn", lambda: object())
    monkeypatch.setattr(sku_performance_aggregate_rows, "load_deep_rows", lambda _conn: [deep])
    monkeypatch.setattr(sku_performance_aggregate_rows, "load_title_rows", lambda _conn: [])

    def counted_norm(value):
        normalized_values.append(str(value))
        return real_norm(value)

    monkeypatch.setattr(sku_performance, "_norm", counted_norm)

    result = sku_performance.sku_content_aggregate_briefs(list(products))

    assert result == {sku: sku_performance._aggregate([]) for sku in products}
    assert normalized_values == [
        "unrelated presence",
        "unrelated title",
        "unrelated brand",
        "unrelated summary",
    ]


def test_gtm_alias_fast_path_is_semantically_equal_to_canonical_matcher() -> None:
    aliases = [
        {"alias": "85mm f18", "alias_norm": "85mm f18", "confidence": 1.0},
        {"alias": "85mm", "alias_norm": "85mm", "confidence": 0.9},
    ]
    matcher = sku_performance._AliasMatcher(aliases)
    texts = [
        "",
        "85mm f18",
        "camera 85mm f18 review",
        "x85mm f18",
        "85mm f180",
        "x85mm and 85mm later",
        "an 85mm only review",
        "unrelated 50mm f18",
    ]

    for text in texts:
        assert sku_performance_aggregate_rows.match_alias(matcher, text) == matcher.match(text)


def test_gtm_deep_projection_keeps_only_aggregate_and_match_fields() -> None:
    conn = _ScriptedConn(lambda _sql, _params: [])

    sku_performance_aggregate_rows.load_deep_rows(conn)

    sql = conn.calls[0][0]
    assert "ac.result #>> '{layer1_visual_content,product_presence}' AS product_presence" in sql
    assert "ac.result #>> '{layer1_visual_content,brand_exposure}' AS brand_exposure" in sql
    assert "ac.result #>> '{layer1_visual_content,content_summary}' AS content_summary" in sql
    assert "e.content_url AS content_url" not in sql
    assert "e.publish_date" not in sql
    assert "e.posted_at" not in sql
    assert "marketing_value" not in sql


def test_multi_sku_aggregate_projection_keeps_not_found_and_empty_state(monkeypatch) -> None:
    monkeypatch.setattr(
        sku_performance,
        "resolve_sku",
        lambda sku: {"sku": sku, "model_name": "No matching content"} if sku == "empty" else None,
    )
    monkeypatch.setattr(sku_performance, "_aliases_for", lambda _product: [])

    result = sku_performance.sku_content_aggregate_briefs(["missing", "empty", "empty"])

    assert result["missing"] is None
    assert result["empty"] == sku_performance._aggregate([])


class _RowsResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _ScriptedConn:
    def __init__(self, handler):
        self._handler = handler
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        bound = tuple(params)
        self.calls.append((normalized, bound))
        return _RowsResult(self._handler(normalized, bound))


def test_rate_card_batch_loads_once_and_reuses_single_item_contract() -> None:
    def rows(sql, _params):
        if "FROM vkpi_kol_pool WHERE id IN" in sql:
            return [
                {
                    "id": 1,
                    "handle": "one",
                    "display_name": "One",
                    "platform": "youtube",
                    "followers": 100_000,
                    "avg_views": 20_000,
                },
                {
                    "id": 2,
                    "handle": "two",
                    "display_name": "Two",
                    "platform": "instagram",
                    "followers": 50_000,
                    "avg_views": 10_000,
                },
            ]
        if "FROM vkpi_kol_rates" in sql:
            return [
                {
                    "id": 10,
                    "kol_pool_id": 1,
                    "platform": "youtube",
                    "content_type": "video",
                    "amount_usd": 900,
                    "currency": "USD",
                    "source": "contract",
                    "confidence": "high",
                    "note": "",
                    "effective_date": None,
                    "created_at": "2026-08-01T00:00:00Z",
                    "batch_rank": 1,
                }
            ]
        if "AVG(view_count) AS av" in sql:
            return []
        raise AssertionError(sql)

    conn = _ScriptedConn(rows)
    result = rate_card.estimate_rates([1, 2, 1], conn=conn)

    assert len(conn.calls) == 3
    assert result[1]["status"] == "ready"
    assert result[1]["method"] == "recorded_rates_median_v0"
    assert result[1]["estimated_usd_p50"] == 900.0
    assert result[2]["status"] == "ready"
    assert result[2]["method"] == "cpm_benchmark_v0"
    assert result[2]["basis"]["views_used"] == 10_000
    assert rate_card._BATCH_READS.get() is None


def test_rate_card_batch_sql_failure_falls_back_to_single_item(monkeypatch) -> None:
    conn = _ScriptedConn(lambda _sql, _params: (_ for _ in ()).throw(RuntimeError("batch unavailable")))
    calls = []
    monkeypatch.setattr(
        rate_card,
        "estimate_rate",
        lambda kol_id, *, conn=None: calls.append((kol_id, conn))
        or {"status": "ready", "kol_pool_id": kol_id},
    )

    result = rate_card.estimate_rates([1, 2], conn=conn)

    assert result == {
        1: {"status": "ready", "kol_pool_id": 1},
        2: {"status": "ready", "kol_pool_id": 2},
    }
    assert calls == [(1, conn), (2, conn)]


def test_forecast_batch_loads_once_and_never_writes(monkeypatch) -> None:
    def rows(sql, _params):
        if "FROM vkpi_kol_pool WHERE id IN" in sql:
            return [
                {"id": 1, "handle": "one", "display_name": "One", "platform": "youtube"},
                {"id": 2, "handle": "two", "display_name": "Two", "platform": "instagram"},
            ]
        if "FROM vkpi_analysis_cache" in sql:
            return []
        if "FROM vkpi_kol_video_evidence" in sql:
            return [
                {
                    "evidence_id": 11,
                    "kol_pool_id": 1,
                    "title": "first",
                    "content_url": "https://example.test/11",
                    "platform": "youtube",
                    "view_count": 100,
                    "like_count": 10,
                    "comment_count": 1,
                    "posted_at": "2026-08-01T00:00:00Z",
                    "is_active": True,
                    "batch_rank": 1,
                },
                {
                    "evidence_id": 12,
                    "kol_pool_id": 1,
                    "title": "second",
                    "content_url": "https://example.test/12",
                    "platform": "youtube",
                    "view_count": 300,
                    "like_count": 20,
                    "comment_count": 2,
                    "posted_at": "2026-08-02T00:00:00Z",
                    "is_active": True,
                    "batch_rank": 2,
                },
                {
                    "evidence_id": 21,
                    "kol_pool_id": 2,
                    "title": "third",
                    "content_url": "https://example.test/21",
                    "platform": "instagram",
                    "view_count": 900,
                    "like_count": 90,
                    "comment_count": 9,
                    "posted_at": "2026-08-03T00:00:00Z",
                    "is_active": True,
                    "batch_rank": 1,
                },
            ]
        if "FROM vkpi_products" in sql:
            return [
                {
                    "sku": "AF-50",
                    "series": "Air",
                    "category_main": "lens",
                    "category_detail": "prime",
                    "model_name": "50mm",
                    "marketing_name": "Air 50",
                    "price_usd": 199,
                    "status": "active",
                }
            ]
        raise AssertionError(sql)

    conn = _ScriptedConn(rows)
    monkeypatch.setattr(performance_forecast, "_focal_tools", lambda: None)

    result = performance_forecast.forecast_for_kols(
        [None, "bad-id", 1, 2, 1], sku="AF-50", conn=conn, context="sim", dry_run=True,
    )

    assert len(conn.calls) == 4
    assert not any("INSERT" in sql.upper() for sql, _params in conn.calls)
    assert result[1]["status"] == "ready"
    assert result[1]["expected_views_p50"] == 200
    assert result[2]["status"] == "ready"
    assert result[2]["expected_views_p50"] == 900
    assert result[1]["sku_adjustment"]["status"] == "unavailable"
    assert performance_forecast._BATCH_READS.get() is None


def test_gtm_candidate_adapter_uses_each_batch_engine_once(monkeypatch) -> None:
    calls = {"rates": 0, "forecasts": 0, "single_rate": 0, "single_forecast": 0}

    class RateEngine:
        @staticmethod
        def estimate_rates(ids, *, conn=None):
            calls["rates"] += 1
            return {
                int(kol_id): {
                    "status": "ready",
                    "estimated_usd_p50": 100 + int(kol_id),
                    "estimated_usd_low": 80,
                    "estimated_usd_high": 140,
                    "method": "fixture",
                    "confidence": "medium",
                }
                for kol_id in ids
            }

        @staticmethod
        def estimate_rate(_kol_id, *, conn=None):
            calls["single_rate"] += 1
            raise AssertionError("batch result should satisfy every candidate")

        @staticmethod
        def _tier_for_followers(_followers):
            return "micro"

    class ForecastEngine:
        @staticmethod
        def forecast_for_kols(ids, sku=None, *, conn=None, context="drawer", dry_run=False):
            calls["forecasts"] += 1
            assert context == "sim"
            assert dry_run is True
            return {
                int(kol_id): {
                    "status": "ready",
                    "expected_views_p10": 100,
                    "expected_views_p50": 200 + int(kol_id),
                    "expected_views_p90": 300,
                    "confidence": "medium",
                }
                for kol_id in ids
            }

        @staticmethod
        def forecast_for_kol(_kol_id, sku=None, *, conn=None, **_kwargs):
            calls["single_forecast"] += 1
            raise AssertionError("batch result should satisfy every candidate")

    def db_rows(sql, _params):
        if "SELECT id, followers, avg_views, platform FROM vkpi_kol_pool" in sql:
            return [
                {"id": 1, "followers": 50_000, "avg_views": 10_000, "platform": "youtube"},
                {"id": 2, "followers": 75_000, "avg_views": 15_000, "platform": "instagram"},
            ]
        raise AssertionError(sql)

    conn = _ScriptedConn(db_rows)
    monkeypatch.setattr(
        strategy_sim,
        "_load_engines",
        lambda: (RateEngine, ForecastEngine, object(), ""),
    )
    monkeypatch.setattr("app.db.connection.get_conn", lambda: conn)
    candidates, _roster, missing = gtm_candidate_batch.build_candidates(
        "AF-50",
        [
            {"kol_pool_id": 1, "handle": "one", "platform": "youtube", "score": 90},
            {"kol_pool_id": 2, "handle": "two", "platform": "instagram", "score": 80},
        ],
    )

    assert missing == ""
    assert len(candidates) == 2
    assert calls == {"rates": 1, "forecasts": 1, "single_rate": 0, "single_forecast": 0}
    assert [candidate["views_p50"] for candidate in candidates] == [201.0, 202.0]


def test_gtm_candidate_adapter_falls_back_per_item_when_batches_raise(monkeypatch) -> None:
    calls = {"single_rate": 0, "single_forecast": 0}

    class RateEngine:
        @staticmethod
        def estimate_rates(_ids, *, conn=None):
            raise RuntimeError("batch rate unavailable")

        @staticmethod
        def estimate_rate(kol_id, *, conn=None):
            calls["single_rate"] += 1
            return {"status": "ready", "estimated_usd_p50": 100 + int(kol_id)}

        @staticmethod
        def _tier_for_followers(_followers):
            return "micro"

    class ForecastEngine:
        @staticmethod
        def forecast_for_kols(_ids, sku=None, *, conn=None, context="drawer", dry_run=False):
            raise RuntimeError("batch forecast unavailable")

        @staticmethod
        def forecast_for_kol(kol_id, sku=None, *, conn=None, **_kwargs):
            calls["single_forecast"] += 1
            return {"status": "ready", "expected_views_p50": 200 + int(kol_id)}

    conn = _ScriptedConn(
        lambda sql, _params: [
            {"id": 1, "followers": 50_000, "avg_views": 10_000, "platform": "youtube"},
            {"id": 2, "followers": 75_000, "avg_views": 15_000, "platform": "instagram"},
        ]
        if "SELECT id, followers, avg_views, platform FROM vkpi_kol_pool" in sql
        else (_ for _ in ()).throw(AssertionError(sql))
    )
    monkeypatch.setattr(
        strategy_sim,
        "_load_engines",
        lambda: (RateEngine, ForecastEngine, object(), ""),
    )
    monkeypatch.setattr("app.db.connection.get_conn", lambda: conn)

    candidates, _roster, _missing = gtm_candidate_batch.build_candidates(
        "AF-50",
        [
            {"kol_pool_id": 1, "handle": "one", "platform": "youtube"},
            {"kol_pool_id": 2, "handle": "two", "platform": "instagram"},
        ],
    )

    assert len(candidates) == 2
    assert calls == {"single_rate": 2, "single_forecast": 2}
