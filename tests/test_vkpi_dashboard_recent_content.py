from app.api.routers import vkpi_dashboard_staff


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
