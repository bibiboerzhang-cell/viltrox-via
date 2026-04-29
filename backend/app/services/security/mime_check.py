"""
services/security/mime_check.py — lightweight upload magic-number checks.

The project may use python-magic in production later, but these checks avoid
trusting client-supplied Content-Type while keeping local setup simple.
"""
from __future__ import annotations

from pathlib import Path

VIDEO_MIME_TYPES = {
    "video/mp4",
    "video/quicktime",
    "video/webm",
    "video/x-msvideo",
    "video/mpeg",
}

IMAGE_MIME_TYPES = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
}


def _head(path: str | Path, size: int = 64) -> bytes:
    with open(path, "rb") as fh:
        return fh.read(size)


def detect_upload_kind(path: str | Path) -> str:
    data = _head(path)
    if len(data) < 4:
        return "unknown"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image"
    if data.startswith(b"\xff\xd8\xff"):
        return "image"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image"
    if data.startswith(b"RIFF") and data[8:12] == b"AVI ":
        return "video"
    if data.startswith(b"\x1aE\xdf\xa3"):
        return "video"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brand = data[8:16].lower()
        if any(marker in brand for marker in (b"mp4", b"isom", b"iso2", b"qt  ", b"m4v", b"3gp")):
            return "video"
    if b"moov" in data or b"mdat" in data or b"mvhd" in data:
        return "video"
    return "unknown"


def validate_upload_magic(path: str | Path, expected: str) -> bool:
    return detect_upload_kind(path) == expected
