from __future__ import annotations

import json
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any

import pytest

from app.domains.kol import history_match, pool, pool_common, pool_summary
from app.domains.kol.pool_read_projection import (
    build_pool_read_selection,
    clause_with_pool_read_exclusions,
    prepare_pool_read_selection,
    project_pool_avatar,
    project_pool_read_item,
    project_pool_recall_items,
)
from app.domains.kol.pool_read_projection_cache import (
    cached_global_pool_selection,
    clear_pool_read_selection_cache,
)
from app.domains.kol.pool_read_avatar_hydration import profile_avatar_fallback_needed


def _row(
    pool_id: int,
    handle: str,
    profile_url: str,
    display_name: str,
    *,
    platform: str = "youtube",
    bio: str = "",
    avatar_url: str = "",
    raw_platform_data: dict[str, Any] | None = None,
    source_type: str = "",
    source_ref: str = "",
) -> dict[str, Any]:
    return {
        "id": pool_id,
        "platform": platform,
        "handle": handle,
        "profile_url": profile_url,
        "display_name": display_name,
        "bio": bio,
        "avatar_url": avatar_url,
        "raw_platform_data": raw_platform_data or {},
        "duplicate_of_id": None,
        "source_type": source_type,
        "source_ref": source_ref,
    }


def _cloud_duplicate_rows() -> list[dict[str, Any]]:
    rows = [
        _row(3505, "-【youtube】", "https://youtube.com/@lukewtcleland", "Luke Cleland"),
        _row(4062, "lukewtcleland", "https://youtube.com/@lukewtcleland", "Luke Cleland"),
        _row(3533, "erensarigul", "https://youtube.com/@erenjam", "Eren Sarigul"),
        _row(3971, "erenjam", "https://youtube.com/@erenjam/videos", "Eren Sarigul"),
        _row(3571, "matejsefcik", "https://youtube.com/@matsefcik", "Matej Sefcik"),
        _row(3572, "matsefcik", "https://youtube.com/channel/UCXAUVZzv0HRD_ZkvSxEnAdQ", "Matej Sefcik"),
    ]
    for native_pool_id, handle_pool_id, channel_id, handle, name in (
        (4946, 4997, "UCS3I7p5rSe3xUXSLiEb8MBA", "visualartphotography", "Visual Art Photography Tutorials"),
        (4948, 4971, "UCTSgfO_OkfRX8dm4YhCg3fg", "slrlounge", "SLR Lounge | Photography Tutorials"),
        (4950, 4974, "UCqVzptQx3IeYMVMKhsIPIbQ", "2minutephotographytutorials", "2-Minute Photography Tutorials"),
        (4952, 4987, "UC9C-iWKHChgKjKOayQKhJoQ", "focuspocusphotography", "Focus Pocus Photography Tutorials"),
    ):
        profile_url = f"https://youtube.com/channel/{channel_id}"
        rows.extend(
            [
                _row(native_pool_id, channel_id, profile_url, name),
                _row(handle_pool_id, handle, profile_url, name),
            ]
        )
    return rows


def _cloud_official_rows() -> list[dict[str, Any]]:
    return [
        _row(
            1534,
            "nikon",
            "https://opticallimits.com/",
            "opticallimits - 【MEDIA】",
            platform="media",
            source_type="promo_plan_xlsx",
            source_ref="海外市场推广计划表-Viltrox_AF 35+55mm F1.8 EVO FE+Z.xlsx",
        ),
        _row(
            4515,
            "UCS8XbKPaGqcXeamTuiLkg3A",
            "https://youtube.com/channel/UCS8XbKPaGqcXeamTuiLkg3A",
            "FUJIFILM Sample Images",
            bio=(
                "A channel for photographers who are interested in Fujifilm Fujinon photography. "
                "There is no better way to appreciate the image quality of Fuji products than to "
                "have a look at sample pictures on your computer. In this Fuji guide, we provide "
                "you with short video clips, which contain sample images for Fujifilm X-series / "
                "GFX system cameras and Fujinon lenses review. We post landscape photos, street "
                "shots, architectural images, portraits, stills, wildlife, sports and event "
                "pictures with various focal length range and lighting situations. Enjoy Fujifilm "
                "photography tips and this photo gallery with samples!"
            ),
            source_type="manual",
        ),
        _row(4561, "viltrox_id", "https://tiktok.com/@viltrox_id", "", platform="tiktok"),
        _row(4581, "viltrox.cee", "https://tiktok.com/@viltrox.cee", "", platform="tiktok"),
    ]


def _luke_bridge() -> list[dict[str, Any]]:
    return [
        {
            "id": 1847,
            "kol_pool_id": 4062,
            "item_type": "existing_kol",
            "source_url": "https://youtube.com/@lukewtcleland",
            "payload_json": {
                "platform": "youtube",
                "handle": "lukewtcleland",
                "channel_id": "UCpcfvXTJ1u3SFO-fUO6APVQ",
            },
        }
    ]


