#!/usr/bin/env python3
"""V-KPI 本地算力 Worker(Mac CLI)— 里程碑 W3。

用法:
    python tools/local_worker/worker.py --server http://127.0.0.1:8102 --token <staff_bearer> register --name my-mac
    python tools/local_worker/worker.py --server http://127.0.0.1:8102 --token <staff_bearer> run
    python tools/local_worker/worker.py --server http://127.0.0.1:8102 --token <staff_bearer> status

安全模型(本 MVP 的存在意义,红线):
- 本 worker 永不持有任何长期 API key(LLM/Apify/R2 等 key 全部留在服务端)。
- 每个任务只凭服务端签发的「短期任务 token」提交结果;token 过期即作废,
  worker 只把它当不透明字符串,不知道也不需要签名密钥。
- 本 worker 不直连数据库、不写任何业务表;所有结果先落服务端 lease/staging 表,
  经服务端校验(hash/结构/URL 匹配)后才由校验桥落库。
- --token 传的是员工登录 bearer(访问 /api/admin/* 用),不是任何数据源 key;
  它不会被写入磁盘。

只支持四类安全任务(与服务端契约一致,禁止扩类):
    video_precheck   — HEAD + yt-dlp --simulate 判断视频可达性
    metadata_extract — yt-dlp --dump-json 提取元数据(不下载)
    download_frames  — yt-dlp 下载 + ffmpeg 抽帧到本地工作目录,算 sha256
    comment_clean    — 对 payload 里的评论数组做清洗 + 语言词表标注

外部依赖:标准库 + requests;yt-dlp / ffmpeg 走系统命令并在启动时自检。
故意不 import 服务端任何模块(避免把服务端配置依赖拖进本地环境)。
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import platform as _platform
import re
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:  # pragma: no cover — 人话报错
    print("缺少 requests 库:请先执行  pip install requests  (仓库 .venv 已内置)")
    sys.exit(2)

# ──────────────────────────────────────────────
# 常量与本地目录
# ──────────────────────────────────────────────
WORKER_VERSION = "0.1.0"
API_PREFIX = "/api/admin/vkpi/local-workers"
SAFE_TASK_TYPES = ("video_precheck", "metadata_extract", "download_frames", "comment_clean")

WORKER_HOME = Path(os.environ.get("VKPI_WORKER_HOME", "~/.vkpi_worker")).expanduser()
DEVICE_FILE = WORKER_HOME / "device.json"
PENDING_DIR = WORKER_HOME / "pending"   # submit 失败暂存,下轮重试
WORK_ROOT = WORKER_HOME / "work"        # download_frames 产物 ~/.vkpi_worker/work/<lease_id>/

DEFAULT_POLL_INTERVAL_SEC = 10
HTTP_TIMEOUT_SEC = 30
YTDLP_SIMULATE_TIMEOUT_SEC = 180
YTDLP_METADATA_TIMEOUT_SEC = 240
YTDLP_DOWNLOAD_TIMEOUT_SEC = 900
FFMPEG_TIMEOUT_SEC = 300


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(state: str, msg: str) -> None:
    """状态行:[HH:MM:SS] [空闲/运行中/完成/失败] 消息。"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [{state}] {msg}", flush=True)


def _ensure_dirs() -> None:
    for d in (WORKER_HOME, PENDING_DIR, WORK_ROOT):
        d.mkdir(parents=True, exist_ok=True)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _run_cmd(cmd: list[str], timeout: int) -> tuple[int, str, str]:
    """跑系统命令,返回 (rc, stdout, stderr);超时 rc=-1。不抛异常。"""
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return (
            int(proc.returncode or 0),
            proc.stdout.decode(errors="ignore"),
            proc.stderr.decode(errors="ignore"),
        )
    except subprocess.TimeoutExpired:
        return -1, "", f"命令超时(>{timeout}s): {' '.join(cmd[:3])}..."
    except FileNotFoundError as exc:
        return -2, "", f"命令不存在: {exc}"
    except Exception as exc:  # noqa: BLE001
        return -3, "", f"命令异常: {exc}"


