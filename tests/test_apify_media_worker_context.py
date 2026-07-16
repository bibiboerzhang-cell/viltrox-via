from __future__ import annotations

import hashlib
from contextlib import nullcontext
from pathlib import Path

import pytest

from app.platform.apify_budget import apify_execution_context, current_apify_execution_context
from app.services.scraping import apify as apify_scraper
from app.workers import apify_jobs_worker_media as media


class _RowsCursor:
    def __init__(self, rows):
        self.rows = rows
        self.sql = ""
        self.params = ()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=()):
        self.sql = " ".join(str(sql).split())
        self.params = params

    def fetchall(self):
        return self.rows


class _RowsConn:
    def __init__(self, rows):
        self.cursor_value = _RowsCursor(rows)

    def cursor(self, **_kwargs):
        return self.cursor_value

    def transaction(self):
        return nullcontext()


def test_instagram_media_resolution_keeps_durable_fence_in_parent_process(monkeypatch):
    observed: list[tuple[str, int] | None] = []

    async def fake_scrape(url: str, platform: str, *, timeout_secs: int | None = None):
        observed.append(current_apify_execution_context())
        assert url == "https://www.instagram.com/reel/abc123/"
        assert platform == "instagram"
        assert timeout_secs == media.MEDIA_RESOLVE_TIMEOUT_SECONDS
        return {
            "scraped_ok": True,
            "video_url": "https://cdn.example.test/reel.mp4",
        }

    monkeypatch.setenv("APIFY_TOKEN", "configured-for-test")
    monkeypatch.setattr(apify_scraper, "scrape_with_apify", fake_scrape)
    monkeypatch.setattr(
        media.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("media resolver must not spawn a child")),
    )

    with apify_execution_context("apify-job:123", 17):
        result = media._resolve_video_media({"content_url": "https://www.instagram.com/reel/abc123/"})

    assert observed == [("apify-job:123", 17)]
    assert result["ok"] is True
    assert result["direct_video_url"] == "https://cdn.example.test/reel.mp4"
    assert "durable_execution_context_required" not in str(result)


def test_media_resolution_without_worker_fence_fails_closed_without_provider_call(monkeypatch):
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("provider adapter must not run without a durable fence")

    monkeypatch.setenv("APIFY_TOKEN", "configured-for-test")
    monkeypatch.setattr(apify_scraper, "scrape_with_apify", forbidden)

    result = media._resolve_video_media({"content_url": "https://www.tiktok.com/@creator/video/123"})

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert "durable_execution_context_required" in result["reason"]


def test_r2_cache_hit_is_materialized_before_provider_resolver(monkeypatch, tmp_path: Path):
    payload = b"real-cached-tiktok-video"
    checksum = hashlib.sha256(payload).hexdigest()
    conn = _RowsConn(
        [
            {
                "id": 11153,
                "platform": "tiktok",
                "external_id": "7501797229913459999",
                "source_url": "https://www.tiktok.com/@exposureworks/video/7501797229913459999",
                "digest": "source-url-digest",
                "checksum": checksum,
                "content_type": "video/mp4",
                "size_bytes": len(payload),
                "storage_backend": "r2",
                "local_path": "",
                "r2_key": "private/video.mp4",
                "updated_at": "2026-07-16T17:27:24Z",
            }
        ]
    )

    def fake_download(r2_key: str, local_path: str):
        assert r2_key == "private/video.mp4"
        Path(local_path).write_bytes(payload)
        return local_path

    monkeypatch.setattr("app.services.media.r2.download_file", fake_download)
    monkeypatch.setattr(
        media,
        "_resolve_video_media",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("provider resolver must not run on a validated cache hit")
        ),
    )

    result = media._resolve_cached_or_provider_video(
        conn,
        {"content_url": "https://www.tiktok.com/@exposureworks/video/7501797229913459999"},
        str(tmp_path),
    )

    assert result["ok"] is True
    assert result["cache_hit"] is True
    assert result["cache_source"] == "r2_cache"
    assert result["cache_asset_id"] == 11153
    assert result["bytes"] == len(payload)
    assert Path(result["path"]).read_bytes() == payload
    assert "source_url_hash=%s" in conn.cursor_value.sql


