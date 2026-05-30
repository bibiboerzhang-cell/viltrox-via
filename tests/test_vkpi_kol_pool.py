"""Tests for V-KPI KOL Pool helpers and DB lifecycle."""
from __future__ import annotations

import pytest

from app.db.connection import get_conn
from app.domains.kol import pool as kol_pool
from app.domains.kol.pool_common import _country_code, _country_name
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema


MARKER = "vkpi-kol-pool-unit"
EMAIL = "vkpi-kol-pool-unit@example.com"


@pytest.fixture(autouse=True)
def _ensure_schema():
    ensure_vkpi_product_industry_schema()
    yield


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

        conn.execute("DELETE FROM vkpi_kol_pool WHERE source_ref=? OR handle LIKE ?", (MARKER, f"{MARKER}%"))
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


def test_list_pool_omits_heavy_raw_platform_data_but_detail_keeps_it(seeded_staff):
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
    detail = kol_pool.get_item(row_id)["item"]
    assert detail["raw_platform_data"] == raw_json


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
