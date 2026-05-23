from app.services.vkpi import apify_batch_refresh


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