# ──────────────────────────────────────────────
# 启动自检(人话)
# ──────────────────────────────────────────────
def self_check(verbose: bool = True) -> dict:
    """检查 yt-dlp / ffmpeg / ffprobe 是否可用,返回工具路径表。"""
    tools = {
        "yt-dlp": shutil.which("yt-dlp"),
        "ffmpeg": shutil.which("ffmpeg"),
        "ffprobe": shutil.which("ffprobe"),
    }
    if verbose:
        if not tools["yt-dlp"]:
            _log("失败", "自检:未找到 yt-dlp —— 视频类任务不可用。安装:brew install yt-dlp(或 pipx install yt-dlp)")
        if not tools["ffmpeg"]:
            _log("失败", "自检:未找到 ffmpeg —— 抽帧任务(download_frames)不可用。安装:brew install ffmpeg")
        found = [k for k, v in tools.items() if v]
        if found:
            _log("空闲", f"自检通过工具: {', '.join(found)}")
    return tools


def capable_task_types(tools: dict) -> list[str]:
    """按本机工具裁剪可领取的任务类型(video_precheck 无 yt-dlp 时降级为纯 HEAD)。"""
    types = ["video_precheck", "comment_clean"]
    if tools.get("yt-dlp"):
        types.append("metadata_extract")
        if tools.get("ffmpeg"):
            types.append("download_frames")
    return [t for t in SAFE_TASK_TYPES if t in types]


# ──────────────────────────────────────────────
# 服务端客户端(契约固定,勿改路径)
# ──────────────────────────────────────────────
class ServerError(RuntimeError):
    pass


class ServerClient:
    def __init__(self, server: str, token: str, timeout: int = HTTP_TIMEOUT_SEC):
        self.base = server.rstrip("/") + API_PREFIX
        self.timeout = timeout
        self.sess = requests.Session()
        self.sess.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "X-Requested-With": "XMLHttpRequest",
                "User-Agent": f"vkpi-local-worker/{WORKER_VERSION}",
            }
        )

    def _parse(self, resp: requests.Response) -> dict:
        if resp.status_code >= 400:
            raise ServerError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            return resp.json()
        except ValueError as exc:
            raise ServerError(f"服务端返回非 JSON: {resp.text[:200]}") from exc

    def _post(self, path: str, payload: dict) -> dict:
        return self._parse(self.sess.post(self.base + path, json=payload, timeout=self.timeout))

    def _get(self, path: str) -> dict | list:
        return self._parse(self.sess.get(self.base + path, timeout=self.timeout))

    # 契约端点 ------------------------------------------------------------
    def register(self, device_name: str, capabilities: dict) -> dict:
        return self._post(
            "/devices/register",
            {"device_name": device_name, "platform": _platform.platform(), "capabilities": capabilities},
        )

    def heartbeat(self, device_id: str, current_task: dict | None, stats: dict) -> dict:
        return self._post(f"/devices/{device_id}/heartbeat", {"current_task": current_task, "stats": stats})

    def lease(self, device_id: str, task_types: list[str]) -> dict:
        return self._post("/lease", {"device_id": device_id, "task_types": task_types})

    def submit(self, lease_id, task_token: str, result: dict, files_meta: list[dict]) -> dict:
        return self._post(
            f"/lease/{lease_id}/submit",
            {"task_token": task_token, "result": result, "files_meta": files_meta},
        )

    def devices(self):
        return self._get("/devices")


# ──────────────────────────────────────────────
# 本地设备档案 + 断点暂存
# ──────────────────────────────────────────────
def _load_device() -> dict | None:
    if DEVICE_FILE.exists():
        try:
            return json.loads(DEVICE_FILE.read_text())
        except Exception:  # noqa: BLE001
            return None
    return None


