"""Characterization locks for the CC knife on two read-side hotspots.

Targets (行为不变刀):
* ``url_deep_crawl_execute._crawl_profile_basics``(改前 CC 53)
* ``pool_detail._batch_cached_video_urls``(改前 CC 51)

All tests are provider-free: crawlers/DB/cache layers are recorded doubles.
They must pass byte-for-byte against the pre-refactor bodies first, then stay
green after the decomposition.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


# ─────────────────────────── _crawl_profile_basics ───────────────────────────


class _Classified:
    """Runtime stand-in: ClassifiedUrl is TYPE_CHECKING-only in the module."""

    def __init__(self, platform: str, channel_id: str = "") -> None:
        self.platform = platform
        self.channel_id = channel_id


class _YouTubeCrawler:
    def __init__(self, profile_payload: Any, videos_payload: Any = None) -> None:
        self.profile_payload = profile_payload
        self.videos_payload = videos_payload
        self.profile_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.video_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def crawl_channel_profile(self, *args: Any, **kwargs: Any) -> Any:
        self.profile_calls.append((args, kwargs))
        return self.profile_payload

    def crawl_channel_videos(self, *args: Any, **kwargs: Any) -> Any:
        self.video_calls.append((args, kwargs))
        return self.videos_payload


class _ProfileOnlyCrawler:
    """No ``crawl_channel_videos`` attribute at all (hasattr gate must hold)."""

    def __init__(self, profile_payload: Any) -> None:
        self.profile_payload = profile_payload
        self.profile_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def crawl_channel_profile(self, *args: Any, **kwargs: Any) -> Any:
        self.profile_calls.append((args, kwargs))
        return self.profile_payload


def _crawl(monkeypatch: pytest.MonkeyPatch, crawler: Any, classified: _Classified, **kwargs: Any) -> dict[str, Any]:
    from app.domains.kol import url_deep_crawl_execute as execute

    monkeypatch.setattr(execute, "_crawler_for", lambda _platform: crawler)
    return execute._crawl_profile_basics(classified, **kwargs)


def test_youtube_full_path_calls_videos_with_stripped_since_and_filters_dicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crawler = _YouTubeCrawler(
        profile_payload={
            "items": [{"id": "UC-real"}],
            "sync_status": "ok",
            "provider_source": "youtube_api",
            "videos": [{"id": "fallback"}],
        },
        videos_payload={
            "items": [{"id": "v1"}, "junk", {"id": "v2"}],
            "sync_status": "synced",
            "provider_source": "apify",
        },
    )
    out = _crawl(
        monkeypatch, crawler, _Classified("youtube", channel_id="UC-cls"),
        target="https://youtube.com/@creator", max_posts=7, since="  2026-08-18  ",
    )

    # profile call carries NO since on youtube(原路径不变);videos call carries it stripped.
    assert crawler.profile_calls == [
        (("https://youtube.com/@creator",), {"channel_id": "", "max_posts": 7})
    ]
    assert crawler.video_calls == [
        (("UC-real",), {"max_results": 7, "since": "2026-08-18"})
    ]
    assert out["videos_items"] == [{"id": "v1"}, {"id": "v2"}]
    assert out["status"] == "ok"  # profile sync_status wins over videos payload
    assert out["provider_source"] == "youtube_api"
    assert out["profile_payload"] is crawler.profile_payload
    assert out["videos_payload"] is crawler.videos_payload
    assert isinstance(out["elapsed_ms"], int) and out["elapsed_ms"] >= 0
    assert set(out) == {
        "profile_payload", "videos_payload", "videos_items",
        "status", "provider_source", "elapsed_ms",
    }


def test_youtube_without_video_crawler_uses_classified_channel_and_profile_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crawler = _ProfileOnlyCrawler(
        {"items": [], "videos": [{"id": "f1"}, 42], "provider_status": "ready"}
    )
    out = _crawl(
        monkeypatch, crawler, _Classified("youtube", channel_id="UC-cls"),
        target="creator", max_posts=3,
    )
    assert out["videos_items"] == [{"id": "f1"}]
    assert out["videos_payload"] == {}
    assert out["status"] == "ready"  # sync_status absent → provider_status
    assert out["provider_source"] == ""


def test_youtube_without_any_channel_id_never_calls_videos(monkeypatch: pytest.MonkeyPatch) -> None:
    crawler = _YouTubeCrawler(profile_payload={"items": [{"noise": 1}]})
    out = _crawl(monkeypatch, crawler, _Classified("youtube"), target="creator", max_posts=3)
    assert crawler.video_calls == []
    assert out["videos_items"] == []
    assert out["status"] == "unknown"
    assert out["provider_source"] == ""


def test_generic_platform_passes_since_and_takes_content_items_from_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crawler = _ProfileOnlyCrawler(
        {
            "videos": [{"webVideoUrl": "https://t/1"}, {"webVideoUrl": "https://t/2"}],
            "sync_status": "ok",
            "provider_source": "apify",
        }
    )
    out = _crawl(
        monkeypatch, crawler, _Classified("tiktok"),
        target="creator", max_posts=5, since=" 2026-08-18 ",
    )
    assert crawler.profile_calls == [
        (("creator",), {"channel_id": "", "max_posts": 5, "since": "2026-08-18"})
    ]
    assert out["videos_items"] == [
        {"webVideoUrl": "https://t/1"},
        {"webVideoUrl": "https://t/2"},
    ]
    assert out["status"] == "ok"
    assert out["provider_source"] == "apify"


def test_instagram_profile_object_drills_into_latest_posts(monkeypatch: pytest.MonkeyPatch) -> None:
    profile_obj = {
        "id": "ig1",
        "username": "creator",
        "latestPosts": [{"caption": "hi"}, "junk", {"noise": 1}],
    }
    crawler = _ProfileOnlyCrawler({"items": [profile_obj], "provider_status": "ok"})
    out = _crawl(monkeypatch, crawler, _Classified("instagram"), target="creator", max_posts=5)
    assert out["videos_items"] == [{"caption": "hi"}]
    assert out["status"] == "ok"


def test_instagram_drill_skips_nested_lists_without_content_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_obj = {
        "id": "ig1",
        "username": "creator",
        # 非内容对象的非空列表不许提前 break,要继续钻下一个嵌套键。
        "posts": [{"noise": 1}],
        "videos": [{"shortCode": "abc"}],
    }
    crawler = _ProfileOnlyCrawler({"items": [profile_obj]})
    out = _crawl(monkeypatch, crawler, _Classified("instagram"), target="creator", max_posts=5)
    assert out["videos_items"] == [{"shortCode": "abc"}]


def test_non_dict_payload_is_coerced_and_status_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    crawler = _ProfileOnlyCrawler(None)
    out = _crawl(monkeypatch, crawler, _Classified("tiktok"), target="creator", max_posts=5)
    assert out == {
        "profile_payload": {},
        "videos_payload": {},
        "videos_items": [],
        "status": "unknown",
        "provider_source": "",
        "elapsed_ms": out["elapsed_ms"],
    }


def test_youtube_status_and_source_fall_back_to_videos_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crawler = _YouTubeCrawler(
        profile_payload={"items": [{"id": "UC-x"}]},
        videos_payload={"items": [], "provider_status": "degraded", "provider_source": "apify"},
    )
    out = _crawl(monkeypatch, crawler, _Classified("youtube"), target="creator", max_posts=2)
    assert out["status"] == "degraded"
    assert out["provider_source"] == "apify"
    assert out["videos_items"] == []


# ─────────────────────────── _batch_cached_video_urls ───────────────────────────


class _Rows:
    def __init__(self, many: list[Any] | None = None) -> None:
        self.many = many or []

    def fetchall(self) -> list[Any]:
        return list(self.many)


class _BatchConn:
    def __init__(self, assets: list[dict[str, Any]] | None = None, error: Exception | None = None) -> None:
        self.assets = assets or []
        self.error = error
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, sql: str, params: Any = ()) -> _Rows:
        compact = " ".join(str(sql).split())
        self.calls.append((compact, tuple(params)))
        if self.error is not None:
            raise self.error
        return _Rows(many=self.assets)


def _ig_row(
    evidence_id: int,
    *,
    platform: str = "instagram",
    content_url: str | None = None,
    cached_url: Any = None,
    digest: Any = None,
    backend: Any = None,
    r2_key: Any = None,
) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "platform": platform,
        "content_url": content_url
        if content_url is not None
        else f"https://www.instagram.com/reel/Vid{evidence_id}code/",
        "cached_video_url": cached_url,
        "cached_video_digest": digest,
        "cached_video_storage_backend": backend,
        "cached_video_r2_key": r2_key,
    }


@pytest.fixture()
def batch_env(monkeypatch: pytest.MonkeyPatch):
    from app.domains.kol import pool_detail
    from app.domains.media import cache, cache_core

    monkeypatch.setattr(pool_detail, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(cache, "cached_video_file", lambda _digest: None)
    monkeypatch.setattr(cache, "cached_video_url_for_item", lambda *_a, **_k: None)
    monkeypatch.setattr(cache_core, "_resolved_cached_asset_row", lambda _row: "")
    return pool_detail, cache, cache_core


def test_non_postgres_runtime_returns_none_without_touching_conn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domains.kol import pool_detail

    monkeypatch.setattr(pool_detail, "is_postgres_runtime", lambda: False)
    conn = _BatchConn()
    assert pool_detail._batch_cached_video_urls(conn, [_ig_row(1)]) is None
    assert conn.calls == []


def test_only_unbatched_platforms_returns_authoritative_empty_dict_without_db_read(
    batch_env,
) -> None:
    pool_detail, _cache, _cache_core = batch_env
    conn = _BatchConn()
    rows = [
        _ig_row(11, platform="youtube", content_url="https://youtu.be/aaaaaaaaaaa"),
        _ig_row(0),  # 无 evidence_id 也排除在批量候选之外
    ]
    resolved = pool_detail._batch_cached_video_urls(conn, rows)
    assert resolved == {}
    assert resolved is not None
    assert conn.calls == []


def test_sql_shape_pairs_and_digests_are_deduped_in_order(batch_env) -> None:
    pool_detail, _cache, _cache_core = batch_env
    conn = _BatchConn()
    shared_url = "https://www.instagram.com/reel/SharedCode1/"
    rows = [
        _ig_row(701, content_url=shared_url, digest="a" * 64),
        _ig_row(702, content_url=shared_url, digest="a" * 64),
        _ig_row(
            703,
            platform="tiktok",
            content_url="https://www.tiktok.com/@c/video/1234567890123456789",
            digest="not-a-digest",
        ),
    ]
    pool_detail._batch_cached_video_urls(conn, rows)

    assert len(conn.calls) == 1
    sql, params = conn.calls[0]
    assert "FROM vkpi_media_cache_assets" in sql
    assert "media_kind='video' AND status='cached'" in sql
    assert sql.count("(platform=? AND external_id=?)") == 5
    assert "digest IN (?)" in sql
    assert "ORDER BY updated_at DESC, id DESC" in sql
    assert params == (
        "instagram", "SharedCode1",
        "instagram", "701",
        "instagram", "702",
        "tiktok", "1234567890123456789",
        "tiktok", "703",
        "a" * 64,
    )


def test_db_read_failure_returns_none_so_caller_keeps_legacy_resolver(batch_env) -> None:
    pool_detail, _cache, _cache_core = batch_env
    conn = _BatchConn(error=RuntimeError("rolling schema"))
    assert pool_detail._batch_cached_video_urls(conn, [_ig_row(21)]) is None


def test_digest_hint_with_local_bytes_wins_immediately(batch_env, monkeypatch) -> None:
    pool_detail, cache, _cache_core = batch_env
    monkeypatch.setattr(cache, "cached_video_file", lambda digest: f"/tmp/{digest}.mp4")
    conn = _BatchConn()
    digest = "b" * 64
    resolved = pool_detail._batch_cached_video_urls(conn, [_ig_row(31, digest=digest)])
    assert resolved == {31: f"/api/vkpi-media/video-cache/{digest}"}


def test_stale_digest_hint_projects_item_columns_before_db_asset(batch_env, monkeypatch) -> None:
    pool_detail, _cache, cache_core = batch_env
    seen_rows: list[dict[str, Any]] = []

    def fake_resolved(row: Any) -> str:
        row = dict(row)
        seen_rows.append(row)
        return str(row.get("cache_url") or "") if str(row.get("cache_url") or "").startswith("https://") else ""

    monkeypatch.setattr(cache_core, "_resolved_cached_asset_row", fake_resolved)
    conn = _BatchConn()
    digest = "c" * 64
    row = _ig_row(
        41,
        digest=digest,
        cached_url="https://public-media.example/from-item.mp4",
        backend="r2",
        r2_key="private/from-item.mp4",
    )
    resolved = pool_detail._batch_cached_video_urls(conn, [row])
    assert resolved == {41: "https://public-media.example/from-item.mp4"}
    assert seen_rows[0] == {
        "digest": digest,
        "cache_url": "https://public-media.example/from-item.mp4",
        "storage_backend": "r2",
        "r2_key": "private/from-item.mp4",
    }


def test_stale_digest_hint_falls_back_to_batched_db_asset_row(batch_env, monkeypatch) -> None:
    pool_detail, _cache, cache_core = batch_env
    digest = "d" * 64
    asset = {
        "digest": digest,
        "cache_url": "https://public-media.example/from-db.mp4",
        "storage_backend": "r2",
        "r2_key": "private/from-db.mp4",
        "platform": "instagram",
        "external_id": "irrelevant",
    }
    monkeypatch.setattr(
        cache_core,
        "_resolved_cached_asset_row",
        lambda row: str(dict(row).get("cache_url") or "")
        if str(dict(row).get("cache_url") or "").startswith("https://public-media")
        else "",
    )
    conn = _BatchConn(assets=[asset])
    resolved = pool_detail._batch_cached_video_urls(conn, [_ig_row(51, digest=digest)])
    assert resolved == {51: "https://public-media.example/from-db.mp4"}


def test_public_raw_url_passes_through_but_digest_route_needs_proof(batch_env) -> None:
    pool_detail, _cache, _cache_core = batch_env
    conn = _BatchConn()
    rows = [
        _ig_row(61, cached_url="https://public-media.example/direct.mp4"),
        _ig_row(62, cached_url=f"/api/vkpi-media/video-cache/{'e' * 64}"),
    ]
    resolved = pool_detail._batch_cached_video_urls(conn, rows)
    # 61: 非 digest 路由的公开 URL 直接放行;62: digest 路由无本地/R2 证明,不许照单发回。
    assert resolved == {61: "https://public-media.example/direct.mp4"}


def test_identity_resolver_gets_one_chance_with_db_fallback_disabled(batch_env, monkeypatch) -> None:
    pool_detail, cache, _cache_core = batch_env
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def fake_for_item(*args: Any, **kwargs: Any) -> str:
        calls.append((args, kwargs))
        return "https://public-media.example/native.mp4" if args[1] == "VidNative9" else ""

    monkeypatch.setattr(cache, "cached_video_url_for_item", fake_for_item)
    conn = _BatchConn()
    row = _ig_row(71, content_url="https://www.instagram.com/reel/VidNative9/")
    resolved = pool_detail._batch_cached_video_urls(conn, [row])
    assert resolved == {71: "https://public-media.example/native.mp4"}
    assert calls[0] == (("instagram", "VidNative9"), {"allow_db_fallback": False})


def test_type_error_from_rolling_double_falls_back_to_batched_projection(
    batch_env, monkeypatch
) -> None:
    pool_detail, cache, cache_core = batch_env

    def two_arg_only(_platform: str, _video_id: str) -> str:
        raise TypeError("unexpected keyword argument 'allow_db_fallback'")

    monkeypatch.setattr(cache, "cached_video_url_for_item", two_arg_only)
    monkeypatch.setattr(
        cache_core,
        "_resolved_cached_asset_row",
        lambda row: str(dict(row).get("cache_url") or ""),
    )
    asset = {
        "digest": "f" * 64,
        "cache_url": "https://public-media.example/pair.mp4",
        "storage_backend": "r2",
        "r2_key": "private/pair.mp4",
        "platform": "instagram",
        "external_id": "VidPair1",
    }
    conn = _BatchConn(assets=[asset])
    row = _ig_row(81, content_url="https://www.instagram.com/reel/VidPair1/")
    resolved = pool_detail._batch_cached_video_urls(conn, [row])
    assert resolved == {81: "https://public-media.example/pair.mp4"}


def test_batched_read_resolves_many_rows_with_single_query(batch_env, monkeypatch) -> None:
    pool_detail, _cache, cache_core = batch_env
    monkeypatch.setattr(
        cache_core,
        "_resolved_cached_asset_row",
        lambda row: str(dict(row).get("cache_url") or ""),
    )
    assets = [
        {
            "digest": str(index) * 64,
            "cache_url": f"https://public-media.example/batch-{index}.mp4",
            "storage_backend": "r2",
            "r2_key": f"private/batch-{index}.mp4",
            "platform": "instagram",
            "external_id": f"VidBatch{index}",
        }
        for index in (1, 2, 3)
    ]
    conn = _BatchConn(assets=assets)
    rows = [
        _ig_row(90 + index, content_url=f"https://www.instagram.com/reel/VidBatch{index}/")
        for index in (1, 2, 3)
    ]
    resolved = pool_detail._batch_cached_video_urls(conn, rows)
    assert len(conn.calls) == 1
    assert resolved == {
        91: "https://public-media.example/batch-1.mp4",
        92: "https://public-media.example/batch-2.mp4",
        93: "https://public-media.example/batch-3.mp4",
    }


def test_unresolvable_rows_are_left_out_of_the_result(batch_env) -> None:
    pool_detail, _cache, _cache_core = batch_env
    conn = _BatchConn()
    resolved = pool_detail._batch_cached_video_urls(conn, [_ig_row(99)])
    assert resolved == {}


# ─────────────────────────── CC 棘轮(_DecisionCounter 口径) ───────────────────────────


def test_both_shells_and_their_helpers_stay_below_cc_limits() -> None:
    from scripts.vkpi_engineering_health_collect import collect_complexity

    expectations = {
        "backend/app/domains/kol/url_deep_crawl_execute.py": {
            "shell": "_crawl_profile_basics",
            "helpers": (
                "_crawl_youtube_profile_basics",
                "_youtube_profile_channel_id",
                "_dict_video_items",
                "_crawl_generic_profile_basics",
                "_content_video_items",
                "_nested_profile_video_items",
                "_crawl_payload_status",
                "_crawl_provider_source",
            ),
        },
        "backend/app/domains/kol/pool_detail.py": {
            "shell": "_batch_cached_video_urls",
            "helpers": (
                "_batch_cache_hex_digest",
                "_batch_cache_candidates",
                "_batch_item_cache_pairs",
                "_batch_cache_asset_rows",
                "_batch_cache_asset_index",
                "_batch_resolve_cache_asset",
                "_batch_item_hint_value",
                "_batch_item_cache_value",
            ),
        },
    }
    for relative_path, spec in expectations.items():
        path = REPO_ROOT / relative_path
        rows = collect_complexity({str(path): ast.parse(path.read_text(encoding="utf-8"))})
        by_name = {row.qualified_name: row.cc for row in rows}
        assert by_name[spec["shell"]] <= 10, (relative_path, spec["shell"], by_name[spec["shell"]])
        for helper in spec["helpers"]:
            assert by_name[helper] <= 12, (relative_path, helper, by_name[helper])
