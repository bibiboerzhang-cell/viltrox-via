"""Media cache domain facade."""
from __future__ import annotations

from app.domains.media.cache import (
    VideoCacheCancelled,
    cache_image,
    cache_video,
    cache_video_for_item,
    cached_image_file,
    cached_image_url,
    cached_video_file,
    cached_video_redirect_url,
    cached_video_url,
    cached_video_url_for_item,
    migrate_local_video_cache_to_r2,
    prewarm_official_media_cache,
    video_cache_item_state,
)
from app.domains.media.cache_core import MAX_IMAGE_BYTES, ensure_vkpi_media_cache_schema

__all__ = [
    "MAX_IMAGE_BYTES",
    "VideoCacheCancelled",
    "cache_image",
    "cache_video",
    "cache_video_for_item",
    "cached_image_file",
    "cached_image_url",
    "cached_video_file",
    "cached_video_redirect_url",
    "cached_video_url",
    "cached_video_url_for_item",
    "ensure_vkpi_media_cache_schema",
    "migrate_local_video_cache_to_r2",
    "prewarm_official_media_cache",
    "video_cache_item_state",
]
