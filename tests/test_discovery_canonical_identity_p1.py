from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from app.domains.discovery import federation
from app.domains.kol import (
    discovery_filters,
    pool,
    profile_basics,
    profile_discovery,
    profile_online_inventory,
    search_sessions_items,
)
from app.domains.kol.identity import canonical_creator_aliases
from app.domains.kol.search_sessions_items import canonicalize_session_creator_items


CHANNEL_ID = "UCjYD2qQxWn9tY5mN0AbCdEf"


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return self.rows

    def fetchone(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None


@pytest.mark.parametrize(
    ("platform", "first_url", "second_url"),
    [
        (
            "facebook",
            "https://facebook.com/story.php?story_fbid=101&id=11",
            "https://facebook.com/story.php?story_fbid=202&id=22",
        ),
        (
            "facebook",
            "https://facebook.com/profile.php?id=11",
            "https://facebook.com/profile.php?id=22",
        ),
        (
            "facebook",
            "https://facebook.com/groups/11/posts/101",
            "https://facebook.com/groups/22/posts/202",
        ),
        (
            "instagram",
            "https://instagram.com/stories/alicecamera/101",
            "https://instagram.com/stories/bobcamera/202",
        ),
        (
            "instagram",
            "https://instagram.com/alicecamera/reel/101",
            "https://instagram.com/bobcamera/p/202",
        ),
        (
            "tiktok",
            "https://tiktok.com/@alicecamera/video/101",
            "https://tiktok.com/@bobcamera/video/202",
        ),
        (
            "tiktok",
            "https://tiktok.com/tag/camera",
            "https://tiktok.com/tag/cinema",
        ),
        (
            "twitter",
            "https://x.com/alicecamera/status/101",
            "https://x.com/bobcamera/status/202",
        ),
        (
            "youtube",
            "https://youtube.com/shorts/video-one",
            "https://youtube.com/shorts/video-two",
        ),
        (
            "youtube",
            "https://youtube.com/@alicecamera/shorts/video-one",
            "https://youtube.com/@bobcamera/live/video-two",
        ),
    ],
)
def test_non_profile_social_routes_never_bridge_distinct_explicit_handles(
    platform: str,
    first_url: str,
    second_url: str,
) -> None:
    first = canonical_creator_aliases(
        {"platform": platform, "handle": "alicecamera", "source_url": first_url}
    )
    second = canonical_creator_aliases(
        {"platform": platform, "handle": "bobcamera", "source_url": second_url}
    )

    assert first == {f"{platform}:handle:alicecamera"}
    assert second == {f"{platform}:handle:bobcamera"}
    assert first.isdisjoint(second)


@pytest.mark.parametrize(
    ("platform", "handle", "profile_url"),
    [
        ("youtube", "@alicecamera", "https://youtube.com/@alicecamera/videos"),
        ("instagram", "alicecamera", "https://instagram.com/alicecamera/"),
        ("tiktok", "@alicecamera", "https://tiktok.com/@alicecamera/"),
        ("facebook", "alice.camera", "https://facebook.com/alice.camera/"),
        ("twitter", "alicecamera", "https://x.com/alicecamera/"),
    ],
)
def test_explicit_profile_routes_still_bridge_the_same_creator_handle(
    platform: str,
    handle: str,
    profile_url: str,
) -> None:
    from_handle = canonical_creator_aliases({"platform": platform, "handle": handle})
    from_profile = canonical_creator_aliases(
        {"platform": platform, "profile_url": profile_url}
    )

    assert from_handle.intersection(from_profile)


def test_session_projection_does_not_fold_distinct_instagram_story_owners() -> None:
    folded = canonicalize_session_creator_items(
        [
            {
                "id": 1,
                "item_type": "new_creator",
                "dedupe_key": "alice-story",
                "source_url": "https://instagram.com/stories/alicecamera/101",
                "payload": {"platform": "instagram", "handle": "alicecamera"},
            },
            {
                "id": 2,
                "item_type": "new_creator",
                "dedupe_key": "bob-story",
                "source_url": "https://instagram.com/stories/bobcamera/202",
                "payload": {"platform": "instagram", "handle": "bobcamera"},
            },
        ]
    )

    assert [item["id"] for item in folded] == [1, 2]


def test_federation_does_not_fold_distinct_creators_through_content_routes() -> None:
    deduped = federation._dedupe(
        [
            {
                "platform": "facebook",
                "handle": "alice.camera",
                "source_url": "https://facebook.com/story.php?story_fbid=101&id=11",
            },
            {
                "platform": "facebook",
                "handle": "bob.camera",
                "source_url": "https://facebook.com/story.php?story_fbid=202&id=22",
            },
        ]
    )

    assert [item["handle"] for item in deduped] == ["alice.camera", "bob.camera"]


def test_record_items_keeps_distinct_creators_that_have_facebook_story_urls() -> None:
    class _Conn:
        def __init__(self) -> None:
            self.sql: list[str] = []
            self.commits = 0

        def execute(self, sql: str, _params: tuple[Any, ...] = ()) -> _Rows:
            self.sql.append(" ".join(sql.split()))
            if "SELECT id FROM vkpi_kol_search_sessions" in sql:
                return _Rows([{"id": 77}])
            raise AssertionError(sql)

        def commit(self) -> None:
            self.commits += 1

    conn = _Conn()
    written: list[dict[str, Any]] = []
    updated: dict[str, Any] = {}
    items = [
        {
            "id": 1,
            "item_type": "new_creator",
            "dedupe_key": "alice-story",
            "source_url": "https://facebook.com/story.php?story_fbid=101&id=11",
            "payload": {"platform": "facebook", "handle": "alice.camera"},
        },
        {
            "id": 2,
            "item_type": "new_creator",
            "dedupe_key": "bob-story",
            "source_url": "https://facebook.com/story.php?story_fbid=202&id=22",
            "payload": {"platform": "facebook", "handle": "bob.camera"},
        },
    ]

    result = search_sessions_items.record_items(
        77,
        items,
        summary={"items_written": 2},
        get_conn_fn=lambda: conn,
        upsert_item_fn=lambda _conn, session_id, item: (
            written.append(item) or {**item, "session_id": session_id}
        ),
        update_session_fn=lambda _conn, session_id, **values: updated.update(
            {"session_id": session_id, **values}
        ),
    )

    assert result["items_written"] == 2
    assert [item["id"] for item in written] == [1, 2]
    assert updated["summary"]["items_written"] == 2
    assert conn.commits == 1
    assert not any("DELETE" in sql or "UPDATE" in sql for sql in conn.sql)


def test_youtube_uc_handle_and_custom_handle_share_observed_native_alias() -> None:
    legacy_pool_row = {
        "kol_pool_id": 12297,
        "platform": "yt",
        "handle": CHANNEL_ID,
        "profile_url": f"https://m.youtube.com/channel/{CHANNEL_ID}/?feature=share",
    }
    provider_row = {
        "platform": "youtube",
        "handle": "@gcrustypork",
        "channel_id": CHANNEL_ID,
        "channel_url": "https://www.youtube.com/@GCrustyPork/?si=tracking",
    }

    overlap = canonical_creator_aliases(legacy_pool_row).intersection(
        canonical_creator_aliases(provider_row)
    )

    assert overlap == {f"youtube:id:{CHANNEL_ID.casefold()}"}
    assert "youtube:handle:gcrustypork" in canonical_creator_aliases(provider_row)


def test_provider_folds_real_shaped_uc_and_handle_rows_before_filtering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_search(_platform: str, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "status": "done",
            "items": [
                {
                    "handle": CHANNEL_ID,
                    "channel_id": CHANNEL_ID,
                    "channel_url": f"https://youtube.com/channel/{CHANNEL_ID}",
                    "channel_name": "G Crusty Pork Camera",
                    "sample_title": "cinema lens camera review",
                    "followers": 12_300,
                    "views": 8_000,
                },
                {
                    "handle": "@GCrustyPork",
                    "channel_id": CHANNEL_ID,
                    "channel_url": "https://www.youtube.com/@GCrustyPork/?si=abc",
                    "channel_name": "G Crusty Pork Camera",
                    "sample_title": "cinema lens camera review",
                    "followers": 12_300,
                    "views": 8_000,
                },
            ],
        }

    monkeypatch.setattr(profile_discovery, "search_platform_content", fake_search)
    monkeypatch.setattr(
        profile_discovery.history_match,
        "annotate_platform_items",
        lambda items, *, platform: items,
    )
    monkeypatch.setattr(profile_discovery, "_auto_enroll_discoveries", lambda _items: 0)
    monkeypatch.setattr(profile_discovery, "_warm_discovery_avatar_cache", lambda _items: None, raising=False)

    result = asyncio.run(
        profile_discovery.discover_new_creators(
            query_text="cinema lens camera reviewer",
            platforms=["youtube"],
            limit=10,
        )
    )

    assert len(result["new_creators"]) == 1
    assert result["new_creators"][0]["handle"] == "@GCrustyPork"
    assert result["new_creators"][0]["channel_id"] == CHANNEL_ID


def test_session_write_and_read_projection_fold_same_pool_and_uc_handle_shapes() -> None:
    rows = [
        {
            "id": 11,
            "dedupe_key": "existing:12297:first",
            "item_type": "existing_kol",
            "rank": 3,
            "kol_pool_id": 12297,
            "payload": {
                "platform": "youtube",
                "handle": CHANNEL_ID,
                "channel_id": CHANNEL_ID,
            },
        },
        {
            "id": 12,
            "dedupe_key": "existing:12297:again",
            "item_type": "existing_kol",
            "rank": 8,
            "kol_pool_id": 12297,
            "payload": {
                "platform": "youtube",
                "handle": "@GCrustyPork",
                "channel_id": CHANNEL_ID,
                "avatar_url": "https://images.example/avatar.jpg",
            },
        },
    ]

    folded = canonicalize_session_creator_items(rows)

    assert len(folded) == 1
    assert folded[0]["rank"] == 3
    assert folded[0]["dedupe_key"] == "discovery:pool:12297"
    assert folded[0]["payload"]["avatar_url"].endswith("avatar.jpg")


def test_session_identity_dedupe_closes_transitive_uc_handle_bridge() -> None:
    folded = canonicalize_session_creator_items([
        {
            "id": 1,
            "dedupe_key": "uc-only",
            "item_type": "new_creator",
            "payload": {"platform": "youtube", "handle": CHANNEL_ID},
        },
        {
            "id": 2,
            "dedupe_key": "handle-only",
            "item_type": "new_creator",
            "payload": {"platform": "youtube", "handle": "@GCrustyPork"},
        },
        {
            "id": 3,
            "dedupe_key": "bridge",
            "item_type": "new_creator",
            "payload": {
                "platform": "youtube",
                "handle": "@GCrustyPork",
                "channel_id": CHANNEL_ID,
            },
        },
    ])

    assert len(folded) == 1


def test_pool_identity_lookup_bridges_legacy_uc_row_to_provider_handle() -> None:
    class FakeSQLite:
        def execute(self, sql: str, _params: tuple[Any, ...] = ()) -> _Rows:
            if "FROM vkpi_kol_pool_aliases" in sql:
                return _Rows([])
            if "FROM vkpi_kol_pool" in sql:
                return _Rows([
                    {
                        "id": 4321,
                        "platform": "youtube",
                        "handle": CHANNEL_ID,
                        "profile_url": f"https://youtube.com/channel/{CHANNEL_ID}",
                        "raw_platform_data": "{}",
                    }
                ])
            raise AssertionError(sql)

    matches = profile_online_inventory._matching_pool_ids(
        FakeSQLite(),
        {
            "platform": "youtube",
            "handle": "@GCrustyPork",
            "channel_id": CHANNEL_ID,
            "profile_url": "https://youtube.com/@GCrustyPork",
        },
    )

    assert matches == {4321}


def test_unified_official_gate_blocks_confirmed_accounts_but_not_reviewers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert discovery_filters.discovery_account_gate_verdict({
        "platform": "youtube",
        "handle": "feelworldlofficial",
        "display_name": "FEELWORLD",
    }) == "brand_official"
    assert discovery_filters.discovery_account_gate_verdict({
        "platform": "youtube",
        "handle": "panavisionofficial",
        "display_name": "Panavision",
        "bio": "The official channel. Founded in 1954, we manufacture cinema camera systems.",
    }) == "brand_official"
    assert discovery_filters.discovery_account_gate_verdict({
        "platform": "tiktok",
        "handle": "tamron_europe",
        "display_name": "TAMRON",
        "profile_url": "https://www.tiktok.com/@tamron_europe",
        "bio": "Gear | Tips | Creator Inspo. By TAMRON Europe. All our links.",
    }) == "brand_official"
    assert discovery_filters.discovery_account_gate_verdict({
        "platform": "youtube",
        "handle": "viltroxreviewer",
        "display_name": "Alex - Viltrox reviewer",
        "bio": "I'm an independent filmmaker reviewing lenses and cameras.",
    }) == ""
    assert discovery_filters.discovery_account_gate_verdict({
        "platform": "youtube",
        "handle": "alex-films",
        "display_name": "Viltrox",
        "bio": "I'm an independent filmmaker reviewing Viltrox and other lenses.",
    }) == ""
    assert discovery_filters.discovery_account_gate_verdict({
        "platform": "tiktok",
        "handle": "tamron_europe_review",
        "display_name": "Alex reviews Tamron Europe",
        "profile_url": "https://www.tiktok.com/@tamron_europe_review",
        "bio": "I'm an independent photographer sharing my own Tamron lens reviews.",
    }) == ""
    assert discovery_filters.discovery_account_gate_verdict({
        "platform": "tiktok",
        "handle": "tamron_europe",
        "display_name": "TAMRON",
        "profile_url": "https://www.tiktok.com/@tamron_europe_fan",
        "bio": "Gear | Tips | Creator Inspo. By TAMRON Europe. All our links.",
    }) == ""
    assert discovery_filters.discovery_account_gate_verdict({
        "platform": "instagram",
        "profile_url": "https://www.instagram.com/viltrox.official/",
    }) == "own_brand"
    assert discovery_filters.discovery_account_gate_verdict({
        "platform": "youtube",
        "display_name": "Viltrox",
        "bio": "Official channel of our brand. We manufacture camera lenses and provide warranty service.",
    }) == "own_brand"

    monkeypatch.setattr(profile_basics, "_table_columns", lambda *_args: {"id"})
    with pytest.raises(ValueError, match="discovery_account_rejected:brand_official"):
        profile_basics.write_kol_profile_basics(
            None,
            {
                "platform": "youtube",
                "handle": "feelworldlofficial",
                "display_name": "FEELWORLD",
            },
            dry_run=True,
            conn=object(),
        )


def test_brand_gate_skips_full_lexicon_scan_when_ownership_signals_are_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Personal/neutral creators cannot satisfy the remaining official proof path."""
    brands = {"feelworld": ["feelworld"], "tamron": ["tamron"]}
    monkeypatch.setattr(
        discovery_filters,
        "_brand_identity_hit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("brand keyword scan must be skipped without ownership proof")
        ),
    )

    assert discovery_filters._competitor_brand_official(
        {
            "platform": "youtube",
            "handle": "alex-films",
            "display_name": "Alex reviews FEELWORLD",
            "bio": "I'm an independent filmmaker and camera reviewer.",
        },
        competitor_brands=brands,
    ) == ""
    assert discovery_filters._competitor_brand_official(
        {
            "platform": "youtube",
            "handle": "alex-films",
            "display_name": "Alex Films",
            "bio": "Camera tests and filmmaking tutorials.",
        },
        competitor_brands=brands,
    ) == ""


def test_discovery_funnel_reports_unique_creator_denominator() -> None:
    class FunnelConn:
        def execute(self, sql: str, _params: tuple[Any, ...] = ()) -> _Rows:
            assert "item_type IN" in sql
            return _Rows([
                {
                    "id": 1,
                    "item_type": "new_creator",
                    "kol_pool_id": None,
                    "source_url": f"https://youtube.com/channel/{CHANNEL_ID}",
                    "dedupe_key": "new:youtube:uc",
                    "payload_json": {
                        "platform": "youtube",
                        "handle": CHANNEL_ID,
                        "channel_id": CHANNEL_ID,
                    },
                },
                {
                    "id": 2,
                    "item_type": "existing_kol",
                    "kol_pool_id": None,
                    "source_url": "https://youtube.com/@GCrustyPork",
                    "dedupe_key": "existing:youtube:handle",
                    "payload_json": {
                        "platform": "youtube",
                        "handle": "@GCrustyPork",
                        "channel_id": CHANNEL_ID,
                    },
                },
                {
                    "id": 3,
                    "item_type": "recall_candidate",
                    "kol_pool_id": 9000,
                    "source_url": "https://instagram.com/another.creator/",
                    "dedupe_key": "recall:9000",
                    "payload_json": {
                        "platform": "instagram",
                        "handle": "another.creator",
                    },
                },
            ])

    assert pool._canonical_discovery_funnel_counts(FunnelConn()) == (3, 2)


def test_federation_pool_fallback_and_transitive_bridge_each_collapse_to_one() -> None:
    same_pool = federation._dedupe([
        {
            "kol_pool_id": 812,
            "platform": "youtube",
            "handle": "@old-camera-name",
        },
        {
            "kol_pool_id": 812,
            "platform": "youtube",
            "handle": "@renamed-camera-channel",
        },
    ])
    assert len(same_pool) == 1

    transitive = federation._dedupe([
        {
            "platform": "youtube",
            "handle": CHANNEL_ID,
        },
        {
            "platform": "youtube",
            "handle": "@GCrustyPork",
        },
        {
            "platform": "youtube",
            "handle": "@GCrustyPork",
            "channel_id": CHANNEL_ID,
        },
    ])
    assert len(transitive) == 1


def test_federation_apify_keeps_profile_avatar_status_and_content_thumbnail_separate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_search(platform: str, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        if platform == "youtube":
            return {
                "status": "done",
                "items": [{
                    "handle": "@creator",
                    "channel_id": CHANNEL_ID,
                    "channel_name": "Creator",
                    "channel_url": "https://www.youtube.com/@creator/?si=tracking",
                    "source_url": "https://youtube.com/watch?v=video123",
                    "avatar_url": "https://yt3.ggpht.com/profile-avatar?sz=88",
                    "avatar_url_status": "durable",
                    "thumbnail_url": "https://i.ytimg.com/vi/video123/hqdefault.jpg",
                }],
            }
        if platform == "tiktok":
            return {
                "status": "done",
                "items": [{
                    "handle": "second",
                    "channel_name": "Second",
                    "channel_url": "https://www.tiktok.com/@second/",
                    "avatar_url": "",
                    "avatar_url_status": "expired",
                    "thumbnail_url": "https://p16-sign.tiktokcdn.com/video-cover.jpeg",
                }],
            }
        return {"status": "done", "items": []}

    from app.services.intelligence import account_search_discovery

    monkeypatch.setattr(
        federation,
        "current_apify_execution_context",
        lambda: object(),
    )
    monkeypatch.setattr(
        account_search_discovery,
        "search_platform_content",
        fake_search,
    )

    rows, status = federation._apify_search("camera creators", 6)
    by_platform = {row["platform"]: row for row in rows}

    assert status == "ok"
    assert by_platform["youtube"]["profile_url"] == "https://youtube.com/@creator/"
    assert by_platform["youtube"]["channel_url"] == "https://youtube.com/@creator/"
    assert by_platform["youtube"]["avatar_url"] == "https://yt3.ggpht.com/profile-avatar?sz=88"
    assert by_platform["youtube"]["avatar_url_status"] == "durable"
    assert by_platform["youtube"]["thumbnail_url"].endswith("/video123/hqdefault.jpg")
    assert by_platform["tiktok"]["avatar_url"] == ""
    assert by_platform["tiktok"]["avatar_url_status"] == "expired"
    assert "video-cover" in by_platform["tiktok"]["thumbnail_url"]


def _create_identity_writer_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE vkpi_kol_pool (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pool_uid TEXT,
            platform TEXT NOT NULL,
            handle TEXT NOT NULL,
            display_name TEXT DEFAULT '',
            profile_url TEXT DEFAULT '',
            raw_platform_data TEXT NOT NULL DEFAULT '{}',
            profile_backfilled_at TEXT,
            duplicate_of_id INTEGER,
            viltrox_fit_score REAL,
            viltrox_fit_reason TEXT,
            UNIQUE(platform, handle)
        );
        CREATE TABLE vkpi_kol_pool_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kol_pool_id INTEGER NOT NULL,
            platform TEXT NOT NULL,
            handle TEXT NOT NULL,
            profile_url TEXT DEFAULT '',
            confidence REAL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT,
            UNIQUE(platform, handle)
        );
        """
    )
    conn.commit()


