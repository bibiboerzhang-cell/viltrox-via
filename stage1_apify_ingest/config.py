"""
Stage 1 ingest config + path conventions.

All secrets come from env vars. Path conventions are the contract that Stage 2
reads against, so key shapes must not change.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field


def channel_key(kol_id: str) -> str:
    return f"{kol_id}/_channel.json"


def video_meta_key(kol_id: str, video_id: str) -> str:
    return f"{kol_id}/{video_id}/meta.json"


def transcript_key(kol_id: str, video_id: str) -> str:
    return f"{kol_id}/{video_id}/transcript.json"


def storyboard_key(kol_id: str, video_id: str, frame_idx: int) -> str:
    return f"{kol_id}/{video_id}/storyboard/{frame_idx}.jpg"


def kol_manifest_key(kol_id: str) -> str:
    return f"{kol_id}/_manifest.json"


GLOBAL_MANIFEST_KEY = "_global_manifest.json"


def _env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return default


def _account_id_from_endpoint(endpoint: str) -> str:
    match = re.search(r"https?://([^.]+)\.r2\.cloudflarestorage\.com", endpoint)
    return match.group(1) if match else ""


@dataclass
class Settings:
    local_root: str = field(
        default_factory=lambda: os.environ.get("INGEST_LOCAL_ROOT", "./kol_assets")
    )

    r2_account_id: str = field(
        default_factory=lambda: _env("R2_ACCOUNT_ID")
        or _account_id_from_endpoint(_env("R2_ENDPOINT", "R2_ENDPOINT_URL"))
    )
    r2_access_key: str = field(default_factory=lambda: _env("R2_ACCESS_KEY_ID"))
    r2_secret_key: str = field(default_factory=lambda: _env("R2_SECRET_ACCESS_KEY"))
    r2_bucket: str = field(default_factory=lambda: _env("R2_BUCKET", "R2_BUCKET_NAME", default="vkpi-kol-assets"))
    r2_endpoint_override: str = field(default_factory=lambda: _env("R2_ENDPOINT", "R2_ENDPOINT_URL"))

    @property
    def r2_endpoint(self) -> str:
        if self.r2_endpoint_override:
            return self.r2_endpoint_override
        return f"https://{self.r2_account_id}.r2.cloudflarestorage.com"

    apify_token: str = field(default_factory=lambda: _env("APIFY_TOKEN", "APIFY_API_TOKEN"))

    # Pinned Stage 1 actors. Secret tokens still come only from env.
    apify_channel_actor: str = "streamers/youtube-scraper"
    apify_transcript_actor: str = "pintostudio/youtube-transcript-scraper"

    fetch_transcript: bool = True
    fetch_storyboard: bool = False
    max_videos_per_kol: int = 0

    concurrency: int = 4
    max_retries: int = 3


SETTINGS = Settings()

