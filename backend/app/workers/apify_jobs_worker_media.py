"""媒体解析 / 子进程分析器 / R2 回灌簇,从 apify_jobs_worker.py 整簇 move 出来。

函数体逐字不变 → 行为必然不变;原文件 re-export 兜住所有调用点。
依赖原文件的超时常量(GEMINI_CALL_*/MEDIA_RESOLVE_*)在本模块**底部** import
(避免循环导入;调用点均在函数体内运行期解析)。红线:本簇零 fit 写。
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.core.logging import get_logger
from app.domains.media.cache import cache_local_video_file
from app.domains.kol.url_deep_crawl_helpers import _video_id as _content_url_video_id
from app.workers.apify_jobs_worker_helpers import (
    _json,
    _parse_apify_resolver_stdout,
    _parse_last_json_stdout,
    _platform_from_content_url,
    _url_host,
)


logger = get_logger(__name__)


def _warm_video_to_r2_from_local(
    *,
    job_id: Any,
    platform: str,
    content_url: str,
    direct_video_url: str,
    local_path: str,
) -> None:
    """C3:final_v1 已把 ins/tiktok 视频下到 tmpdir 喂 Gemini,临时目录删除前把同一份字节
    登记进视频缓存(传 R2 + 写 sidecar),否则前端 cached_video_url 永远为空、视频播不出。

    缓存 external_id 必须是 content_url 的子串(pool.py 的 JOIN:content_url LIKE %external_id%)
    → 用 _content_url_video_id 从 URL 解析平台视频 id(tiktok 数字 id / ins shortcode)。
    全程 try/except 兜底:R2 回灌失败绝不影响已完成的分析结果。"""
    try:
        platform_key = str(platform or "").strip().lower()
        if platform_key not in {"instagram", "tiktok"} or not local_path:
            return
        parsed = urlparse(str(content_url or ""))
        video_id = _content_url_video_id(platform_key, (parsed.hostname or "").lower(), parsed.path, parsed.query)
        if not video_id:
            logger.warning("C3 R2 回灌跳过 | job_id=%s reason=no_video_id_from_url url=%s", job_id, str(content_url)[:120])
            return
        result = cache_local_video_file(
            platform_key,
            str(video_id),
            str(local_path),
            source_url=str(direct_video_url or content_url or ""),
        )
        logger.info(
            "C3 R2 回灌 | job_id=%s platform=%s video_id=%s status=%s backend=%s",
            job_id, platform_key, video_id, result.get("status"), result.get("storage_backend"),
        )
    except Exception as exc:
        logger.warning("C3 R2 回灌异常(不阻断分析) | job_id=%s error=%s", job_id, exc)


def _mock_result(job: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "mock": True,
        "note": "placeholder analysis result; no LLM or network call was made",
        "job_id": job["id"],
        "job_type": job.get("job_type"),
        "target_type": payload.get("target_type"),
        "target_id": str(payload.get("target_id")),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _scrape_with_apify_timeout(content_url: str, platform: str) -> dict[str, Any]:
    backend_dir = Path(__file__).resolve().parents[2]
    repo_dir = Path(__file__).resolve().parents[3]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(backend_dir) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    child_code = r"""
import asyncio
import json
import sys

from app.services.scraping import apify as apify_scraper


async def main() -> None:
    result = await apify_scraper.scrape_with_apify(sys.argv[1], sys.argv[2])
    print(json.dumps(result, ensure_ascii=False, default=str), flush=True)


try:
    asyncio.run(main())
