"""
services/media/url_archive.py — URL submission video archive helpers
"""
from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.db.repositories.assets import register_submission_asset
from app.services.media.r2 import upload_file as r2_upload
from app.services.scraping.ytdlp import download_video_ytdlp


def _guess_extension(source_url: str, content_type: str = "") -> str:
    parsed = urlparse(source_url or "")
    ext = Path(parsed.path or "").suffix.lower()
    if ext:
        return ext
    guessed = mimetypes.guess_extension((content_type or "").split(";")[0].strip())
    return guessed or ".mp4"


async def _download_direct_video(url: str, dst_path: str) -> dict:
    timeout = httpx.Timeout(60.0, connect=20.0, read=120.0)
    headers = {
        "User-Agent": "ViltroxKOLAudit/3.0 (internal archival)",
        "Accept": "*/*",
    }
    size_bytes = 0
    content_type = ""
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        async with client.stream("GET", url, headers=headers) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").split(";")[0].strip()
            with open(dst_path, "wb") as handle:
                async for chunk in response.aiter_bytes():
                    if not chunk:
                        continue
                    handle.write(chunk)
                    size_bytes += len(chunk)
    return {
        "success": size_bytes > 0 and os.path.exists(dst_path),
        "path": dst_path if os.path.exists(dst_path) else "",
        "size_bytes": size_bytes,
        "content_type": content_type,
        "method": "direct",
    }


async def archive_submission_video_url(
    *,
    submission_id: int,
    source_url: str,
    platform: str,
    scraped_video_url: str = "",
    title: str = "",
    content_type_hint: str = "",
) -> dict:
    if not submission_id or not (scraped_video_url or source_url):
        return {"archived": False, "error": "missing submission_id or source"}

    direct_url = (scraped_video_url or "").strip()
    source_url = (source_url or "").strip()
    platform_key = (platform or "unknown").strip().lower().replace(" ", "_")

    with tempfile.TemporaryDirectory(prefix="viltrox-archive-") as tmpdir:
        download_result = None

        if direct_url.startswith("http://") or direct_url.startswith("https://"):
            ext = _guess_extension(direct_url, content_type_hint)
            direct_path = os.path.join(tmpdir, f"direct{ext}")
            try:
                download_result = await _download_direct_video(direct_url, direct_path)
            except Exception as exc:
                download_result = {
                    "success": False,
                    "error": f"direct download failed: {exc}",
                    "method": "direct",
                }

        if not download_result or not download_result.get("success"):
            fallback = await asyncio.to_thread(download_video_ytdlp, source_url, tmpdir, 180)
            if not fallback.get("success"):
                err = fallback.get("error") or (download_result or {}).get("error") or "archive download failed"
                return {"archived": False, "error": err}
            download_result = {
                "success": True,
                "path": fallback.get("path", ""),
                "size_bytes": os.path.getsize(fallback["path"]) if fallback.get("path") and os.path.exists(fallback["path"]) else 0,
                "content_type": content_type_hint or "video/mp4",
                "duration_ms": int(float(fallback.get("duration") or 0) * 1000),
                "method": "yt-dlp",
                "scope": "analysis_clip" if platform_key == "youtube" else "source_video",
            }

        local_path = download_result.get("path", "")
        if not local_path or not os.path.exists(local_path):
            return {"archived": False, "error": "downloaded file missing"}

        ext = Path(local_path).suffix or _guess_extension(direct_url or source_url, download_result.get("content_type", "") or content_type_hint)
        hash_seed = hashlib.sha1(f"{source_url}|{direct_url}|{title}".encode("utf-8", errors="ignore")).hexdigest()[:12]
        r2_key = f"videos/url_submissions/{submission_id}/{platform_key}_{hash_seed}{ext}"
        mime_type = download_result.get("content_type") or content_type_hint or mimetypes.guess_type(local_path)[0] or "video/mp4"

        try:
            await asyncio.to_thread(r2_upload, local_path, r2_key, mime_type)
        except Exception as exc:
            return {"archived": False, "error": f"R2 upload failed: {exc}"}

        await asyncio.to_thread(
            register_submission_asset,
            submission_id=int(submission_id),
            asset_role="url_video_archive",
            storage_key=r2_key,
            mime_type=mime_type,
            size_bytes=int(download_result.get("size_bytes") or os.path.getsize(local_path)),
            duration_ms=int(download_result.get("duration_ms") or 0),
            replace_existing=True,
        )

        return {
            "archived": True,
            "asset_role": "url_video_archive",
            "r2_key": r2_key,
            "mime_type": mime_type,
            "size_bytes": int(download_result.get("size_bytes") or os.path.getsize(local_path)),
            "method": download_result.get("method", ""),
            "scope": download_result.get("scope", "source_video"),
            "source_url": source_url,
            "video_url": direct_url,
        }
