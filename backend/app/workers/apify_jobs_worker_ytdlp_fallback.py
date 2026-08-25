"""IG/TikTok 媒体兜底:Apify 拿不到可下载 URL 时用 yt-dlp 复核/接管(2026-08-24)。

背景:IG/TT 主链 = R2 缓存 → Apify 抓直链 → HTTP 下载。Apify「抓成功但没
视频 URL」有两种真相——图文/轮播帖(真没视频)与真视频被剥链。yt-dlp
(走 YTDLP_PROXY)复核,只在该失败因上兜底:

- 确认无视频(IG 专属文案 / rc=0 空轮播)→ ``no_video_confirmed``,调用方转
  终态 blocked 并回写 media_kind='image' 让入队闸拦住后来者。
- 有视频轨 → 下载到调用方 tmpdir,返回 ready(``local_path_ready``),
  后续走本地分析 + R2 回灌,与缓存命中同路。
- 其余一律 inconclusive:保留 Apify 原判(可重试),只附脱敏诊断。
  注意「No video formats found」是 yt-dlp 通用错误(真视频被剥链也报它,
  2026-08-24 复审实锤),绝不能当图文定论。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.domains.media.cache_core import MAX_VIDEO_BYTES
from app.services.media.resolution_state import stamp_video_media_resolution
from app.workers.apify_jobs_worker_helpers import _redact_sensitive_text

logger = get_logger("workers.apify_jobs_worker_ytdlp_fallback")

PROBE_TIMEOUT_SECONDS = 75
DOWNLOAD_TIMEOUT_SECONDS = 240
# 无视频「定论」只认 IG 提取器专属文案;通用错误(No video formats found)不算。
_NO_VIDEO_MARKERS = ("There is no video in this post",)


def _base_cmd() -> list[str]:
    cmd = [sys.executable, "-m", "yt_dlp"]
    proxy = str(os.environ.get("YTDLP_PROXY") or "").strip()
    if proxy:
        cmd += ["--proxy", proxy]
    return cmd + ["--no-warnings"]


def _run(cmd: list[str], timeout_seconds: int) -> tuple[int, str, str]:
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout_seconds, check=False
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _probe(url: str) -> dict[str, Any]:
    """返回 {verdict: video|no_video|inconclusive, target_url, playlist_index, diag}。"""
    diag: dict[str, Any] = {"stage": "probe"}
    try:
        rc, stdout, stderr = _run(_base_cmd() + ["-J", url], PROBE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        diag["error"] = "probe_timeout"
        return {"verdict": "inconclusive", "diag": diag}
    except OSError as exc:
        diag["error"] = f"probe_exec:{exc}"[:160]
        return {"verdict": "inconclusive", "diag": diag}
    if any(marker in stderr for marker in _NO_VIDEO_MARKERS):
        diag["marker"] = "no_video"
        return {"verdict": "no_video", "diag": diag}
    if rc != 0:
        diag["error"] = f"probe_rc={rc}:{_redact_sensitive_text(stderr[-200:], limit=200)}"
        return {"verdict": "inconclusive", "diag": diag}
    try:
        meta = json.loads(stdout)
    except ValueError:
        diag["error"] = "probe_no_json"
        return {"verdict": "inconclusive", "diag": diag}
    entries = meta.get("entries") if isinstance(meta.get("entries"), list) else None
    if entries is None:
        if meta.get("duration") or meta.get("formats"):
            return {"verdict": "video", "target_url": url, "playlist_index": None, "diag": diag}
        # rc=0 单条却无轨:罕见暧昧态,宁可不定论也不误盖图文章。
        diag["error"] = "single_entry_no_formats"
        return {"verdict": "inconclusive", "diag": diag}
    for idx, entry in enumerate(entries):
        if isinstance(entry, dict) and (entry.get("duration") or entry.get("formats")):
            diag["carousel_entries"] = len(entries)
            target = str(entry.get("webpage_url") or "").strip() or url
            index = None if target != url else idx + 1
            return {"verdict": "video", "target_url": target, "playlist_index": index, "diag": diag}
    diag["marker"] = "carousel_no_video_entries"
    return {"verdict": "no_video", "diag": diag}


def _download(target_url: str, playlist_index: int | None, output_dir: str) -> dict[str, Any]:
    template = str(Path(output_dir) / "ytdlp_fallback.%(ext)s")
    cmd = _base_cmd() + ["-f", "mp4/best", "--max-filesize", str(MAX_VIDEO_BYTES), "-o", template]
    if playlist_index is not None:
        cmd += ["--playlist-items", str(playlist_index)]
    cmd.append(target_url)
    try:
        rc, _stdout, stderr = _run(cmd, DOWNLOAD_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "download_timeout"}
    except OSError as exc:
        return {"ok": False, "error": f"download_exec:{exc}"[:160]}
    if rc != 0:
        return {"ok": False, "error": f"download_rc={rc}:{_redact_sensitive_text(stderr[-200:], limit=200)}"}
    produced = sorted(Path(output_dir).glob("ytdlp_fallback.*"))
    if not produced:
        # --max-filesize 超限时 yt-dlp rc=0 但跳过写盘;如实归为超大不装成功。
        return {"ok": False, "error": "download_no_file_or_too_large"}
    path = produced[0]
    size = path.stat().st_size
    if size <= 0 or size > MAX_VIDEO_BYTES:
        return {"ok": False, "error": f"download_size_invalid:{size}"}
    return {"ok": True, "path": str(path), "bytes": int(size)}


def ytdlp_fallback_resolve(
    evidence: dict[str, Any], output_dir: str, *, apify_resolved: dict[str, Any]
) -> dict[str, Any]:
    """Apify 直链失败后的第二轮;任何非确定结论都原样保留 Apify 判决。"""
    url = str(evidence.get("content_url") or "").strip()
    platform = str(apify_resolved.get("platform") or "").strip().lower()
    out = stamp_video_media_resolution(apify_resolved)
    if not url:
        return out
    probe = _probe(url)
    out["ytdlp"] = probe.get("diag") or {}
    if probe["verdict"] == "no_video":
        out["no_video_confirmed"] = True
        out["reason"] = f"media_resolve_failed:{platform}:image_post_no_video_confirmed"
        return stamp_video_media_resolution(
            out,
            media_resolved=False,
            downloadable=False,
            confirmed_non_video=True,
        )
    if probe["verdict"] != "video":
        return out
    download = _download(probe.get("target_url") or url, probe.get("playlist_index"), output_dir)
    if not download.get("ok"):
        out["ytdlp"] = {**out["ytdlp"], "stage": "download", "error": download.get("error")}
        return out
    logger.info(
        "ytdlp fallback rescued media | platform=%s bytes=%s", platform, download["bytes"]
    )
    return stamp_video_media_resolution(
        {
            **apify_resolved,
            "ok": True,
            "status": "ready",
            "reason": "media_resolved_ytdlp_fallback",
            "method": "ytdlp_fallback",
            "local_path_ready": True,
            "path": str(download["path"]),
            "bytes": int(download["bytes"]),
            "content_type": "video/mp4",
            "cache_hit": False,
            "provider_calls_performed": True,
            "direct_video_url": "",
            "direct_video_url_host": "",
            "ytdlp": {**(probe.get("diag") or {}), "stage": "download_ok"},
        },
        media_resolved=True,
        downloadable=True,
        confirmed_non_video=False,
    )


def persist_image_post_verdict(conn: Any, evidence: dict[str, Any]) -> None:
    """无视频终态回写 media_kind='image',入队闸(video_analysis_enqueue 识别闸)即拦后来者。

    media_kind 列存在双向 schema 漂移(线上有列/本地可能没有):缺列/任何失败只告警,
    绝不阻断已定的 blocked 终态。只覆盖空值,不改人工/管线已标注的口径。
    """
    evidence_id = int(evidence.get("id") or 0)
    if evidence_id <= 0:
        return
    try:
        with conn.transaction():
            conn.execute(
                "UPDATE vkpi_kol_video_evidence SET media_kind='image' "
                "WHERE id=%s AND (media_kind IS NULL OR media_kind='')",
                (evidence_id,),
            )
    except Exception as exc:
        logger.warning("media_kind image verdict skipped | evidence_id=%s err=%s", evidence_id, exc)
