import asyncio

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


def test_run_apify_batch_without_token_is_not_configured(monkeypatch) -> None:
    monkeypatch.delenv("APIFY_TOKEN", raising=False)

    result = apify_batch_refresh.run_apify_batch({"batch_key": "instagram-1", "platform": "instagram", "targets": []})

    assert result["provider_status"] == "not_configured"
    assert result["sync_status"] == "not_configured"


def test_execute_apify_batch_plan_blocks_provider_calls_by_default() -> None:
    plan = {
        "max_concurrent_runs": 2,
        "batches": [{"batch_key": "instagram-1", "platform": "instagram", "targets": [{"kol_pool_id": 1}]}],
    }

    result = asyncio.run(apify_batch_refresh.execute_apify_batch_plan(plan))

    assert result["executed"] is False
    assert result["reason"] == "provider_calls_not_allowed"
    assert result["batch_count"] == 1
    assert result["max_concurrent_runs"] == 2
    assert result["results"] == []
    assert result["summary"]["retry_count"] == 0
    assert result["summary"]["kol_statuses"][0]["status"] == "planned"


def test_execute_apify_batch_plan_summarizes_mocked_runs(monkeypatch) -> None:
    calls: list[str] = []

    def fake_run(batch, **_kwargs):
        calls.append(batch["batch_key"])
        return {
            "batch_key": batch["batch_key"],
            "platform": batch["platform"],
            "provider_status": "ok",
            "sync_status": "synced",
            "mapped": {
                "matched": [{"kol_pool_id": target["kol_pool_id"]} for target in batch["targets"]],
                "unmatched": [],
                "matched_count": len(batch["targets"]),
                "unmatched_count": 0,
            },
        }

    monkeypatch.setattr(apify_batch_refresh, "run_apify_batch", fake_run)
    plan = {
        "max_concurrent_runs": 2,
        "batches": [
            {"batch_key": "instagram-1", "platform": "instagram", "targets": [{"kol_pool_id": 1}]},
            {"batch_key": "tiktok-1", "platform": "tiktok", "targets": [{"kol_pool_id": 2}, {"kol_pool_id": 3}]},
        ],
    }

    result = asyncio.run(apify_batch_refresh.execute_apify_batch_plan(plan, allow_provider_calls=True, api_token="test"))

    assert sorted(calls) == ["instagram-1", "tiktok-1"]
    assert result["executed"] is True
    assert result["synced_batches"] == 2
    assert result["failed_batches"] == 0
    assert result["matched_items"] == 3
    assert result["summary"]["target_count"] == 3
    assert result["summary"]["retry_count"] == 0


def test_summarize_batch_execution_tracks_unmatched_and_failed_targets() -> None:
    plan = {
        "strategy": "apify_batch_first",
        "batches": [
            {
                "batch_key": "instagram-1",
                "platform": "instagram",
                "targets": [{"kol_pool_id": 1}, {"kol_pool_id": 2}],
            },
            {
                "batch_key": "tiktok-1",
                "platform": "tiktok",
                "targets": [{"kol_pool_id": 3}],
            },
        ],
    }
    results = [
        {
            "batch_key": "instagram-1",
            "platform": "instagram",
            "provider_status": "ok",
            "sync_status": "synced",
            "mapped": {"matched": [{"kol_pool_id": 1}], "unmatched": [{"username": "unknown"}], "matched_count": 1, "unmatched_count": 1},
        },
        {
            "batch_key": "tiktok-1",
            "platform": "tiktok",
            "provider_status": "error",
            "sync_status": "error",
            "error": "actor timeout",
        },
    ]

    summary = apify_batch_refresh.summarize_batch_execution(plan, results, executed=True)

    assert summary["target_count"] == 3
    assert summary["matched_items"] == 1
    assert summary["unmatched_items"] == 1
    assert summary["failed_batches"] == 1
    assert summary["retry_kol_pool_ids"] == [2, 3]
    statuses = {item["kol_pool_id"]: item["status"] for item in summary["kol_statuses"]}
    assert statuses == {1: "matched", 2: "unmatched", 3: "error"}
