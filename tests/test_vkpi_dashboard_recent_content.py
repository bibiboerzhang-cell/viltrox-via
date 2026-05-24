from app.api.routers import vkpi_dashboard_staff


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


def test_dashboard_recent_content_returns_empty_payload(monkeypatch):
    monkeypatch.setattr(vkpi_dashboard_staff, "_dashboard_recent_official_content", lambda limit: [])
    monkeypatch.setattr(vkpi_dashboard_staff, "_dashboard_recent_ugc_content", lambda limit: [])

    payload = vkpi_dashboard_staff.dashboard_recent_content(limit=12, staff={})

    assert payload["items"] == []
    assert payload["count"] == 0
    assert payload["kind_counts"] == {}
    assert payload["is_real"] is True
    assert "viltrox_matrix_scan_posts" in payload["sources"]
    assert "vkpi_brand_signal" in payload["sources"]


def test_dashboard_recent_content_merges_and_sorts_sources(monkeypatch):
    monkeypatch.setattr(
        vkpi_dashboard_staff,
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
        vkpi_dashboard_staff,
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

    monkeypatch.setattr(vkpi_dashboard_staff, "get_conn", lambda: Conn())
    monkeypatch.setattr(
        vkpi_dashboard_staff.kol_pool,
        "_country_distribution",
        lambda conn, limit: [
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
