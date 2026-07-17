from __future__ import annotations

from app.domains.market import hashtag_trends


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, post_rows=None, observation_rows=None, mention_rows=None):
        self.post_rows = post_rows or []
        self.observation_rows = observation_rows or []
        self.mention_rows = mention_rows or []

    def execute(self, query, params=()):
        del params
        if "FROM vkpi_industry_posts" in query:
            return _Rows(self.post_rows)
        if "FROM vkpi_market_observations" in query:
            return _Rows(self.observation_rows)
        if "FROM vkpi_market_mentions" in query:
            return _Rows(self.mention_rows)
        raise AssertionError(query)


def _install(monkeypatch, *, posts=None, observations=None, mentions=None, tables=None):
    conn = _Conn(posts, observations, mentions)
    available = set(
        tables
        or {
            "vkpi_industry_posts",
            "vkpi_market_observations",
            "vkpi_market_mentions",
            "vkpi_market_sources",
        }
    )
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
    assert result["summary"]["source_rows"] == {
        "industry_posts": 1,
        "market_observations": 1,
        "market_mentions": 0,
    }
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
        "market_mentions": 0,
    }


def test_listening_mentions_feed_trends_with_real_engagement_and_platform_filter(monkeypatch):
    """市场监听帖(Reddit/X 落表)出词:词表命中 + metadata 互动数 + 平台过滤同口径。"""
    _install(
        monkeypatch,
        posts=[],
        observations=[],
        mentions=[
            {
                "id": 101,
                "platform": "reddit",
                "mention_text": "Viltrox 85mm f/1.8 vs Sigma on Sony bodies · long term review",
                "metadata_json": '{"origin":"listening","url":"https://www.reddit.com/r/SonyAlpha/comments/abc/","score":57,"num_comments":14,"published_at":"2026-07-15T09:00:00Z"}',
                "created_at": "2026-07-15T10:00:00Z",
                "source_url": "https://www.reddit.com/r/SonyAlpha/comments/abc/",
            },
            {
                "id": 102,
                "platform": "x",
                "mention_text": "The new Viltrox lab lens is wild",
                "metadata_json": '{"origin":"listening","likes":30,"retweets":5,"replies":2,"published_at":"2026-07-15T11:00:00Z"}',
                "created_at": "2026-07-15T11:05:00Z",
                "source_url": "",
            },
        ],
    )

    result = hashtag_trends.build_hashtag_trends_v0(limit=20, days=14)

    viltrox = next(item for item in result["trends"] if item["hashtag"] == "viltrox")
    assert viltrox["evidence_count"] == 2
    assert viltrox["sources"] == ["market_mentions"]
    assert viltrox["engagement"] == (57 + 14) + (30 + 5 + 2)
    assert set(viltrox["platforms"]) == {"reddit", "x"}
    assert result["summary"]["source_rows"]["market_mentions"] == 2
    assert result["summary"]["source_label"] == "市场监听"
    # 证据引用带原帖 URL(metadata url 优先)
    assert any(
        str(ref.get("url") or "").startswith("https://www.reddit.com/")
        for ref in viltrox["sample_refs"]
    )
