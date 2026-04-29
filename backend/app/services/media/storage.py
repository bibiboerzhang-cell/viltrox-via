"""
services/media/storage.py — upload asset location + analysis path resolution

`storage_key` / `r2_key` are persistence identifiers.
`analysis_path` is the local, decoded/readable file path that AI analyzers may consume.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.config import CREATOR_DIR, FRAMES_DIR, UPLOAD_DIR


PROJECT_ROOT = Path(__file__).resolve().parents[4]
BACKEND_ROOT = PROJECT_ROOT / "backend"
UPLOAD_ROOT = (PROJECT_ROOT / UPLOAD_DIR).resolve() if not UPLOAD_DIR.is_absolute() else UPLOAD_DIR.resolve()
FRAMES_ROOT = (PROJECT_ROOT / FRAMES_DIR).resolve() if not FRAMES_DIR.is_absolute() else FRAMES_DIR.resolve()
CREATOR_ROOT = (PROJECT_ROOT / CREATOR_DIR).resolve() if not CREATOR_DIR.is_absolute() else CREATOR_DIR.resolve()


def resolve_local_media_path(pathish: str) -> str:
    text = str(pathish or "").strip()
    if not text:
        return ""

    if text.startswith("videos/"):
        return ""

    raw_path = Path(text)
    candidates: list[Path] = []

    if raw_path.is_absolute():
        candidates.append(raw_path)
    else:
        normalized = text.lstrip("/")
        candidates.extend(
            [
                PROJECT_ROOT / normalized,
                BACKEND_ROOT / normalized,
            ]
        )
        basename = raw_path.name.strip()
        if basename:
            candidates.extend(
                [
                    UPLOAD_ROOT / basename,
                    FRAMES_ROOT / basename,
                    CREATOR_ROOT / basename,
                ]
            )

    for candidate in candidates:
        try:
            if candidate.exists():
                return str(candidate.resolve())
        except Exception:
            continue
    return ""


def normalize_uploaded_video_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not payload:
        return None

    normalized = dict(payload)
    storage_key = str(normalized.get("storage_key") or "").strip()
    path_value = str(normalized.get("path") or "").strip()
    r2_key = str(normalized.get("r2_key") or "").strip()

    if storage_key.startswith("videos/") and not r2_key:
        r2_key = storage_key

    local_path = resolve_local_media_path(path_value or storage_key)

    normalized["storage_key"] = storage_key or path_value
    normalized["r2_key"] = r2_key
    normalized["path"] = local_path
    normalized["analysis_path"] = local_path
    normalized["video_factory_required"] = bool(r2_key and not local_path)
    return normalized
