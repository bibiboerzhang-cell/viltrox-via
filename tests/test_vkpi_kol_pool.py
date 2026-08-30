"""Tests for V-KPI KOL Pool helpers and DB lifecycle."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.db import connection as db_connection
from app.db.connection import get_conn
from app.domains.discovery import enroll, federation
from app.domains.kol import pool as kol_pool
from app.domains.kol.pool_common import _country_code, _country_name
from app.platform.db import schema_product_industry as product_industry_schema
from app.platform.db import schema_kol_ops as kol_ops_schema
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema


MARKER = "vkpi-kol-pool-unit"
EMAIL = "vkpi-kol-pool-unit@example.com"


@pytest.fixture(scope="module", autouse=True)
def _kol_pool_test_db(tmp_path_factory: pytest.TempPathFactory):
    """Run this module against a private SQLite database.

    These tests exercise the production KOL Pool schema guard and real SQL,
    while the identity tables it depends on are owned by other schema layers.
    Seed only those three base tables here so this module never inherits the
    repository ``submissions.db`` or another test module's schema state.
    """
    db_path = (tmp_path_factory.mktemp("kol-pool") / "kol-pool.db").resolve()
    repository_db = (Path(__file__).resolve().parents[1] / "submissions.db").resolve()
    assert db_path != repository_db

    old_db_path = db_connection.DB_PATH
    old_runtime_backend = db_connection.DB_RUNTIME_BACKEND
    old_runtime_url = db_connection.DB_RUNTIME_URL
    old_product_ready = product_industry_schema._SCHEMA_READY
    old_kol_ready = kol_ops_schema._SCHEMA_READY

    db_connection.close_db_runtime_sync()
    db_connection.DB_PATH = db_path
    db_connection.DB_RUNTIME_BACKEND = "sqlite"
    db_connection.DB_RUNTIME_URL = ""
    product_industry_schema._SCHEMA_READY = False
    kol_ops_schema._SCHEMA_READY = False
    kol_pool._clear_kol_pool_read_cache()

    conn = get_conn()
    try:
        actual_path = Path(str(conn.execute("PRAGMA database_list").fetchone()[2])).resolve()
        assert actual_path == db_path
        conn.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT,
                email TEXT UNIQUE,
                password_hash TEXT,
                name TEXT,
                status TEXT DEFAULT 'pending',
                role TEXT DEFAULT 'creator',
                email_verified INTEGER DEFAULT 0
            );
            CREATE TABLE staff (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE,
                role TEXT NOT NULL DEFAULT 'readonly',
                permissions_json TEXT NOT NULL DEFAULT '{}',
                mfa_enabled INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                invited_at TEXT,
                is_owner INTEGER NOT NULL DEFAULT 0,
                email_domain_verified INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE kols (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_name TEXT NOT NULL,
                channel_url TEXT,
                platform TEXT NOT NULL,
                country TEXT,
                follower_count INTEGER DEFAULT 0,
                avg_views INTEGER DEFAULT 0,
                contact_status TEXT DEFAULT 'cold',
                notes TEXT,
                assigned_staff_id INTEGER,
                created_by_staff_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        ensure_vkpi_product_industry_schema()
        conn.commit()
        yield db_path
    finally:
        kol_pool._clear_kol_pool_read_cache()
        db_connection.close_db_runtime_sync()
        db_connection.DB_PATH = old_db_path
        db_connection.DB_RUNTIME_BACKEND = old_runtime_backend
        db_connection.DB_RUNTIME_URL = old_runtime_url
        product_industry_schema._SCHEMA_READY = old_product_ready
        kol_ops_schema._SCHEMA_READY = old_kol_ready


def _staff_context(staff_id: int) -> dict[str, object]:
    return {"id": staff_id, "staff_id": staff_id, "role": "admin", "is_owner": True}


@pytest.fixture
def seeded_staff():
    conn = get_conn()
    now = "2026-05-01T10:00:00Z"

    def cleanup() -> None:
        kol_ids = [
            int(row["id"])
            for row in conn.execute("SELECT id FROM kols WHERE notes LIKE ? OR channel_name LIKE ?", (f"%{MARKER}%", f"{MARKER}%")).fetchall()
        ]
        staff_ids = [
            int(row["id"])
            for row in conn.execute(
                "SELECT s.id FROM staff s JOIN users u ON u.id=s.user_id WHERE u.email=?",
                (EMAIL,),
            ).fetchall()
        ]
        user_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM users WHERE email=?", (EMAIL,)).fetchall()]

        conn.execute("DELETE FROM vkpi_kol_pool WHERE source_ref=? OR handle LIKE ?", (MARKER, f"%{MARKER}%"))
        for kol_id in kol_ids:
            conn.execute("DELETE FROM kols WHERE id=?", (kol_id,))
        for staff_id in staff_ids:
            conn.execute("DELETE FROM staff WHERE id=?", (staff_id,))
        for user_id in user_ids:
            conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        conn.commit()

    try:
        cleanup()
        conn.execute(
            """INSERT INTO users
               (created_at, email, password_hash, name, status, role, email_verified)
               VALUES (?,?,?,?,?,?,?)""",
            (now, EMAIL, "v2:00:00", "Kol Pool Unit", "approved", "admin", 1),
        )
        user_id = int(conn.execute("SELECT id FROM users WHERE email=?", (EMAIL,)).fetchone()["id"])
        conn.execute(
            """INSERT INTO staff
               (user_id, role, permissions_json, mfa_enabled, active, invited_at, is_owner, email_domain_verified)
               VALUES (?,?,?,?,?,?,?,?)""",
            (user_id, "admin", '{"vkpi":"admin"}', 0, 1, now, 1, 1),
        )
        staff_id = int(conn.execute("SELECT id FROM staff WHERE user_id=?", (user_id,)).fetchone()["id"])
        conn.commit()
        yield {"staff_id": staff_id}
    finally:
        cleanup()


def test_normalize_item_preserves_zero_metric_values():
    item = kol_pool._normalize_item(
        {
            "platform": "ig",
            "handle": "zero-metrics",
            "followers": 0,
            "following": 0,
            "posts_count": 0,
            "avg_views": 0,
            "avg_likes": 0,
            "avg_comments": 0,
            "engagement_rate": 0,
        }
    )

    assert item["platform"] == "instagram"
    assert item["followers"] == 0
    assert item["following"] == 0
    assert item["posts_count"] == 0
    assert item["avg_views"] == 0
    assert item["avg_likes"] == 0
    assert item["avg_comments"] == 0
    assert item["engagement_rate"] == 0


def test_pool_api_dtos_omit_heavy_raw_platform_data(seeded_staff):
    conn = get_conn()
    now = "2026-05-01T10:00:00Z"
    handle = f"{MARKER}-raw-list"
    raw_json = '{"profile":{"items":[{"description":"large raw payload"}]},"videos":[{"title":"sample"}]}'
    conn.execute(
        """
        INSERT INTO vkpi_kol_pool
          (pool_uid, platform, handle, profile_url, display_name, avatar_url, bio, email,
           followers, following, posts_count, avg_views, avg_likes, avg_comments,
           engagement_rate, source_type, source_ref, raw_platform_data, created_by_staff_id,
           last_seen_at, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            f"{MARKER}-raw-list-uid",
            "youtube",
            handle,
            "https://youtube.com/@raw-list",
            "Raw List",
            "",
            "list row should stay light",
            "",
            100,
            None,
            3,
            50,
            5,
            1,
            0.1,
            "unit",
            MARKER,
            raw_json,
            seeded_staff["staff_id"],
            now,
            now,
            now,
        ),
    )
    conn.commit()
    kol_pool._clear_kol_pool_read_cache()

    listed = kol_pool.list_pool(query=handle, limit=10)
    assert listed["items"]
    assert "raw_platform_data" not in listed["items"][0]

    row_id = int(listed["items"][0]["id"])
    conn.execute(
        """
        INSERT INTO vkpi_kol_video_evidence
          (kol_pool_id, content_url, platform, title, view_count, evidence_type, source)
        VALUES (?,?,?,?,?,?,?)
        """,
        (row_id, "https://youtube.com/watch?v=abcdefghijk", "youtube", "Evidence", 42, "video", "unit"),
    )
    conn.commit()
    detail = kol_pool.get_item(row_id)["item"]
    assert "raw_platform_data" not in detail
    assert detail["video_evidence"][0]["content_url"] == "https://youtube.com/watch?v=abcdefghijk"
    assert detail["video_evidence"][0]["view_count"] == 42


