from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from typing import Any

import pytest

from app.domains.kol import (
    profile_discovery_provider,
    profile_discovery_session,
    profile_online_inventory,
    profile_online_qualification,
    profile_recall_qualification,
    search_sessions_online,
    targeted_search_contract,
)
from app.domains.kol.search_sessions_items import (
    _prune_authoritative_online_snapshot,
    _prune_authoritative_recall_snapshot,
)


AS_OF = datetime(2026, 8, 17, tzinfo=timezone.utc)


def _candidate(index: int, **overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "platform": "youtube",
        "handle": f"portrait{index}",
        "channel_id": f"UCstrict{index:04d}",
        "display_name": f"Portrait Lighting {index}",
        "profile_url": f"https://www.youtube.com/@portrait{index}",
        "avatar_url": f"https://images.example/{index}.jpg",
        "followers": 5_000 + index,
        "country": "US",
        "country_source": "platform_profile",
        "language": "en",
        "language_source": "platform_profile",
        "profile_type": "creator",
        "profile_type_source": "provider_declared",
        "activation_sample_count": 5,
        "activation_metrics_source": "fixture.recent_video_aggregate",
        "activation_metrics_scope": "recent_video_aggregate_45d",
        "bio": "portrait lighting studio tutorial creator",
        "latest_real_video": {
            "posted_at": "2026-08-01T00:00:00Z",
            "video_id": f"video{index:05d}",
            "platform": "youtube",
            "title": "portrait lighting studio tutorial",
            "source": "platform_video_api",
        },
    }
    item.update(overrides)
    return item


def _policy() -> dict[str, Any]:
    return profile_online_qualification.online_policy(
        market="US",
        platforms=["youtube"],
        languages=["en"],
        profile_types=["creator"],
    )


def _prospective_brief() -> dict[str, Any]:
    return {
        "objective": "prospective_growth",
        "product": {"capability": "on-camera flash"},
    }


def _query_cell(cell_id: str, segment: str) -> dict[str, Any]:
    return {
        "query_cell_id": cell_id,
        "objective": "prospective_growth",
        "segment": segment,
        "primary_query": f"{segment} photographer on-camera flash",
        "required_evidence_groups": [
            "product_use_fit",
            "segment_use_case",
            "market_activation",
        ],
        "locked_term_groups": targeted_search_contract.build_locked_term_groups(
            capability="on-camera flash",
            segment=segment,
            segment_label=segment,
        ),
    }


def _telephoto_query_cell(cell_id: str, segment: str) -> dict[str, Any]:
    return {
        "query_cell_id": cell_id,
        "objective": "prospective_growth",
        "segment": segment,
        "primary_query": f"{segment} photographer telephoto portrait lens",
        "required_evidence_groups": [
            "product_use_fit",
            "segment_use_case",
            "market_activation",
        ],
        "locked_term_groups": targeted_search_contract.build_locked_term_groups(
            capability="telephoto portrait lens",
            segment=segment,
            segment_label=segment,
        ),
    }


def test_online_collector_cancellation_propagates_instead_of_becoming_provider_failure() -> None:
    async def cancelled_fetch(**_kwargs: Any) -> dict[str, Any]:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(profile_online_qualification.collect_strict_online_candidates(
            query_text="food photographer on-camera flash",
            policy=_policy(),
            local_canonical_keys=set(),
            fetch_batch=cancelled_fetch,
            enroll_candidate=lambda _raw: {"kol_pool_id": 1},
            candidate_budget=30,
            max_provider_rounds=1,
            search_brief=_prospective_brief(),
            as_of=AS_OF,
        ))


def test_online_without_follower_filter_admits_small_and_unknown_reach() -> None:
    policy = _policy()
    assert policy["followers_filter"] == {
        "requested": False,
        "minimum": None,
        "maximum": None,
        "source": "not_requested",
        "unknown_policy": "allow",
    }

    result = profile_online_qualification.qualify_online_candidates(
        [_candidate(20, followers=299), _candidate(21, followers=None)],
        query_text="portrait lighting",
        policy=policy,
        as_of=AS_OF,
    )

    assert result["counts"] == {"selected": 2}
    proofs = [item["qualification_evidence"]["followers"] for item in result["accepted"]]
    assert [proof["value"] for proof in proofs] == [299, None]
    assert all(proof["filter_requested"] is False for proof in proofs)
    assert all(proof["passed"] is True for proof in proofs)
    assert all(proof["status"] == "passed" for proof in proofs)


