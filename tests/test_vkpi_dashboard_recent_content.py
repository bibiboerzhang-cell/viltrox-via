from app.api.routers import vkpi_dashboard_staff
from app.db.repositories import viltrox_matrix
from app.domains.dashboard import kol_distribution as dashboard_kol_distribution
from app.domains.dashboard import recent_content as dashboard_recent_content


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


def test_dashboard_official_content_prefers_matrix_and_preserves_shape(monkeypatch):
    monkeypatch.setattr(
        dashboard_recent_content.channels,
        "official_account_matrix",
        lambda limit: {
            "platforms": [{
                "platform": "youtube",
                "accounts": [{
                    "handle": "viltrox",
                    "display_name": "Viltrox",
                    "posts": [{
                        "title": "New lens",
                        "url": "https://example.test/new-lens",
                        "posted_at": "2026-08-31T12:00:00Z",
                        "views": "101.0",
                        "like_count": "9",
                        "comment_count": 3,
                        "share_count": 2,
                        "content_type": "video",
                        "thumbnail": "https://example.test/thumb.jpg",
                        "canonical_post_uid": "youtube:new-lens",
                    }],
                }],
            }],
        },
    )
    monkeypatch.setattr(
        viltrox_matrix,
        "get_latest_viltrox_scan_bundle",
        lambda: (_ for _ in ()).throw(AssertionError("fallback must not run")),
    )

    rows = dashboard_recent_content._dashboard_recent_official_content(5)

    assert rows == [{
        "content_kind": "official",
        "title": "New lens",
        "url": "https://example.test/new-lens",
        "platform": "youtube",
        "account_handle": "viltrox",
        "account_display_name": "Viltrox",
        "posted_at": "2026-08-31T12:00:00Z",
        "views": 101,
        "likes": 9,
        "comments": 3,
        "shares": 2,
        "media_type": "video",
        "thumbnail_url": "https://example.test/thumb.jpg",
        "source_table": "vkpi_employee_channel_metrics",
        "source_id": "youtube:new-lens",
    }]


def test_dashboard_official_matrix_failure_discards_partial_rows_before_fallback(monkeypatch):
    monkeypatch.setattr(
        dashboard_recent_content.channels,
        "official_account_matrix",
        lambda limit: {
            "platforms": [
                {
                    "platform": "youtube",
                    "accounts": [{
                        "handle": "partial",
                        "posts": [{"title": "must be discarded", "url": "https://bad"}],
                    }],
                },
                None,
            ],
        },
    )
    monkeypatch.setattr(
        viltrox_matrix,
        "get_latest_viltrox_scan_bundle",
        lambda: {
            "posts": [{
                "title": "Fallback",
                "post_url": "https://example.test/fallback",
                "platform": "instagram",
                "handle": "viltrox",
                "published_at": "2026-08-30T12:00:00Z",
            }]
        },
    )

    rows = dashboard_recent_content._dashboard_recent_official_content(5)

    assert [row["title"] for row in rows] == ["Fallback"]
    assert rows[0]["source_table"] == "viltrox_matrix_scan_posts"


def test_dashboard_recent_content_returns_empty_payload(monkeypatch):
    monkeypatch.setattr(dashboard_recent_content, "_dashboard_recent_official_content", lambda limit: [])
    monkeypatch.setattr(dashboard_recent_content, "_dashboard_recent_ugc_content", lambda limit: [])

    payload = vkpi_dashboard_staff.dashboard_recent_content(limit=12, staff={})

    assert payload["items"] == []
    assert payload["count"] == 0
    assert payload["kind_counts"] == {}
    assert payload["is_real"] is True
    assert "viltrox_matrix_scan_posts" in payload["sources"]
    assert "vkpi_brand_signal" in payload["sources"]


def test_dashboard_recent_content_merges_and_sorts_sources(monkeypatch):
    monkeypatch.setattr(
        dashboard_recent_content,
        "_dashboard_recent_official_content",
        lambda limit: [
            {
                "content_kind": "official",
                "title": "Official older",
                "posted_at": "2026-05-20T08:00:00Z",
                "url": "https://example.com/official",
            }
        ],
    )
    monkeypatch.setattr(
        dashboard_recent_content,
        "_dashboard_recent_ugc_content",
        lambda limit: [
            {
                "content_kind": "ugc",
                "title": "UGC newer",
                "posted_at": "2026-05-21T08:00:00Z",
                "url": "https://example.com/ugc",
            }
        ],
    )

    payload = vkpi_dashboard_staff.dashboard_recent_content(limit=12, staff={})

    assert [item["title"] for item in payload["items"]] == ["UGC newer", "Official older"]
    assert payload["count"] == 2
    assert payload["kind_counts"] == {"ugc": 1, "official": 1}


def test_dashboard_kol_distribution_includes_platforms_and_exposure(monkeypatch):
    class Conn:
        def execute(self, query):
            if "GROUP BY country, platform" in query:
                return _Rows(
                    [
                        {"country": "United States", "platform": "YouTube", "n": 2, "exposure": 300},
                        {"country": "United States", "platform": "Instagram", "n": 1, "exposure": 50},
                    ]
                )
            if "COUNT(*) AS n FROM vkpi_kol_pool" in query:
                return _Rows([{"n": 3}])
            raise AssertionError(query)

    monkeypatch.setattr(dashboard_kol_distribution, "get_conn", lambda: Conn())
    monkeypatch.setattr(
        dashboard_kol_distribution.kol_pool,
        "_country_distribution",
        # C3 员工轻隔离:签名新增 kol_ids_sql(staff={} 走全局,值恒 None)
        lambda conn, limit, kol_ids_sql=None: [
            {
                "country_code": "US",
                "country_name": "United States",
                "kol_count": 3,
                "raw_values": ["United States"],
            }
        ],
    )

    payload = vkpi_dashboard_staff.dashboard_kol_distribution(limit=200, staff={})

    assert payload["is_real"] is True
    assert payload["country_count"] == 1
    country = payload["countries"][0]
    assert country["code"] == "US"
    assert country["count"] == 3
    assert country["exposure"] == 350
    assert country["platforms"] == [
        {"platform": "YouTube", "count": 2, "exposure": 300},
        {"platform": "Instagram", "count": 1, "exposure": 50},
    ]