def test_import_items_dedups_by_platform_handle_and_updates_row(seeded_staff):
    staff = _staff_context(seeded_staff["staff_id"])

    first = kol_pool.import_items(
        [
            {
                "platform": "instagram",
                "handle": f"{MARKER}-dupe",
                "display_name": "First Name",
                "followers": 100,
            }
        ],
        source_type="unit",
        source_ref=MARKER,
        staff=staff,
    )
    second = kol_pool.import_items(
        [
            {
                "platform": "instagram",
                "handle": f"{MARKER}-dupe",
                "display_name": "Updated Name",
                "followers": 200,
            }
        ],
        source_type="unit",
        source_ref=MARKER,
        staff=staff,
    )

    rows = kol_pool.list_pool(query=f"{MARKER}-dupe", limit=10)["items"]

    assert first["imported"] == 1
    assert second["imported"] == 1
    assert len(rows) == 1
    assert rows[0]["display_name"] == "Updated Name"
    assert int(rows[0]["followers"]) == 200


def test_canonical_thin_import_preserves_rich_master_and_merges_raw_aliases(seeded_staff):
    staff = _staff_context(seeded_staff["staff_id"])
    channel_id = "UCvkpiKolPoolRichMaster12345"
    first = kol_pool.import_items(
        [{
            "platform": "youtube",
            "handle": channel_id,
            "channel_id": channel_id,
            "display_name": "Rich Camera Creator",
            "profile_url": f"https://youtube.com/channel/{channel_id}",
            "avatar_url": "https://images.example/rich-avatar.jpg",
            "bio": "Independent filmmaker and lens reviewer",
            "email": "rich-creator@example.test",
            "followers": 12000,
            "following": 321,
            "posts_count": 87,
            "avg_views": 4500,
            "avg_likes": 640,
            "avg_comments": 33,
            "engagement_rate": 4.2,
            "identity_aliases": ["legacy-uc"],
            "discovery_identity_v1": {
                "channel_id": channel_id,
                "identity_aliases": ["legacy-handle"],
                "rich_only": "keep-me",
            },
        }],
        source_type="unit-rich",
        source_ref=MARKER,
        staff=staff,
    )
    second = kol_pool.import_items(
        [{
            "platform": "youtube",
            "handle": "@rich-camera-alias",
            "channel_id": channel_id,
            "display_name": "   ",
            "profile_url": " ",
            "avatar_url": " ",
            "bio": "",
            "email": None,
            "identity_aliases": ["fresh-handle"],
            "discovery_identity_v1": {
                "channel_id": channel_id,
                "identity_aliases": ["fresh-handle"],
            },
        }],
        source_type="unit-thin",
        source_ref=MARKER,
        staff=staff,
    )

    assert second["canonical_matches"] == 1
    assert int(second["items"][0]["id"]) == int(first["items"][0]["id"])
    row = dict(get_conn().execute(
        "SELECT * FROM vkpi_kol_pool WHERE id=?",
        (int(first["items"][0]["id"]),),
    ).fetchone())
    assert row["display_name"] == "Rich Camera Creator"
    assert row["profile_url"] == f"https://youtube.com/channel/{channel_id}"
    assert row["avatar_url"] == "https://images.example/rich-avatar.jpg"
    assert row["bio"] == "Independent filmmaker and lens reviewer"
    assert row["email"] == "rich-creator@example.test"
    assert [row[key] for key in (
        "followers", "following", "posts_count", "avg_views", "avg_likes", "avg_comments",
    )] == [12000, 321, 87, 4500, 640, 33]
    assert float(row["engagement_rate"]) == 4.2
    raw = json.loads(row["raw_platform_data"])
    assert raw["identity_aliases"] == ["legacy-uc", "fresh-handle"]
    assert raw["discovery_identity_v1"]["identity_aliases"] == [
        "legacy-handle", "fresh-handle",
    ]
    assert raw["discovery_identity_v1"]["rich_only"] == "keep-me"