def _save_device(info: dict) -> None:
    _ensure_dirs()
    DEVICE_FILE.write_text(json.dumps(info, ensure_ascii=False, indent=2))


def _save_pending(lease_id, task_token: str, result: dict, files_meta: list[dict], expires_at) -> None:
    """submit 失败(断网等)时暂存,下轮循环重试;token 本身是短期的,过期自动放弃。"""
    _ensure_dirs()
    path = PENDING_DIR / f"{lease_id}.json"
    path.write_text(
        json.dumps(
            {
                "lease_id": lease_id,
                "task_token": task_token,
                "result": result,
                "files_meta": files_meta,
                "expires_at": expires_at,
                "saved_at": _now_iso(),
            },
            ensure_ascii=False,
        )
    )


def _retry_pending(client: ServerClient) -> None:
    if not PENDING_DIR.exists():
        return
    for path in sorted(PENDING_DIR.glob("*.json")):
        try:
            item = json.loads(path.read_text())
        except Exception:  # noqa: BLE001
            path.unlink(missing_ok=True)
            continue
        try:
            resp = client.submit(item["lease_id"], item["task_token"], item["result"], item["files_meta"])
            _log("完成", f"暂存结果补交成功 lease={item['lease_id']} accepted={resp.get('accepted')}")
            path.unlink(missing_ok=True)
        except ServerError as exc:
            # 4xx = token 过期/lease 已终态,放弃;其他保留下轮再试
            msg = str(exc)
            if msg.startswith("HTTP 4"):
                _log("失败", f"暂存结果被拒(token 过期或 lease 终态),放弃 lease={item['lease_id']}: {msg[:120]}")
                path.unlink(missing_ok=True)
            else:
                _log("失败", f"暂存结果补交仍失败,下轮重试 lease={item['lease_id']}: {msg[:120]}")
        except requests.RequestException as exc:
            _log("失败", f"暂存结果补交网络失败,下轮重试 lease={item['lease_id']}: {str(exc)[:120]}")


