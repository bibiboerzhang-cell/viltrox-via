from app.services.vkpi import apify_batch_refresh


def test_parse_chunk_overrides_only_accepts_supported_platforms() -> None:
    overrides = apify_batch_refresh.parse_chunk_overrides("instagram=20,tiktok=10,unknown=7,x=200")

    assert overrides == {"instagram": 20, "tiktok": 10, "x": 100}


def test_plan_apify_batches_uses_platform_chunk_sizes_and_bounded_concurrency(monkeypatch) -> None:
    monkeypatch.setenv("APIFY_INSTAGRAM_POSTS_ACTOR_ID", "custom/instagram")
    rows = [
        {"id": idx, "platform": "instagram", "handle": f"ig{idx}", "refresh_tier": "hot"}
        for idx in range(1, 52)
    ]
    rows.extend({"id": 100 + idx, "platform": "tiktok", "handle": f"tt{idx}"} for idx in range(1, 27))
    rows.append({"id": 999, "platform": "unknown", "handle": "skipme"})

    plan = apify_batch_refresh.plan_apify_batches(rows, max_posts=1, max_concurrent=99)

    assert plan["strategy"] == "apify_batch_first"
    assert plan["max_concurrent_runs"] == 3
    assert plan["total_targets"] == 77
    assert plan["batch_count"] == 4
    assert plan["platforms"] == {"instagram": 51, "tiktok": 26}
    assert plan["skipped"][0]["reason"] == "unsupported_platform"

    ig_batches = [batch for batch in plan["batches"] if batch["platform"] == "instagram"]
    tt_batches = [batch for batch in plan["batches"] if batch["platform"] == "tiktok"]
    assert [batch["target_count"] for batch in ig_batches] == [50, 1]
    assert [batch["target_count"] for batch in tt_batches] == [25, 1]
    assert ig_batches[0]["actor_id"] == "custom~instagram"
    assert len(ig_batches[0]["run_input"]["directUrls"]) == 50
    assert len(tt_batches[0]["run_input"]["profiles"]) == 25


def test_build_actor_input_uses_safe_platform_specific_inputs() -> None:
    target = {"kol_pool_id": 1, "platform": "youtube", "handle": "viltrox", "profile_url": "https://www.youtube.com/@viltrox"}

    payload = apify_batch_refresh.build_actor_input("youtube", [target], max_posts=5)

    assert payload == {
        "startUrls": [{"url": "https://www.youtube.com/@viltrox"}],
        "maxResults": 5,
        "maxResultsShorts": 0,
        "maxResultStreams": 0,
    }


def test_map_dataset_items_to_targets_matches_instagram_and_tiktok_shapes() -> None:
    targets = [
        {"kol_pool_id": 10, "handle": "viltrox", "profile_url": "https://www.instagram.com/viltrox/"},
        {"kol_pool_id": 20, "handle": "viltroxofficial", "profile_url": "https://www.tiktok.com/@viltroxofficial"},
    ]
    items = [
        {"username": "viltrox", "inputUrl": "https://www.instagram.com/viltrox/"},
        {"authorMeta": {"name": "viltroxofficial", "profileUrl": "https://www.tiktok.com/@viltroxofficial"}},
        {"username": "not-in-targets"},
    ]

    mapped = apify_batch_refresh.map_dataset_items_to_targets(items, targets)

    assert mapped["matched_count"] == 2
    assert [item["kol_pool_id"] for item in mapped["matched"]] == [10, 20]
    assert mapped["unmatched_count"] == 1


def test_qualified_apify_batch_plan_uses_refresh_tier_selector(monkeypatch) -> None:
    rows = [
        {"id": 1, "platform": "instagram", "handle": "viltrox", "profile_url": "", "refresh_tier": "hot"},
        {"id": 2, "platform": "tiktok", "handle": "viltroxofficial", "profile_url": "", "refresh_tier": "hot"},
    ]
    calls: dict[str, object] = {}

    def fake_rows(**kwargs):
        calls["rows"] = kwargs
        return rows

    def fake_counts(**kwargs):
        calls["counts"] = kwargs
        return {"selector_ready": True, "source_total": 2, "source_by_platform": {"instagram": 1, "tiktok": 1}, "tier_distribution": {"hot": 2}}

    monkeypatch.setattr(apify_batch_refresh.refresh_tier, "qualified_refresh_rows", fake_rows)
    monkeypatch.setattr(apify_batch_refresh.refresh_tier, "qualified_source_counts", fake_counts)

    plan = apify_batch_refresh.qualified_apify_batch_plan(
        limit=25,
        offset=5,
        stale_before="2026-05-23T00:00:00Z",
        platforms={"instagram", "tiktok"},
        tiers={"hot"},
        max_posts=1,
        max_concurrent=2,
        chunk_overrides={"instagram": 1, "tiktok": 1},
    )

    assert plan["mode"] == "plan_only"
    assert plan["execution_enabled"] is False
    assert plan["selector"] == "qualified"
    assert plan["total_targets"] == 2
    assert plan["batch_count"] == 2
    assert calls["rows"]["offset"] == 5
    assert calls["rows"]["stale_before"] == "2026-05-23T00:00:00Z"
