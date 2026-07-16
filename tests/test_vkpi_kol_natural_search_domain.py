from app.domains.kol import natural_search


def test_kol_natural_search_uses_existing_kol_pool_without_provider_calls(monkeypatch):
    raw_rows = [
        {
            "id": 1,
            "platform": "youtube",
            "handle": "@lenscreator",
            "channel_name": "Lens Creator",
            "country": "US",
            "snapshot_follower_count": 80000,
            "contact_email": "creator@example.com",
            "account_score": 68,
            "niche": "35mm lens review",
            "snapshot_scanned_at": "2026-05-01T00:00:00Z",
        }
    ]

    monkeypatch.setattr(natural_search, "list_kols", lambda **kwargs: {"kols": raw_rows, "kwargs": kwargs})
    monkeypatch.setattr(natural_search.history_match, "search_pool_for_natural", lambda *_args, **_kwargs: [])

    payload = natural_search._natural_search_payload(
        {"query": "找 youtube 美国 35mm 中腰部 有联系方式", "limit": 5},
        staff={"id": 10},
    )

    assert payload["method"] == "local_natural_search_v1_existing_kols"
    assert payload["parsed"]["platform"] == "youtube"
    assert payload["parsed"]["country"] == "US"
    assert payload["items"][0]["id"] == 1
    assert "已有联系方式" in payload["items"][0]["natural_match_reasons"]


def test_natural_search_strictly_filters_list_and_history_rows_by_platform(monkeypatch):
    monkeypatch.setattr(
        natural_search,
        "list_kols",
        lambda **_kwargs: {
            "kols": [
                {"id": 1, "platform": "youtube", "handle": "wrong", "channel_name": "Camera Review"},
                {"id": 2, "platform": "instagram", "handle": "right", "channel_name": "Camera Review"},
            ]
        },
    )
    monkeypatch.setattr(
        natural_search.history_match,
        "search_pool_for_natural",
        lambda *_args, **_kwargs: [
            {"id": 3, "platform": "youtube", "handle": "history-wrong", "channel_name": "Camera Review"}
        ],
    )

    payload = natural_search._natural_search_payload(
        {"query": "找 instagram 摄影博主", "limit": 10},
        staff={"id": 10},
    )

    assert [item["id"] for item in payload["items"]] == [2]
    assert {item["platform"] for item in payload["items"]} == {"instagram"}