def test_federation_enroll_persists_profile_and_avatar_evidence_without_thumbnail_promotion(
    seeded_staff,
    monkeypatch: pytest.MonkeyPatch,
):
    handle = f"{MARKER}-avatar-e2e"
    profile_url = f"https://www.youtube.com/@{handle}/?si=tracking"
    avatar_url = "https://yt3.ggpht.com/profile-avatar-e2e"
    thumbnail_url = "https://i.ytimg.com/vi/e2e-video/hqdefault.jpg"

    async def fake_search(platform: str, *_args, **_kwargs):
        if platform != "youtube":
            return {"status": "done", "items": []}
        return {
            "status": "done",
            "items": [{
                "handle": f"@{handle}",
                "channel_id": "UCvkpiFederationAvatarE2E12345",
                "channel_name": "Federated Creator",
                "channel_url": profile_url,
                "avatar_url": avatar_url,
                "avatar_url_status": "durable",
                "thumbnail_url": thumbnail_url,
                "source_url": "https://youtube.com/watch?v=e2e-video",
            }],
        }

    from app.domains.discovery import buildout
    from app.domains.intelligence import semantic_recall
    from app.domains.platform import event_ledger
    from app.services.intelligence import account_search_discovery

    monkeypatch.setattr(federation, "current_apify_execution_context", lambda: object())
    monkeypatch.setattr(
        federation,
        "list_providers",
        lambda _kind="": [{"name": "apify_search", "enabled": True}],
    )
    monkeypatch.setattr(account_search_discovery, "search_platform_content", fake_search)
    monkeypatch.setattr(semantic_recall, "unified_recall", lambda *_args, **_kwargs: {"results": []})
    monkeypatch.setattr(buildout, "ignite_profile_buildout", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(event_ledger, "emit", lambda *_args, **_kwargs: None)

    result = enroll.federated_discover_and_enroll(
        "camera creators",
        limit=3,
        staff=_staff_context(seeded_staff["staff_id"]),
        include_external=True,
    )

    assert result["enrolled"] == 1
    row = dict(get_conn().execute(
        "SELECT profile_url, avatar_url, raw_platform_data FROM vkpi_kol_pool WHERE id=?",
        (int(result["enrolled_ids"][0]),),
    ).fetchone())
    assert row["profile_url"] == f"https://youtube.com/@{handle}/"
    assert row["profile_url"] != f"@{handle}"
    assert row["avatar_url"] == avatar_url
    assert row["avatar_url"] != thumbnail_url
    raw = json.loads(row["raw_platform_data"])
    assert raw["profile_url"] == row["profile_url"]
    assert raw["channel_url"] == row["profile_url"]
    assert raw["avatar_url"] == avatar_url
    assert raw["avatar_url_status"] == "durable"
    assert raw["thumbnail_url"] == thumbnail_url


def test_enroll_canonical_bridge_merges_evidence_and_blocks_handle_only_duplicate(seeded_staff):
    channel_id = "UCvkpiEnrollCanonicalBridge12345"
    handle = f"{MARKER}-canonical-alias"
    seeded = kol_pool.import_items(
        [{
            "platform": "youtube",
            "handle": channel_id,
            "channel_id": channel_id,
            "display_name": "Canonical Bridge Creator",
            "rich_only": "preserve-me",
        }],
        source_type="unit",
        source_ref=MARKER,
        staff=_staff_context(seeded_staff["staff_id"]),
    )
    master_id = int(seeded["items"][0]["id"])
    profile_url = f"https://youtube.com/@{handle}/"
    avatar_url = "https://yt3.ggpht.com/canonical-bridge-avatar"

    bridged = enroll.enroll_candidates([{
        "source": "apify_search",
        "platform": "youtube",
        "handle": f"@{handle}",
        "channel_id": channel_id,
        "name": "Canonical Bridge Creator",
        "profile_url": profile_url,
        "avatar_url": avatar_url,
        "avatar_url_status": "durable",
        "thumbnail_url": "https://i.ytimg.com/vi/bridge/cover.jpg",
    }])
    assert bridged["enrolled"] == 0
    assert bridged["skipped"] == 1

    row = dict(get_conn().execute(
        "SELECT profile_url, avatar_url, raw_platform_data FROM vkpi_kol_pool WHERE id=?",
        (master_id,),
    ).fetchone())
    assert row["profile_url"] == profile_url
    assert row["avatar_url"] == avatar_url
    raw = json.loads(row["raw_platform_data"])
    assert raw["rich_only"] == "preserve-me"
    assert raw["avatar_url_status"] == "durable"
    alias = get_conn().execute(
        "SELECT kol_pool_id FROM vkpi_kol_pool_aliases WHERE platform=? AND handle=?",
        ("youtube", handle),
    ).fetchone()
    assert alias and int(alias["kol_pool_id"]) == master_id

    handle_only = enroll.enroll_candidates([{
        "source": "apify_search",
        "platform": "youtube",
        "handle": f"@{handle}",
        "name": "Canonical Bridge Creator",
    }])
    assert handle_only["enrolled"] == 0
    assert handle_only["skipped"] == 1
    assert int(get_conn().execute(
        "SELECT COUNT(*) AS n FROM vkpi_kol_pool WHERE id=? OR handle=?",
        (master_id, f"@{handle}"),
    ).fetchone()["n"]) == 1


def test_enroll_canonical_avatar_quality_keeps_durable_and_prewarms_accepted_ephemeral(
    seeded_staff,
    monkeypatch: pytest.MonkeyPatch,
):
    from app.domains.kol import profile_discovery_provider

    warmed: list[str] = []
    monkeypatch.setattr(
        profile_discovery_provider,
        "_warm_discovery_avatar_cache",
        lambda items, **_kwargs: warmed.extend(str(item["avatar_url"]) for item in items),
    )
    live_ephemeral = "https://scontent.cdninstagram.com/avatar.jpg?oe=FFFFFFFF&_nc_sid=test"

    durable_id = int(kol_pool.import_items(
        [{
            "platform": "instagram",
            "handle": f"{MARKER}-durable-old",
            "account_id": "ig-canonical-durable-1",
            "avatar_url": "https://images.example/incumbent-durable.jpg",
            "avatar_url_status": "durable",
        }],
        source_type="unit",
        source_ref=MARKER,
        staff=_staff_context(seeded_staff["staff_id"]),
    )["items"][0]["id"])
    durable_bridge = f"{MARKER}-durable-new"
    enroll.enroll_candidates([{
        "source": "apify_search",
        "platform": "instagram",
        "handle": durable_bridge,
        "account_id": "ig-canonical-durable-1",
        "avatar_url": live_ephemeral,
        "avatar_url_status": "ephemeral",
    }])
    durable = dict(get_conn().execute(
        "SELECT avatar_url, raw_platform_data FROM vkpi_kol_pool WHERE id=?",
        (durable_id,),
    ).fetchone())
    durable_raw = json.loads(durable["raw_platform_data"])
    assert durable["avatar_url"] == "https://images.example/incumbent-durable.jpg"
    assert durable_raw["avatar_url_status"] == "durable"
    assert durable_raw["avatar_observation_v1"]["decision"] == "kept_incumbent"
    assert warmed == []
    assert int(get_conn().execute(
        "SELECT kol_pool_id FROM vkpi_kol_pool_aliases WHERE platform=? AND handle=?",
        ("instagram", durable_bridge),
    ).fetchone()["kol_pool_id"]) == durable_id

    missing_id = int(kol_pool.import_items(
        [{
            "platform": "instagram",
            "handle": f"{MARKER}-missing-old",
            "account_id": "ig-canonical-missing-1",
            "avatar_url_status": "missing",
        }],
        source_type="unit",
        source_ref=MARKER,
        staff=_staff_context(seeded_staff["staff_id"]),
    )["items"][0]["id"])
    enroll.enroll_candidates([{
        "source": "apify_search",
        "platform": "instagram",
        "handle": f"{MARKER}-missing-new",
        "account_id": "ig-canonical-missing-1",
        "avatar_url": live_ephemeral,
        "avatar_url_status": "ephemeral",
    }])
    missing = dict(get_conn().execute(
        "SELECT avatar_url, raw_platform_data FROM vkpi_kol_pool WHERE id=?",
        (missing_id,),
    ).fetchone())
    missing_raw = json.loads(missing["raw_platform_data"])
    assert missing["avatar_url"] == live_ephemeral
    assert missing_raw["avatar_url_status"] == "ephemeral"
    assert missing_raw["avatar_observation_v1"]["proxy_prewarm_requested"] is True
    assert warmed == [live_ephemeral]


def test_missing_and_complete_filters_use_real_pool_columns(seeded_staff):
    staff = _staff_context(seeded_staff["staff_id"])
    result = kol_pool.import_items(
        [
            {
                "platform": "instagram",
                "handle": f"{MARKER}-complete",
                "display_name": "Complete Candidate",
                "avatar_url": "https://example.com/a.jpg",
                "avg_views": 1000,
                "engagement_rate": 2.5,
            },
            {
                "platform": "instagram",
                "handle": f"{MARKER}-missing",
                "display_name": "Missing Candidate",
            },
        ],
        source_type="unit",
        source_ref=MARKER,
        staff=staff,
    )
    conn = get_conn()
    conn.execute(
        "UPDATE vkpi_kol_pool SET viltrox_fit_score=? WHERE handle=?",
        (88, f"{MARKER}-complete"),
    )
    conn.commit()

    missing = kol_pool.list_pool(query=MARKER, data_status="missing", limit=10)["items"]
    complete = kol_pool.list_pool(query=MARKER, data_status="complete", limit=10)["items"]

    assert result["imported"] == 2
    assert {row["handle"] for row in missing} == {f"{MARKER}-missing"}
    assert {row["handle"] for row in complete} == {f"{MARKER}-complete"}


def test_list_pool_supports_offset_pagination(seeded_staff):
    staff = _staff_context(seeded_staff["staff_id"])
    kol_pool.import_items(
        [
            {
                "platform": "instagram",
                "handle": f"{MARKER}-page-{idx}",
                "display_name": f"Paged Candidate {idx}",
                "followers": 100 + idx,
            }
            for idx in range(3)
        ],
        source_type="unit",
        source_ref=MARKER,
        staff=staff,
    )

    first = kol_pool.list_pool(query=f"{MARKER}-page", sort_by="followers", limit=1, offset=0)["items"]
    second = kol_pool.list_pool(query=f"{MARKER}-page", sort_by="followers", limit=1, offset=1)["items"]

    assert len(first) == 1
    assert len(second) == 1
    assert first[0]["handle"] != second[0]["handle"]


def test_country_distribution_and_country_filter_normalize_variants(seeded_staff):
    conn = get_conn()
    now = "2026-05-01T10:00:00Z"
    rows = [
        (f"{MARKER}-country-us-cn", "instagram", "美国", "Country US CN"),
        (f"{MARKER}-country-us-code", "youtube", "US", "Country US Code"),
        (f"{MARKER}-country-be-cn", "instagram", "比利时", "Country Belgium CN"),
        (f"{MARKER}-country-tw", "youtube", "台湾", "Country TW CN"),
        (f"{MARKER}-country-hk", "instagram", "Hong Kong", "Country HK EN"),
    ]
    for handle, platform, country, display_name in rows:
        conn.execute(
            """
            INSERT INTO vkpi_kol_pool
              (pool_uid, platform, handle, profile_url, display_name, avatar_url, bio, email,
               country, followers, following, posts_count, avg_views, avg_likes, avg_comments,
               engagement_rate, source_type, source_ref, raw_platform_data, created_by_staff_id,
               last_seen_at, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                f"{handle}-uid",
                platform,
                handle,
                f"https://example.com/{handle}",
                display_name,
                "",
                "",
                "",
                country,
                100,
                None,
                1,
                10,
                1,
                0,
                1.0,
                "unit",
                MARKER,
                "{}",
                seeded_staff["staff_id"],
                now,
                now,
                now,
            ),
        )
    conn.commit()
    kol_pool._clear_kol_pool_read_cache()

    summary = kol_pool.summary()
    distribution = {row["country_code"]: row for row in summary["country_distribution"]}
    us_rows = kol_pool.list_pool(query=MARKER, country="United States", limit=10)["items"]
    be_rows = kol_pool.list_pool(query=MARKER, country="Belgium", limit=10)["items"]
    tw_rows = kol_pool.list_pool(query=MARKER, country="China TW", limit=10)["items"]
    hk_rows = kol_pool.list_pool(query=MARKER, country="China HK", limit=10)["items"]

    assert distribution["US"]["country_name"] == "United States"
    assert distribution["BE"]["country_name"] == "Belgium"
    assert _country_code("中国台湾") == "TW"
    assert _country_name("Taiwan", "TW") == "China TW"
    assert _country_code("Hong Kong") == "HK"
    assert _country_name("Hong Kong", "HK") == "China HK"
    assert {row["handle"] for row in us_rows} == {f"{MARKER}-country-us-cn", f"{MARKER}-country-us-code"}
    assert {row["handle"] for row in be_rows} == {f"{MARKER}-country-be-cn"}
    assert {row["handle"] for row in tw_rows} == {f"{MARKER}-country-tw"}
    assert {row["handle"] for row in hk_rows} == {f"{MARKER}-country-hk"}


def test_batch_enrich_is_capped_and_skips_unsupported_platforms(seeded_staff):
    staff = _staff_context(seeded_staff["staff_id"])
    imported = kol_pool.import_items(
        [
            {"platform": "other", "handle": f"{MARKER}-unsupported-{idx}", "display_name": f"Unsupported {idx}"}
            for idx in range(7)
        ],
        source_type="unit",
        source_ref=MARKER,
        staff=staff,
    )
    ids = [int(row["id"]) for row in imported["items"]]

    result = kol_pool.batch_enrich_items(ids=ids, limit=10, staff=staff)

    assert result["attempted"] == 5
    assert result["capped"] is True
    assert result["enriched"] == 0
    assert {item["reason"] for item in result["skipped"]} == {"unsupported"}


def test_promote_to_main_creates_then_reuses_linked_kol(seeded_staff):
    staff = _staff_context(seeded_staff["staff_id"])
    imported = kol_pool.import_items(
        [
            {
                "platform": "instagram",
                "handle": f"{MARKER}-promote",
                "display_name": "Promote Candidate",
                "profile_url": f"https://www.instagram.com/{MARKER}-promote/",
                "avatar_url": "https://example.com/avatar.jpg",
                "followers": 1234,
                "avg_views": 456,
            }
        ],
        source_type="unit",
        source_ref=MARKER,
        staff=staff,
    )
    pool_id = int(imported["items"][0]["id"])

    created = kol_pool.promote_to_main(pool_id, staff=staff)
    reused = kol_pool.promote_to_main(pool_id, staff=staff)

    assert created["linked"] is True
    assert created["mode"] == "created"
    assert int(created["main_kol_id"]) > 0
    assert reused["linked"] is True
    assert reused["mode"] == "already_linked"
    assert int(reused["main_kol_id"]) == int(created["main_kol_id"])