def test_cloud_real_ambiguous_brand_rows_fail_open() -> None:
    selection = build_pool_read_selection(
        _cloud_official_rows(),
        session_items=[],
        bridge_evidence_available=True,
    )

    assert selection.official_ids == frozenset({4561, 4581})
    assert {1534, 4515}.issubset(selection.visible_ids)
    assert {
        selection.audit_by_id[pool_id]["canonical_identity_status"]
        for pool_id in (1534, 4515)
    } == {"unique"}
    assert selection.diagnostics["excluded_confirmed_official"] == 2
    assert selection.diagnostics["official_verdict_counts"] == {"own_brand": 2}


def test_cloud_real_tamron_europe_self_attribution_is_hidden_conservatively() -> None:
    tamron_official = _row(
        4791,
        "tamron_europe",
        "https://www.tiktok.com/@tamron_europe",
        "TAMRON",
        platform="tiktok",
        bio=(
            "Gear | Tips | Creator Inspo #withmytamron\n"
            "By TAMRON Europe\n"
            "All our links"
        ),
    )
    independent_reviewer = _row(
        4792,
        "tamron_europe_review",
        "https://www.tiktok.com/@tamron_europe_review",
        "Alex reviews Tamron Europe",
        platform="tiktok",
        bio="I'm an independent photographer sharing my own Tamron lens reviews.",
    )

    selection = build_pool_read_selection(
        [tamron_official, independent_reviewer],
        session_items=[],
        bridge_evidence_available=True,
    )

    assert selection.official_ids == frozenset({4791})
    assert selection.visible_ids == frozenset({4792})
    assert selection.diagnostics["official_verdict_counts"] == {"brand_official": 1}


def test_missing_bridge_evidence_keeps_every_overlap_for_manual_review() -> None:
    rows = _cloud_duplicate_rows()[2:4]
    selection = build_pool_read_selection(
        rows,
        session_items=None,
        bridge_evidence_available=False,
    )

    assert selection.visible_ids == frozenset({3533, 3971})
    assert selection.folded_ids == frozenset()
    assert selection.diagnostics["canonical_manual_review_groups"] == 1
    assert {selection.audit_by_id[pool_id]["canonical_identity_status"] for pool_id in selection.visible_ids} == {
        "manual_review_conflict"
    }


def test_canonical_representative_does_not_change_with_signed_avatar_expiry() -> None:
    expired = "https://p16-common-sign.tiktokcdn-us.com/avatar.jpeg?x-expires=1"
    live = "https://p16-common-sign.tiktokcdn-us.com/avatar.jpeg?x-expires=4102444800"
    rows = [
        _row(
            7001, "stablecreator", "https://tiktok.com/@stablecreator", "Stable Creator",
            platform="tiktok", avatar_url=expired,
        ),
        _row(
            7002, "stablecreator", "https://tiktok.com/@stablecreator", "Stable Creator",
            platform="tiktok", avatar_url=live,
        ),
    ]

    selection = build_pool_read_selection(rows, session_items=[], bridge_evidence_available=True)

    assert selection.canonical_by_id == {7001: 7001, 7002: 7001}
    assert selection.folded_ids == frozenset({7002})


def test_global_selection_never_probes_avatar_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.domains.kol.pool_read_projection._default_cached_avatar_lookup",
        lambda _url: (_ for _ in ()).throw(AssertionError("global selection must not stat cache")),
    )
    expired = "https://p16-common-sign.tiktokcdn-us.com/avatar.jpeg?x-expires=1"

    selection = build_pool_read_selection(
        [_row(7101, "nocache", "https://tiktok.com/@nocache", "No Cache", platform="tiktok", avatar_url=expired)],
        session_items=[],
        bridge_evidence_available=True,
    )

    assert selection.avatar_by_id[7101]["avatar_url_status"] == "expired"


def test_raw_avatar_hydration_skips_direct_url_that_projection_would_prefer() -> None:
    live = "https://p16-common-sign.tiktokcdn-us.com/avatar.jpeg?x-expires=4102444800"
    expired = "https://p16-common-sign.tiktokcdn-us.com/avatar.jpeg?x-expires=1"

    assert profile_avatar_fallback_needed(live) is False
    assert profile_avatar_fallback_needed(expired) is True
    assert profile_avatar_fallback_needed("") is True
    assert profile_avatar_fallback_needed("https://i.ytimg.com/vi/video/hqdefault.jpg") is True


def test_returned_item_rechecks_signed_avatar_after_selection_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signed = "https://p16-common-sign.tiktokcdn-us.com/avatar.jpeg?x-expires=99"
    verdicts = iter(((signed, "ephemeral"), ("", "expired")))
    monkeypatch.setattr(
        "app.domains.kol.pool_read_projection._avatar_url_policy",
        lambda *_args, **_kwargs: next(verdicts),
    )
    row = _row(
        7103, "signedcreator", "https://tiktok.com/@signedcreator", "Signed Creator",
        platform="tiktok", avatar_url=signed,
    )
    selection = build_pool_read_selection(
        [row], session_items=[], bridge_evidence_available=True,
    )

    projected = project_pool_read_item({"id": 7103, "avatar_url": signed}, selection)

    assert projected["avatar_url"] == ""
    assert projected["avatar_url_status"] == "expired"
    assert projected["avatar_fallback"] == "initials"


