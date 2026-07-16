from __future__ import annotations

from app.domains.market import hashtag_trends


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, post_rows=None, observation_rows=None):
        self.post_rows = post_rows or []
        self.observation_rows = observation_rows or []

    def execute(self, query, params=()):
        del params
        if "FROM vkpi_industry_posts" in query:
            return _Rows(self.post_rows)
        if "FROM vkpi_market_observations" in query:
            return _Rows(self.observation_rows)
        raise AssertionError(query)


def _install(monkeypatch, *, posts=None, observations=None, tables=None):
    conn = _Conn(posts, observations)
    available = set(tables or {"vkpi_industry_posts", "vkpi_market_observations"})
    monkeypatch.setattr(hashtag_trends, "get_conn", lambda: conn)
    monkeypatch.setattr(hashtag_trends, "table_exists", lambda name: name in available)


def test_trends_merge_same_term_across_sources_without_duplicate_cards(monkeypatch):
    _install(
        monkeypatch,
        posts=[{
            "id": 1,
            "platform": "youtube",
            "hashtags_json": '["#Sony", "#sony", "#35mm"]',
            "views": 100,
            "likes": 10,
            "comments": 2,
            "post_url": "https://example.test/post",
            "observed_at": "2026-07-16T10:00:00Z",
        }],
        observations=[{
            "id": 9,
            "topic": "Sony 35mm f1.4 mirrorless launch",
            "suggested_action": "Compare Sigma lens pricing",
            "kind": "竞品",
            "source": "competitor_radar",
            "evidence_refs": '[{"source_url":"https://example.test/evidence"}]',
            "observed_at": "2026-07-16T11:00:00Z",
        }],
    )

    result = hashtag_trends.build_hashtag_trends_v0(limit=20, days=14)

    sony = next(item for item in result["trends"] if item["hashtag"] == "sony")
    assert sony["evidence_count"] == 2
    assert sony["source_count"] == 2
    assert sony["sources"] == ["industry_posts", "market_observations"]
    assert sony["engagement"] == 112
    assert result["summary"]["unique_terms"] == len(result["trends"])
    assert result["summary"]["source_rows"] == {"industry_posts": 1, "market_observations": 1}
    assert result["summary"]["source"] == "multi_source"
    assert result["summary"]["source_label"] == "行业帖 + 市场观测"


def test_platform_filter_does_not_mix_cross_platform_observations(monkeypatch):
    _install(
        monkeypatch,
        posts=[{
            "id": 2,
            "platform": "instagram",
            "hashtags_json": '["#viltrox"]',
            "views": 0,
            "likes": 2,
            "comments": 0,
            "post_url": "",
            "observed_at": "2026-07-15T10:00:00Z",
        }],
        observations=[{
            "id": 3,
            "topic": "Canon mirrorless launch",
            "suggested_action": "",
            "kind": "竞品",
            "source": "competitor_radar",
            "evidence_refs": "[]",
            "observed_at": "2026-07-15T11:00:00Z",
        }],
    )

    result = hashtag_trends.build_hashtag_trends_v0(platform="instagram")

    assert [item["hashtag"] for item in result["trends"]] == ["viltrox"]
    assert result["summary"]["source_rows"]["market_observations"] == 0
    assert result["summary"]["source_label"] == "行业帖"


def test_trends_truthfully_return_empty_when_sources_have_no_terms(monkeypatch):
    _install(monkeypatch, posts=[], observations=[])

    result = hashtag_trends.build_hashtag_trends_v0()

    assert result["trends"] == []
    assert result["summary"]["status"] == "empty"
    assert result["summary"]["claim_status"] == "descriptive_only"
    assert result["summary"]["source"] == "none"
    assert result["summary"]["source_label"] == "暂无来源"


def test_missing_optional_tables_are_safe(monkeypatch):
    _install(monkeypatch, tables=set())

    result = hashtag_trends.build_hashtag_trends_v0()

    assert result["summary"]["source_rows"] == {
        "industry_posts": 0,
        "market_observations": 0,
    }
