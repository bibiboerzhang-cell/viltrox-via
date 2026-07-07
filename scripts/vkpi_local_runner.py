#!/usr/bin/env python3
"""vkpi_local_runner.py — V-KPI 本地算力 Worker 客户端(CLI MVP)

在员工电脑上运行,把服务器下放的四类安全任务(video_precheck / metadata_extract /
download_frames / comment_clean)领回本地跑,不占用服务器算力。全程只拿短期任务
token,永不持有长期 API key;结果只交回 staging 由服务端校验后落库。

契约对齐 backend/app/api/routers/vkpi_local_workers.py 五端点。仅用标准库
(urllib) + 可选外部二进制(yt-dlp / ffmpeg),不引第三方 pip 依赖。

用法:
    # 登录取 token(密码走 env,不落命令行历史):
    export VKPI_RUNNER_PASSWORD='...'
    python scripts/vkpi_local_runner.py \
        --base http://127.0.0.1:8102 --email admin@viltrox.com \
        --device-name "$(hostname)" --once

    # 或直接给 token(跳过登录):
    export VKPI_RUNNER_TOKEN='...'
    python scripts/vkpi_local_runner.py --base https://... --device-name mac-01

    --once      领一条跑完即退出(冒烟用);缺省则常驻轮询
    --poll-interval  无任务时的轮询秒数(默认 20)
    --task-types     只领这些类型(逗号分隔);缺省领全部安全类型
    --staging-dir    本地产物暂存目录(默认系统临时目录下 vkpi_runner/)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from typing import Any

SAFE_TASK_TYPES = ("video_precheck", "metadata_extract", "download_frames", "comment_clean")


# ── HTTP 薄封装(仅标准库) ────────────────────────────────────────────────────


class ApiError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:300]}")
        self.status = status
        self.body = body


def _request(base: str, path: str, *, method: str = "POST", token: str = "", payload: dict | None = None) -> dict:
    url = base.rstrip("/") + path
    data = json.dumps(payload or {}).encode("utf-8") if payload is not None else None
    headers = {"content-type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    # 本地栈 CSRF 对 cookie 生效;Bearer 头绕过,补 Origin 兜底同源校验。
    headers["Origin"] = base.rstrip("/")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise ApiError(exc.code, exc.read().decode("utf-8", errors="replace")) from exc
    return json.loads(raw) if raw else {}


def login(base: str, email: str, password: str) -> str:
    """登录换 Bearer token(绝不把 token 打印到日志)。"""
    out = _request(base, "/api/auth/login", payload={"email": email.strip().lower(), "password": password})
    token = str(out.get("token") or "")
    if not token:
        raise RuntimeError(f"login returned no token: {json.dumps(out)[:200]}")
    return token


# ── 本地任务执行器(四类安全任务) ────────────────────────────────────────────


def _sha256_file(path: str) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def _have(binary: str) -> bool:
    return shutil.which(binary) is not None


def _run(cmd: list[str], timeout: int = 300) -> tuple[int, str, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def _guess_lang(text: str) -> str:
    """极轻量语言启发(仅本地预筛用,非权威):CJK 段判 zh/ja/ko,否则 en/und。"""
    has_han = any("一" <= c <= "鿿" for c in text)
    has_kana = any("぀" <= c <= "ヿ" for c in text)
    has_hangul = any("가" <= c <= "힣" for c in text)
    if has_hangul:
        return "ko"
    if has_kana:
        return "ja"
    if has_han:
        return "zh"
    if any(c.isalpha() for c in text):
        return "en"
    return "und"


def exec_comment_clean(payload: dict, staging_dir: str) -> tuple[dict, list[dict]]:
    """纯本地:评论清洗——去空白/去重/语言预判。无网络无外部依赖,MVP 首选验证任务。

    输出对齐服务端 validation._problems_comment_clean:result.comments 为对象数组,
    每项带 text(非空)+ lang(语言字段)。
    """
    comments = payload.get("comments") or payload.get("items") or []
    if not isinstance(comments, list):
        comments = []
    seen: set[str] = set()
    cleaned: list[dict] = []
    for raw in comments:
        text = (raw.get("text") if isinstance(raw, dict) else str(raw)) or ""
        text = " ".join(str(text).split()).strip()
        if text and text.lower() not in seen:
            seen.add(text.lower())
            lang = (raw.get("lang") or raw.get("language")) if isinstance(raw, dict) else None
            cleaned.append({"text": text, "lang": str(lang) if lang else _guess_lang(text)})
    result = {
        "task": "comment_clean",
        "url": str(payload.get("url") or ""),
        "input_count": len(comments),
        "cleaned_count": len(cleaned),
        "duplicates_removed": len(comments) - len(cleaned),
        "comments": cleaned[:500],
    }
    return result, []


def exec_metadata_extract(payload: dict, staging_dir: str) -> tuple[dict, list[dict]]:
    """yt-dlp --dump-single-json 抽视频元数据(标题/时长/播放量等),不下载视频体。"""
    url = str(payload.get("url") or "").strip()
    if not url:
        return {"task": "metadata_extract", "error": "payload.url missing"}, []
    if not _have("yt-dlp"):
        return {"task": "metadata_extract", "url": url, "error": "yt-dlp not installed", "skipped": True}, []
    code, out, err = _run(["yt-dlp", "--dump-single-json", "--skip-download", url], timeout=120)
    if code != 0:
        return {"task": "metadata_extract", "url": url, "error": (err or "yt-dlp failed")[:300]}, []
    meta = json.loads(out)
    result = {
        "task": "metadata_extract",
        "url": url,  # 服务端要求回显 leased job url
        "title": meta.get("title"),
        "duration_seconds": meta.get("duration"),  # 服务端字段名 duration_seconds
        "duration": meta.get("duration"),
        "view_count": meta.get("view_count") or 0,
        "like_count": meta.get("like_count"),
        "comment_count": meta.get("comment_count"),
        "uploader": meta.get("uploader"),
        "upload_date": meta.get("upload_date"),
    }
    return result, []


def exec_video_precheck(payload: dict, staging_dir: str) -> tuple[dict, list[dict]]:
    """轻量可用性探测:视频是否可访问 + 基础属性(比 metadata 更省,不拉完整 json)。"""
    url = str(payload.get("url") or "").strip()
    if not url:
        return {"task": "video_precheck", "error": "payload.url missing"}, []
    if not _have("yt-dlp"):
        return {"task": "video_precheck", "url": url, "error": "yt-dlp not installed", "skipped": True}, []
    code, out, err = _run(["yt-dlp", "--print", "%(id)s|%(duration)s|%(availability)s", "--skip-download", url], timeout=60)
    available = code == 0
    parts = (out.strip().split("|") if available else [])
    # 对齐服务端 _problems_video_precheck:需 http_status(0..999)+ playable(bool)+ reason。
    return {
        "task": "video_precheck",
        "url": url,
        "http_status": 200 if available else 404,
        "playable": available,
        "reason": "yt-dlp probe ok" if available else (err or "unavailable")[:200],
        "video_id": parts[0] if len(parts) > 0 else None,
        "duration": parts[1] if len(parts) > 1 else None,
    }, []


def exec_download_frames(payload: dict, staging_dir: str) -> tuple[dict, list[dict]]:
    """yt-dlp 下载 + ffmpeg 抽帧;每帧计 sha256 报 files_meta(体本身留本地暂存)。"""
    url = str(payload.get("url") or "").strip()
    fps = payload.get("fps") or 0.2  # 默认每 5 秒一帧
    if not url:
        return {"task": "download_frames", "error": "payload.url missing"}, []
    if not (_have("yt-dlp") and _have("ffmpeg")):
        missing = [b for b in ("yt-dlp", "ffmpeg") if not _have(b)]
        return {"task": "download_frames", "url": url, "error": f"missing: {missing}", "skipped": True}, []
    work = tempfile.mkdtemp(prefix="frames_", dir=staging_dir)
    video_path = os.path.join(work, "video.mp4")
    code, _out, err = _run(["yt-dlp", "-f", "worst[ext=mp4]/worst", "-o", video_path, url], timeout=300)
    if code != 0 or not os.path.exists(video_path):
        return {"task": "download_frames", "url": url, "error": (err or "download failed")[:300]}, []
    code, _o, err = _run(["ffmpeg", "-i", video_path, "-vf", f"fps={fps}", os.path.join(work, "frame_%04d.jpg")], timeout=180)
    frames = sorted(f for f in os.listdir(work) if f.startswith("frame_"))
    files_meta = []
    for name in frames:
        sha, size = _sha256_file(os.path.join(work, name))
        files_meta.append({"name": name, "sha256": sha, "bytes": size})
    return {
        "task": "download_frames",
        "url": url,
        "frame_count": len(frames),
        "staging_path": work,
    }, files_meta


EXECUTORS = {
    "comment_clean": exec_comment_clean,
    "metadata_extract": exec_metadata_extract,
    "video_precheck": exec_video_precheck,
    "download_frames": exec_download_frames,
}


# ── 主循环 ────────────────────────────────────────────────────────────────────


def _capabilities() -> dict:
    return {
        "runner": "cli-mvp",
        "python": platform.python_version(),
        "os": platform.platform(),
        "has_yt_dlp": _have("yt-dlp"),
        "has_ffmpeg": _have("ffmpeg"),
    }


def register(base: str, token: str, device_name: str) -> str:
    out = _request(base, "/api/admin/vkpi/local-workers/devices/register", token=token, payload={
        "device_name": device_name,
        "platform": platform.system().lower(),
        "capabilities": _capabilities(),
    })
    device_id = str(out.get("device_id") or "")
    if not device_id:
        raise RuntimeError(f"register returned no device_id: {json.dumps(out)[:200]}")
    print(f"[runner] registered device_id={device_id} reused={out.get('reused')} "
          f"safe_types={out.get('safe_task_types')} dev_insecure={out.get('dev_insecure')}")
    return device_id


def heartbeat(base: str, token: str, device_id: str, current_task: str | None = None) -> None:
    try:
        _request(base, f"/api/admin/vkpi/local-workers/devices/{device_id}/heartbeat", token=token, payload={
            "current_task": current_task,
            "stats": {"caps": _capabilities()},
        })
    except ApiError as exc:
        print(f"[runner] heartbeat failed: {exc}", file=sys.stderr)


def lease_and_run(base: str, token: str, device_id: str, task_types: list[str], staging_dir: str) -> bool:
    """领一条活并跑完提交;领到并处理返回 True,无活返回 False。"""
    lease = _request(base, "/api/admin/vkpi/local-workers/lease", token=token, payload={
        "device_id": device_id,
        "task_types": task_types,
    })
    if lease.get("status") == "no_task":
        return False
    lease_id = lease["lease_id"]
    task_type = lease["task_type"]
    payload = lease.get("payload") or {}
    task_token = lease["task_token"]
    print(f"[runner] leased lease_id={lease_id} task_type={task_type} job_id={lease.get('job_id')}")
    heartbeat(base, token, device_id, current_task=task_type)

    executor = EXECUTORS.get(task_type)
    if executor is None:
        print(f"[runner] no executor for {task_type}, skipping", file=sys.stderr)
        return True
    try:
        result, files_meta = executor(payload, staging_dir)
    except Exception as exc:  # noqa: BLE001 — 执行失败诚实上报,不吞
        result, files_meta = {"task": task_type, "error": f"executor crashed: {str(exc)[:300]}"}, []

    submit = _request(base, f"/api/admin/vkpi/local-workers/lease/{lease_id}/submit", token=token, payload={
        "task_token": task_token,
        "result": result,
        "files_meta": files_meta,
    })
    print(f"[runner] submitted lease_id={lease_id} accepted={submit.get('accepted')} "
          f"validated={submit.get('validated')} notes={submit.get('notes')}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="V-KPI 本地算力 Worker CLI runner")
    ap.add_argument("--base", default=os.environ.get("VKPI_RUNNER_BASE", "http://127.0.0.1:8102"))
    ap.add_argument("--email", default=os.environ.get("VKPI_RUNNER_EMAIL", ""))
    ap.add_argument("--device-name", default=os.environ.get("VKPI_RUNNER_DEVICE", platform.node() or "cli-runner"))
    ap.add_argument("--task-types", default="")
    ap.add_argument("--poll-interval", type=int, default=20)
    ap.add_argument("--once", action="store_true", help="领一条跑完即退出(冒烟)")
    ap.add_argument("--staging-dir", default=os.path.join(tempfile.gettempdir(), "vkpi_runner"))
    args = ap.parse_args()

    os.makedirs(args.staging_dir, exist_ok=True)
    task_types = [t.strip() for t in args.task_types.split(",") if t.strip()] or list(SAFE_TASK_TYPES)
    bad = [t for t in task_types if t not in SAFE_TASK_TYPES]
    if bad:
        print(f"[runner] task types not in safe whitelist: {bad}", file=sys.stderr)
        return 2

    token = os.environ.get("VKPI_RUNNER_TOKEN", "").strip()
    if not token:
        if not args.email:
            print("[runner] need --email + VKPI_RUNNER_PASSWORD, or VKPI_RUNNER_TOKEN", file=sys.stderr)
            return 2
        password = os.environ.get("VKPI_RUNNER_PASSWORD", "")
        if not password:
            print("[runner] set VKPI_RUNNER_PASSWORD env", file=sys.stderr)
            return 2
        token = login(args.base, args.email, password)
        print("[runner] logged in OK")

    device_id = register(args.base, token, args.device_name)

    if args.once:
        did = lease_and_run(args.base, token, device_id, task_types, args.staging_dir)
        if not did:
            print("[runner] no task available")
        return 0

    print(f"[runner] polling every {args.poll_interval}s for {task_types} (Ctrl-C to stop)")
    while True:
        try:
            heartbeat(args.base, token, device_id)
            worked = lease_and_run(args.base, token, device_id, task_types, args.staging_dir)
            time.sleep(1 if worked else args.poll_interval)
        except KeyboardInterrupt:
            print("\n[runner] stopped")
            return 0
        except ApiError as exc:
            print(f"[runner] api error (will retry): {exc}", file=sys.stderr)
            time.sleep(args.poll_interval)


if __name__ == "__main__":
    sys.exit(main())