def test_returned_item_uses_prewarmed_raw_profile_avatar(monkeypatch: pytest.MonkeyPatch) -> None:
    signed = "https://p16-common-sign.tiktokcdn-us.com/avatar.jpeg?x-expires=1"
    cached = "/api/vkpi-media/image-cache/" + "b" * 64
    monkeypatch.setattr(
        "app.domains.kol.pool_read_projection._default_cached_avatar_lookup",
        lambda value: cached if value == signed else "",
    )
    row = _row(
        7102, "cachedraw", "https://tiktok.com/@cachedraw", "Cached Raw",
        platform="tiktok", raw_platform_data={"profile": {"avatar_url": signed}},
    )
    selection = build_pool_read_selection(
        [row], session_items=[], bridge_evidence_available=True,
    )

    projected = project_pool_read_item({"id": 7102, "avatar_url": ""}, selection)

    assert projected["avatar_url"] == cached
    assert projected["avatar_url_source"] == "local_prewarm_cache"
    assert projected["avatar_upstream_status"] == "expired"


def test_avatar_projection_uses_only_profile_evidence_and_existing_cache() -> None:
    expired = "https://p16-common-sign.tiktokcdn-us.com/avatar.jpeg?x-expires=1&x-signature=old"
    cached_path = "/api/vkpi-media/image-cache/" + "a" * 64
    cached = project_pool_avatar(
        {"avatar_url": expired},
        cached_avatar_lookup=lambda value: cached_path if value == expired else "",
    )
    assert cached["avatar_url"] == cached_path
    assert cached["avatar_url_status"] == "durable"
    assert cached["avatar_upstream_status"] == "expired"
    assert cached["avatar_url_source"] == "local_prewarm_cache"

    stable_external = "https://yt3.ggpht.com/profile-avatar"
    stable_uncached = project_pool_avatar(
        {"avatar_url": stable_external},
        cached_avatar_lookup=lambda _value: "",
    )
    assert stable_uncached["avatar_url"] == stable_external
    assert stable_uncached["avatar_url_status"] == "external"
    assert stable_uncached["avatar_upstream_status"] == "durable"
    assert stable_uncached["avatar_health"] == {
        "status": "external",
        "upstream_status": "durable",
        "source": "pool_avatar_url",
        "fallback": "",
    }

    stable_cached_path = "/api/vkpi-media/image-cache/" + "c" * 64
    stable_cached = project_pool_avatar(
        {"avatar_url": stable_external},
        cached_avatar_lookup=lambda value: stable_cached_path if value == stable_external else "",
    )
    assert stable_cached["avatar_url"] == stable_cached_path
    assert stable_cached["avatar_url_status"] == "durable"
    assert stable_cached["avatar_upstream_status"] == "durable"
    assert stable_cached["avatar_url_source"] == "local_prewarm_cache"

    raw_profile = project_pool_avatar(
        {
            "avatar_url": "",
            "raw_platform_data": {
                "profile": {
                    "snippet": {
                        "thumbnails": {
                            "high": {"url": "https://yt3.ggpht.com/profile-avatar"}
                        }
                    }
                }
            },
        },
        cached_avatar_lookup=lambda _value: "",
    )
    assert raw_profile["avatar_url"] == "https://yt3.ggpht.com/profile-avatar"
    assert raw_profile["avatar_url_status"] == "external"
    assert raw_profile["avatar_upstream_status"] == "durable"
    assert raw_profile["avatar_url_source"] == "raw_profile_avatar"

    video_only = project_pool_avatar(
        {
            "avatar_url": "",
            "raw_platform_data": {
                "snippet": {"thumbnails": {"high": {"url": "https://i.ytimg.com/video-cover.jpg"}}},
                "videos": [{"thumbnail_url": "https://images.example/second-cover.jpg"}],
            },
        },
        cached_avatar_lookup=lambda _value: "",
    )
    assert video_only["avatar_url"] == ""
    assert video_only["avatar_url_status"] == "missing"
    assert video_only["avatar_fallback"] == "initials"

    dead = project_pool_avatar(
        {"avatar_url": expired},
        cached_avatar_lookup=lambda _value: "",
    )
    assert dead["avatar_url"] == ""
    assert dead["avatar_url_status"] == "expired"
    assert dead["avatar_url_source"] == "initials_fallback"

    video_cover = project_pool_avatar(
        {"avatar_url": "https://i.ytimg.com/vi/video123/hqdefault.jpg"},
        cached_avatar_lookup=lambda _value: "",
    )
    assert video_cover["avatar_url"] == ""
    assert video_cover["avatar_url_status"] == "invalid"
    assert video_cover["avatar_fallback"] == "initials"


