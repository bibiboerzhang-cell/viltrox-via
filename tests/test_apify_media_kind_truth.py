"""Conservative provider-metadata classification for IG/TikTok media."""
from __future__ import annotations

import asyncio

from app.services.media.resolution_state import (
    needs_secondary_video_probe,
    stamp_video_media_resolution,
)
from app.services.scraping import apify
from app.services.scraping.apify import _instagram_media_kind, _tiktok_media_kind


class _Dataset:
    def __init__(self, item: dict) -> None:
        self.item = item

    def iterate_items(self):
        return iter([self.item])


class _Client:
    def __init__(self, item: dict) -> None:
        self.item = item

    def dataset(self, _dataset_id: str) -> _Dataset:
        return _Dataset(self.item)


def test_instagram_explicit_single_image_is_non_video() -> None:
    assert _instagram_media_kind({"type": "Image"}) == "image"


def test_instagram_all_image_sidecar_is_non_video() -> None:
    assert _instagram_media_kind(
        {
            "type": "Sidecar",
            "childPosts": [
                {"type": "Image", "displayUrl": "https://img.example/a.jpg"},
                {"type": "GraphImage", "displayUrl": "https://img.example/b.jpg"},
            ],
        }
    ) == "image"


def test_instagram_sidecar_with_video_child_is_not_mislabeled_image() -> None:
    assert _instagram_media_kind(
        {
            "type": "Sidecar",
            "childPosts": [{"type": "Image"}, {"type": "Video"}],
        }
    ) == "video"


def test_instagram_missing_url_and_missing_type_remains_unknown() -> None:
    # Missing download fields are not evidence of either a photo or a video.
    assert _instagram_media_kind({"type": "Sidecar", "childPosts": [{}]}) == ""


def test_instagram_partially_typed_sidecar_remains_unknown() -> None:
    assert _instagram_media_kind(
        {"type": "Sidecar", "childPosts": [{"type": "Image"}, {}]}
    ) == ""


def test_direct_instagram_video_url_wins_over_stale_image_label() -> None:
    assert _instagram_media_kind(
        {"type": "Image"}, video_url="https://cdn.example/video.mp4"
    ) == "video"


def test_tiktok_photo_mode_is_non_video() -> None:
    assert _tiktok_media_kind(
        {"isPhotoMode": "true", "images": [{"url": "https://img.example/a.jpg"}]}
    ) == "image"


def test_tiktok_video_metadata_is_video_even_without_download_url() -> None:
    assert _tiktok_media_kind({"videoMeta": {"duration": 17}}) == "video"


def test_tiktok_ambiguous_item_remains_unknown() -> None:
    assert _tiktok_media_kind({"text": "metadata only"}) == ""


def test_confirmed_non_video_overrides_contradictory_ready_flags() -> None:
    result = stamp_video_media_resolution(
        {"ok": True, "path": "/tmp/not-really-video.jpg"},
        scrape_success=True,
        media_resolved=True,
        downloadable=True,
        confirmed_non_video=True,
    )
    assert result["confirmed_non_video"] is True
    assert result["media_resolved"] is False
    assert result["downloadable"] is False
    assert result["media_resolution_state"] == "confirmed_non_video"


def test_resolved_but_not_downloadable_stays_distinct_and_skips_reprobe() -> None:
    result = stamp_video_media_resolution(
        {"ok": False, "scraped_ok": True},
        media_resolved=True,
        downloadable=False,
        confirmed_non_video=False,
    )
    assert result["media_resolved"] is True
    assert result["downloadable"] is False
    assert result["media_resolution_state"] == "media_resolved_not_downloadable"
    assert needs_secondary_video_probe(result) is False


def test_legacy_scraped_ok_metadata_only_is_probe_eligible() -> None:
    assert needs_secondary_video_probe(
        {"ok": False, "scraped_ok": True, "reason": "old_or_changed_wording"}
    ) is True


def test_instagram_scraper_projects_explicit_image_truth(monkeypatch) -> None:
    monkeypatch.setattr(apify, "_client", _Client({"type": "Image", "caption": "photo"}))
    monkeypatch.setattr(
        apify,
        "call_apify_actor",
        lambda *_args, **_kwargs: {"id": "run-ig", "defaultDatasetId": "ds-ig"},
    )
    monkeypatch.setattr(apify, "_record_run_cost", lambda *_args, **_kwargs: None)

    result = asyncio.run(apify.scrape_instagram("https://www.instagram.com/p/image/"))

    assert result["scraped_ok"] is True
    assert result["video_url"] == ""
    assert result["media_kind"] == "image"
    assert result["confirmed_non_video"] is True


def test_tiktok_scraper_projects_photo_mode_truth(monkeypatch) -> None:
    monkeypatch.setattr(
        apify,
        "_client",
        _Client({"isPhotoMode": True, "images": [{"url": "https://img.example/a"}]}),
    )
    monkeypatch.setattr(
        apify,
        "call_apify_actor",
        lambda *_args, **_kwargs: {"id": "run-tt", "defaultDatasetId": "ds-tt"},
    )
    monkeypatch.setattr(apify, "_record_run_cost", lambda *_args, **_kwargs: None)

    result = asyncio.run(apify.scrape_tiktok("https://www.tiktok.com/@x/video/1"))

    assert result["scraped_ok"] is True
    assert result["video_url"] == ""
    assert result["media_kind"] == "image"
    assert result["confirmed_non_video"] is True
