"""Provider-free tests for native video cache identity and legacy lookup."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


class _Rows:
    def __init__(self, *, one: Any = None, many: list[Any] | None = None) -> None:
        self.one = one
        self.many = many or []

    def fetchone(self):
        return self.one

    def fetchall(self):
        return list(self.many)


class _AssetConn:
    def __init__(self, *, exact: Any = None, legacy: list[Any] | None = None) -> None:
        self.exact = exact
        self.legacy = legacy or []
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, sql: str, params=()):
        compact = " ".join(str(sql).split())
        self.calls.append((compact, tuple(params)))
        if "external_id=?" in compact:
            return _Rows(one=self.exact)
        if "source_url LIKE ?" in compact:
            return _Rows(many=self.legacy)
        raise AssertionError(compact)


class _EvidenceConn:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def execute(self, sql: str, _params=()):
        if "FROM vkpi_kol_video_evidence" in str(sql):
            return _Rows(many=self.rows)
        raise AssertionError(str(sql))


def test_profile_warmup_extracts_native_ids_and_rejects_lookalike_hosts() -> None:
    from app.domains.kol.url_deep_crawl_queue import _content_url_video_id

    assert _content_url_video_id(
        "instagram", "https://www.instagram.com/creator/reel/DX8prCJOe6V/?utm_source=test"
    ) == "DX8prCJOe6V"
    assert _content_url_video_id(
        "instagram", "https://www.instagram.com/p/DYw3UWUCJ_6/"
    ) == "DYw3UWUCJ_6"
    assert _content_url_video_id(
        "tiktok", "https://www.tiktok.com/@creator/video/9876543210123456789"
    ) == "9876543210123456789"
    assert _content_url_video_id(
        "instagram", "https://instagram.com.evil.example/reel/DX8prCJOe6V/"
    ) == ""
    assert _content_url_video_id("tiktok", "javascript:alert(1)") == ""


def test_profile_warmup_uses_native_key_and_skips_unresolved_without_evidence_alias(
    monkeypatch,
) -> None:
    from app.domains.kol import search_sessions, url_deep_crawl, url_deep_crawl_queue
    from app.domains.media import cache

    rows = [
        {
            "id": 2279,
            "platform": "instagram",
            "content_url": "https://www.instagram.com/reel/DX8prCJOe6V/",
            "thumbnail_url": "",
        },
        {
            "id": 2280,
            "platform": "instagram",
            "content_url": "https://instagram.com.evil.example/reel/BadCode/",
            "thumbnail_url": "",
        },
        {
            "id": 2281,
            "platform": "tiktok",
            "content_url": "https://www.tiktok.com/@creator/video/9876543210123456789",
            "thumbnail_url": "",
        },
    ]
    monkeypatch.setattr(
        url_deep_crawl,
        "dry_run_url_deep_crawl",
        lambda _body: {"url_type": "profile", "profile_flow": {"status": "ready"}},
    )
    monkeypatch.setattr(search_sessions, "ensure_session_for_result", lambda **_kwargs: None)
    monkeypatch.setattr(url_deep_crawl_queue, "get_conn", lambda: _EvidenceConn(rows))
    warmed: list[tuple[str, str, str]] = []
    monkeypatch.setattr(cache, "cache_image", lambda _url: {"status": "cached"})
    monkeypatch.setattr(
        cache,
        "cache_video_for_item",
        lambda platform, video_id, url: warmed.append((platform, video_id, url))
        or {"status": "cached"},
    )

    url_deep_crawl_queue.run_profile_deep_crawl_for_job(
        {
            "url": "https://www.instagram.com/creator/",
            "kol_pool_id": 88,
            "mode": "account_deep",
            "representative_video_limit": 1,
        }
    )

    assert [(platform, video_id) for platform, video_id, _url in warmed] == [
        ("instagram", "DX8prCJOe6V"),
        ("tiktok", "9876543210123456789"),
    ]
    assert all(video_id not in {"2279", "2280", "2281"} for _platform, video_id, _url in warmed)


def test_item_lookup_falls_back_to_exact_db_asset_without_exposing_storage_fields(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from app.domains.media import cache_core

    conn = _AssetConn(
        exact={
            "digest": "a" * 64,
            "cache_url": "https://public-media.example/video.mp4",
            "storage_backend": "r2",
            "r2_key": "private/internal/object.mp4",
        }
    )
    monkeypatch.setattr(cache_core, "ensure_vkpi_media_cache_schema", lambda: None)
    monkeypatch.setattr(cache_core, "get_conn", lambda: conn)
    monkeypatch.setattr(cache_core, "VIDEO_CACHE_DIR", tmp_path)

    resolved = cache_core._cached_asset_url_for_item("instagram", "DX8prCJOe6V")

    assert resolved == "https://public-media.example/video.mp4"
    assert "private/internal" not in resolved
    exact_sql, exact_params = conn.calls[0]
    assert exact_params == ("instagram", "DX8prCJOe6V")
    assert "local_path" not in exact_sql


def test_native_lookup_recovers_legacy_evidence_id_asset_by_exact_source_path(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from app.domains.media import cache_core

    legacy = {
        "digest": "b" * 64,
        "cache_url": "https://public-media.example/legacy.mp4",
        "storage_backend": "r2",
        "r2_key": "private/legacy-object.mp4",
        "source_url": "https://www.instagram.com/creator/reel/DX8prCJOe6V/?utm_source=old",
    }
    lookalike = {
        **legacy,
        "cache_url": "https://public-media.example/wrong.mp4",
        "source_url": "https://instagram.com.evil.example/reel/DX8prCJOe6V/",
    }
    wrong_path = {
        **legacy,
        "cache_url": "https://public-media.example/wrong-path.mp4",
        "source_url": "https://www.instagram.com/creator/DX8prCJOe6V/",
    }
    conn = _AssetConn(exact=None, legacy=[lookalike, wrong_path, legacy])
    monkeypatch.setattr(cache_core, "ensure_vkpi_media_cache_schema", lambda: None)
    monkeypatch.setattr(cache_core, "get_conn", lambda: conn)
    monkeypatch.setattr(cache_core, "VIDEO_CACHE_DIR", tmp_path)

    resolved = cache_core._cached_asset_url_for_item("instagram", "DX8prCJOe6V")

    assert resolved == "https://public-media.example/legacy.mp4"
    assert len(conn.calls) == 2
    assert conn.calls[1][1][0] == "instagram"
    assert "DX8prCJOe6V" in conn.calls[1][1][1]


def test_persisted_expired_presigned_url_is_replaced_with_fresh_presign(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from app.domains.media import cache_core
    from app.services.media import r2

    stale = (
        "https://private-r2.example/video.mp4"
        "?X-Amz-Credential=old&X-Amz-Signature=expired&X-Amz-Expires=60"
    )
    fresh = (
        "https://private-r2.example/video.mp4"
        "?X-Amz-Credential=new&X-Amz-Signature=fresh&X-Amz-Expires=900"
    )
    conn = _AssetConn(
        exact={
            "digest": "c" * 64,
            "cache_url": stale,
            "storage_backend": "r2",
            "r2_key": "private/video.mp4",
        }
    )
    calls: list[str] = []
    monkeypatch.setattr(cache_core, "ensure_vkpi_media_cache_schema", lambda: None)
    monkeypatch.setattr(cache_core, "get_conn", lambda: conn)
    monkeypatch.setattr(cache_core, "VIDEO_CACHE_DIR", tmp_path)
    monkeypatch.setattr(cache_core, "_media_cache_r2_enabled", lambda: True)
    monkeypatch.setattr(cache_core, "_r2_public_url", lambda _key: "")
    monkeypatch.setattr(
        r2,
        "get_presigned_url",
        lambda key: calls.append(key) or fresh,
    )

    resolved = cache_core._cached_asset_url_for_item("instagram", "DX8prCJOe6V")

    assert resolved == fresh
    assert resolved != stale
    assert calls == ["private/video.mp4"]


def test_missing_or_invalid_sidecar_uses_db_fallback_for_native_and_legacy_ids(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from app.domains.media import cache

    sidecar = tmp_path / "missing.json"
    requested: list[tuple[str, str]] = []
    monkeypatch.setattr(cache, "_video_item_sidecar_path", lambda _platform, _video_id: sidecar)
    monkeypatch.setattr(
        cache,
        "_cached_asset_url_for_item",
        lambda platform, external_id: requested.append((platform, external_id))
        or f"https://public-media.example/{external_id}.mp4",
    )

    assert cache.cached_video_url_for_item("instagram", "DX8prCJOe6V") == (
        "https://public-media.example/DX8prCJOe6V.mp4"
    )
    sidecar.write_text("{not-json", encoding="utf-8")
    assert cache.cached_video_url_for_item("instagram", "2279") == (
        "https://public-media.example/2279.mp4"
    )
    assert requested == [("instagram", "DX8prCJOe6V"), ("instagram", "2279")]


def test_expired_presigned_sidecar_is_not_replayed(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from app.domains.media import cache

    digest = "e" * 64
    stale = (
        "https://private-r2.example/video.mp4"
        "?Signature=expired&Expires=1"
    )
    fresh = (
        "https://private-r2.example/video.mp4"
        "?X-Amz-Signature=fresh&X-Amz-Expires=900"
    )
    sidecar = tmp_path / "native.json"
    sidecar.write_text(
        json.dumps(
            {
                "platform": "instagram",
                "video_id": "DX8prCJOe6V",
                "digest": digest,
                "cached_url": stale,
                "storage_backend": "r2",
                "r2_key": "private/video.mp4",
            }
        ),
        encoding="utf-8",
    )
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    monkeypatch.setattr(cache, "VIDEO_CACHE_DIR", video_dir)
    monkeypatch.setattr(cache, "_video_item_sidecar_path", lambda _platform, _video_id: sidecar)
    monkeypatch.setattr(cache, "_cached_asset_url_by_digest", lambda _kind, _digest: "")
    presigned_keys: list[str] = []
    monkeypatch.setattr(
        cache,
        "_fresh_presigned_asset_url",
        lambda key, **_kwargs: presigned_keys.append(key) or fresh,
    )
    monkeypatch.setattr(
        cache,
        "_cached_asset_url_for_item",
        lambda *_args: (_ for _ in ()).throw(AssertionError("digest fallback should resolve")),
    )

    resolved = cache.cached_video_url_for_item("instagram", "DX8prCJOe6V")

    assert resolved == fresh
    assert resolved != stale
    assert presigned_keys == ["private/video.mp4"]


def test_legacy_evidence_sidecar_remains_readable_without_migration(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from app.domains.media import cache

    digest = "d" * 64
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    (video_dir / digest).write_bytes(b"video")
    sidecar = tmp_path / "legacy-2279.json"
    sidecar.write_text(
        json.dumps(
            {
                "platform": "instagram",
                "video_id": "2279",
                "digest": digest,
                "cached_url": f"/api/vkpi-media/video-cache/{digest}",
                "storage_backend": "local",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cache, "VIDEO_CACHE_DIR", video_dir)
    monkeypatch.setattr(cache, "_video_item_sidecar_path", lambda _platform, _video_id: sidecar)
    monkeypatch.setattr(
        cache,
        "_cached_asset_url_for_item",
        lambda *_args: (_ for _ in ()).throw(AssertionError("valid legacy sidecar must win")),
    )

    assert cache.cached_video_url_for_item("instagram", "2279") == (
        f"/api/vkpi-media/video-cache/{digest}"
    )


def _detail_video_row(*, evidence_id: int, digest: str, native_id: str) -> dict[str, Any]:
    content_url = f"https://www.instagram.com/reel/{native_id}/"
    return {
        "evidence_id": evidence_id,
        "id": evidence_id,
        "kol_pool_id": 3648,
        "project_id": None,
        "content_url": content_url,
        "platform": "instagram",
        "title": native_id,
        "video_title": native_id,
        "thumbnail_url": "",
        "cached_thumbnail_url": None,
        "cached_video_url": f"/api/vkpi-media/video-cache/{digest}",
        "cached_video_digest": digest,
        "view_count": 0,
        "like_count": 0,
        "comment_count": 0,
        "share_count": 0,
        "duration_seconds": 0,
        "publish_date": None,
        "posted_at": None,
        "evidence_type": "video",
        "image_urls": "[]",
        "has_final_v1_cache": False,
        "llm_viltrox_detected_text": None,
        "llm_viltrox_products": None,
        "llm_competitor_mentions": None,
        "has_keyframe_qa_cache": False,
    }


def test_pool_detail_drops_four_stale_local_ledger_routes_and_uses_original_post(
    monkeypatch,
) -> None:
    """A cached ledger label without local bytes or R2 resolution is not playback proof."""

    from app.domains.kol import pool_detail
    from app.domains.media import cache

    rows = [
        _detail_video_row(evidence_id=581, digest="1" * 64, native_id="StaleVideoA"),
        _detail_video_row(evidence_id=1746, digest="2" * 64, native_id="StaleVideoB"),
        _detail_video_row(evidence_id=1748, digest="3" * 64, native_id="StaleVideoC"),
        _detail_video_row(evidence_id=1747, digest="4" * 64, native_id="StaleVideoD"),
    ]
    monkeypatch.setattr(pool_detail, "get_conn", lambda: _EvidenceConn(rows))
    monkeypatch.setattr(pool_detail, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(cache, "cached_image_url", lambda _url: "")
    monkeypatch.setattr(cache, "cached_video_file", lambda _digest: None)
    monkeypatch.setattr(cache, "cached_video_redirect_url", lambda _digest: "")
    monkeypatch.setattr(cache, "cached_video_url_for_item", lambda _platform, _video_id: None)

    projected = pool_detail._video_evidence_for_kol(3648, limit=4)

    assert len(projected) == 4
    assert [item["cached_video_url"] for item in projected] == [None, None, None, None]
    assert [item["watch_url"] for item in projected] == [row["content_url"] for row in rows]
    assert all("cached_video_digest" not in item for item in projected)


def test_pool_detail_batches_exact_media_cache_identity_reads(monkeypatch) -> None:
    from app.domains.kol import pool_detail
    from app.domains.media import cache, cache_core

    rows = [
        _detail_video_row(
            evidence_id=700 + index,
            digest=str(index) * 64,
            native_id=f"BatchVideo{index}",
        )
        for index in range(1, 5)
    ]
    assets = [
        {
            "digest": chr(96 + index) * 64,
            "cache_url": f"https://public-media.example/batch-{index}.mp4",
            "storage_backend": "r2",
            "r2_key": f"private/batch-{index}.mp4",
            "platform": "instagram",
            "external_id": f"BatchVideo{index}",
        }
        for index in range(1, 5)
    ]

    class BatchConn:
        def __init__(self) -> None:
            self.asset_reads = 0

        def execute(self, sql: str, _params=()):
            compact = " ".join(str(sql).split())
            if "FROM vkpi_media_cache_assets" in compact:
                self.asset_reads += 1
                return _Rows(many=assets)
            raise AssertionError(compact)

    conn = BatchConn()
    monkeypatch.setattr(pool_detail, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(cache, "cached_video_file", lambda _digest: None)
    monkeypatch.setattr(
        cache,
        "cached_video_url_for_item",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        cache_core,
        "_resolved_cached_asset_row",
        lambda row: (
            str(dict(row).get("cache_url") or "")
            if str(dict(row).get("cache_url") or "").startswith("https://")
            else ""
        ),
    )

    resolved = pool_detail._batch_cached_video_urls(conn, rows)

    assert conn.asset_reads == 1
    assert resolved == {
        701: "https://public-media.example/batch-1.mp4",
        702: "https://public-media.example/batch-2.mp4",
        703: "https://public-media.example/batch-3.mp4",
        704: "https://public-media.example/batch-4.mp4",
    }


def test_pool_detail_keeps_legacy_cache_resolution_for_unbatched_platforms(monkeypatch) -> None:
    from app.domains.kol import pool_detail, video_tracking
    from app.domains.media import cache

    platforms = ("bilibili", "douyin", "xiaohongshu")
    rows: list[dict[str, Any]] = []
    for index, platform in enumerate(platforms, start=1):
        row = _detail_video_row(
            evidence_id=800 + index,
            digest=str(index) * 64,
            native_id=f"LegacyPlatform{index}",
        )
        row.update(
            {
                "platform": platform,
                "content_url": f"https://{platform}.example/video/{index}",
                "cached_video_url": None,
                "cached_video_digest": None,
            }
        )
        rows.append(row)

    monkeypatch.setattr(pool_detail, "get_conn", lambda: _EvidenceConn(rows))
    monkeypatch.setattr(pool_detail, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(
        pool_detail.content_metric_snapshots,
        "metric_trends_for_evidence",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(video_tracking, "product_links_for_evidence", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(cache, "cached_image_url", lambda _url: "")
    monkeypatch.setattr(pool_detail, "_batch_cached_video_urls", lambda *_args, **_kwargs: {})
    legacy_calls: list[str] = []
    monkeypatch.setattr(
        pool_detail,
        "_validated_cached_video_url",
        lambda item, platform: legacy_calls.append(platform)
        or f"https://public-media.example/{platform}-{item['evidence_id']}.mp4",
    )

    projected = pool_detail._video_evidence_for_kol(3648, limit=3)

    assert legacy_calls == list(platforms)
    assert [item["cached_video_url"] for item in projected] == [
        "https://public-media.example/bilibili-801.mp4",
        "https://public-media.example/douyin-802.mp4",
        "https://public-media.example/xiaohongshu-803.mp4",
    ]


def test_pool_detail_keeps_verified_local_range_cache_route(monkeypatch, tmp_path: Path) -> None:
    from app.domains.kol import pool_detail
    from app.domains.media import cache

    digest = "a" * 64
    local_file = tmp_path / digest
    local_file.write_bytes(b"video")
    content_type = tmp_path / f"{digest}.content-type"
    content_type.write_text("video/mp4", encoding="utf-8")
    monkeypatch.setattr(cache, "cached_video_file", lambda value: (local_file, "video/mp4") if value == digest else None)
    monkeypatch.setattr(
        cache,
        "cached_video_redirect_url",
        lambda _digest: (_ for _ in ()).throw(AssertionError("local bytes must win")),
    )
    monkeypatch.setattr(
        cache,
        "cached_video_url_for_item",
        lambda *_args: (_ for _ in ()).throw(AssertionError("identity fallback must not run")),
    )

    resolved = pool_detail._validated_cached_video_url(
        {
            "id": 88,
            "platform": "instagram",
            "content_url": "https://www.instagram.com/reel/RangeVideoA/",
            "cached_video_digest": digest,
            "cached_video_url": f"/api/vkpi-media/video-cache/{digest}",
        },
        "instagram",
    )

    assert resolved == f"/api/vkpi-media/video-cache/{digest}"


def test_pool_detail_re_resolves_digest_route_to_playable_r2_url(monkeypatch) -> None:
    from app.domains.kol import pool_detail
    from app.domains.media import cache

    digest = "b" * 64
    r2_url = "https://public-media.example/r2/video.mp4"
    monkeypatch.setattr(cache, "cached_video_file", lambda _digest: None)
    monkeypatch.setattr(cache, "cached_video_redirect_url", lambda value: r2_url if value == digest else "")
    monkeypatch.setattr(
        cache,
        "cached_video_url_for_item",
        lambda *_args: (_ for _ in ()).throw(AssertionError("digest R2 resolution must win")),
    )

    resolved = pool_detail._validated_cached_video_url(
        {
            "id": 89,
            "platform": "instagram",
            "content_url": "https://www.instagram.com/reel/R2VideoA/",
            "cached_video_digest": digest,
            "cached_video_url": f"/api/vkpi-media/video-cache/{digest}",
        },
        "instagram",
    )

    assert resolved == r2_url