def test_online_explicit_follower_range_gates_both_bounds_and_keeps_unknown_pending() -> None:
    policy = profile_online_qualification.online_policy(
        market="US",
        platforms=["youtube"],
        languages=["en"],
        profile_types=["creator"],
        followers_min=10_000,
        followers_max=50_000,
        source="operator_ui",
    )
    assert policy["followers_filter"] == {
        "requested": True,
        "minimum": 10_000,
        "maximum": 50_000,
        "source": "operator_ui",
        "unknown_policy": "pending",
    }

    result = profile_online_qualification.qualify_online_candidates(
        [
            _candidate(22, followers=9_999),
            _candidate(23, followers=10_000),
            _candidate(24, followers=50_001),
            _candidate(25, followers=None),
        ],
        query_text="portrait lighting",
        policy=policy,
        as_of=AS_OF,
    )

    assert [item["handle"] for item in result["accepted"]] == ["portrait23"]
    assert result["counts"] == {"rejected": 2, "selected": 1, "pending": 1}
    assert result["rejected_by_reason"] == {
        "followers_below_minimum": 1,
        "followers_above_maximum": 1,
        "followers_unknown": 1,
    }


def test_online_unknown_follower_reject_policy_is_explicit_not_pending() -> None:
    policy = profile_online_qualification.online_policy(
        platforms=["youtube"],
        followers_min=1_000,
        unknown_policy="reject",
        source="operator_ui",
    )
    result = profile_online_qualification.qualify_online_candidates(
        [_candidate(26, followers=None)],
        query_text="portrait lighting",
        policy=policy,
        as_of=AS_OF,
    )

    assert result["accepted"] == []
    assert result["counts"] == {"rejected": 1}
    assert result["rejected_by_reason"] == {"followers_unknown_rejected": 1}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"followers_min": -1},
        {"followers_max": "many"},
        {"followers_min": 50_000, "followers_max": 10_000},
        {"followers_min": 1_000, "unknown_policy": "allow"},
    ],
)
def test_online_follower_policy_rejects_invalid_or_unsafe_ranges(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        profile_online_qualification.online_policy(platforms=["youtube"], **kwargs)


def test_bare_published_or_channel_creation_timestamp_never_satisfies_activity() -> None:
    raw = _candidate(2)
    raw.pop("latest_real_video")
    raw.update({
        "published": "2026-08-16T00:00:00Z",
        "source_url": "https://www.youtube.com/channel/UCstrict0002",
    })
    result = profile_online_qualification.qualify_online_candidates(
        [raw], query_text="portrait lighting", policy=_policy(), as_of=AS_OF,
    )

    assert result["accepted"] == []
    assert result["counts"] == {"pending": 1}
    assert result["rejected_by_reason"]["latest_video_unknown"] == 1


def test_untrusted_market_and_unknown_operator_facets_fail_closed() -> None:
    untrusted = _candidate(3, country_source="")
    untrusted.pop("language")
    untrusted.pop("profile_type")
    result = profile_online_qualification.qualify_online_candidates(
        [untrusted], query_text="portrait lighting", policy=_policy(), as_of=AS_OF,
    )

    assert result["accepted"] == []
    assert result["counts"] == {"rejected": 1}
    assert result["rejected_by_reason"] == {
        "market_untrusted_source": 1,
        "language_unknown": 1,
        "profile_type_unknown": 1,
    }


def test_market_echo_is_not_country_but_unknown_market_is_allowed_when_unfiltered() -> None:
    raw = _candidate(4)
    raw.pop("country")
    raw.pop("country_source")
    raw["market"] = "US"
    filtered = profile_online_qualification.qualify_online_candidates(
        [raw], query_text="portrait lighting", policy=_policy(), as_of=AS_OF,
    )
    assert filtered["accepted"] == []
    assert filtered["rejected_by_reason"]["market_unknown"] == 1

    unfiltered_policy = profile_online_qualification.online_policy(
        platforms=["youtube"], languages=["en"], profile_types=["creator"],
    )
    unfiltered = profile_online_qualification.qualify_online_candidates(
        [raw], query_text="portrait lighting", policy=unfiltered_policy, as_of=AS_OF,
    )
    assert len(unfiltered["accepted"]) == 1


def test_flat_content_video_is_promoted_but_profile_published_timestamp_is_not() -> None:
    content = _candidate(5)
    content.pop("latest_real_video")
    content.update({
        "published": "2026-08-10T00:00:00Z",
        "source_url": "https://www.instagram.com/reel/ABC12345/",
        "platform": "instagram",
        "handle": "portrait5",
        "profile_url": "https://www.instagram.com/portrait5/",
    })
    content_policy = profile_online_qualification.online_policy(
        market="US", platforms=["instagram"], languages=["en"], profile_types=["creator"],
    )
    assert len(profile_online_qualification.qualify_online_candidates(
        [content], query_text="portrait lighting", policy=content_policy, as_of=AS_OF,
    )["accepted"]) == 1

    profile = dict(content)
    profile["source_url"] = "https://www.instagram.com/portrait5/"
    assert profile_online_qualification.qualify_online_candidates(
        [profile], query_text="portrait lighting", policy=content_policy, as_of=AS_OF,
    )["accepted"] == []


def test_identity_aliases_cross_native_handle_and_url_strength() -> None:
    handle_only = {
        "platform": "youtube", "handle": "Ｐortrait1",
        "profile_url": "http://www.youtube.com/@Portrait1/",
    }
    native_plus_handle = {
        "platform": "youtube", "handle": "portrait1", "channel_id": "UCnative",
        "profile_url": "https://youtube.com/@portrait1",
    }
    renamed_same_native = {
        "platform": "youtube", "handle": "renamed", "channel_id": "UCnative",
    }
    handle_aliases = profile_recall_qualification.canonical_creator_aliases(handle_only)
    native_aliases = profile_recall_qualification.canonical_creator_aliases(native_plus_handle)
    renamed_aliases = profile_recall_qualification.canonical_creator_aliases(renamed_same_native)
    assert handle_aliases.intersection(native_aliases)
    assert native_aliases.intersection(renamed_aliases) == {"youtube:id:ucnative"}

    qualified = profile_online_qualification.qualify_online_candidates(
        [_candidate(1)],
        query_text="portrait lighting",
        policy=_policy(),
        local_canonical_keys={"youtube:handle:portrait1"},
        as_of=AS_OF,
    )
    assert qualified["accepted"] == []
    assert qualified["counts"] == {"duplicate_local": 1}


def test_public_identity_projection_blocks_contact_canaries_before_pool_and_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canaries = ("private@example.test", "+1 202 555 0199", "t.me/private-route")
    raw = _candidate(
        6,
        display_name=f"Portrait {canaries[0]}",
        profile_url="https://www.youtube.com/@portrait6",
        avatar_url=f"https://{canaries[2]}",
    )
    qualified = profile_online_qualification.qualify_online_candidates(
        [raw], query_text="portrait lighting", policy=_policy(), as_of=AS_OF,
    )["accepted"][0]
    assert qualified["display_name"] == ""
    assert qualified["profile_url"] == "https://www.youtube.com/@portrait6"
    assert qualified["avatar_url"] == ""
    assert not any(canary in json.dumps(qualified) for canary in canaries)

    captured: dict[str, Any] = {}

    def writer(_pool_id: Any, profile_data: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        captured.update(profile_data)
        return {"kol_pool_id": 606, "matched_existing": False}

    class MaterializeConn:
        def execute(self, _sql: str, _params: tuple[Any, ...] = ()) -> _Cursor:
            return _Cursor([])

        def commit(self) -> None:
            pass

        def rollback(self) -> None:
            pass

    monkeypatch.setattr("app.domains.kol.profile_basics.write_kol_profile_basics", writer)
    result = profile_online_qualification.materialize_online_candidate(raw, conn=MaterializeConn())
    assert result["kol_pool_id"] == 606
    assert captured["display_name"] == ""
    assert captured["profile_url"] == "https://www.youtube.com/@portrait6"
    assert captured["avatar_url"] == ""
    assert not any(canary in json.dumps(captured) for canary in canaries)

    unsafe_handle = _candidate(7, handle="private@example.test")
    rejected = profile_online_qualification.qualify_online_candidates(
        [unsafe_handle], query_text="portrait lighting", policy=_policy(), as_of=AS_OF,
    )
    assert rejected["accepted"] == []
    assert rejected["rejected_by_reason"]["account_unsafe_identity"] == 1

    unsafe_locator = _candidate(
        8,
        profile_url="https://www.youtube.com/@portrait8?token=private",
    )
    rejected_locator = profile_online_qualification.qualify_online_candidates(
        [unsafe_locator], query_text="portrait lighting", policy=_policy(), as_of=AS_OF,
    )
    assert rejected_locator["accepted"] == []
    assert rejected_locator["rejected_by_reason"]["account_unsafe_identity"] == 1


@pytest.mark.parametrize("storage", ["pool", "alias"])
def test_materialize_rechecks_nfkc_pool_and_alias_rows_inside_identity_lock(
    monkeypatch: pytest.MonkeyPatch,
    storage: str,
) -> None:
    class AliasRaceConn:
        def __init__(self) -> None:
            self.rolled_back = False
            self.params: list[tuple[Any, ...]] = []

        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Cursor:
            self.params.append(params)
            if "FROM vkpi_kol_pool_aliases" in sql and storage == "alias":
                return _Cursor([{
                    "kol_pool_id": 777,
                    "platform": "youtube",
                    "handle": "ＰortraitCase",
                    "profile_url": "https://www.youtube.com/@PortraitCase",
                    "metadata_json": "{}",
                }])
            if "FROM vkpi_kol_pool_aliases" in sql:
                return _Cursor([])
            if "FROM vkpi_kol_pool" in sql and storage == "pool":
                return _Cursor([{
                    "id": 777,
                    "platform": "youtube",
                    "handle": "ＰortraitCase",
                    "profile_url": "https://www.youtube.com/@PortraitCase",
                    "raw_platform_data": "{}",
                }])
            if "FROM vkpi_kol_pool" in sql:
                return _Cursor([])
            raise AssertionError(sql)

        def rollback(self) -> None:
            self.rolled_back = True

    monkeypatch.setattr(
        "app.domains.kol.profile_basics.write_kol_profile_basics",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("duplicate alias must not be written")),
    )
    conn = AliasRaceConn()
    result = profile_online_inventory.materialize_online_candidate(
        _candidate(9, handle="ⓅortraitCase", profile_url="https://www.youtube.com/@PortraitCase"),
        conn=conn,
    )

    assert result == {
        "duplicate_local_inventory": True,
        "kol_pool_id": 777,
        "operation": "existing",
        "db_reads": 2,
    }
    assert conn.rolled_back is True
    assert conn.params == [("youtube",), ("youtube",)]


def test_online_profile_followup_requires_explicit_session_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = {
        "id": 91,
        "item_type": "online_qualified_candidate",
        "kol_pool_id": 901,
        "source_url": "https://www.youtube.com/@portraitapproved",
        "payload": {},
    }
    monkeypatch.setattr(profile_discovery_session.search_sessions, "get_session_item", lambda *_args: item)
    monkeypatch.setattr(
        profile_discovery_session.search_sessions,
        "get_session",
        lambda *_args: {"approved_kol_ids": []},
    )
    with pytest.raises(ValueError, match="requires an approved pool candidate"):
        profile_discovery_session.profile_crawl_plan_for_session_item(session_id=51, item_id=91)

    monkeypatch.setattr(
        profile_discovery_session.search_sessions,
        "get_session",
        lambda *_args: {"approved_kol_ids": [901]},
    )
    plan = profile_discovery_session.profile_crawl_plan_for_session_item(session_id=51, item_id=91)
    assert plan["item_type"] == "online_qualified_candidate"
    assert plan["profile_url"] == "https://www.youtube.com/@portraitapproved"


def test_collector_returns_30_pool_backed_net_new_and_skips_inventory_and_local_duplicate() -> None:
    rows = [_candidate(index) for index in range(40)]
    local_key = profile_recall_qualification.canonical_creator_key(rows[0])
    rows[1]["history_kol_pool_id"] = 99
    enrolled: list[str] = []

    async def fetch_batch(**_kwargs: Any) -> dict[str, Any]:
        return {
            "status": "ready",
            "new_creators": rows,
            "existing_matches": [_candidate(999, history_kol_pool_id=999)],
            "provider_calls": True,
        }

    def enroll(raw: dict[str, Any]) -> dict[str, Any]:
        enrolled.append(raw["handle"])
        return {"kol_pool_id": 10_000 + len(enrolled), "matched_existing": False}

    result = asyncio.run(profile_online_qualification.collect_strict_online_candidates(
        query_text="portrait lighting",
        policy=_policy(),
        local_canonical_keys={local_key},
        fetch_batch=fetch_batch,
        enroll_candidate=enroll,
        candidate_budget=50,
        max_provider_rounds=1,
        as_of=AS_OF,
    ))

    assert result["status"] == "ready"
    assert result["returned_count"] == 30
    assert result["shortfall"] == 0
    assert result["duplicate_local_count"] == 1
    assert result["duplicate_local_inventory_count"] == 1
    assert len(enrolled) == 30
    assert all(item["kol_pool_id"] for item in result["items"])
    assert [item["server_rank"] for item in result["items"]] == list(range(1, 31))
    assert [item["global_unique_rank"] for item in result["items"]] == list(range(2, 32))
    assert all(item["contact_preview"] == {"status": "not_enriched", "channel_count": 0} for item in result["items"])


def test_materialize_race_duplicate_does_not_take_quota_and_overflow_refills() -> None:
    rows = [_candidate(index) for index in range(31)]
    calls = 0

    async def fetch_batch(**_kwargs: Any) -> dict[str, Any]:
        return {"status": "ready", "new_creators": rows, "provider_calls": True}

    def enroll(_raw: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"kol_pool_id": 7, "matched_existing": True}
        return {"kol_pool_id": 20_000 + calls, "matched_existing": False}

    result = asyncio.run(profile_online_qualification.collect_strict_online_candidates(
        query_text="portrait lighting",
        policy=_policy(),
        local_canonical_keys=set(),
        fetch_batch=fetch_batch,
        enroll_candidate=enroll,
        candidate_budget=50,
        max_provider_rounds=1,
        as_of=AS_OF,
    ))

    assert result["returned_count"] == 30
    assert result["duplicate_local_inventory_count"] == 1
    assert calls == 31


def test_many_materialize_inventory_hits_and_one_exception_keep_refilling() -> None:
    rows = [_candidate(index) for index in range(55)]
    calls = 0

    async def fetch_batch(**_kwargs: Any) -> dict[str, Any]:
        return {"status": "ready", "new_creators": rows, "provider_calls": True}

    def enroll(_raw: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls <= 20:
            return {"kol_pool_id": calls, "matched_existing": True}
        if calls == 21:
            raise RuntimeError("single row write race")
        return {"kol_pool_id": 40_000 + calls, "matched_existing": False}

    result = asyncio.run(profile_online_qualification.collect_strict_online_candidates(
        query_text="portrait lighting",
        policy=_policy(),
        local_canonical_keys=set(),
        fetch_batch=fetch_batch,
        enroll_candidate=enroll,
        candidate_budget=150,
        max_provider_rounds=1,
        as_of=AS_OF,
    ))

    assert result["returned_count"] == 30
    assert result["duplicate_local_inventory_count"] == 20
    assert result["rejected_by_reason"]["enrollment_failed"] == 1
    assert calls == 51


def test_provider_without_cursor_returns_honest_candidate_exhausted_shortfall() -> None:
    rows = [_candidate(index) for index in range(10)]

    async def fetch_batch(**_kwargs: Any) -> dict[str, Any]:
        return {"status": "ready", "new_creators": rows, "provider_calls": True}

    result = asyncio.run(profile_online_qualification.collect_strict_online_candidates(
        query_text="portrait lighting",
        policy=_policy(),
        local_canonical_keys=set(),
        fetch_batch=fetch_batch,
        enroll_candidate=lambda raw: {"kol_pool_id": 30_000 + int(raw["handle"].replace("portrait", ""))},
        candidate_budget=50,
        max_provider_rounds=3,
        as_of=AS_OF,
    ))

    assert result["status"] == "shortfall"
    assert result["returned_count"] == 10
    assert result["shortfall"] == 20
    assert result["provider_rounds"] == 1
    assert result["shortfall_reasons"]["bounded_provider_batch_exhausted"] == 20


def test_strict_discovery_switch_never_legacy_enrolls_or_warms_raw_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def search_platform_content(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"status": "ready", "items": [_candidate(1, followers=500)]}

    monkeypatch.setattr(profile_discovery_provider, "search_platform_content", search_platform_content)
    monkeypatch.setattr(profile_discovery_provider.history_match, "annotate_platform_items", lambda raw, **_kwargs: raw)
    monkeypatch.setattr(
        profile_discovery_provider,
        "_auto_enroll_discoveries",
        lambda _items: (_ for _ in ()).throw(AssertionError("strict discovery wrote before qualification")),
    )
    monkeypatch.setattr(
        profile_discovery_provider,
        "_warm_discovery_avatar_cache",
        lambda _items: (_ for _ in ()).throw(AssertionError("strict discovery warmed rejected raw data")),
    )

    result = asyncio.run(profile_discovery_provider.discover_new_creators(
        query_text="portrait lighting",
        platforms=["youtube"],
        market="US",
        limit=10,
        auto_enroll=False,
    ))
    assert result["counts"]["auto_enrolled"] == 0
    assert len(result["new_creators"]) == 1
    assert result["new_creators"][0]["followers"] == 500
    assert result["counts"]["filtered_low_reach"] == 0


def test_strict_discovery_keeps_concurrent_three_platform_overfetch_above_50(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def search_platform_content(platform: str, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for index in range(50):
            handle = f"camera_{platform}_{index}"
            profile_url = (
                f"https://www.youtube.com/@{handle}"
                if platform == "youtube"
                else f"https://www.instagram.com/{handle}/"
                if platform == "instagram"
                else f"https://www.tiktok.com/@{handle}"
            )
            items.append({
                "handle": handle,
                "channel_name": f"Camera filmmaker {index}",
                "channel_url": profile_url,
                "source_url": profile_url,
                "sample_title": "camera filmmaking portrait lighting",
                "followers": 5_000,
                "bio": "camera filmmaker creator",
            })
        return {"status": "ready", "items": items}

    monkeypatch.setattr(profile_discovery_provider, "search_platform_content", search_platform_content)
    monkeypatch.setattr(profile_discovery_provider.history_match, "annotate_platform_items", lambda raw, **_kwargs: raw)
    result = asyncio.run(profile_discovery_provider.discover_new_creators(
        query_text="portrait lighting camera filmmaking",
        platforms=["youtube", "instagram", "tiktok"],
        market="US",
        limit=150,
        per_platform_limit=50,
        auto_enroll=False,
    ))
    assert len(result["new_creators"]) == 150
    assert result["limit"] == 150


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def fetchone(self) -> dict[str, Any] | None:
        return dict(self.rows[0]) if self.rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.rows]


class _LocalKeyConn:
    def execute(self, sql: str, _params: tuple[Any, ...]) -> _Cursor:
        if "FROM vkpi_kol_search_sessions" in sql:
            return _Cursor([{"id": 51, "created_by": 7}])
        if "FROM vkpi_kol_search_session_items" in sql:
            return _Cursor([
                {
                    "kol_pool_id": 1,
                    "payload_json": "{}",
                    "platform": "youtube",
                    "handle": "Ｐortrait",
                    "profile_url": "https://www.youtube.com/@portrait?utm_source=x",
                    "raw_platform_data": json.dumps({"channel_id": "UCstable"}),
                }
            ])
        raise AssertionError(sql)


def test_local_dedupe_keys_are_loaded_from_server_session_and_prefer_native_id() -> None:
    aliases = profile_online_qualification.local_canonical_keys_for_session(
        51, conn=_LocalKeyConn(),
    )
    assert aliases == {
        "youtube:id:ucstable",
        "youtube:handle:portrait",
        "youtube:url:https://youtube.com/@portrait",
    }


def _attached_result() -> dict[str, Any]:
    qualified = profile_online_qualification.qualify_online_candidates(
        [_candidate(1)], query_text="portrait lighting", policy=_policy(), as_of=AS_OF,
    )["accepted"][0]
    qualified.update({
        "kol_pool_id": 901,
        "server_rank": 1,
        "global_unique_rank": 31,
        "contact_preview": {"status": "not_enriched", "channel_count": 0},
        "snapshot_revision": 1,
        "snapshot_id": "snapshotabc123",
    })
    qualified["qualification_evidence"].update({
        "kol_pool_id": 901,
        "canonical_fingerprint": qualified["canonical_fingerprint"],
        "snapshot_revision": 1,
        "snapshot_id": "snapshotabc123",
        "server_rank": 1,
        "global_unique_rank": 31,
    })
    return {
        "schema": "smart_online_net_new_qualified_v1",
        "policy_version": 1,
        "server_owned": True,
        "origin_lane": "online",
        "source": "platform_discovery_strict",
        "query": {"query_text": "portrait lighting", "source": "server_effective_query"},
        "status": "shortfall",
        "terminal": True,
        "snapshot_complete": True,
        "snapshot_revision": 1,
        "snapshot_id": "snapshotabc123",
        "target_count": 30,
        "evaluated_count": 1,
        "unique_evaluated_count": 1,
        "cell_evaluation_count": 2,
        "qualified_cell_count": 1,
        "multi_cell_candidate_count": 1,
        "strict_qualified_count": 1,
        "net_new_accepted_count": 1,
        "returned_count": 1,
        "pending_count": 0,
        "rejected_count": 0,
        "duplicate_local_count": 0,
        "duplicate_local_inventory_count": 0,
        "duplicate_online_count": 0,
        "provider_rounds": 1,
        "provider_calls": 1,
        "candidate_budget": 50,
        "candidate_budget_used": 1,
        "exhausted": True,
        "shortfall": 29,
        "shortfall_reasons": {"candidate_exhausted": 29},
        "rejected_by_reason": {"pending_content_evidence": 2},
        "items": [qualified],
    }


def test_attach_uses_server_effective_query_and_writes_authoritative_online_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        "app.domains.kol.search_sessions.get_session",
        lambda _session_id: {
            "query_text": "寻找美国摄影达人",
            "result_summary": {"local_qualification": {"schema": "smart_local_qualified_v2"}},
        },
    )

    def record(_session_id: int, items: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        captured.update(items=items, kwargs=kwargs)
        return {"items": items, "status": kwargs.get("status")}

    monkeypatch.setattr("app.domains.kol.search_sessions.record_items", record)
    result = search_sessions_online.attach_online_qualified_result(51, _attached_result())

    assert len(captured["items"]) == 1
    item = captured["items"][0]
    assert item["item_type"] == "online_qualified_candidate"
    assert item["kol_pool_id"] == 901
    assert item["payload"]["qualification_evidence"]["relevance"]["evidence"]
    assert result["online_qualification"]["snapshot_revision"] == 1
    assert result["online_qualification"]["unique_evaluated_count"] == 1
    assert result["online_qualification"]["cell_evaluation_count"] == 2
    assert result["online_qualification"]["qualified_cell_count"] == 1
    assert result["online_qualification"]["multi_cell_candidate_count"] == 1
    assert result["online_qualification"]["pending_content_evidence_count"] == 2
    assert result["online_qualification"]["content_evidence_followup"] == {
        "status": "not_scheduled",
        "candidate_count": 2,
        "counts_toward_target": False,
        "inline_provider_or_llm_calls": False,
    }


def test_attach_preserves_server_controlled_people_role_evidence_roundtrip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    result = _attached_result()
    item = result["items"][0]
    locked = targeted_search_contract.build_locked_term_groups(
        capability="",
        segment="night",
        role_terms=["camera operator"],
    )
    cell = {
        "query_cell_id": "segment_1_night",
        "objective": "prospective_growth",
        "segment": "night",
        "primary_query": "night camera operator",
        "required_scene_terms": ["night"],
        "required_role_terms": ["camera operator"],
        "product_evidence_required": False,
        "locked_term_groups": locked,
    }
    role_evidence = {
        "field": "bio",
        "term": "camera operator",
        "canonical_term": "camera operator",
        "observed_term": "camera operator",
        "evidence_group": "people_role",
        "evidence_relation": "direct_capability_or_scene_alias",
        "source": "server_allowlisted_alias_evidence",
    }
    item.update({
        "query_cell_id": cell["query_cell_id"],
        "query_cell_segment": "night",
        "query_cell_query": cell["primary_query"],
        "matched_query_cells": [cell],
    })
    item["match_evidence"].append(role_evidence)
    item["qualification_evidence"]["relevance"]["evidence"].append(role_evidence)
    monkeypatch.setattr(
        "app.domains.kol.search_sessions.get_session",
        lambda _session_id: {"query_text": "night camera operator", "result_summary": {}},
    )

    def record(_session_id: int, items: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        captured.update(items=items, kwargs=kwargs)
        return {"items": items}

    monkeypatch.setattr("app.domains.kol.search_sessions.record_items", record)
    search_sessions_online.attach_online_qualified_result(51, result)

    payload = captured["items"][0]["payload"]
    assert role_evidence in payload["match_evidence"]
    assert role_evidence in payload["qualification_evidence"]["relevance"]["evidence"]
    assert payload["matched_query_cells"][0]["required_role_terms"] == ["camera operator"]


@pytest.mark.parametrize(
    ("scope", "field", "value"),
    [
        ("item", "kol_pool_id", 902),
        ("proof", "canonical_fingerprint", "f" * 64),
        ("item", "snapshot_id", "other-snapshot"),
        ("proof", "snapshot_revision", 2),
        ("proof", "server_rank", 2),
        ("item", "global_unique_rank", 61),
    ],
)
def test_attach_rejects_online_rows_whose_proof_is_not_bound_to_the_row_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    scope: str,
    field: str,
    value: Any,
) -> None:
    result = _attached_result()
    item = result["items"][0]
    target = item if scope == "item" else item["qualification_evidence"]
    target[field] = value
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        "app.domains.kol.search_sessions.get_session",
        lambda _session_id: {"query_text": "portrait lighting", "result_summary": {}},
    )

    def record(_session_id: int, items: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        captured.update(items=items, kwargs=kwargs)
        return {"items": items}

    monkeypatch.setattr("app.domains.kol.search_sessions.record_items", record)
    attached = search_sessions_online.attach_online_qualified_result(51, result)

    assert captured["items"] == []
    assert attached["online_qualification"]["returned_count"] == 0
    assert attached["online_qualification"]["shortfall"] == 30


class _PruneConn:
    def __init__(self) -> None:
        self.sql: list[str] = []

    def execute(self, sql: str, _params: tuple[Any, ...]) -> _Cursor:
        self.sql.append(" ".join(sql.split()))
        return _Cursor([])


def test_online_authoritative_prune_never_deletes_local_recall_rows() -> None:
    conn = _PruneConn()
    _prune_authoritative_online_snapshot(
        conn,
        51,
        [{"item_type": "online_qualified_candidate", "dedupe_key": "online:youtube:id:1"}],
        summary={
            "_authoritative_snapshot_lane": "online",
            "online_qualification": {
                "schema": "smart_online_net_new_qualified_v1",
                "server_owned": True,
                "snapshot_complete": True,
            }
        },
    )
    assert len(conn.sql) == 1
    assert "item_type='online_qualified_candidate'" in conn.sql[0]
    assert "recall_candidate" not in conn.sql[0]


def test_merged_historical_snapshot_flags_never_trigger_cross_lane_prune() -> None:
    conn = _PruneConn()
    merged = {
        "kind": "kol_recall",
        "recall_snapshot_attached": True,
        "recall_snapshot_complete": True,
        "online_qualification": {
            "schema": "smart_online_net_new_qualified_v1",
            "server_owned": True,
            "snapshot_complete": True,
        },
    }
    _prune_authoritative_recall_snapshot(conn, 51, [], summary={**merged, "_authoritative_snapshot_lane": "online"})
    _prune_authoritative_online_snapshot(conn, 51, [], summary=merged)
    assert conn.sql == []
