#!/usr/bin/env python3
"""Prepare a deterministic local KOL inventory folder.

This script is intentionally a planner/exporter, not a downloader. It reads the
current KOL inventory and known video evidence, then creates a local folder
layout plus JSONL manifests that later Apify/yt-dlp/Decodo download workers can
consume.

Default scope is "inventory", defined as KOLs present in vkpi_kol_pool_favorites.
No database writes, no provider calls, no LLM calls.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.db.connection import get_conn  # noqa: E402


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_slug(value: Any, *, fallback: str, max_len: int = 72) -> str:
    raw = _text(value).lower()
    raw = re.sub(r"[^a-z0-9]+", "-", raw)
    raw = re.sub(r"-{2,}", "-", raw).strip("-")
    if not raw:
        raw = fallback
    return raw[:max_len].strip("-") or fallback


def _short_hash(value: Any, length: int = 10) -> str:
    return hashlib.sha1(_text(value).encode("utf-8")).hexdigest()[:length]


def _iso_date(value: Any) -> str:
    raw = _text(value)
    if not raw:
        return "undated"
    # Accept ISO strings and YYYY-MM-DD-ish values without being strict.
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        return "".join(m.groups())
    return "undated"


def _video_uid(platform: str, url: str, fallback_id: Any) -> str:
    url = _text(url)
    parsed = urlparse(url)
    platform = _text(platform).lower()
    if platform == "youtube" or "youtube.com" in parsed.netloc or "youtu.be" in parsed.netloc:
        if "youtu.be" in parsed.netloc:
            candidate = parsed.path.strip("/").split("/")[0]
            if candidate:
                return candidate[:32]
        qs_v = parse_qs(parsed.query).get("v", [""])[0]
        if qs_v:
            return qs_v[:32]
        parts = [p for p in parsed.path.split("/") if p]
        for marker in ("shorts", "embed", "live"):
            if marker in parts:
                idx = parts.index(marker)
                if idx + 1 < len(parts):
                    return parts[idx + 1][:32]
    if platform == "tiktok" or "tiktok.com" in parsed.netloc:
        m = re.search(r"/video/(\d+)", parsed.path)
        if m:
            return m.group(1)[:32]
    if platform == "instagram" or "instagram.com" in parsed.netloc:
        m = re.search(r"/(?:p|reel|tv)/([^/?#]+)", parsed.path)
        if m:
            return m.group(1)[:32]
    fallback = _text(fallback_id)
    return fallback if fallback else _short_hash(url)


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _select_kols(scope: str, limit: int | None) -> list[dict[str, Any]]:
    conn = get_conn()
    base = """
        SELECT
            p.id,
            COALESCE(p.handle, '') AS handle,
            COALESCE(p.display_name, '') AS display_name,
            COALESCE(p.platform, '') AS platform,
            COALESCE(p.profile_url, '') AS profile_url,
            '' AS channel_id,
            COALESCE(p.posts_count, 0) AS posts_count,
            COALESCE(p.followers, 0) AS followers_count,
            COALESCE(p.avg_views, 0) AS avg_views,
            COALESCE(p.country, '') AS country,
            COALESCE(p.dashboard_account_type, '') AS profile_type,
            '' AS recall_status,
            p.created_at,
            p.updated_at
        FROM vkpi_kol_pool p
    """
    params: tuple[Any, ...] = ()
    if scope == "inventory":
        base += " WHERE EXISTS (SELECT 1 FROM vkpi_kol_pool_favorites f WHERE f.kol_pool_id = p.id)"
    base += " ORDER BY LOWER(COALESCE(p.platform, '')), LOWER(COALESCE(p.handle, p.display_name, '')), p.id"
    if limit:
        base += " LIMIT ?"
        params = (int(limit),)
    return [_row_dict(row) for row in conn.execute(base, params).fetchall()]


def _select_evidence(kol_ids: list[int]) -> list[dict[str, Any]]:
    if not kol_ids:
        return []
    conn = get_conn()
    placeholders = ",".join("?" for _ in kol_ids)
    rows = conn.execute(
        f"""
        SELECT
            e.id,
            e.kol_pool_id,
            COALESCE(e.platform, '') AS platform,
            COALESCE(e.content_url, '') AS content_url,
            COALESCE(NULLIF(e.title, ''), NULLIF(e.video_title, ''), '') AS title,
            '' AS description,
            COALESCE(e.thumbnail_url, '') AS thumbnail_url,
            COALESCE(e.view_count, 0) AS view_count,
            COALESCE(e.like_count, 0) AS like_count,
            COALESCE(e.comment_count, 0) AS comment_count,
            e.publish_date,
            COALESCE(e.duration_seconds, 0) AS duration_seconds,
            COALESCE(e.scrape_source, '') AS scrape_source,
            COALESCE(e.scrape_status, '') AS scrape_status,
            e.created_at,
            e.updated_at,
            ac.id AS final_v1_cache_id,
            ac.status AS final_v1_status
        FROM vkpi_kol_video_evidence e
        LEFT JOIN vkpi_analysis_cache ac
          ON ac.target_type='video'
         AND ac.target_id=CAST(e.id AS TEXT)
         AND ac.derive_method='video_analysis_final_v1'
         AND ac.status='ready'
        WHERE e.kol_pool_id IN ({placeholders})
        ORDER BY e.kol_pool_id, e.publish_date DESC NULLS LAST, e.id DESC
        """,
        tuple(kol_ids),
    ).fetchall()
    return [_row_dict(row) for row in rows]


def _build_video_entry(base_dir: Path, kol_dir_name: str, kol: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    platform = _text(evidence.get("platform")) or _text(kol.get("platform")) or "unknown"
    content_url = _text(evidence.get("content_url"))
    uid = _video_uid(platform, content_url, evidence.get("id"))
    date_part = _iso_date(evidence.get("publish_date"))
    title_slug = _safe_slug(evidence.get("title"), fallback=f"evidence-{evidence.get('id')}", max_len=54)
    video_dir_name = f"{date_part}_{platform}_{uid}_{title_slug}"
    rel_video_dir = Path(kol_dir_name) / "videos" / video_dir_name
    rel_analysis_dir = rel_video_dir / "analysis"
    rel_frames_dir = rel_video_dir / "frames"
    rel_transcripts_dir = rel_video_dir / "transcripts"
    return {
        "inventory_version": "vkpi_local_inventory_v1",
        "kol_pool_id": int(kol["id"]),
        "kol_handle": _text(kol.get("handle")),
        "kol_display_name": _text(kol.get("display_name")),
        "kol_platform": _text(kol.get("platform")),
        "evidence_id": int(evidence["id"]),
        "platform": platform,
        "content_url": content_url,
        "title": _text(evidence.get("title")),
        "publish_date": _text(evidence.get("publish_date")),
        "duration_seconds": int(evidence.get("duration_seconds") or 0),
        "view_count": int(evidence.get("view_count") or 0),
        "like_count": int(evidence.get("like_count") or 0),
        "comment_count": int(evidence.get("comment_count") or 0),
        "thumbnail_url": _text(evidence.get("thumbnail_url")),
        "final_v1_cache_id": evidence.get("final_v1_cache_id"),
        "final_v1_status": _text(evidence.get("final_v1_status")),
        "local": {
            "kol_dir": str(Path(kol_dir_name)),
            "video_dir": str(rel_video_dir),
            "metadata_json": str(rel_video_dir / "metadata.json"),
            "source_video": str(rel_video_dir / "source.mp4"),
            "analysis_proxy_video": str(rel_video_dir / "analysis_proxy_480p_1fps5.mp4"),
            "thumbnail": str(rel_video_dir / "thumbnail.jpg"),
            "frames_dir": str(rel_frames_dir),
            "transcripts_dir": str(rel_transcripts_dir),
            "analysis_dir": str(rel_analysis_dir),
            "final_v1_json": str(rel_analysis_dir / "final_v1.json"),
            "keyframe_qa_json": str(rel_analysis_dir / "keyframe_qa.json"),
        },
        "download_policy": {
            "source": "prefer_existing_cache_then_ytdlp_decodo_or_apify_media_url",
            "store_full_source": True,
            "store_analysis_proxy": True,
            "dedupe_key": f"{platform}:{uid}:{_short_hash(content_url)}",
        },
    }


def prepare_inventory(args: argparse.Namespace) -> dict[str, Any]:
    out_root = Path(args.output or (ROOT / "exports" / f"kol_local_inventory_{_now_stamp()}")).resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    kols = _select_kols(args.scope, args.limit)
    kol_ids = [int(k["id"]) for k in kols]
    evidence_rows = _select_evidence(kol_ids)
    evidence_by_kol: dict[int, list[dict[str, Any]]] = {}
    for row in evidence_rows:
        evidence_by_kol.setdefault(int(row["kol_pool_id"]), []).append(row)

    inventory_rows: list[dict[str, Any]] = []
    download_rows: list[dict[str, Any]] = []
    total_known_duration = 0
    total_known_duration_count = 0

    for kol in kols:
        platform = _text(kol.get("platform")) or "unknown"
        handle_or_name = _text(kol.get("handle")) or _text(kol.get("display_name")) or f"kol-{kol['id']}"
        kol_dir_name = f"KOL-{int(kol['id']):06d}_{platform}_{_safe_slug(handle_or_name, fallback='unknown')}"
        kol_dir = out_root / kol_dir_name
        videos_dir = kol_dir / "videos"
        videos_dir.mkdir(parents=True, exist_ok=True)

        evs = evidence_by_kol.get(int(kol["id"]), [])
        video_entries = [_build_video_entry(out_root, kol_dir_name, kol, evidence) for evidence in evs]
        if args.materialize_folders:
            for entry in video_entries:
                video_dir = out_root / entry["local"]["video_dir"]
                (video_dir / "analysis").mkdir(parents=True, exist_ok=True)
                (video_dir / "frames").mkdir(parents=True, exist_ok=True)
                (video_dir / "transcripts").mkdir(parents=True, exist_ok=True)
                _write_json(video_dir / "metadata.json", entry)

        profile_payload = {
            "inventory_version": "vkpi_local_inventory_v1",
            "kol_pool_id": int(kol["id"]),
            "platform": platform,
            "handle": _text(kol.get("handle")),
            "display_name": _text(kol.get("display_name")),
            "profile_url": _text(kol.get("profile_url")),
            "channel_id": _text(kol.get("channel_id")),
            "country": _text(kol.get("country")),
            "profile_type": _text(kol.get("profile_type")),
            "recall_status": _text(kol.get("recall_status")),
            "posts_count": int(kol.get("posts_count") or 0),
            "followers_count": int(kol.get("followers_count") or 0),
            "avg_views": int(kol.get("avg_views") or 0),
            "known_evidence_count": len(video_entries),
            "local_dir": kol_dir_name,
        }
        _write_json(kol_dir / "profile.json", profile_payload)
        _write_jsonl(kol_dir / "videos_manifest.jsonl", video_entries)

        inventory_rows.append(profile_payload)
        download_rows.extend(video_entries)
        for entry in video_entries:
            duration = int(entry.get("duration_seconds") or 0)
            if duration > 0:
                total_known_duration += duration
                total_known_duration_count += 1

    _write_jsonl(out_root / "inventory_manifest.jsonl", inventory_rows)
    _write_jsonl(out_root / "download_queue.jsonl", download_rows)
    summary = {
        "inventory_version": "vkpi_local_inventory_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": args.scope,
        "kol_count": len(kols),
        "known_evidence_count": len(download_rows),
        "known_duration_seconds": total_known_duration,
        "known_duration_hours": round(total_known_duration / 3600, 3),
        "known_duration_count": total_known_duration_count,
        "output_dir": str(out_root),
        "materialize_folders": bool(args.materialize_folders),
        "download_started": False,
        "provider_calls": False,
        "database_writes": False,
    }
    _write_json(out_root / "summary.json", summary)
    (out_root / "README.md").write_text(_readme(summary), encoding="utf-8")
    return summary


def _readme(summary: dict[str, Any]) -> str:
    return f"""# V-KPI Local KOL Inventory