def test_alias_conflict_never_rebinds_or_swallows_failure() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _create_identity_writer_schema(conn)
    conn.execute(
        """
        INSERT INTO vkpi_kol_pool_aliases
            (kol_pool_id, platform, handle, metadata_json)
        VALUES (41, 'youtube', 'claimed-handle', '{}')
        """
    )

    with pytest.raises(RuntimeError, match="different pool master"):
        profile_basics._record_creator_identity_alias(
            conn,
            99,
            {"platform": "youtube", "handle": "@claimed-handle"},
            canonical_match=False,
        )

    owner = conn.execute(
        "SELECT kol_pool_id FROM vkpi_kol_pool_aliases WHERE platform='youtube' AND handle='claimed-handle'"
    ).fetchone()
    assert int(owner["kol_pool_id"]) == 41

    missing_alias_table = sqlite3.connect(":memory:")
    missing_alias_table.row_factory = sqlite3.Row
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        profile_basics._record_creator_identity_alias(
            missing_alias_table,
            99,
            {"platform": "youtube", "handle": "@cannot-be-recorded"},
            canonical_match=False,
        )


def test_canonical_write_fails_closed_on_ambiguous_or_unavailable_alias_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        profile_online_inventory,
        "_matching_pool_ids",
        lambda *_args, **_kwargs: {41, 99},
    )
    with pytest.raises(RuntimeError, match="ambiguous across multiple pool masters"):
        profile_basics._canonical_existing_pool_id(
            object(),
            {"platform": "youtube", "handle": "@ambiguous"},
        )

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _create_identity_writer_schema(conn)
    conn.execute("DROP TABLE vkpi_kol_pool_aliases")
    monkeypatch.undo()
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        profile_basics.write_kol_profile_basics(
            None,
            {"platform": "youtube", "handle": "@unavailable-alias-table"},
            dry_run=False,
            conn=conn,
        )
    assert conn.in_transaction is False
    assert conn.execute("SELECT COUNT(*) AS n FROM vkpi_kol_pool").fetchone()["n"] == 0