def _sqlite_cloud_fixture() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE vkpi_kol_pool (
            id INTEGER PRIMARY KEY,
            platform TEXT,
            handle TEXT,
            profile_url TEXT,
            display_name TEXT,
            avatar_url TEXT,
            bio TEXT,
            raw_platform_data TEXT,
            duplicate_of_id INTEGER,
            linked_main_kol_id INTEGER,
            source_type TEXT,
            country TEXT,
            created_at TEXT,
            followers INTEGER DEFAULT 0,
            avg_views INTEGER,
            avg_likes INTEGER,
            avg_comments INTEGER,
            engagement_rate REAL,
            viltrox_fit_score REAL,
            updated_at TEXT,
            last_seen_at TEXT,
            last_video_at TEXT
        );
        CREATE TABLE vkpi_kol_search_session_items (
            id INTEGER PRIMARY KEY,
            kol_pool_id INTEGER,
            item_type TEXT,
            source_url TEXT,
            payload_json TEXT
        );
        CREATE TABLE vkpi_kol_video_evidence (id INTEGER PRIMARY KEY, kol_pool_id INTEGER);
        CREATE TABLE vkpi_kol_llm_deep_analysis_results (id INTEGER PRIMARY KEY, kol_pool_id INTEGER);
        """
    )
    for row in [*_cloud_duplicate_rows(), *_cloud_official_rows()]:
        conn.execute(
            """
            INSERT INTO vkpi_kol_pool
              (id, platform, handle, profile_url, display_name, avatar_url, bio,
               raw_platform_data, duplicate_of_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"], row["platform"], row["handle"], row["profile_url"],
                row["display_name"], row["avatar_url"], row["bio"],
                json.dumps(row["raw_platform_data"]), row["duplicate_of_id"],
            ),
        )
    bridge = _luke_bridge()[0]
    conn.execute(
        "INSERT INTO vkpi_kol_search_session_items VALUES (?, ?, ?, ?, ?)",
        (
            bridge["id"], bridge["kol_pool_id"], bridge["item_type"],
            bridge["source_url"], json.dumps(bridge["payload_json"]),
        ),
    )
    conn.commit()
    return conn


def test_pool_list_uses_projection_without_mutating_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _sqlite_cloud_fixture()
    conn.executemany(
        "INSERT INTO vkpi_kol_video_evidence (id, kol_pool_id) VALUES (?, ?)",
        [(101, 3533), (102, 3533)],
    )
    conn.execute(
        "INSERT INTO vkpi_kol_llm_deep_analysis_results (id, kol_pool_id) VALUES (201, 3533)"
    )
    conn.commit()
    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    monkeypatch.setattr(pool, "ensure_vkpi_product_industry_schema", lambda: None)
    monkeypatch.setattr(pool, "get_conn", lambda: conn)
    monkeypatch.setattr(pool, "cache_get", lambda _key: None)
    monkeypatch.setattr(pool, "_kol_pool_cache_key", lambda *_args, **_kwargs: "projection-test")
    monkeypatch.setattr(pool, "_kol_pool_cache_store", lambda _key, payload: payload)

    result = pool.list_pool(limit=50, sort_by="followers")

    assert {int(item["id"]) for item in result["items"]} == {
        1534, 3572, 3971, 4062, 4515, 4971, 4974, 4987, 4997,
    }
    assert result["projection"]["canonical_folded_rows"] == 7
    assert result["projection"]["canonical_manual_review_groups"] == 0
    assert result["projection"]["excluded_confirmed_official"] == 2
    luke = next(item for item in result["items"] if int(item["id"]) == 4062)
    assert luke["canonical_duplicate_ids"] == [3505]
    eren = next(item for item in result["items"] if int(item["id"]) == 3971)
    assert eren["canonical_duplicate_ids"] == [3533]
    assert eren["avatar_url_status"] == "missing"
    assert eren["avatar_fallback"] == "initials"
    assert eren["video_evidence_count"] == 2
    assert eren["llm_deep_analysis_count"] == 1
    assert eren["evidence_scope_pool_ids"] == [3971, 3533]
    assert eren["evidence_scope_partial"] is False
    alias_result = pool.list_pool(limit=50, query="erensarigul")["items"]
    assert [int(item["id"]) for item in alias_result] == [3971]
    assert alias_result[0]["canonical_duplicate_ids"] == [3533]
    mutating = re.compile(r"\b(?:INSERT|UPDATE|DELETE|MERGE|REPLACE)\b", re.IGNORECASE)
    assert not any(mutating.search(statement) for statement in statements)


def test_workspace_counts_and_paginates_after_global_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _sqlite_cloud_fixture()
    monkeypatch.setattr(pool, "ensure_vkpi_product_industry_schema", lambda: None)
    monkeypatch.setattr(pool, "get_conn", lambda: conn)
    monkeypatch.setattr(pool, "cache_get", lambda _key: None)
    monkeypatch.setattr(pool, "_kol_pool_cache_key", lambda *_args, **_kwargs: "projection-test")
    monkeypatch.setattr(pool, "_kol_pool_cache_store", lambda _key, payload: payload)
    monkeypatch.setattr(pool, "summary", lambda: {"total": 18, "by_platform": [], "country_distribution": []})

    result = pool.workspace(limit=1, query="Luke")
    all_result = pool.workspace(limit=1)

    assert result["counts"]["filtered"] == 1
    assert result["counts"]["returned"] == 1
    assert result["counts"]["has_more"] is False
    assert result["list"]["has_more"] is False
    assert all_result["counts"]["filtered"] == 9
    assert all_result["counts"]["returned"] == 1
    assert all_result["counts"]["has_more"] is True


