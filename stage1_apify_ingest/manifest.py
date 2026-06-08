"""
Manifest is the contract handed to Stage 2 and the resume/skip source for Stage
1 itself. Status is tracked per video.
"""
from __future__ import annotations

import time
from typing import Optional

from . import config
from .storage import DualStore


def _now() -> float:
    return time.time()


class ManifestStore:
    def __init__(self, store: DualStore):
        self.store = store

    def load_global(self) -> dict:
        manifest = self.store.read_json_local(config.GLOBAL_MANIFEST_KEY)
        return manifest or {"kols": {}, "updated_at": None}

    def save_global(self, manifest: dict) -> None:
        manifest["updated_at"] = _now()
        self.store.put_json(config.GLOBAL_MANIFEST_KEY, manifest)

    def mark_kol(self, manifest: dict, kol_id: str, status: str, video_count: int) -> None:
        manifest["kols"][kol_id] = {
            "status": status,
            "video_count": video_count,
            "updated_at": _now(),
        }

    def load_kol(self, kol_id: str) -> dict:
        manifest = self.store.read_json_local(config.kol_manifest_key(kol_id))
        return manifest or {"kol_id": kol_id, "videos": {}, "updated_at": None}

    def save_kol(self, manifest: dict) -> None:
        manifest["updated_at"] = _now()
        self.store.put_json(config.kol_manifest_key(manifest["kol_id"]), manifest)

    def set_video(
        self,
        manifest: dict,
        video_id: str,
        status: str,
        error: Optional[str] = None,
    ) -> None:
        entry = manifest["videos"].get(video_id, {"retries": 0})
        if status == "failed":
            entry["retries"] = entry.get("retries", 0) + 1
            entry["error"] = error
        elif status == "done":
            entry.pop("error", None)
        entry["status"] = status
        entry["updated_at"] = _now()
        manifest["videos"][video_id] = entry

    def failed_videos(self, manifest: dict, max_retries: int) -> list[str]:
        return [
            video_id
            for video_id, entry in manifest["videos"].items()
            if entry.get("status") == "failed" and entry.get("retries", 0) < max_retries
        ]