def test_postgres_identity_lock_is_transaction_scoped_and_deterministic() -> None:
    class PostgresCompatConnection:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[Any, ...]]] = []

        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Rows:
            self.calls.append((sql, params))
            return _Rows([])

    identity = {
        "platform": "youtube",
        "handle": "@GCrustyPork",
        "channel_id": CHANNEL_ID,
    }
    first = PostgresCompatConnection()
    second = PostgresCompatConnection()
    profile_basics._lock_creator_identity_write_boundary(first, identity)
    profile_basics._lock_creator_identity_write_boundary(second, identity)

    assert first.calls
    assert all("pg_advisory_xact_lock" in sql for sql, _params in first.calls)
    assert [params for _sql, params in first.calls] == [
        params for _sql, params in second.calls
    ]


def test_sqlite_concurrent_canonical_writers_create_one_master(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "canonical-creator.sqlite3"
    setup = sqlite3.connect(db_path)
    setup.row_factory = sqlite3.Row
    _create_identity_writer_schema(setup)
    setup.close()
    monkeypatch.setattr(
        "app.domains.kol.contact_acquisition_queue.enqueue_contact_acquisition",
        lambda *_args, **_kwargs: None,
    )
    barrier = threading.Barrier(2)
    profiles = [
        {
            "platform": "youtube",
            "handle": CHANNEL_ID,
            "display_name": "G Crusty Pork Camera",
            "profile_url": f"https://youtube.com/channel/{CHANNEL_ID}",
            "raw_platform_data": json.dumps({
                "discovery_identity_v1": {"channel_id": CHANNEL_ID}
            }),
        },
        {
            "platform": "youtube",
            "handle": "@GCrustyPork",
            "display_name": "G Crusty Pork Camera",
            "profile_url": "https://youtube.com/@GCrustyPork",
            "raw_platform_data": json.dumps({
                "discovery_identity_v1": {"channel_id": CHANNEL_ID}
            }),
        },
    ]

    def write(profile: dict[str, Any]) -> dict[str, Any]:
        conn = sqlite3.connect(db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        barrier.wait(timeout=5)
        try:
            return profile_basics.write_kol_profile_basics(
                None,
                profile,
                dry_run=False,
                conn=conn,
            )
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(write, profiles))

    verify = sqlite3.connect(db_path)
    verify.row_factory = sqlite3.Row
    masters = verify.execute("SELECT id FROM vkpi_kol_pool").fetchall()
    aliases = verify.execute(
        "SELECT kol_pool_id, handle FROM vkpi_kol_pool_aliases ORDER BY handle"
    ).fetchall()
    verify.close()

    assert len(masters) == 1
    assert {int(result["kol_pool_id"]) for result in results} == {int(masters[0]["id"])}
    assert {str(row["handle"]) for row in aliases} == {
        CHANNEL_ID,
        "gcrustypork",
    }
    assert {int(row["kol_pool_id"]) for row in aliases} == {int(masters[0]["id"])}