Generated: `{summary['created_at']}`

This folder is a deterministic local inventory scaffold. It contains manifests
and expected file paths for later download/cache workers.

## Files

- `summary.json` — aggregate counts for this export.
- `inventory_manifest.jsonl` — one row per KOL.
- `download_queue.jsonl` — one row per known evidence/video URL.
- `KOL-000123_platform_handle/profile.json` — KOL-level metadata.
- `KOL-000123_platform_handle/videos_manifest.jsonl` — that KOL's known videos.

## Naming Contract

KOL folder:

`KOL-{{kol_pool_id:06d}}_{{platform}}_{{handle_slug}}`

Video folder:

`{{YYYYMMDD|undated}}_{{platform}}_{{video_uid}}_{{title_slug}}`

Inside each video folder:

- `metadata.json`
- `source.mp4`
- `analysis_proxy_480p_1fps5.mp4`
- `thumbnail.jpg`
- `frames/`
- `transcripts/`
- `analysis/final_v1.json`
- `analysis/keyframe_qa.json`

## Safety

This run did not call Apify, Decodo, yt-dlp, Gemini, or any paid provider.
It did not write to the database.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare a local KOL inventory folder and download manifest.")
    parser.add_argument("--scope", choices=("inventory", "all"), default="inventory", help="inventory = favorited/local KOLs; all = all vkpi_kol_pool rows.")
    parser.add_argument("--output", default="", help="Output directory. Default: exports/kol_local_inventory_<UTCSTAMP>.")
    parser.add_argument("--limit", type=int, default=0, help="Limit KOL count for a dry run.")
    parser.add_argument("--materialize-folders", action="store_true", help="Create per-video folders and metadata.json files.")
    args = parser.parse_args()
    summary = prepare_inventory(args)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