# ──────────────────────────────────────────────
# 四类安全任务执行体(独立实现,不 import 服务端)
# ──────────────────────────────────────────────
def _payload_url(payload: dict) -> str:
    for key in ("url", "video_url", "webpage_url", "watch_url"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def task_video_precheck(payload: dict, ctx: dict) -> tuple[dict, list[dict]]:
    """可达性预检:HTTP HEAD + yt-dlp --simulate(思路同 services/scraping/ytdlp.py,独立实现)。"""
    url = _payload_url(payload)
    if not url:
        return {"ok": False, "error_code": "bad_payload", "reason": "payload 缺少 url"}, []
    out: dict = {
        "ok": False,
        "url": url,
        "http_status": None,
        "http_error": None,
        "ytdlp_rc": None,
        "ytdlp_error": None,
        "reachable": False,
        "method": "head+ytdlp_simulate",
        "checked_at": _now_iso(),
    }
    # 1) HTTP HEAD(轻量粗判)
    try:
        proxies = {"http": ctx["proxy"], "https": ctx["proxy"]} if ctx.get("proxy") else None
        resp = requests.head(url, timeout=15, allow_redirects=True, proxies=proxies)
        out["http_status"] = resp.status_code
    except requests.RequestException as exc:
        out["http_error"] = str(exc)[:200]
    # 2) yt-dlp --simulate(权威判定:能解析出可下载格式即可达)
    ytdlp = ctx["tools"].get("yt-dlp")
    if ytdlp:
        cmd = [ytdlp, "--simulate", "--no-playlist", "--quiet"]
        if ctx.get("proxy"):
            cmd += ["--proxy", ctx["proxy"]]
        cmd.append(url)
        rc, _stdout, stderr = _run_cmd(cmd, timeout=YTDLP_SIMULATE_TIMEOUT_SEC)
        out["ytdlp_rc"] = rc
        if rc != 0:
            out["ytdlp_error"] = stderr[-300:]
    else:
        out["method"] = "head_only"
        out["ytdlp_error"] = "yt-dlp 未安装,降级纯 HEAD 判定"
    if out["ytdlp_rc"] is not None:
        out["reachable"] = out["ytdlp_rc"] == 0
    else:
        out["reachable"] = 200 <= (out["http_status"] or 0) < 400
    out["ok"] = True  # 预检本身完成;可达与否看 reachable
    if not out["reachable"]:
        out["error_code"] = "unreachable"
    return out, []


_META_WHITELIST = (
    "id", "title", "description", "duration", "view_count", "like_count",
    "comment_count", "channel", "channel_id", "channel_url", "uploader",
    "uploader_id", "upload_date", "webpage_url", "extractor", "thumbnail",
    "width", "height", "fps", "language", "tags",
)


def task_metadata_extract(payload: dict, ctx: dict) -> tuple[dict, list[dict]]:
    """yt-dlp --dump-json 提取元数据(不下载视频)。"""
    url = _payload_url(payload)
    if not url:
        return {"ok": False, "error_code": "bad_payload", "reason": "payload 缺少 url"}, []
    ytdlp = ctx["tools"].get("yt-dlp")
    if not ytdlp:
        return {"ok": False, "error_code": "tool_missing", "reason": "yt-dlp 未安装"}, []
    cmd = [ytdlp, "--dump-json", "--skip-download", "--no-playlist", "--quiet"]
    if ctx.get("proxy"):
        cmd += ["--proxy", ctx["proxy"]]
    cmd.append(url)
    rc, stdout, stderr = _run_cmd(cmd, timeout=YTDLP_METADATA_TIMEOUT_SEC)
    if rc != 0 or not stdout.strip():
        code = "timeout" if rc == -1 else "ytdlp_failed"
        return {"ok": False, "error_code": code, "url": url, "reason": stderr[-300:]}, []
    try:
        raw = json.loads(stdout.strip().splitlines()[0])
    except ValueError:
        return {"ok": False, "error_code": "ytdlp_failed", "url": url, "reason": "dump-json 输出无法解析"}, []
    meta = {k: raw.get(k) for k in _META_WHITELIST if k in raw}
    if isinstance(meta.get("description"), str):
        meta["description"] = meta["description"][:2000]
    if isinstance(meta.get("tags"), list):
        meta["tags"] = meta["tags"][:30]
    return {"ok": True, "url": url, "metadata": meta, "extracted_at": _now_iso()}, []


def task_download_frames(payload: dict, ctx: dict) -> tuple[dict, list[dict]]:
    """yt-dlp 下载 + ffmpeg 抽帧到 ~/.vkpi_worker/work/<lease_id>/,逐文件算 sha256。

    抽帧策略参考 services/media/frames.py(前段 1 帧/5 秒),独立实现。
    """
    url = _payload_url(payload)
    if not url:
        return {"ok": False, "error_code": "bad_payload", "reason": "payload 缺少 url"}, []
    ytdlp = ctx["tools"].get("yt-dlp")
    ffmpeg = ctx["tools"].get("ffmpeg")
    if not ytdlp or not ffmpeg:
        return {"ok": False, "error_code": "tool_missing", "reason": "需要 yt-dlp + ffmpeg(brew install yt-dlp ffmpeg)"}, []

    workdir = WORK_ROOT / str(ctx["lease_id"])
    workdir.mkdir(parents=True, exist_ok=True)
    video_path = workdir / "video.mp4"

    # 1) 下载(720p 封顶,和服务端管线同档)
    dl_cmd = [
        ytdlp,
        "-f", "best[ext=mp4][height<=720]/18/best[height<=720]/best",
        "--merge-output-format", "mp4",
        "-o", str(video_path),
        "--no-playlist",
        "--quiet",
    ]
    if ctx.get("proxy"):
        dl_cmd += ["--proxy", ctx["proxy"]]
    dl_cmd.append(url)
    rc, _stdout, stderr = _run_cmd(dl_cmd, timeout=int(payload.get("download_timeout_sec") or YTDLP_DOWNLOAD_TIMEOUT_SEC))
    if rc != 0 or not video_path.exists() or video_path.stat().st_size < 1000:
        code = "timeout" if rc == -1 else "ytdlp_failed"
        return {"ok": False, "error_code": code, "url": url, "reason": stderr[-300:]}, []

    # 2) 时长探测(可失败,不阻塞)
    duration = None
    ffprobe = ctx["tools"].get("ffprobe")
    if ffprobe:
        rc_p, stdout_p, _err_p = _run_cmd(
            [ffprobe, "-v", "quiet", "-print_format", "json", "-show_format", str(video_path)],
            timeout=30,
        )
        if rc_p == 0:
            try:
                duration = float(json.loads(stdout_p)["format"]["duration"])
            except Exception:  # noqa: BLE001
                duration = None

    # 3) ffmpeg 抽帧:1 帧/5 秒,960 宽,上限 max_frames
    max_frames = int(payload.get("max_frames") or 12)
    max_frames = max(1, min(max_frames, 24))
    ff_cmd = [
        ffmpeg, "-y", "-i", str(video_path),
        "-vf", "fps=1/5,scale=960:-1",
        "-frames:v", str(max_frames),
        "-q:v", "4",
        str(workdir / "frame_%04d.jpg"),
    ]
    rc_f, _stdout_f, stderr_f = _run_cmd(ff_cmd, timeout=FFMPEG_TIMEOUT_SEC)
    frames = sorted(workdir.glob("frame_*.jpg"))
    if rc_f != 0 and not frames:
        return {"ok": False, "error_code": "ffmpeg_failed", "url": url, "reason": stderr_f[-300:]}, []

    # 4) files_meta:name/sha256/bytes(服务端凭 hash 校验,本地文件不直传业务表)
    files_meta = []
    for path in [video_path, *frames]:
        files_meta.append({"name": path.name, "sha256": _sha256_file(path), "bytes": path.stat().st_size})
    result = {
        "ok": True,
        "url": url,
        "frame_count": len(frames),
        "video_bytes": video_path.stat().st_size,
        "duration_sec": duration,
        "workdir": str(workdir),
        "frame_interval_sec": 5,
        "extracted_at": _now_iso(),
    }
    return result, files_meta


# 语言词表(轻量启发式;CJK/西里尔按字符区间,拉丁按常用功能词命中)
_LANG_WORDLISTS = {
    "en": {"the", "and", "this", "that", "with", "for", "you", "your", "is", "are", "it", "of", "to", "in"},
    "es": {"que", "los", "las", "una", "por", "para", "con", "muy", "este", "esta", "gracias"},
    "de": {"und", "der", "die", "das", "ich", "nicht", "mit", "ist", "ein", "eine", "sehr"},
    "fr": {"les", "des", "est", "pour", "avec", "dans", "une", "cette", "tres", "merci"},
    "pt": {"que", "para", "com", "uma", "muito", "este", "esta", "obrigado", "voce"},
}
_SPAM_PATTERNS = [
    re.compile(r"https?://", re.IGNORECASE),
    re.compile(r"(check\s+(out\s+)?my|sub4sub|follow\s+me|free\s+gift|promo\s*code)", re.IGNORECASE),
    re.compile(r"(telegram|whatsapp|t\.me/)", re.IGNORECASE),
]
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _guess_language(text: str) -> str:
    han = sum(1 for ch in text if "一" <= ch <= "鿿")
    kana = sum(1 for ch in text if "぀" <= ch <= "ヿ")
    hangul = sum(1 for ch in text if "가" <= ch <= "힯")
    cyrillic = sum(1 for ch in text if "Ѐ" <= ch <= "ӿ")
    total = max(1, len(text))
    if kana / total > 0.05:
        return "ja"
    if hangul / total > 0.1:
        return "ko"
    if han / total > 0.15:
        return "zh"
    if cyrillic / total > 0.2:
        return "ru"
    words = set(re.findall(r"[a-zA-Z']+", text.lower()))
    best, best_hits = "unknown", 0
    for lang, wordlist in _LANG_WORDLISTS.items():
        hits = len(words & wordlist)
        if hits > best_hits:
            best, best_hits = lang, hits
    return best if best_hits >= 1 else "unknown"


def task_comment_clean(payload: dict, ctx: dict) -> tuple[dict, list[dict]]:
    """清洗 payload 里的评论数组:去 HTML/空白/重复/垃圾,附语言标注。"""
    del ctx
    comments = payload.get("comments")
    if not isinstance(comments, list):
        return {"ok": False, "error_code": "bad_payload", "reason": "payload 缺少 comments 数组"}, []
    cleaned: list[dict] = []
    seen: set[str] = set()
    dropped_empty = dropped_dupe = dropped_spam = 0
    lang_counts: dict[str, int] = {}
    for idx, item in enumerate(comments):
        if isinstance(item, dict):
            text = str(item.get("text") or item.get("comment") or "")
            cid = item.get("id")
        else:
            text = str(item or "")
            cid = None
        text = _WS_RE.sub(" ", _TAG_RE.sub(" ", html.unescape(text))).strip()
        if not text:
            dropped_empty += 1
            continue
        key = text.lower()
        if key in seen:
            dropped_dupe += 1
            continue
        seen.add(key)
        if any(p.search(text) for p in _SPAM_PATTERNS):
            dropped_spam += 1
            continue
        lang = _guess_language(text)
        lang_counts[lang] = lang_counts.get(lang, 0) + 1
        entry: dict = {"index": idx, "text": text[:2000], "language": lang}
        if cid is not None:
            entry["id"] = cid
        cleaned.append(entry)
    result = {
        "ok": True,
        "cleaned": cleaned,
        "stats": {
            "input": len(comments),
            "kept": len(cleaned),
            "dropped_empty": dropped_empty,
            "dropped_duplicate": dropped_dupe,
            "dropped_spam": dropped_spam,
            "languages": lang_counts,
        },
        "cleaned_at": _now_iso(),
    }
    return result, []


TASK_HANDLERS = {
    "video_precheck": task_video_precheck,
    "metadata_extract": task_metadata_extract,
    "download_frames": task_download_frames,
    "comment_clean": task_comment_clean,
}


def _execute(task_type: str, payload: dict, ctx: dict) -> tuple[dict, list[dict]]:
    """执行入口:异常兜底,永不让单任务炸掉主循环。"""
    handler = TASK_HANDLERS.get(task_type)
    if handler is None:
        return {"ok": False, "error_code": "unsupported_task_type", "task_type": task_type}, []
    try:
        result, files_meta = handler(payload or {}, ctx)
    except Exception as exc:  # noqa: BLE001 — 捕获一切,诚实上报
        result = {
            "ok": False,
            "error_code": "exception",
            "reason": f"{exc}"[:300],
            "traceback_tail": traceback.format_exc()[-500:],
        }
        files_meta = []
    result.setdefault("task_type", task_type)
    result.setdefault("worker_version", WORKER_VERSION)
    return result, files_meta


# ──────────────────────────────────────────────
# 子命令
# ──────────────────────────────────────────────
def cmd_register(args: argparse.Namespace, client: ServerClient) -> int:
    tools = self_check()
    capabilities = {
        "task_types": capable_task_types(tools),
        "tools": {k: bool(v) for k, v in tools.items()},
        "worker_version": WORKER_VERSION,
        "python": sys.version.split()[0],
    }
    name = args.name or f"{os.environ.get('USER', 'mac')}-{_platform.node() or 'local'}"
    try:
        resp = client.register(name, capabilities)
    except (ServerError, requests.RequestException) as exc:
        _log("失败", f"注册失败: {exc}")
        return 1
    device_id = resp.get("device_id")
    if not device_id:
        _log("失败", f"服务端未返回 device_id: {json.dumps(resp, ensure_ascii=False)[:200]}")
        return 1
    _save_device(
        {
            "device_id": device_id,
            "device_name": name,
            "platform": _platform.platform(),
            "server": args.server,
            "capabilities": capabilities,
            "registered_at": _now_iso(),
        }
    )
    _log("完成", f"注册成功 device_id={device_id} 能力={','.join(capabilities['task_types'])}")
    _log("空闲", f"设备档案已写入 {DEVICE_FILE}(不含任何 token)")
    return 0


def cmd_status(args: argparse.Namespace, client: ServerClient) -> int:
    local = _load_device()
    if local:
        print(f"本机设备: {local.get('device_name')} device_id={local.get('device_id')} server={local.get('server')}")
    else:
        print("本机尚未注册(先跑 register 子命令)")
    try:
        devices = client.devices()
    except (ServerError, requests.RequestException) as exc:
        _log("失败", f"拉取设备列表失败: {exc}")
        return 1
    rows = devices if isinstance(devices, list) else devices.get("devices") or devices.get("items") or []
    print(f"服务端设备共 {len(rows)} 台:")
    for dev in rows:
        print(
            "  - {id} | {name} | {plat} | 状态={status} | 最近心跳={seen} | 当前任务={task}".format(
                id=dev.get("device_id", "?"),
                name=dev.get("device_name", "?"),
                plat=str(dev.get("platform", "?"))[:30],
                status=dev.get("status", "?"),
                seen=dev.get("last_seen_at", "-"),
                task=dev.get("current_task") or "-",
            )
        )
    return 0


def cmd_run(args: argparse.Namespace, client: ServerClient) -> int:
    device = _load_device()
    if not device:
        _log("失败", "尚未注册:先执行 register 子命令")
        return 1
    device_id = device["device_id"]
    tools = self_check()
    task_types = [t.strip() for t in (args.task_types or "").split(",") if t.strip()] or capable_task_types(tools)
    illegal = [t for t in task_types if t not in SAFE_TASK_TYPES]
    if illegal:
        _log("失败", f"不在安全任务类型白名单内: {illegal}(仅允许 {', '.join(SAFE_TASK_TYPES)})")
        return 1
    proxy = args.proxy or os.environ.get("YTDLP_PROXY", "")
    if proxy:
        _log("空闲", "已启用代理(来自 --proxy 或 YTDLP_PROXY 环境变量)")
    _log("空闲", f"开始领活循环 device={device_id} 可领类型={','.join(task_types)} 轮询间隔={args.interval}s")

    stats = {"done": 0, "failed": 0, "started_at": _now_iso()}
    loops = 0
    try:
        while True:
            loops += 1
            got_lease = False
            try:
                _retry_pending(client)
                client.heartbeat(device_id, current_task=None, stats=dict(stats))
                lease = client.lease(device_id, task_types)
                if not isinstance(lease, dict) or lease.get("status") == "no_task" or not lease.get("lease_id"):
                    _log("空闲", "无任务")
                else:
                    got_lease = True
                    lease_id = lease["lease_id"]
                    task_type = lease.get("task_type", "?")
                    payload = lease.get("payload") or {}
                    task_token = lease.get("task_token", "")
                    expires_at = lease.get("expires_at")
                    _log("运行中", f"lease={lease_id} job={lease.get('job_id')} type={task_type}")
                    try:
                        client.heartbeat(
                            device_id,
                            current_task={"lease_id": lease_id, "task_type": task_type},
                            stats=dict(stats),
                        )
                    except (ServerError, requests.RequestException):
                        pass  # 心跳失败不阻塞执行
                    ctx = {"lease_id": lease_id, "tools": tools, "proxy": proxy}
                    result, files_meta = _execute(task_type, payload, ctx)
                    ok = bool(result.get("ok"))
                    try:
                        resp = client.submit(lease_id, task_token, result, files_meta)
                        state = "完成" if ok else "失败"
                        if ok:
                            stats["done"] += 1
                        else:
                            stats["failed"] += 1
                        _log(
                            state,
                            "lease={l} type={t} error_code={e} 提交 accepted={a} validated={v} notes={n}".format(
                                l=lease_id,
                                t=task_type,
                                e=result.get("error_code", "-"),
                                a=resp.get("accepted"),
                                v=resp.get("validated"),
                                n=str(resp.get("notes", ""))[:120],
                            ),
                        )
                    except (ServerError, requests.RequestException) as exc:
                        stats["failed"] += 1
                        _save_pending(lease_id, task_token, result, files_meta, expires_at)
                        _log("失败", f"lease={lease_id} error_code=submit_failed 已暂存待重试: {str(exc)[:150]}")
            except KeyboardInterrupt:
                raise
            except (ServerError, requests.RequestException) as exc:
                _log("失败", f"error_code=network_error 本轮跳过,下轮重试: {str(exc)[:150]}")
            except Exception as exc:  # noqa: BLE001
                _log("失败", f"error_code=loop_exception 本轮跳过,下轮重试: {str(exc)[:150]}")

            if args.once and got_lease:
                break
            if args.max_loops and loops >= args.max_loops:
                break
            time.sleep(max(1, int(args.interval)))
    except KeyboardInterrupt:
        _log("空闲", "收到中断,退出前尝试补交暂存结果…")
        try:
            _retry_pending(client)
        except Exception:  # noqa: BLE001
            pass
    _log("空闲", f"循环结束 完成={stats['done']} 失败={stats['failed']}")
    return 0


# ──────────────────────────────────────────────
# 入口
# ──────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vkpi-local-worker",
        description="V-KPI 本地算力 Worker(短期任务 token 模型,本地永不持有长期 API key)",
    )
    parser.add_argument("--server", default=os.environ.get("VKPI_WORKER_SERVER", "http://127.0.0.1:8102"),
                        help="服务端地址,默认 http://127.0.0.1:8102")
    parser.add_argument("--token", default=os.environ.get("VKPI_WORKER_STAFF_TOKEN", ""),
                        help="员工登录 bearer(仅内存持有,不落盘;也可用 VKPI_WORKER_STAFF_TOKEN 环境变量)")
    parser.add_argument("--proxy", default="", help="yt-dlp/HEAD 用代理(缺省读 YTDLP_PROXY 环境变量)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_reg = sub.add_parser("register", help="注册本机为 worker 设备")
    p_reg.add_argument("--name", default="", help="设备名(缺省 用户名-主机名)")

    p_run = sub.add_parser("run", help="领活循环:心跳-领任务-执行-提交")
    p_run.add_argument("--interval", type=int, default=DEFAULT_POLL_INTERVAL_SEC, help="轮询间隔秒,默认 10")
    p_run.add_argument("--once", action="store_true", help="处理完一个任务即退出(冒烟/调试用)")
    p_run.add_argument("--max-loops", type=int, default=0, help="最多轮询 N 轮后退出,0=不限")
    p_run.add_argument("--task-types", default="", help="逗号分隔的任务类型,缺省按本机工具能力自动裁剪")

    sub.add_parser("status", help="查看服务端设备列表与本机注册状态")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.token:
        _log("失败", "缺少 --token(员工登录 bearer)。安全说明:这是访问服务端的身份凭证,不是数据源 key;不会写入磁盘。")
        return 2
    _ensure_dirs()
    client = ServerClient(args.server, args.token)
    if args.command == "register":
        return cmd_register(args, client)
    if args.command == "status":
        return cmd_status(args, client)
    if args.command == "run":
        return cmd_run(args, client)
    return 2


if __name__ == "__main__":
    sys.exit(main())
