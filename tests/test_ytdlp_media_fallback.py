"""IG/TT yt-dlp 媒体兜底契约(2026-08-24):
① 图文帖(单图/轮播)确认无视频 → no_video_confirmed 终态口径;
② 真视频被 Apify 剥链 → yt-dlp 接管下载,ready + local_path_ready;
③ yt-dlp 自身失败/超大 → 保留 Apify 原判(可重试),只附诊断;
④ media_kind 回写只覆盖空值、缺列告警不阻断;
⑤ 接线源契约:只在「抓成功没视频 URL」一种失败因兜底;media_kind 图章只盖 yt-dlp 定论。"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from app.workers import apify_jobs_worker_ytdlp_fallback as fb

APIFY_FAIL = {
    "ok": False,
    "status": "failed",
    "platform": "instagram",
    "scraped_ok": True,
    "reason": "media_resolve_failed:instagram:scraped_no_downloadable_url",
    "cache_hit": False,
    "provider_calls_performed": True,
}
EVIDENCE = {"id": 5722, "content_url": "https://www.instagram.com/p/DZUm9lEDWaD/"}


class _Proc:
    def __init__(self, rc: int, stdout: str = "", stderr: str = ""):
        self.returncode = rc
        self.stdout = stdout
        self.stderr = stderr


def test_generic_no_formats_error_is_inconclusive_not_image_verdict(monkeypatch, tmp_path):
    """「No video formats found」是 yt-dlp 通用错误(真视频被剥链同样报)——绝不当图文定论。"""
    monkeypatch.setattr(
        fb.subprocess,
        "run",
        lambda *a, **k: _Proc(1, "", "ERROR: [Instagram] X: No video formats found!"),
    )
    out = fb.ytdlp_fallback_resolve(EVIDENCE, str(tmp_path), apify_resolved=dict(APIFY_FAIL))
    assert "no_video_confirmed" not in out
    assert out["reason"] == APIFY_FAIL["reason"]
    assert out["ok"] is False


def test_empty_entries_carousel_is_no_video_confirmed(monkeypatch, tmp_path):
    """本版 yt-dlp 对全图轮播 = rc=0 + entries=[](图片节点被提取器跳过)→ 定论无视频。"""
    monkeypatch.setattr(
        fb.subprocess,
        "run",
        lambda *a, **k: _Proc(0, '{"entries": []}', ""),
    )
    out = fb.ytdlp_fallback_resolve(EVIDENCE, str(tmp_path), apify_resolved=dict(APIFY_FAIL))
    assert out["no_video_confirmed"] is True
    assert out["reason"] == "media_resolve_failed:instagram:image_post_no_video_confirmed"
    assert out["ok"] is False


def test_single_image_post_marker_is_no_video(monkeypatch, tmp_path):
    monkeypatch.setattr(
        fb.subprocess,
        "run",
        lambda *a, **k: _Proc(1, "", "ERROR: [Instagram] Y: There is no video in this post"),
    )
    out = fb.ytdlp_fallback_resolve(EVIDENCE, str(tmp_path), apify_resolved=dict(APIFY_FAIL))
    assert out["no_video_confirmed"] is True


def test_real_video_rescued_via_download(monkeypatch, tmp_path):
    calls: list[list[str]] = []

    def fake_run(cmd, capture_output, text, timeout, check):
        calls.append(list(cmd))
        if "-J" in cmd:
            return _Proc(0, '{"duration": 31.2, "formats": [{"format_id": "mp4"}]}', "")
        Path(tmp_path, "ytdlp_fallback.mp4").write_bytes(b"x" * 2048)
        return _Proc(0, "", "")

    monkeypatch.setattr(fb.subprocess, "run", fake_run)
    out = fb.ytdlp_fallback_resolve(EVIDENCE, str(tmp_path), apify_resolved=dict(APIFY_FAIL))
    assert out["ok"] is True and out["status"] == "ready"
    assert out["local_path_ready"] is True and out["cache_hit"] is False
    assert out["bytes"] == 2048 and out["path"].endswith("ytdlp_fallback.mp4")
    assert out["provider_calls_performed"] is True
    assert out["method"] == "ytdlp_fallback"
    # 下载命令必须带体积上限,且探测/下载各一次调用。
    assert len(calls) == 2 and "--max-filesize" in calls[1]
    assert calls[0][:3] == [fb.sys.executable, "-m", "yt_dlp"]


def test_carousel_video_entry_targets_that_entry(monkeypatch, tmp_path):
    calls: list[list[str]] = []

    def fake_run(cmd, capture_output, text, timeout, check):
        calls.append(list(cmd))
        if "-J" in cmd:
            return _Proc(0, '{"entries": [{"id": "a"}, {"duration": 9, "webpage_url": ""}]}', "")
        Path(tmp_path, "ytdlp_fallback.mp4").write_bytes(b"x" * 10)
        return _Proc(0, "", "")

    monkeypatch.setattr(fb.subprocess, "run", fake_run)
    out = fb.ytdlp_fallback_resolve(EVIDENCE, str(tmp_path), apify_resolved=dict(APIFY_FAIL))
    assert out["ok"] is True
    dl = calls[1]
    assert "--playlist-items" in dl and dl[dl.index("--playlist-items") + 1] == "2"


def test_ytdlp_failure_keeps_original_apify_verdict(monkeypatch, tmp_path):
    def fake_run(cmd, capture_output, text, timeout, check):
        if "-J" in cmd:
            raise subprocess.TimeoutExpired(cmd, timeout)
        raise AssertionError("download must not run after inconclusive probe")

    monkeypatch.setattr(fb.subprocess, "run", fake_run)
    out = fb.ytdlp_fallback_resolve(EVIDENCE, str(tmp_path), apify_resolved=dict(APIFY_FAIL))
    assert out["ok"] is False
    assert out["reason"] == APIFY_FAIL["reason"]
    assert "no_video_confirmed" not in out
    assert out["ytdlp"]["error"] == "probe_timeout"


def test_oversize_download_keeps_original_verdict(monkeypatch, tmp_path):
    def fake_run(cmd, capture_output, text, timeout, check):
        if "-J" in cmd:
            return _Proc(0, '{"duration": 12, "formats": [{}]}', "")
        return _Proc(0, "", "")  # rc=0 但没写盘 = --max-filesize 跳过

    monkeypatch.setattr(fb.subprocess, "run", fake_run)
    out = fb.ytdlp_fallback_resolve(EVIDENCE, str(tmp_path), apify_resolved=dict(APIFY_FAIL))
    assert out["ok"] is False and out["reason"] == APIFY_FAIL["reason"]
    assert out["ytdlp"]["error"] == "download_no_file_or_too_large"


class _TxConn:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.sql: list[tuple[str, tuple]] = []

    def transaction(self):
        conn = self

        class _Ctx:
            def __enter__(self):
                return conn

            def __exit__(self, *a: Any):
                return False

        return _Ctx()

    def execute(self, sql: str, params: tuple = ()):  # noqa: ANN001
        if self.fail:
            raise RuntimeError("UndefinedColumn: media_kind")
        self.sql.append((sql, params))


def test_persist_image_verdict_writes_only_empty_media_kind():
    conn = _TxConn()
    fb.persist_image_post_verdict(conn, {"id": 5722})
    assert len(conn.sql) == 1
    sql, params = conn.sql[0]
    assert "media_kind='image'" in sql and "media_kind IS NULL OR media_kind=''" in sql
    assert params == (5722,)
    fb.persist_image_post_verdict(conn, {"id": 0})
    assert len(conn.sql) == 1  # 无 id 不写


def test_persist_image_verdict_missing_column_never_raises():
    fb.persist_image_post_verdict(_TxConn(fail=True), {"id": 5722})  # 只告警不炸


def test_wiring_source_contract():
    workers = Path(fb.__file__).resolve().parent
    media = workers.joinpath("apify_jobs_worker_media.py").read_text(encoding="utf-8")
    assert "ytdlp_fallback_resolve" in media
    assert 'reason.endswith("scraped_no_downloadable_url")' in media
    assert "scrape_empty_or_blocked" not in media.split("yt-dlp 兜底")[1].split("return resolved")[0]
    gemini = workers.joinpath("apify_jobs_worker_gemini.py").read_text(encoding="utf-8")
    assert 'resolved.get("cache_hit") or resolved.get("local_path_ready")' in gemini
    # 复审 HIGH 契约:media_kind 图章只盖 yt-dlp 定论,IG 老口径 blocked 不落章。
    assert 'if resolved.get("no_video_confirmed"):\n                        _persist_image_post_verdict(conn, evidence)' in gemini