def test_alias_remap_revalidates_structural_filters_on_canonical_row(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _sqlite_cloud_fixture()
    conn.execute("UPDATE vkpi_kol_pool SET country='US' WHERE id=3533")
    conn.execute("UPDATE vkpi_kol_pool SET country='BE' WHERE id=3971")
    conn.commit()
    monkeypatch.setattr(pool, "ensure_vkpi_product_industry_schema", lambda: None)
    monkeypatch.setattr(pool, "get_conn", lambda: conn)
    monkeypatch.setattr(pool, "cache_get", lambda _key: None)
    monkeypatch.setattr(pool, "_kol_pool_cache_store", lambda _key, payload: payload)
    monkeypatch.setattr(pool, "summary", lambda: {"total": 8, "by_platform": [], "country_distribution": []})

    canonical_country = pool.workspace(limit=10, query="erensarigul", country="BE")
    duplicate_country = pool.workspace(limit=10, query="erensarigul", country="US")
    structural_only = pool.workspace(limit=10, country="US")

    assert [int(item["id"]) for item in canonical_country["list"]["items"]] == [3971]
    assert canonical_country["counts"]["filtered"] == 1
    assert duplicate_country["list"]["items"] == []
    assert structural_only["list"]["items"] == []


def test_data_status_uses_projected_avatar_health(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _sqlite_cloud_fixture()
    expired = "https://p16-common-sign.tiktokcdn-us.com/avatar.jpeg?x-expires=1"
    conn.execute(
        "UPDATE vkpi_kol_pool SET avatar_url=?, avg_views=1, engagement_rate=1, "
        "viltrox_fit_score=1 WHERE id=3971",
        (expired,),
    )
    conn.execute(
        "UPDATE vkpi_kol_pool SET avatar_url='https://yt3.ggpht.com/durable', "
        "avg_views=1, engagement_rate=1, viltrox_fit_score=1 WHERE id=3572"
    )
    conn.commit()
    monkeypatch.setattr(pool, "ensure_vkpi_product_industry_schema", lambda: None)
    monkeypatch.setattr(pool, "get_conn", lambda: conn)
    monkeypatch.setattr(pool, "cache_get", lambda _key: None)
    monkeypatch.setattr(pool, "_kol_pool_cache_store", lambda _key, payload: payload)
    monkeypatch.setattr(pool, "summary", lambda: {"total": 8, "by_platform": [], "country_distribution": []})

    complete = pool.workspace(limit=20, data_status="complete")
    missing = pool.workspace(limit=20, data_status="missing")

    assert [int(item["id"]) for item in complete["list"]["items"]] == [3572]
    assert 3971 in {int(item["id"]) for item in missing["list"]["items"]}
    assert complete["counts"]["by_data_status"] == {"complete": 1, "missing": 8}


def test_summary_uses_the_same_employee_visible_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _sqlite_cloud_fixture()
    monkeypatch.setattr(pool, "ensure_vkpi_product_industry_schema", lambda: None)
    monkeypatch.setattr(pool, "get_conn", lambda: conn)
    monkeypatch.setattr(pool, "cache_get", lambda _key: None)
    monkeypatch.setattr(pool, "_kol_pool_cache_store", lambda _key, payload: payload)

    result = pool.summary()

    assert result["total"] == 9
    assert result["candidate_asset_count"] == 9
    assert result["by_platform"] == [
        {"platform": "youtube", "n": 8},
        {"platform": "media", "n": 1},
    ]
    assert result["read_projection"]["physical_master_rows"] == 18
    assert result["read_projection"]["canonical_folded_rows"] == 7
    assert result["read_projection"]["excluded_confirmed_official"] == 2


def test_summary_funnel_counts_folded_evidence_once_per_canonical_creator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _sqlite_cloud_fixture()
    conn.execute("ALTER TABLE vkpi_kol_llm_deep_analysis_results ADD COLUMN status TEXT")
    conn.execute("ALTER TABLE vkpi_kol_llm_deep_analysis_results ADD COLUMN created_at TEXT")
    conn.execute(
        "CREATE TABLE vkpi_kol_pool_favorites "
        "(id INTEGER PRIMARY KEY, kol_pool_id INTEGER, created_at TEXT)"
    )
    conn.executemany(
        "INSERT INTO vkpi_kol_llm_deep_analysis_results "
        "(id, kol_pool_id, status, created_at) VALUES (?, ?, 'ready', datetime('now'))",
        [(301, 3533), (302, 3971)],
    )
    conn.executemany(
        "INSERT INTO vkpi_kol_pool_favorites (id, kol_pool_id, created_at) "
        "VALUES (?, ?, datetime('now'))",
        [(401, 3533), (402, 3971)],
    )
    conn.commit()
    monkeypatch.setattr(pool, "ensure_vkpi_product_industry_schema", lambda: None)
    monkeypatch.setattr(pool, "get_conn", lambda: conn)
    monkeypatch.setattr(pool, "cache_get", lambda _key: None)
    monkeypatch.setattr(pool, "_kol_pool_cache_store", lambda _key, payload: payload)

    funnel = pool.summary()["discovery_funnel_30d"]

    assert funnel["deep_analyzed"] == 1
    assert funnel["favorited"] == 1


def test_history_match_remaps_alias_and_hides_confirmed_official(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _sqlite_cloud_fixture()
    conn.execute(
        "INSERT INTO vkpi_kol_pool "
        "(id, platform, handle, profile_url, display_name, raw_platform_data, duplicate_of_id) "
        "VALUES (8000, 'youtube', 'erensarigul', 'https://youtube.com/@erenjam', "
        "'Legacy folded Eren', '{}', 3971)"
    )
    conn.commit()
    conn.execute(
        "CREATE TABLE vkpi_legacy_cooperations_staging "
        "(id INTEGER PRIMARY KEY, matched_kol_pool_id INTEGER, product TEXT, project TEXT, "
        "status TEXT, cooperation_date TEXT, content_link TEXT)"
    )
    conn.execute(
        "INSERT INTO vkpi_legacy_cooperations_staging VALUES "
        "(1, 3533, 'Lens', 'Launch', 'published', '2026-08-01', 'https://example.com/evidence')"
    )
    conn.commit()
    monkeypatch.setattr(history_match, "ensure_vkpi_product_industry_schema", lambda: None)
    monkeypatch.setattr(history_match, "get_conn", lambda: conn)
    monkeypatch.setattr(history_match, "is_postgres_runtime", lambda: False)

    eren = history_match.find_history_match({"handle": "erensarigul"}, platform="youtube")
    own_brand = history_match.find_history_match({"handle": "viltrox_id"}, platform="tiktok")

    assert eren is not None
    assert eren["kol_pool_id"] == 3971
    assert eren["canonical_duplicate_ids"] == [3533]
    assert eren["canonical_identity_status"] == "canonical_read_folded"
    assert eren["cooperation_count"] == 1
    assert eren["evidence_scope_pool_ids"] == [3971, 3533]
    assert eren["evidence_scope_partial"] is True
    assert own_brand is None


def test_history_payload_preserves_projected_avatar_provenance() -> None:
    conn = _sqlite_cloud_fixture()
    cached = "/api/vkpi-media/image-cache/" + "c" * 64
    payload = history_match._history_payload(
        conn,
        {
            "id": 3971,
            "avatar_url": cached,
            "avatar_url_status": "durable",
            "avatar_upstream_status": "expired",
            "avatar_url_source": "local_prewarm_cache",
            "avatar_fallback": "",
            "avatar_health": {
                "status": "durable", "upstream_status": "expired",
                "source": "local_prewarm_cache", "fallback": "",
            },
            "canonical_duplicate_ids": [],
        },
        match_type="test",
        confidence=1.0,
    )

    assert payload["avatar_url"] == cached
    assert payload["avatar_url_source"] == "local_prewarm_cache"
    assert payload["avatar_upstream_status"] == "expired"


def test_history_raw_post_fallback_keeps_its_own_provenance() -> None:
    conn = _sqlite_cloud_fixture()
    payload = history_match._history_payload(
        conn,
        {
            "id": 3971,
            "raw_platform_data": json.dumps({
                "posts": [{
                    "id": "raw-1", "title": "Raw sample",
                    "url": "https://example.com/raw-1",
                    "views": 100, "likes": 10, "comments": 2,
                }],
            }),
            "canonical_duplicate_ids": [],
        },
        match_type="test",
        confidence=1.0,
    )

    assert payload["recent_posts"][0]["source_kind"] == "history_pool_sample"
    assert payload["engagement_rate"] == 12.0
    assert payload["engagement_rate_source"] == "history_pool_sample"
    assert payload["evidence_scope_partial"] is True


def test_selection_cache_serializes_builders_and_clear_invalidates() -> None:
    clear_pool_read_selection_cache()
    calls = 0

    def build() -> object:
        nonlocal calls
        calls += 1
        return object()

    with ThreadPoolExecutor(max_workers=8) as executor:
        values = list(executor.map(
            lambda _index: cached_global_pool_selection(enabled=True, builder=build),
            range(8),
        ))
    assert calls == 1
    assert len({id(value) for value in values}) == 1

    clear_pool_read_selection_cache()
    next_value = cached_global_pool_selection(enabled=True, builder=build)
    assert calls == 2
    assert next_value is not values[0]
    clear_pool_read_selection_cache()


def test_selection_cache_rebuilds_when_source_revision_changes() -> None:
    clear_pool_read_selection_cache()
    calls = 0

    def build() -> object:
        nonlocal calls
        calls += 1
        return object()

    first = cached_global_pool_selection(enabled=True, builder=build, cache_key="r1")
    again = cached_global_pool_selection(enabled=True, builder=build, cache_key="r1")
    changed = cached_global_pool_selection(enabled=True, builder=build, cache_key="r2")

    assert first is again
    assert changed is not first
    assert calls == 2
    clear_pool_read_selection_cache()


def test_workspace_raw_profile_avatar_is_publicly_projected_but_never_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _sqlite_cloud_fixture()
    signed = "https://scontent.cdninstagram.com/raw-profile.jpg?oe=FFFFFFFF&_nc_sid=test"
    conn.execute(
        "UPDATE vkpi_kol_pool SET avatar_url='', raw_platform_data=? WHERE id=3971",
        (json.dumps({"profile": {"profilePicUrlHD": signed}}),),
    )
    conn.commit()
    cache: dict[str, Any] = {}
    monkeypatch.setattr(pool, "ensure_vkpi_product_industry_schema", lambda: None)
    monkeypatch.setattr(pool, "get_conn", lambda: conn)
    monkeypatch.setattr(pool, "cache_get", lambda key: cache.get(key))
    monkeypatch.setattr(
        pool_common,
        "cache_set",
        lambda key, value, *, ttl: cache.__setitem__(key, value),
    )
    monkeypatch.setattr(
        pool,
        "summary",
        lambda: {"total": 8, "by_platform": [], "country_distribution": []},
    )

    first = pool.workspace(limit=20)
    first_eren = next(item for item in first["list"]["items"] if int(item["id"]) == 3971)

    assert first_eren["avatar_url"] == signed
    assert first_eren["avatar_url_status"] == "ephemeral"
    assert "raw_profile_avatar_url" not in first_eren
    cached_json = json.dumps(cache, ensure_ascii=False)
    assert signed not in cached_json
    assert "raw_profile_avatar_url" not in cached_json


def test_workspace_cache_hit_reprojects_ephemeral_avatar_without_persisting_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _sqlite_cloud_fixture()
    signed = "https://scontent.cdninstagram.com/avatar.jpg?oe=FFFFFFFF&_nc_sid=test"
    conn.execute(
        "UPDATE vkpi_kol_pool SET avatar_url=? WHERE id=3971",
        (signed,),
    )
    conn.commit()
    cache: dict[str, Any] = {}
    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    monkeypatch.setattr(pool, "ensure_vkpi_product_industry_schema", lambda: None)
    monkeypatch.setattr(pool, "get_conn", lambda: conn)
    monkeypatch.setattr(pool, "cache_get", lambda key: cache.get(key))
    monkeypatch.setattr(
        pool_common,
        "cache_set",
        lambda key, value, *, ttl: cache.__setitem__(key, value),
    )
    monkeypatch.setattr(
        pool,
        "summary",
        lambda: {"total": 8, "by_platform": [], "country_distribution": []},
    )

    first = pool.workspace(limit=20)
    second = pool.workspace(limit=20)

    first_eren = next(item for item in first["list"]["items"] if int(item["id"]) == 3971)
    second_eren = next(item for item in second["list"]["items"] if int(item["id"]) == 3971)
    assert first["cache"]["hit"] is False
    assert second["cache"] == {"hit": True, "ttl_sec": 30}
    assert first_eren["avatar_url"] == signed
    assert second_eren["avatar_url"] == signed
    assert second_eren["avatar_url_status"] == "ephemeral"
    assert signed not in json.dumps(cache, ensure_ascii=False)
    mutating = re.compile(r"\b(?:INSERT|UPDATE|DELETE|MERGE|REPLACE)\b", re.IGNORECASE)
    assert not any(mutating.search(statement) for statement in statements)


def test_sql_projection_and_semantic_recall_are_read_only() -> None:
    conn = _sqlite_cloud_fixture()
    conn.execute(
        "INSERT INTO vkpi_kol_pool "
        "(id, platform, handle, profile_url, display_name, raw_platform_data, duplicate_of_id) "
        "VALUES (8000, 'youtube', 'erensarigul', 'https://youtube.com/@erenjam', "
        "'Legacy folded Eren', '{}', 3971)"
    )
    conn.commit()
    statements: list[str] = []
    conn.set_trace_callback(statements.append)

    selection = prepare_pool_read_selection(
        conn,
        clause="WHERE duplicate_of_id IS NULL",
        params=(),
    )
    clause, params = clause_with_pool_read_exclusions(
        "WHERE duplicate_of_id IS NULL",
        (),
        selection,
    )
    ids = [int(row["id"]) for row in conn.execute(f"SELECT id FROM vkpi_kol_pool {clause} ORDER BY id", params)]
    projected = project_pool_read_item(dict(conn.execute("SELECT * FROM vkpi_kol_pool WHERE id=3971").fetchone()), selection)
    recall = project_pool_recall_items(
        conn,
        [
            {"kind": "kol", "id": 3533, "title": "legacy Eren", "score": 1.0},
            {"kind": "kol", "id": 3971, "title": "current Eren", "score": 0.9},
            {"kind": "kol", "id": 4561, "title": "Viltrox own brand", "score": 0.8},
            {"kind": "kol", "id": 9999, "title": "not loaded in this snapshot", "score": 0.7},
            {"kind": "kol", "id": 8000, "title": "physical duplicate", "score": 0.6},
        ],
    )

    assert ids == sorted(selection.visible_ids)
    assert projected["canonical_duplicate_ids"] == [3533]
    assert projected["canonical_identity_status"] == "canonical_read_folded"
    assert [(item["id"], item["title"]) for item in recall] == [
        (3971, "Eren Sarigul"),
        (9999, "not loaded in this snapshot"),
    ]
    mutating = re.compile(r"\b(?:INSERT|UPDATE|DELETE|MERGE|REPLACE)\b", re.IGNORECASE)
    assert not any(mutating.search(statement) for statement in statements)
    assert selection.diagnostics["writes_performed"] == 0


def test_prepared_pool_projection_keeps_raw_profile_avatar_fallback() -> None:
    conn = _sqlite_cloud_fixture()
    conn.execute(
        """
        INSERT INTO vkpi_kol_pool
          (id, platform, handle, profile_url, display_name, avatar_url, bio,
           raw_platform_data, duplicate_of_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            7001,
            "youtube",
            "rawavatarcreator",
            "https://youtube.com/@rawavatarcreator",
            "Raw Avatar Creator",
            "",
            "Independent creator",
            json.dumps(
                {
                    "profile": {
                        "snippet": {
                            "thumbnails": {
                                "high": {"url": "https://yt3.ggpht.com/raw-profile-avatar"}
                            }
                        }
                    }
                }
            ),
            None,
        ),
    )
    conn.commit()

    selection = prepare_pool_read_selection(
        conn,
        clause="WHERE duplicate_of_id IS NULL",
        params=(),
    )
    projected = project_pool_read_item(
        dict(conn.execute("SELECT * FROM vkpi_kol_pool WHERE id=7001").fetchone()),
        selection,
    )

    assert projected["avatar_url"] == "https://yt3.ggpht.com/raw-profile-avatar"
    assert projected["avatar_url_status"] == "external"
    assert projected["avatar_upstream_status"] == "durable"
    assert projected["avatar_url_source"] == "raw_profile_avatar"


def test_prepared_projection_extracts_channel_avatar_but_not_video_thumbnail() -> None:
    conn = _sqlite_cloud_fixture()
    channel_avatar = "https://i.ytimg.com/channel-avatar.jpg"
    channel_payload = {
        "profile": {
            "items": [{
                "kind": "youtube#channel",
                "snippet": {"thumbnails": {"high": {"url": channel_avatar}}},
            }]
        }
    }
    conn.execute(
        "UPDATE vkpi_kol_pool SET avatar_url='', raw_platform_data=? WHERE id=3971",
        (json.dumps(channel_payload),),
    )
    conn.commit()

    selection = prepare_pool_read_selection(
        conn, clause="WHERE duplicate_of_id IS NULL", params=(),
    )
    projected = project_pool_read_item({"id": 3971, "avatar_url": ""}, selection)

    assert projected["avatar_url"] == channel_avatar
    assert projected["avatar_url_source"] == "raw_profile_avatar"
    assert selection.row_by_id[3971]["raw_platform_data"] == {}

    channel_payload["profile"]["items"][0]["kind"] = "youtube#video"
    conn.execute(
        "UPDATE vkpi_kol_pool SET raw_platform_data=? WHERE id=3971",
        (json.dumps(channel_payload),),
    )
    conn.commit()
    selection = prepare_pool_read_selection(
        conn, clause="WHERE duplicate_of_id IS NULL", params=(),
    )
    assert selection.row_by_id[3971]["raw_profile_avatar_url"] is None


def test_discovery_funnel_excludes_exact_official_pool_id_without_payload_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Result:
        def fetchall(self) -> list[dict[str, Any]]:
            return [
                {
                    "id": 1, "item_type": "existing_kol", "kol_pool_id": 4561,
                    "source_url": "https://tiktok.com/@anonymous", "dedupe_key": "official",
                    "payload_json": json.dumps({"platform": "tiktok", "handle": "anonymous"}),
                },
                {
                    "id": 2, "item_type": "existing_kol", "kol_pool_id": 7000,
                    "source_url": "https://tiktok.com/@creator", "dedupe_key": "creator",
                    "payload_json": json.dumps({"platform": "tiktok", "handle": "creator"}),
                },
            ]

    class Conn:
        def execute(self, _sql: str) -> Result:
            return Result()

    monkeypatch.setattr(
        pool_summary,
        "prepare_pool_read_selection",
        lambda *_args, **_kwargs: SimpleNamespace(official_ids=frozenset({4561})),
    )

    assert pool_summary.canonical_discovery_funnel_counts(Conn()) == (1, 1)


def test_local_projection_cache_clears_even_when_shared_cache_clear_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        pool_common,
        "cache_clear",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("redis unavailable")),
    )
    monkeypatch.setattr(
        "app.domains.kol.pool_read_projection_cache.clear_pool_read_selection_cache",
        lambda: calls.append("cleared"),
    )

    pool_common._clear_kol_pool_read_cache()

    assert calls == ["cleared"]