def test_invalid_cached_bytes_fall_back_to_existing_provider_resolver(monkeypatch, tmp_path: Path):
    conn = _RowsConn(
        [
            {
                "id": 9,
                "platform": "instagram",
                "external_id": "abc123",
                "source_url": "https://www.instagram.com/reel/abc123/",
                "digest": "source-url-digest",
                "checksum": hashlib.sha256(b"expected").hexdigest(),
                "content_type": "video/mp4",
                "size_bytes": len(b"expected"),
                "storage_backend": "r2",
                "local_path": "",
                "r2_key": "private/corrupt.mp4",
                "updated_at": "2026-07-16T17:27:24Z",
            }
        ]
    )

    def fake_download(_r2_key: str, local_path: str):
        Path(local_path).write_bytes(b"corrupt")
        return local_path

    resolver_calls = []
    monkeypatch.setattr("app.services.media.r2.download_file", fake_download)
    monkeypatch.setattr(
        media,
        "_resolve_video_media",
        lambda evidence: resolver_calls.append(evidence) or {
            "ok": True,
            "status": "ready",
            "reason": "media_resolved",
            "direct_video_url": "https://cdn.example.test/video.mp4",
        },
    )

    result = media._resolve_cached_or_provider_video(
        conn,
        {"content_url": "https://www.instagram.com/reel/abc123/"},
        str(tmp_path),
    )

    assert len(resolver_calls) == 1
    assert result["ok"] is True
    assert result["cache_hit"] is False
    assert result["cache_lookup_reason"] == "media_cache_invalid"
    assert "media_cache_size_mismatch" in result["cache_failure_reasons"]
    assert result["direct_video_url"] == "https://cdn.example.test/video.mp4"


def test_gemini_worker_consumes_cache_path_without_redownload_or_reupload(monkeypatch, tmp_path: Path):
    from app.workers import apify_jobs_worker_gemini as gemini

    cached_path = tmp_path / "cached.mp4"
    cached_path.write_bytes(b"validated-cache-input")
    analyzer_payloads = []
    raw = {
        "analyzed": False,
        "model": gemini.WORKER_GEMINI_MODEL,
        "method": "test_stop_after_media_boundary",
        "error": "expected_stop",
    }
    monkeypatch.setattr(gemini, "_target", lambda _payload: ("video", "3683"))
    monkeypatch.setattr(gemini, "_derive_method", lambda _payload: "video_analysis_final_v1")
    monkeypatch.setattr(
        gemini,
        "_load_video_evidence",
        lambda _conn, _target_id: {
            "id": 3683,
            "content_url": "https://www.tiktok.com/@exposureworks/video/7501797229913459999",
            "title": "Cached TikTok",
            "creator_handle": "exposureworks",
        },
    )
    monkeypatch.setattr(
        gemini,
        "_resolve_cached_or_provider_video",
        lambda _conn, _evidence, _tmpdir: {
            "ok": True,
            "status": "ready",
            "reason": "media_cache_hit",
            "cache_hit": True,
            "cache_source": "r2_cache",
            "cache_asset_id": 11153,
            "platform": "tiktok",
            "path": str(cached_path),
            "bytes": cached_path.stat().st_size,
        },
    )
    monkeypatch.setattr(
        gemini,
        "download_direct_video_url",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("cache hit must not redownload provider media")
        ),
    )
    monkeypatch.setattr(
        gemini,
        "_warm_video_to_r2_from_local",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("cache hit must not upload the same bytes again")
        ),
    )
    monkeypatch.setattr(
        gemini,
        "_run_gemini_analyzer_with_timeout",
        lambda payload, **_kwargs: analyzer_payloads.append(payload) or raw,
    )
    monkeypatch.setattr(
        gemini,
        "_authoritative_gemini_cost",
        lambda *_args, **_kwargs: (0.0, "test", 0, 0),
    )
    monkeypatch.setattr(gemini, "_record_gemini_cost", lambda **_kwargs: {})

    with pytest.raises(RuntimeError, match="expected_stop"):
        gemini._process_gemini_video(
            object(),
            {"id": 1642},
            {
                "target_type": "video",
                "target_id": "3683",
                "derive_method": "video_analysis_final_v1",
                "_llm_execution": {},
            },
            0.0,
        )

    assert len(analyzer_payloads) == 1
    assert analyzer_payloads[0]["mode"] == "local"
    assert analyzer_payloads[0]["video_path"] == str(cached_path)
    assert raw["media_resolution"] == {
        "platform": "tiktok",
        "source_url_host": "www.tiktok.com",
        "direct_video_url_host": None,
        "status": "ready",
        "cache_hit": True,
        "cache_source": "r2_cache",
        "cache_asset_id": 11153,
        "cache_lookup_reason": None,
    }