except BaseException as exc:
    print(json.dumps({"scraped_ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False), flush=True)
    raise
"""
    try:
        proc = subprocess.run(
            [sys.executable, "-c", child_code, content_url, platform],
            cwd=str(repo_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=MEDIA_RESOLVE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            "apify media resolve timeout | platform=%s source_host=%s timeout_sec=%s",
            platform,
            _url_host(content_url),
            MEDIA_RESOLVE_TIMEOUT_SECONDS,
        )
        return {
            "scraped_ok": False,
            "error": "media_resolve_timeout",
            "_timeout": True,
        }
    parsed = _parse_apify_resolver_stdout(proc.stdout)
    if proc.returncode != 0:
        parsed["_child_exit"] = True
        parsed["error"] = str(parsed.get("error") or proc.stderr or f"apify resolver exit {proc.returncode}")[-1000:]
    return parsed


def _gemini_analyzer_child_code() -> str:
    return r"""
import asyncio
import json
import os
import sys
from contextlib import ExitStack
from unittest.mock import patch

from app.services.ai.analyzers import gemini_video as gemini_video_analyzer


def _truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _apply_worker_overrides(payload):
    stack = ExitStack()
    model_override = str(payload.get("gemini_model") or "").strip()
    skip_subtitles = _truthy(payload.get("skip_subtitles", payload.get("gemini_skip_subtitles", os.environ.get("APIFY_WORKER_GEMINI_SKIP_SUBTITLES"))))
    if skip_subtitles:
        stack.enter_context(patch.object(gemini_video_analyzer, "fetch_youtube_subtitles", lambda *_args, **_kwargs: ""))
    if model_override and getattr(gemini_video_analyzer, "gemini_client", None):
        original_generate = gemini_video_analyzer.gemini_client.models.generate_content

        def _forced_generate_content(*args, **kwargs):
            kwargs["model"] = model_override
            return original_generate(*args, **kwargs)

        stack.enter_context(patch.object(gemini_video_analyzer.gemini_client.models, "generate_content", _forced_generate_content))
    return stack, model_override


async def _run(payload):
    mode = str(payload.get("mode") or "").strip()
    if mode == "local":
        return await gemini_video_analyzer.analyze_local_video_with_gemini(
            str(payload.get("video_path") or ""),
            str(payload.get("title") or ""),
            str(payload.get("creator_handle") or ""),
            schema_version=str(payload.get("schema_version") or "v2"),
            performance_context=payload.get("performance_context"),
            final_v1_models=payload.get("gemini_final_v1_models"),
        )
    if mode == "youtube":
        return await gemini_video_analyzer.analyze_youtube_with_gemini(
            str(payload.get("url") or ""),
            str(payload.get("title") or ""),
            str(payload.get("creator_handle") or ""),
            schema_version=str(payload.get("schema_version") or "legacy"),
            performance_context=payload.get("performance_context"),
            final_v1_models=payload.get("gemini_final_v1_models"),
        )
    return {"analyzed": False, "method": "gemini_worker_child", "error": f"unsupported_gemini_child_mode:{mode}"}


def _stamp_model(raw, model_override):
    if not model_override or not isinstance(raw, dict) or not raw.get("analyzed"):
        return raw
    raw["model"] = model_override
    method = str(raw.get("method") or "")
    if method.startswith("gemini_direct_"):
        raw["method"] = f"gemini_direct_{model_override}"
    elif method.startswith("gemini_fileapi_"):
        raw["method"] = f"gemini_fileapi_{model_override}"
    elif method.startswith("gemini_local_fileapi_"):
        raw["method"] = f"gemini_local_fileapi_{model_override}"
    return raw


def main():
    payload = json.load(sys.stdin)
    stack, model_override = _apply_worker_overrides(payload)
    with stack:
        raw = asyncio.run(_run(payload))
    print(json.dumps({"ok": True, "raw": _stamp_model(raw, model_override)}, ensure_ascii=False, default=str), flush=True)


try:
    main()
except BaseException as exc:
    print(json.dumps({"ok": False, "raw": {"analyzed": False, "method": "gemini_worker_child", "error": f"{type(exc).__name__}: {exc}"}}, ensure_ascii=False, default=str), flush=True)
    raise
"""


def _run_gemini_analyzer_with_timeout(payload: dict[str, Any], *, job_id: Any, target_id: str, platform: str) -> dict[str, Any]:
    backend_dir = Path(__file__).resolve().parents[2]
    repo_dir = Path(__file__).resolve().parents[3]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(backend_dir) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    proc = subprocess.Popen(
        [sys.executable, "-c", _gemini_analyzer_child_code()],
        cwd=str(repo_dir),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        preexec_fn=os.setsid if hasattr(os, "setsid") else None,
    )
    try:
        stdout, stderr = proc.communicate(_json(payload), timeout=GEMINI_CALL_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        hard_killed = False
        try:
            if hasattr(os, "killpg"):
                os.killpg(proc.pid, signal.SIGTERM)
            else:
                proc.terminate()
            try:
                stdout, stderr = proc.communicate(timeout=GEMINI_CALL_TERMINATE_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                hard_killed = True
                if hasattr(os, "killpg"):
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
                stdout, stderr = proc.communicate()
        except ProcessLookupError:
            stdout, stderr = proc.communicate()
        except Exception:
            hard_killed = True
            if hasattr(os, "killpg"):
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
            stdout, stderr = proc.communicate()
        logger.warning(
            "gemini analyzer hard timeout | job_id=%s target_id=%s platform=%s timeout_sec=%s hard_killed=%s stdout_tail=%s stderr_tail=%s",
            job_id,
            target_id,
            platform,
            GEMINI_CALL_TIMEOUT_SECONDS,
            hard_killed,
            str(stdout or "")[-500:],
            str(stderr or "")[-500:],
        )
        return {
            "analyzed": False,
            "method": "gemini_worker_subprocess_timeout",
            "error": "gemini_call_timeout",
            "timeout_seconds": GEMINI_CALL_TIMEOUT_SECONDS,
            "provider_subprocess": {
                "timeout": True,
                "hard_killed": hard_killed,
                "stdout_tail": str(stdout or "")[-500:],
                "stderr_tail": str(stderr or "")[-500:],
            },
        }
    parsed = _parse_last_json_stdout(stdout)
    if not parsed:
        return {
            "analyzed": False,
            "method": "gemini_worker_subprocess",
            "error": f"gemini_child_no_json: {(stderr or stdout or '')[-1000:]}",
            "provider_subprocess": {"returncode": proc.returncode},
        }
    raw = parsed.get("raw") if isinstance(parsed.get("raw"), dict) else {}
    if proc.returncode != 0 and not raw.get("error"):
        raw["error"] = f"gemini_child_exit:{proc.returncode}: {(stderr or stdout or '')[-1000:]}"
    raw["provider_subprocess"] = {
        "returncode": proc.returncode,
        "timeout": False,
        "stderr_tail": str(stderr or "")[-500:],
    }
    return raw


def _resolve_video_media(evidence: dict[str, Any]) -> dict[str, Any]:
    content_url = str(evidence.get("content_url") or "").strip()
    platform = _platform_from_content_url(content_url)
    output = {
        "ok": False,
        "platform": platform,
        "source_url_host": _url_host(content_url),
        "direct_video_url": "",
        "direct_video_url_host": "",
        "reason": "",
        "scraped_ok": False,
    }
    if platform == "unsupported":
        output["reason"] = "unsupported_platform"
        output["status"] = "blocked"
        return output
    if platform == "youtube":
        output["ok"] = True
        output["reason"] = "youtube_direct_url_path"
        output["status"] = "ready"
        return output
    if not os.environ.get("APIFY_TOKEN", "").strip():
        output["reason"] = "apify_not_configured"
        output["status"] = "blocked"
        return output
    scraped = _scrape_with_apify_timeout(content_url, platform)
    if scraped.get("_timeout"):
        output["reason"] = "media_resolve_timeout"
        output["status"] = "failed"
        return output
    if scraped.get("_child_exit"):
        output["reason"] = f"media_resolve_child_exit: {scraped.get('error') or platform}"
        output["status"] = "failed"
        return output
    if scraped.get("_parse_error"):
        output["reason"] = str(scraped.get("error") or "media_resolve_parse_failed")
        output["status"] = "failed"
        return output
    output["scraped_ok"] = bool(scraped.get("scraped_ok"))
    direct_video_url = str(scraped.get("video_url") or "").strip()
    if not direct_video_url:
        # 真因诚实化:旧码空 error 时 fallback 字面量 "media_resolve_failed",真因(反爬剥离可下载
        # URL / 代理被挡)被吞成双重包装。分清「抓成功但无 downloadAddr」与「抓本身空/被挡」。
        detail = str(scraped.get("error") or "").strip()
        if not detail:
            detail = "scraped_no_downloadable_url" if scraped.get("scraped_ok") else "scrape_empty_or_blocked"
        output["reason"] = f"media_resolve_failed:{platform}:{detail}"[:240]
        output["status"] = "failed"
        return output
    output.update(
        {
            "ok": True,
            "direct_video_url": direct_video_url,
            "direct_video_url_host": _url_host(direct_video_url),
            "reason": "media_resolved",
            "status": "ready",
        }
    )
    return output


# 原文件留下的超时常量:放模块底部 import(避免循环导入;均在函数体内运行期解析)。
from app.workers.apify_jobs_worker import (  # noqa: E402
    GEMINI_CALL_TERMINATE_GRACE_SECONDS,
    GEMINI_CALL_TIMEOUT_SECONDS,
    MEDIA_RESOLVE_TIMEOUT_SECONDS,
)
