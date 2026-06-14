"""Persistent apify_jobs worker with mock analysis and LLM brake controls."""
from __future__ import annotations

import asyncio
import json
import os
import random
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import urlparse

import psycopg
from psycopg.rows import dict_row

from app.core.config import DB_RUNTIME_URL
from app.core.logging import get_logger
from app.db.connection import close_db_runtime_sync, db_connection_sync_scope
from app.domains.costs import budget_guard
from app.domains.kol.account_dossier_extract import upsert_account_dossier_extract
from app.domains.projects import contracts as project_contracts
from app.domains.projects import retrospective_aggregate as project_retrospective
from app.domains.kol.final_v1_extract import upsert_deep_analysis_from_final_v1_cache
from app.domains.kol import profile_discovery as kol_profile_discovery
from app.domains.kol import search_sessions as kol_search_sessions
from app.platform import llm_gateway
from app.services.media.video_download import download_direct_video_url
from app.services.media.video_keyframes import temporary_keyframes
from app.services.ai.analyzers import gemini_video as gemini_video_analyzer


logger = get_logger(__name__)
POLL_SECONDS = float(os.environ.get("APIFY_WORKER_POLL_SECONDS", "2"))
MEDIA_RESOLVE_TIMEOUT_SECONDS = max(10, int(os.environ.get("APIFY_WORKER_MEDIA_RESOLVE_TIMEOUT_SEC", "90")))
GEMINI_CALL_TIMEOUT_SECONDS = max(30, int(os.environ.get("APIFY_WORKER_GEMINI_CALL_TIMEOUT_SEC", "1200")))
GEMINI_CALL_TERMINATE_GRACE_SECONDS = max(1, int(os.environ.get("APIFY_WORKER_GEMINI_CALL_TERMINATE_GRACE_SEC", "5")))
STALE_RUNNING_MINUTES = max(1, int(os.environ.get("APIFY_WORKER_STALE_RUNNING_MINUTES", "10")))
STALE_RECLAIM_SECONDS = STALE_RUNNING_MINUTES * 60
STALE_RECLAIM_POLL_SECONDS = max(30, int(os.environ.get("APIFY_WORKER_STALE_RECLAIM_POLL_SECONDS", "60")))
RUNNING_HEARTBEAT_SECONDS = max(10, int(os.environ.get("APIFY_WORKER_RUNNING_HEARTBEAT_SECONDS", "30")))
MAX_JOB_ATTEMPTS = max(1, int(os.environ.get("APIFY_WORKER_MAX_ATTEMPTS", "2")))
PROVIDER_RETRY_MAX_ATTEMPTS = max(1, int(os.environ.get("APIFY_WORKER_PROVIDER_RETRY_MAX_ATTEMPTS", "5")))
PROVIDER_RETRY_BASE_SECONDS = max(1, int(os.environ.get("APIFY_WORKER_PROVIDER_RETRY_BASE_SECONDS", "60")))
PROVIDER_RETRY_MAX_DELAY_SECONDS = max(
    PROVIDER_RETRY_BASE_SECONDS,
    int(os.environ.get("APIFY_WORKER_PROVIDER_RETRY_MAX_DELAY_SECONDS", "960")),
)
PROVIDER_RETRY_JITTER_RATIO = max(0.0, min(0.5, float(os.environ.get("APIFY_WORKER_PROVIDER_RETRY_JITTER_RATIO", "0.20"))))
PROVIDER_RETRY_ADOPT_WINDOW_MINUTES = max(0, int(os.environ.get("APIFY_WORKER_PROVIDER_RETRY_ADOPT_WINDOW_MINUTES", "1440")))
LLM_BUDGET_SCOPE = os.environ.get("APIFY_WORKER_LLM_BUDGET_SCOPE", "cron:vkpi_analysis_worker")
LLM_CONCURRENCY_LIMIT = max(1, min(2, int(os.environ.get("APIFY_WORKER_LLM_CONCURRENCY", "1"))))
LLM_MAX_OUTPUT_TOKENS = int(os.environ.get("APIFY_WORKER_LLM_MAX_OUTPUT_TOKENS", "1200"))
GEMINI_QPS_LIMIT = max(0.0, float(os.environ.get("APIFY_WORKER_GEMINI_QPS", "0.05")))
GEMINI_MIN_INTERVAL_SECONDS = max(
    0.0,
    float(os.environ.get("APIFY_WORKER_GEMINI_MIN_INTERVAL_SEC", str((1.0 / GEMINI_QPS_LIMIT) if GEMINI_QPS_LIMIT > 0 else 0.0))),
)
LLM_TARGET_TYPES = {"video", "contract"}
GEMINI_VIDEO_V2_DERIVE_METHODS = {
    "gemini_video_v2",
    "gemini_video_v2_pro_single",
    "gemini_video_v2_flash_pro_judge",
    "gemini_video_v2_flash_gpt55_judge",
    "gemini_video_v2_flash_claude_judge",
}
FINAL_V1_KEYFRAME_QA_DERIVE_METHOD = "video_analysis_final_v1_keyframe_qa"
GEMINI_VIDEO_FINAL_DERIVE_METHODS = {"video_analysis_final_v1", FINAL_V1_KEYFRAME_QA_DERIVE_METHOD}
GEMINI_VIDEO_DERIVE_METHODS = {"gemini", *GEMINI_VIDEO_V2_DERIVE_METHODS, *GEMINI_VIDEO_FINAL_DERIVE_METHODS}
WORKER_GEMINI_MODEL = os.environ.get("APIFY_WORKER_GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
FINAL_V1_GEMINI_MODELS = gemini_video_analyzer.final_v1_gemini_models()
FINAL_V1_KEYFRAME_QA_MODEL = os.environ.get("GEMINI_FINAL_V1_QA_MODEL", "gemini-3.1-pro-preview").strip() or "gemini-3.1-pro-preview"
_stop_event = threading.Event()
_gemini_qps_lock = threading.Lock()
_last_gemini_call_started_at = 0.0


def _request_stop(_signum: int, _frame: Any) -> None:
    _stop_event.set()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(raw: Any, default: Any) -> Any:
    if raw in (None, "", b""):
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        parsed = json.loads(str(raw))
    except Exception:
        return default
    return parsed if parsed is not None else default


def _int_or_none(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed


_SENSITIVE_URL_USERINFO_RE = re.compile(r"\b([a-z][a-z0-9+.-]*://)([^/\s@]+@)", re.IGNORECASE)
_SENSITIVE_AUTH_RE = re.compile(r"\b(authorization)\b\s*([:=])\s*(?:bearer\s+)?([^,\s'\"}\]]+)", re.IGNORECASE)
_SENSITIVE_BEARER_RE = re.compile(r"\bbearer\s+[A-Za-z0-9._~+/\-=]+", re.IGNORECASE)
_SENSITIVE_KV_RE = re.compile(
    r"\b("
    r"proxy|token|api[_-]?key|key|secret|password|passwd|access[_-]?token|refresh[_-]?token|client[_-]?secret"
    r")\b\s*([:=])\s*([^,\s'\"}\]]+)",
    re.IGNORECASE,
)


def _redact_sensitive_text(value: Any, *, limit: int = 2000) -> str:
    text = str(value or "")
    text = _SENSITIVE_URL_USERINFO_RE.sub(lambda match: f"{match.group(1)}***@", text)
    text = _SENSITIVE_AUTH_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}***", text)
    text = _SENSITIVE_BEARER_RE.sub("Bearer ***", text)
    text = _SENSITIVE_KV_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}***", text)
    return text[:limit]


def _error_category(message: str) -> str:
    text = str(message or "").lower()
    if any(marker in text for marker in ("429", "resource_exhausted", "rate limit", "quota exceeded")):
        return "provider_pressure"
    if any(
        marker in text
        for marker in (
            "500",
            "502",
            "503",
            "504",
            "5xx",
            "internal server error",
            "server error",
            "unavailable",
            "service unavailable",
            "high demand",
            "temporarily overloaded",
        )
    ):
        return "provider_pressure"
    if "gemini_call_timeout" in text:
        return "timeout"
    if "media_resolve_failed" in text or "media_resolve" in text:
        return "media_resolve"
    if "yt-dlp" in text or "yt_dlp" in text or "direct_video_download_failed" in text or "download_failed" in text:
        return "download"
    if "unsupported" in text or "invalid_video_url" in text or "not_video" in text or "bad url" in text:
        return "permanent"
    if "stale_running_reclaimed" in text:
        return "stale_running"
    return "other"


def _provider_retry_delay_seconds(next_attempt: int) -> int:
    attempt = max(1, int(next_attempt or 1))
    base_delay = min(PROVIDER_RETRY_MAX_DELAY_SECONDS, PROVIDER_RETRY_BASE_SECONDS * (4 ** max(0, attempt - 1)))
    if PROVIDER_RETRY_JITTER_RATIO <= 0:
        return int(base_delay)
    jitter = random.uniform(0, base_delay * PROVIDER_RETRY_JITTER_RATIO)
    return int(min(PROVIDER_RETRY_MAX_DELAY_SECONDS, round(base_delay + jitter)))


def _respect_gemini_qps() -> None:
    global _last_gemini_call_started_at
    if GEMINI_MIN_INTERVAL_SECONDS <= 0:
        return
    with _gemini_qps_lock:
        now = time.monotonic()
        wait_seconds = (_last_gemini_call_started_at + GEMINI_MIN_INTERVAL_SECONDS) - now
        if wait_seconds > 0:
            logger.info("gemini qps throttle sleep | seconds=%.2f", wait_seconds)
            time.sleep(wait_seconds)
            now = time.monotonic()
        _last_gemini_call_started_at = now


def _provider_retry_reason(message: str, *, next_retry_at: Any | None = None) -> str:
    suffix = ""
    if next_retry_at:
        suffix = f" | next_retry_at={next_retry_at}"
    return f"provider_pressure_retry_scheduled: {message}{suffix}"[:2000]


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


def _derive_method(payload: dict[str, Any]) -> str:
    return str(payload.get("derive_method") or payload.get("analysis_method") or "mock").strip().lower() or "mock"


def _target(payload: dict[str, Any]) -> tuple[str, str]:
    return str(payload.get("target_type") or "").strip(), str(payload.get("target_id") or "").strip()


def _url_host(url: str) -> str:
    try:
        return urlparse(str(url or "")).netloc.lower()
    except Exception:
        return ""


def _platform_from_content_url(url: str) -> str:
    host = _url_host(url)
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m."):
        host = host[2:]
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    if "instagram.com" in host:
        return "instagram"
    if "tiktok.com" in host:
        return "tiktok"
    return "unsupported"


def _parse_apify_resolver_stdout(stdout: str) -> dict[str, Any]:
    for line in reversed(str(stdout or "").splitlines()):
        candidate = line.strip()
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {
        "scraped_ok": False,
        "error": "apify resolver returned no JSON",
        "_parse_error": True,
    }


def _parse_last_json_stdout(stdout: str) -> dict[str, Any] | None:
    for line in reversed(str(stdout or "").splitlines()):
        candidate = line.strip()
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


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
        output["reason"] = str(scraped.get("error") or "media_resolve_failed")
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


def _advisory_lock(conn: psycopg.Connection[Any], scope: str, key: str) -> bool:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT pg_try_advisory_lock(hashtext(%s), hashtext(%s)) AS locked", (scope, key))
        row = cur.fetchone() or {}
        return bool(row.get("locked"))


def _advisory_unlock(conn: psycopg.Connection[Any], scope: str, key: str) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(hashtext(%s), hashtext(%s))", (scope, key))


def _acquire_llm_slot(conn: psycopg.Connection[Any]) -> str | None:
    for index in range(LLM_CONCURRENCY_LIMIT):
        slot = str(index)
        if _advisory_lock(conn, "vkpi_analysis_worker_llm_slot", slot):
            return slot
    return None


def _analysis_cache_exists(conn: psycopg.Connection[Any], target_type: str, target_id: str, derive_method: str) -> bool:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT 1
            FROM vkpi_analysis_cache
            WHERE target_type=%s AND target_id=%s AND derive_method=%s AND status='ready'
            LIMIT 1
            """,
            (target_type, target_id, derive_method),
        )
        return cur.fetchone() is not None


def _search_session_job_state(raw_status: str, reason: str = "") -> tuple[str, str]:
    status = str(raw_status or "").strip().lower()
    reason_text = str(reason or "").strip().lower()
    if status == "running":
        return "running", "analysis"
    if status == "queued":
        return "queued", "analysis"
    if status == "done":
        if "skipped_existing_analysis_cache" in reason_text:
            return "already_analyzed", "summary"
        return "ready", "summary"
    if status in {"failed", "blocked"}:
        return "failed", "analysis"
    return "unknown", "analysis"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _compact_text(value: Any, limit: int = 700) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text[:limit]


def _score_value(value: Any) -> float | None:
    raw = value.get("score") if isinstance(value, dict) else value
    if raw in (None, ""):
        return None
    try:
        parsed = float(raw)
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return 0.0
    if parsed > 100:
        return 100.0
    return round(parsed, 3)


def _score_confidence(value: Any) -> float | None:
    if not isinstance(value, dict) or value.get("confidence") in (None, ""):
        return None
    try:
        parsed = float(value.get("confidence"))
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return 0.0
    if parsed > 1:
        return 1.0
    return round(parsed, 5)


def _final_v1_payload(result: Any) -> dict[str, Any]:
    root = _as_dict(result)
    nested = _as_dict(root.get("video_analysis_final_v1"))
    if _as_dict(nested.get("layer1_visual_content")) or _as_dict(nested.get("layer6_flags_and_scores")):
        return nested
    return root


def _score_entry(layer6: dict[str, Any], key: str) -> dict[str, Any] | None:
    scores = _as_dict(layer6.get("scores"))
    raw = scores.get(key)
    if raw is None and key == "marketing_value_score":
        raw = layer6.get("marketing_value_score")
    value = _score_value(raw)
    if value is None:
        return None
    entry: dict[str, Any] = {"score": value}
    confidence = _score_confidence(raw)
    if confidence is not None:
        entry["confidence"] = confidence
    if isinstance(raw, dict):
        for meta_key in ("rationale", "reason", "evidence"):
            if raw.get(meta_key) is not None:
                entry[meta_key] = _compact_text(raw.get(meta_key), 420)
    return entry


def _search_session_analysis_summary_from_result(
    *,
    cache_id: int | None,
    derive_method: str,
    target_type: str,
    target_id: str,
    evidence: dict[str, Any] | None,
    result: dict[str, Any],
    cost: float | None = None,
) -> dict[str, Any] | None:
    if derive_method != "video_analysis_final_v1" or target_type != "video":
        return None
    payload = _final_v1_payload(result)
    layer1 = _as_dict(payload.get("layer1_visual_content"))
    layer5 = _as_dict(payload.get("layer5_recommendations"))
    layer6 = _as_dict(payload.get("layer6_flags_and_scores"))
    cost_info = _as_dict(payload.get("cost"))
    marketing = _score_entry(layer6, "marketing_value_score")
    if not marketing:
        return {
            "status": "ready",
            "derive_method": derive_method,
            "cache_id": cache_id,
            "source_evidence_id": _int_or_none(target_id),
            "missing": "marketing_value_score",
        }
    score_keys = (
        "content_quality_score",
        "viewer_heart_score",
        "channel_value_score",
        "asset_reuse_score",
        "product_proof_score",
        "marketing_value_score",
    )
    scores = {key: entry for key in score_keys if (entry := _score_entry(layer6, key))}
    evidence = evidence or {}
    return {
        "status": "ready",
        "derive_method": derive_method,
        "cache_id": cache_id,
        "source_evidence_id": _int_or_none(target_id),
        "kol_pool_id": _int_or_none(evidence.get("kol_pool_id") or _as_dict(payload.get("source")).get("kol_pool_id")),
        "source_url": evidence.get("content_url") or _as_dict(payload.get("source")).get("url"),
        "title": _compact_text(evidence.get("title") or evidence.get("video_title") or _as_dict(payload.get("source")).get("title"), 320),
        "llm_v6_fit": marketing.get("score"),
        "confidence": marketing.get("confidence"),
        "scores": scores,
        "summary": _compact_text(layer1.get("content_summary") or layer6.get("key_hook") or layer6.get("final_verdict"), 700),
        "recommendations": {
            "cooperation_recommendation": layer5.get("cooperation_recommendation"),
            "buyout_or_license_recommendation": layer5.get("buyout_or_license_recommendation"),
            "why": layer5.get("why"),
        },
        "risk": {
            "risk_flags": layer6.get("risk_flags"),
            "final_verdict": layer6.get("final_verdict"),
            "key_hook": layer6.get("key_hook"),
        },
        "cost": cost,
        "latency_ms": _int_or_none(cost_info.get("latency_ms")),
    }


def _session_url_enrichment_error(payload: dict[str, Any]) -> str:
    """Return a compact error when account/video enrichment partially failed."""

    def _flow_error(flow: dict[str, Any], label: str) -> str:
        status = str(flow.get("status") or "").strip()
        errors = _int_or_none(flow.get("errors")) or 0
        if errors <= 0 and "error" not in status:
            return ""
        messages: list[str] = []
        for item in flow.get("items") or []:
            if not isinstance(item, dict):
                continue
            error = str(item.get("error") or "").strip()
            if error:
                metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                title = str(
                    metadata.get("title")
                    or metadata.get("content_url")
                    or item.get("title")
                    or item.get("content_url")
                    or "video"
                ).strip()
                messages.append(f"{title}: {error}")
            if len(messages) >= 3:
                break
        detail = "; ".join(messages) if messages else status or "partial_failure"
        return f"{label}: {detail}"

    profile_flow = payload.get("profile_flow") if isinstance(payload.get("profile_flow"), dict) else {}
    video_flow = payload.get("video_flow") if isinstance(payload.get("video_flow"), dict) else {}
    representative = profile_flow.get("representative_video_analysis") or video_flow.get("representative_video_analysis")
    history = profile_flow.get("history_video_evidence") or video_flow.get("history_video_evidence")
    parts = []
    if isinstance(representative, dict):
        error = _flow_error(representative, "代表视频分析")
        if error:
            parts.append(error)
    if isinstance(history, dict):
        error = _flow_error(history, "历史视频物化")
        if error:
            parts.append(error)
    return " | ".join(parts)[:1000]


def _search_session_status_from_items(items: list[dict[str, Any]]) -> str:
    statuses = {str(item.get("status") or "").strip() for item in items}
    if statuses.intersection({"queued", "running", "already_queued"}):
        return "running"
    if statuses.intersection({"failed"}):
        return "partial"
    if statuses.intersection({"partial"}):
        return "partial"
    if statuses:
        return "ready"
    return "ready"


def _search_session_item_counts(items: list[dict[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    by_stage: dict[str, int] = {}
    ready = errors = skipped = executed = 0
    for item in items:
        status = str(item.get("status") or "unknown").strip()
        stage = str(item.get("stage") or "identified").strip()
        by_status[status] = by_status.get(status, 0) + 1
        by_stage[stage] = by_stage.get(stage, 0) + 1
        if status in {"ready", "already_analyzed"}:
            ready += 1
        if status in {"failed", "partial"}:
            errors += 1
        if status == "skipped":
            skipped += 1
        if status not in {"planned", "identified", "matched", "queued", "running", "unknown"}:
            executed += 1
    return {
        "by_status": by_status,
        "by_stage": by_stage,
        "ready": ready,
        "errors": errors,
        "skipped": skipped,
        "executed": executed,
    }


def _rebuild_search_session_summary(
    cur: Any,
    *,
    session_id: int,
    session_status: str,
) -> None:
    cur.execute(
        """
        SELECT result_summary_json
        FROM vkpi_kol_search_sessions
        WHERE id=%s
        LIMIT 1
        """,
        (int(session_id),),
    )
    session_row = cur.fetchone() or {}
    current_summary = session_row.get("result_summary_json")
    current_summary = current_summary if isinstance(current_summary, dict) else _loads(current_summary, {})
    if not isinstance(current_summary, dict):
        current_summary = {}
    cur.execute(
        """
        SELECT id, item_type, status, stage, rank, score, kol_pool_id, evidence_id, job_id, source_url, payload_json, updated_at
        FROM vkpi_kol_search_session_items
        WHERE session_id=%s
        ORDER BY rank NULLS LAST, id
        """,
        (int(session_id),),
    )
    item_rows = cur.fetchall() or []
    items: list[dict[str, Any]] = []
    for row in item_rows:
        payload = row.get("payload_json") if isinstance(row.get("payload_json"), dict) else _loads(row.get("payload_json"), {})
        item = {
            "id": row.get("id"),
            "item_type": row.get("item_type"),
            "status": row.get("status"),
            "stage": row.get("stage"),
            "rank": row.get("rank"),
            "score": row.get("score"),
            "kol_pool_id": row.get("kol_pool_id"),
            "evidence_id": row.get("evidence_id"),
            "job_id": row.get("job_id"),
            "source_url": row.get("source_url"),
            "job_status": payload.get("job_status") if isinstance(payload, dict) else None,
            "job_last_error": payload.get("job_last_error") if isinstance(payload, dict) else None,
            "analysis": payload.get("analysis") if isinstance(payload, dict) else None,
            "updated_at": row.get("updated_at").isoformat() if hasattr(row.get("updated_at"), "isoformat") else row.get("updated_at"),
        }
        items.append(item)
    counts = _search_session_item_counts(items)
    primary = next((item for item in items if str(item.get("item_type") or "").startswith("url_")), items[0] if items else {})
    summary = {
        **current_summary,
        "item_status": primary.get("status"),
        "job_status": primary.get("job_status"),
        "job_last_error": primary.get("job_last_error"),
        "analysis": primary.get("analysis"),
        "counts": counts,
        "items_written": len(items),
        "terminal_synced_at": datetime.now(timezone.utc).isoformat(),
    }
    cur.execute(
        """
        UPDATE vkpi_kol_search_sessions
        SET status=%s,
            result_summary_json=%s::jsonb,
            updated_at=NOW()
        WHERE id=%s
        """,
        (session_status, _json(summary), int(session_id)),
    )


def _search_session_analysis_summary_from_ready_cache(
    conn: psycopg.Connection[Any],
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    target_type, target_id = _target(payload)
    derive_method = _derive_method(payload)
    if derive_method != "video_analysis_final_v1" or target_type != "video" or not target_id:
        return None
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, result, cost
            FROM vkpi_analysis_cache
            WHERE target_type=%s
              AND target_id=%s
              AND derive_method=%s
              AND status='ready'
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (target_type, target_id, derive_method),
        )
        cache = cur.fetchone()
        cur.execute(
            """
            SELECT id, kol_pool_id, content_url, title, video_title
            FROM vkpi_kol_video_evidence
            WHERE id=%s
            LIMIT 1
            """,
            (_int_or_none(target_id),),
        )
        evidence = cur.fetchone() or {}
    if not cache:
        return None
    result = cache.get("result") if isinstance(cache.get("result"), dict) else _loads(cache.get("result"), {})
    return _search_session_analysis_summary_from_result(
        cache_id=_int_or_none(cache.get("id")),
        derive_method=derive_method,
        target_type=target_type,
        target_id=target_id,
        evidence=dict(evidence),
        result=result if isinstance(result, dict) else {},
        cost=float(cache.get("cost") or 0.0),
    )


def _sync_deep_analysis_result_from_cache(
    conn: psycopg.Connection[Any],
    *,
    cache_id: int | None,
    derive_method: str,
    job_id: int,
) -> dict[str, Any] | None:
    if derive_method != "video_analysis_final_v1" or not cache_id:
        return None
    try:
        result = upsert_deep_analysis_from_final_v1_cache(conn, int(cache_id))
    except Exception as exc:
        logger.warning("final_v1 deep-result sync failed | job_id=%s cache_id=%s error=%s", job_id, cache_id, exc)
        return {"status": "failed", "reason": str(exc)[:500], "source_cache_id": cache_id}
    logger.info(
        "final_v1 deep-result sync | job_id=%s cache_id=%s status=%s action=%s deep_result_id=%s",
        job_id,
        cache_id,
        result.get("status"),
        result.get("action"),
        result.get("deep_result_id"),
    )
    return {
        key: result.get(key)
        for key in (
            "status",
            "action",
            "reason",
            "deep_result_id",
            "source_cache_id",
            "source_evidence_id",
            "kol_pool_id",
            "llm_v6_fit",
            "viltrox_fit_score_changed_ids",
        )
        if key in result
    }


def _enqueue_content_fit_after_final_v1(
    conn: psycopg.Connection[Any],
    *,
    job_id: int,
    deep_result: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """L2(用户令「最重档」):final_v1 视频深析就绪后,链式入队**内容契合深析**——读该 KOL 视频
    Gemini 分析 + 评论,LLM 出 creator_type/fit_verdict(发现的新人经 account_deep 抓取入库 +
    视频深析后,在此自动获得内容契合)。镜像 account_dossier followup。
    控量:① 已有 ready content_fit_v1 cache → 复用不重烧;② 已有 queued/running content_fit job → 去重;
    每 KOL 仅一次。LLM 走 content_fit_analysis 的 openai + 预算闸(闸A)。product_sku 尽力从该 KOL 的
    搜索会话取(无则 None→通用类型分析)。绝不写 viltrox_fit_score(独立 cache);失败不阻断 final_v1。"""
    if not deep_result or deep_result.get("status") != "ready":
        return None
    kol_pool_id = _int_or_none(deep_result.get("kol_pool_id"))
    if not kol_pool_id:
        return None
    with conn.transaction():
        with conn.cursor(row_factory=dict_row) as cur:
            # ① 复用:已有 ready content_fit_v1 cache → 不重复烧 LLM
            cur.execute(
                """
                SELECT 1 FROM vkpi_analysis_cache
                WHERE target_type='kol' AND derive_method='content_fit_v1'
                  AND target_id=%s AND status='ready' LIMIT 1
                """,
                (str(kol_pool_id),),
            )
            if cur.fetchone():
                return {"status": "cache_reused", "kol_pool_id": kol_pool_id}
            # ② 去重:已有 queued/running content_fit job
            cur.execute(
                """
                SELECT id, status FROM apify_jobs
                WHERE job_type='kol_content_fit_analysis'
                  AND status IN ('queued', 'running')
                  AND payload->>'kol_pool_id'=%s
                ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (str(kol_pool_id),),
            )
            existing = cur.fetchone()
            if existing:
                return {
                    "status": "already_queued" if existing["status"] == "queued" else "already_running",
                    "job_id": int(existing["id"]),
                    "kol_pool_id": kol_pool_id,
                }
            # product_sku 尽力而为:取最近一次以此 KOL 为候选、且带 product_sku 的搜索会话(无则 None)
            product_sku: str | None = None
            try:
                cur.execute(
                    """
                    SELECT s.input_payload_json->>'product_sku' AS sku
                    FROM vkpi_kol_search_session_items i
                    JOIN vkpi_kol_search_sessions s ON s.id = i.session_id
                    WHERE i.kol_pool_id = %s
                      AND COALESCE(s.input_payload_json->>'product_sku', '') <> ''
                    ORDER BY i.id DESC LIMIT 1
                    """,
                    (int(kol_pool_id),),
                )
                srow = cur.fetchone()
                if srow and srow.get("sku"):
                    product_sku = str(srow["sku"])
            except Exception:
                product_sku = None
            payload = {
                "kol_pool_id": int(kol_pool_id),
                "product_sku": product_sku,
                "derive_method": "content_fit_v1",
                "source": "final_v1_worker_followup",
                "trigger": "final_v1_done",
                "source_job_id": int(job_id),
                "viltrox_fit_score_untouched": True,
                "query_text": f"content fit - kol_pool #{kol_pool_id}",
            }
            cur.execute(
                """
                INSERT INTO apify_jobs (job_type, payload, status, created_at, updated_at)
                VALUES ('kol_content_fit_analysis', %s::jsonb, 'queued', NOW(), NOW())
                RETURNING id, status
                """,
                (_json(payload),),
            )
            row = cur.fetchone() or {}
    return {
        "status": "queued",
        "job_id": int(row["id"]) if row.get("id") is not None else None,
        "kol_pool_id": kol_pool_id,
        "product_sku": product_sku,
    }


def _enqueue_account_dossier_extract_after_final_v1(
    conn: psycopg.Connection[Any],
    *,
    job_id: int,
    deep_result: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not deep_result or deep_result.get("status") != "ready":
        return None
    kol_pool_id = _int_or_none(deep_result.get("kol_pool_id"))
    if not kol_pool_id:
        return None
    with conn.transaction():
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT id, status, created_at, updated_at
                FROM apify_jobs
                WHERE job_type='account_dossier_extract'
                  AND status IN ('queued', 'running')
                  AND payload->>'target_type'='kol_pool'
                  AND payload->>'target_id'=%s
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (str(kol_pool_id),),
            )
            existing = cur.fetchone()
            if existing:
                return {
                    "status": "already_queued" if existing["status"] == "queued" else "already_running",
                    "job_id": int(existing["id"]),
                    "kol_pool_id": kol_pool_id,
                }
            payload = {
                "target_type": "kol_pool",
                "target_id": str(kol_pool_id),
                "derive_method": "kol_account_dossier_extract_v1",
                "analysis_kind": "profile_llm",
                "source": "final_v1_worker_followup",
                "trigger": "final_v1_done",
                "source_job_id": int(job_id),
                "source_cache_id": deep_result.get("source_cache_id"),
                "source_evidence_id": deep_result.get("source_evidence_id"),
                "query_text": f"account dossier - kol_pool #{kol_pool_id}",
            }
            cur.execute(
                """
                INSERT INTO apify_jobs (job_type, payload, status, created_at, updated_at)
                VALUES ('account_dossier_extract', %s::jsonb, 'queued', NOW(), NOW())
                RETURNING id, status, created_at, updated_at
                """,
                (_json(payload),),
            )
            row = cur.fetchone() or {}
    return {
        "status": "queued",
        "job_id": int(row["id"]) if row.get("id") is not None else None,
        "kol_pool_id": kol_pool_id,
    }


def _sync_search_session_job(
    conn: psycopg.Connection[Any],
    job_id: int,
    *,
    raw_status: str,
    reason: str = "",
    analysis_summary: dict[str, Any] | None = None,
) -> None:
    try:
        _sync_search_session_job_impl(
            conn,
            job_id,
            raw_status=raw_status,
            reason=reason,
            analysis_summary=analysis_summary,
        )
    except Exception as exc:
        logger.warning("search session job sync failed | job_id=%s status=%s error=%s", job_id, raw_status, exc)


def _sync_search_session_job_impl(
    conn: psycopg.Connection[Any],
    job_id: int,
    *,
    raw_status: str,
    reason: str = "",
    analysis_summary: dict[str, Any] | None = None,
) -> None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT id, payload, last_error FROM apify_jobs WHERE id=%s", (int(job_id),))
        row = cur.fetchone()
    if not row:
        return
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else _loads(row.get("payload"), {})
    if not isinstance(payload, dict):
        return
    session_id = _int_or_none(payload.get("search_session_id"))
    item_id = _int_or_none(payload.get("search_session_item_id"))
    if not session_id or not item_id:
        return
    item_status, stage = _search_session_job_state(raw_status, reason or row.get("last_error") or "")
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT payload_json
            FROM vkpi_kol_search_session_items
            WHERE id=%s
              AND session_id=%s
            LIMIT 1
            """,
            (int(item_id), int(session_id)),
        )
        item_row = cur.fetchone() or {}
    existing_payload = item_row.get("payload_json") if isinstance(item_row.get("payload_json"), dict) else _loads(item_row.get("payload_json"), {})
    if not isinstance(existing_payload, dict):
        existing_payload = {}
    enrichment_error = _session_url_enrichment_error(existing_payload)
    if item_status in {"ready", "already_analyzed"} and enrichment_error:
        item_status = "partial"
        stage = "summary"
    if analysis_summary is None and item_status in {"ready", "already_analyzed", "partial"}:
        analysis_summary = _search_session_analysis_summary_from_ready_cache(conn, payload)
    payload["search_session_item_status"] = item_status
    payload["search_session_stage"] = stage
    payload["search_session_last_job_status"] = raw_status
    payload["search_session_last_error"] = str(enrichment_error or reason or row.get("last_error") or "")[:500]
    if analysis_summary:
        payload["search_session_cache_id"] = analysis_summary.get("cache_id")
        payload["search_session_analysis_status"] = analysis_summary.get("status")
    item_patch: dict[str, Any] = {
        "job_status": raw_status,
        "job_last_error": str(enrichment_error or reason or row.get("last_error") or "")[:1000],
        "job_updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if analysis_summary:
        item_patch["analysis"] = analysis_summary
    with conn.transaction():
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                UPDATE vkpi_kol_search_session_items
                SET status=%s,
                    stage=%s,
                    payload_json = payload_json || %s::jsonb,
                    updated_at=NOW()
                WHERE id=%s
                  AND session_id=%s
                """,
                (
                    item_status,
                    stage,
                    _json(item_patch),
                    int(item_id),
                    int(session_id),
                ),
            )
            cur.execute(
                """
                SELECT status, stage
                FROM vkpi_kol_search_session_items
                WHERE session_id=%s
                """,
                (int(session_id),),
            )
            session_status = _search_session_status_from_items([dict(item) for item in (cur.fetchall() or [])])
            _rebuild_search_session_summary(cur, session_id=int(session_id), session_status=session_status)
            cur.execute(
                "UPDATE apify_jobs SET payload=%s::jsonb WHERE id=%s",
                (_json(payload), int(job_id)),
            )


def _finish_skipped(conn: psycopg.Connection[Any], job_id: int, reason: str) -> None:
    analysis_summary: dict[str, Any] | None = None
    if "skipped_existing_analysis_cache" in str(reason or ""):
        try:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT payload FROM apify_jobs WHERE id=%s LIMIT 1", (int(job_id),))
                row = cur.fetchone() or {}
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else _loads(row.get("payload"), {})
            analysis_summary = _search_session_analysis_summary_from_ready_cache(conn, payload if isinstance(payload, dict) else {})
            cache_id = _int_or_none((analysis_summary or {}).get("cache_id"))
            deep_result = _sync_deep_analysis_result_from_cache(
                conn,
                cache_id=cache_id,
                derive_method=_derive_method(payload if isinstance(payload, dict) else {}),
                job_id=int(job_id),
            )
            account_extract_job = _enqueue_account_dossier_extract_after_final_v1(
                conn,
                job_id=int(job_id),
                deep_result=deep_result,
            )
            # L2:final_v1 就绪 → 链式入队内容契合深析(每 KOL 一次,cache 复用/去重/预算闸控量)。
            content_fit_job = _enqueue_content_fit_after_final_v1(
                conn,
                job_id=int(job_id),
                deep_result=deep_result,
            )
            if analysis_summary and content_fit_job:
                analysis_summary["content_fit_job"] = content_fit_job
            if analysis_summary and deep_result:
                analysis_summary["deep_result"] = deep_result
            if analysis_summary and account_extract_job:
                analysis_summary["account_dossier_extract_job"] = account_extract_job
        except Exception as exc:
            logger.warning("skipped cache deep/account sync failed | job_id=%s error=%s", job_id, exc)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE apify_jobs
                SET status='done',
                    last_error=%s,
                    last_error_category=NULL,
                    next_retry_at=NULL,
                    updated_at=NOW()
                WHERE id=%s
                """,
                (reason[:2000], job_id),
            )
    _sync_search_session_job(conn, job_id, raw_status="done", reason=reason, analysis_summary=analysis_summary)


def _block_job(conn: psycopg.Connection[Any], job_id: int, reason: str, detail: dict[str, Any] | None = None) -> None:
    payload = {"reason": reason, **(detail or {})}
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE apify_jobs
                SET status='blocked',
                    last_error=%s,
                    last_error_category='blocked',
                    next_retry_at=NULL,
                    updated_at=NOW()
                WHERE id=%s
                """,
                (_json(payload)[:2000], job_id),
            )
    _sync_search_session_job(conn, job_id, raw_status="blocked", reason=_json(payload))


def _requeue_job(conn: psycopg.Connection[Any], job_id: int, reason: str) -> None:
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE apify_jobs
                SET status='queued',
                    last_error=%s,
                    last_error_category=NULL,
                    next_retry_at=NULL,
                    updated_at=NOW()
                WHERE id=%s
                """,
                (reason[:2000], job_id),
            )
    _sync_search_session_job(conn, job_id, raw_status="queued", reason=reason)


def _process_session_advance(conn: psycopg.Connection[Any], job: dict[str, Any], payload: dict[str, Any]) -> None:
    session_id = _int_or_none(payload.get("search_session_id") or payload.get("target_id"))
    if not session_id:
        raise ValueError("session_advance payload must include search_session_id")
    try:
        kol_search_sessions.update_session_result_summary(
            int(session_id),
            status="running",
            summary_patch={
                "profile_batch_advance_job": {
                    "status": "running",
                    "job_id": int(job["id"]),
                    "viltrox_fit_score_untouched": True,
                }
            },
        )
        kol_search_sessions.mark_items_profile_running(
            int(session_id),
            job_id=int(job["id"]),
            reason="session_advance_worker_claimed",
        )
        result = kol_profile_discovery.advance_search_session_items(
            session_id=int(session_id),
            body={**payload, "execute": True},
        )
    except Exception as exc:
        try:
            kol_search_sessions.update_session_result_summary(
                int(session_id),
                status="failed",
                summary_patch={
                    "profile_batch_advance_job": {
                        "status": "failed",
                        "job_id": int(job["id"]),
                        "error": str(exc)[:1000],
                        "viltrox_fit_score_untouched": True,
                    }
                },
            )
        except Exception as inner_exc:
            logger.warning("session_advance failure summary update failed | job_id=%s error=%s", job.get("id"), inner_exc)
        raise

    job_status = "failed" if result.get("status") == "failed" else "done"
    last_error = "" if job_status == "done" else str(result.get("status") or "session_advance_failed")
    payload["session_advance_result"] = {
        "status": result.get("status"),
        "selected": result.get("selected"),
        "eligible": result.get("eligible"),
        "overflow": result.get("overflow"),
        "counts": result.get("counts"),
        "viltrox_fit_score_changed_ids": result.get("viltrox_fit_score_changed_ids"),
        "viltrox_fit_score_untouched": result.get("viltrox_fit_score_untouched"),
    }
    payload["search_session_last_job_status"] = job_status
    payload["search_session_last_error"] = last_error
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE apify_jobs
                SET status=%s,
                    last_error=NULLIF(%s, ''),
                    payload=%s::jsonb,
                    updated_at=NOW()
                WHERE id=%s
                """,
                (job_status, last_error[:2000], _json(payload), int(job["id"])),
            )


def _process_smart_search_profile_advance(conn: psycopg.Connection[Any], job: dict[str, Any], payload: dict[str, Any]) -> None:
    session_id = _int_or_none(payload.get("search_session_id") or payload.get("target_id"))
    if not session_id:
        raise ValueError("smart_search_profile_advance payload must include search_session_id")
    try:
        kol_search_sessions.update_session_result_summary(
            int(session_id),
            status="running",
            summary_patch={
                "smart_search_profile_advance_job": {
                    "status": "running",
                    "job_id": int(job["id"]),
                    "query_text": payload.get("query_text"),
                    "viltrox_fit_score_untouched": True,
                }
            },
        )
        result = asyncio.run(
            kol_profile_discovery.execute_smart_search_profile_advance_pipeline(
                session_id=int(session_id),
                payload={**payload, "job_id": int(job["id"])},
            )
        )
    except Exception as exc:
        try:
            kol_search_sessions.update_session_result_summary(
                int(session_id),
                status="failed",
                summary_patch={
                    "smart_search_profile_advance_job": {
                        "status": "failed",
                        "job_id": int(job["id"]),
                        "query_text": payload.get("query_text"),
                        "error": str(exc)[:1000],
                        "viltrox_fit_score_untouched": True,
                    }
                },
            )
        except Exception as inner_exc:
            logger.warning("smart_search_profile_advance failure summary update failed | job_id=%s error=%s", job.get("id"), inner_exc)
        raise

    job_status = "failed" if result.get("status") == "failed" else "done"
    last_error = "" if job_status == "done" else str(result.get("status") or "smart_search_profile_advance_failed")
    advance = result.get("advance") if isinstance(result.get("advance"), dict) else {}
    new_discovery = result.get("new_discovery") if isinstance(result.get("new_discovery"), dict) else {}
    payload["smart_search_profile_advance_result"] = {
        "status": result.get("status"),
        "session_id": result.get("session_id"),
        "query": result.get("query"),
        "recall": result.get("recall"),
        "new_discovery_status": new_discovery.get("status") if new_discovery else "not_requested",
        "new_discovery_counts": new_discovery.get("counts") if new_discovery else None,
        "advance_status": advance.get("status"),
        "advance_selected": advance.get("selected"),
        "advance_counts": advance.get("counts"),
        "viltrox_fit_score_changed_ids": result.get("viltrox_fit_score_changed_ids"),
        "viltrox_fit_score_untouched": result.get("viltrox_fit_score_untouched"),
    }
    payload["search_session_last_job_status"] = job_status
    payload["search_session_last_error"] = last_error
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE apify_jobs
                SET status=%s,
                    last_error=NULLIF(%s, ''),
                    payload=%s::jsonb,
                    updated_at=NOW()
                WHERE id=%s
                """,
                (job_status, last_error[:2000], _json(payload), int(job["id"])),
            )


def _process_account_dossier_extract(conn: psycopg.Connection[Any], job: dict[str, Any], payload: dict[str, Any]) -> None:
    kol_pool_id = _int_or_none(payload.get("target_id") or payload.get("kol_pool_id"))
    if not kol_pool_id:
        raise ValueError("account_dossier_extract payload must include target_id")
    result = upsert_account_dossier_extract(conn, int(kol_pool_id))
    job_status = "done" if result.get("status") == "ready" else "blocked"
    last_error = "" if job_status == "done" else str(result.get("reason") or result.get("status") or "account_dossier_extract_not_ready")
    payload["account_dossier_extract_result"] = result
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE apify_jobs
                SET status=%s,
                    last_error=NULLIF(%s, ''),
                    payload=%s::jsonb,
                    updated_at=NOW()
                WHERE id=%s
                """,
                (job_status, last_error[:2000], _json(payload), int(job["id"])),
            )


def _resolve_job_staff(conn: psycopg.Connection[Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Build a staff dict for worker-run domain calls.

    payload carries triggered_by_user_id (a *users.id*), NOT a staff.id. Paths that hit
    llm_gateway → vkpi_llm_calls.created_by_staff_id (FK → staff.id) need the real staff.id;
    feeding the user_id there caused job 900's ForeignKeyViolation (user 108 → no staff.id 108;
    the real staff.id is 84). Resolve user_id → staff.id here. Keep user_id for attribution
    columns that legitimately store users.id (e.g. vkpi_analysis_cache.triggered_by_user_id).
    Not-found (non-staff actor, shouldn't reach these endpoints): null out so the FK records NULL
    rather than re-raising — `workflow.staff_id()` falls back to user_id, so we must drop it too.
    """
    user_id = _int_or_none(payload.get("triggered_by_user_id"))
    resolved = _int_or_none(payload.get("staff_id"))
    if resolved is None and user_id is not None:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT id FROM staff WHERE user_id=%s ORDER BY id LIMIT 1", (user_id,))
            row = cur.fetchone()
        if row:
            resolved = _int_or_none(row.get("id"))
    if resolved is None:
        return {"id": None, "staff_id": None, "user_id": None}
    return {"id": resolved, "staff_id": resolved, "user_id": user_id}


def _process_project_contract_extract(conn: psycopg.Connection[Any], job: dict[str, Any], payload: dict[str, Any]) -> None:
    contract_id = _int_or_none(payload.get("target_id"))
    project_id = _int_or_none(payload.get("project_id"))
    if not contract_id or not project_id:
        raise ValueError("project_contract_extract payload must include target_id and project_id")
    staff = _resolve_job_staff(conn, payload)
    # contracts.py uses sqlite-compat '?' via its own get_conn(); run inside the sync scope so
    # placeholders are translated and never touch this worker's raw psycopg connection.
    # R2 ordering: the core writes domain state (extraction_status='ready'/'failed' + cache)
    # and commits BEFORE we mark the job done; on failure it re-raises to _fail_job. Re-runs
    # are idempotent (cache upsert + contract row UPDATE overwrite with the same result).
    with db_connection_sync_scope():
        project_contracts.run_contract_extraction_for_job(int(project_id), int(contract_id), staff=staff)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE apify_jobs SET status='done', last_error=NULL, updated_at=NOW() WHERE id=%s",
                (int(job["id"]),),
            )


def _process_kol_profile_deep_crawl(conn: psycopg.Connection[Any], job: dict[str, Any], payload: dict[str, Any]) -> None:
    """队列铁律(2026-06-12):账号深爬 execute 走队列,泳道可见;内核与 HTTP execute 同一条。"""
    from app.domains.kol import url_deep_crawl as kol_url_deep_crawl

    staff = _resolve_job_staff(conn, payload)
    with db_connection_sync_scope():
        result = kol_url_deep_crawl.run_profile_deep_crawl_for_job(payload, staff=staff)
    status = str((result or {}).get("status") or "")
    ok = status in ("", "ok", "ready", "done", "executed") or bool((result or {}).get("execution"))
    # 诚实闸(job 926 案:35mmc.com 搜索页 URL 标 done 但什么都没干):
    # 非 profile/video 的 URL 内核走 unsupported 短路,任务必须 blocked 而非 done。
    flow_status = str(((result or {}).get("profile_flow") or {}).get("status") or "")
    url_type = str((result or {}).get("url_type") or "")
    if ok and flow_status in ("unsupported", "needs_human_choice") and not (result or {}).get("video_flow"):
        ok = False
        status = f"url_{url_type or 'unknown'}_{flow_status}"
    # search_session_id 回写 payload:queue_view 据此输出 search_session,
    # 泳道「最近完成」才会保留该任务并支持点开会话详情(一闪而过案)。
    session_id = _int_or_none((result or {}).get("search_session_id"))
    if session_id:
        payload["search_session_id"] = int(session_id)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE apify_jobs SET status=%s, last_error=%s, payload=%s::jsonb, updated_at=NOW() WHERE id=%s",
                (
                    "done" if ok else "blocked",
                    None if ok else (status or "deep_crawl_not_executed")[:300],
                    _json(payload),
                    int(job["id"]),
                ),
            )


def _process_logistics_track_sync(conn: psycopg.Connection[Any], job: dict[str, Any], payload: dict[str, Any]) -> None:
    """17track 物流同步(2026-06-12):注册+拉取轨迹写回 shipping 元数据,泳道可见。"""
    from app.domains.logistics import seventeen_track

    staff = _resolve_job_staff(conn, payload)
    with db_connection_sync_scope():
        result = seventeen_track.run_logistics_sync_for_job(payload, staff=staff)
    status = str((result or {}).get("status") or "")
    ok = status == "ready"
    payload["logistics_sync_result"] = {k: v for k, v in (result or {}).items() if k != "results"}
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE apify_jobs SET status=%s, last_error=%s, payload=%s::jsonb, updated_at=NOW() WHERE id=%s",
                (
                    "done" if ok else "blocked",
                    None if ok else (status or "logistics_sync_failed")[:300],
                    _json(payload),
                    int(job["id"]),
                ),
            )


def _process_kol_outreach_draft(conn: psycopg.Connection[Any], job: dict[str, Any], payload: dict[str, Any]) -> None:
    """联系草稿(2026-06-12 裁令):LLM 经队列生成外联消息,产物落 cache(kol_outreach_draft_v1)。"""
    from app.domains.kol import outreach_draft as kol_outreach_draft

    staff = _resolve_job_staff(conn, payload)
    with db_connection_sync_scope():
        result = kol_outreach_draft.run_outreach_draft_for_job(payload, staff=staff)
    status = str((result or {}).get("status") or "")
    ok = status == "ready"
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE apify_jobs SET status=%s, last_error=%s, updated_at=NOW() WHERE id=%s",
                (
                    "done" if ok else "blocked",
                    None if ok else (str((result or {}).get("reason") or status or "outreach_draft_failed"))[:300],
                    int(job["id"]),
                ),
            )


def _process_kol_content_fit_analysis(conn: psycopg.Connection[Any], job: dict[str, Any], payload: dict[str, Any]) -> None:
    """收口路①-2:内容契合深析(「思考中」)。读该 KOL 视频画面/故事 + 评论 → openai(GPT)+3重试
    → 落独立 cache(target_type='kol'/derive_method='content_fit_v1')。

    红线:零触 viltrox_fit_score;LLM/重试/cache 全由 content_fit_analysis 域内负责,本 handler
    只做调度与状态回写。无视频证据 → status='insufficient_evidence',标 blocked(不烧 LLM,诚实)。
    """
    from app.domains.kol import content_fit_analysis as kol_content_fit

    kol_pool_id = _int_or_none(payload.get("kol_pool_id") or payload.get("target_id"))
    if not kol_pool_id:
        raise ValueError("kol_content_fit_analysis payload must include kol_pool_id")
    staff = _resolve_job_staff(conn, payload)
    with db_connection_sync_scope():
        result = kol_content_fit.analyze_content_fit(
            int(kol_pool_id),
            str(payload.get("product_sku") or "") or None,
            staff=staff if isinstance(staff, dict) else None,
        )
    state = str((result or {}).get("state") or (result or {}).get("status") or "")
    ok = state == "ready"
    # insufficient_evidence / llm_failed 不是错误态,但也无 cache 产出 → blocked(可见、不重试雪崩)。
    last_error = "" if ok else (str((result or {}).get("reason") or state or "content_fit_not_ready"))
    payload["content_fit_result"] = {
        "state": state,
        "kol_pool_id": kol_pool_id,
        "fit_verdict": ((result or {}).get("result") or {}).get("fit_verdict"),
        "confidence": ((result or {}).get("result") or {}).get("confidence"),
        "cached": (result or {}).get("cached"),
        "viltrox_fit_score_untouched": True,
    }
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE apify_jobs SET status=%s, last_error=NULLIF(%s, ''), payload=%s::jsonb, updated_at=NOW() WHERE id=%s",
                (
                    "done" if ok else "blocked",
                    last_error[:300],
                    _json(payload),
                    int(job["id"]),
                ),
            )


def _process_contract_invoice_extract(conn: psycopg.Connection[Any], job: dict[str, Any], payload: dict[str, Any]) -> None:
    """发票回填提取(批E,2026-06-12):读本地存证文件 → Claude 提取收款字段 → cache(invoice)。
    失败域内不写 cache,这里标 blocked(模式同 _process_kol_outreach_draft)。"""
    from app.domains.projects import contract_assist

    staff = _resolve_job_staff(conn, payload)
    with db_connection_sync_scope():
        result = contract_assist.run_invoice_extract_for_job(payload, staff=staff)
    status = str((result or {}).get("status") or "")
    ok = status == "ready"
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE apify_jobs SET status=%s, last_error=%s, updated_at=NOW() WHERE id=%s",
                (
                    "done" if ok else "blocked",
                    None if ok else (str((result or {}).get("reason") or status or "invoice_extract_failed"))[:300],
                    int(job["id"]),
                ),
            )


def _process_contract_polish(conn: psycopg.Connection[Any], job: dict[str, Any], payload: dict[str, Any]) -> None:
    """合同条款 LLM 润色(批E,2026-06-12):llm_gateway(preferred openai)→ cache(contract_polish)。
    失败域内不写 cache,这里标 blocked(模式同 _process_kol_outreach_draft)。"""
    from app.domains.projects import contract_assist

    staff = _resolve_job_staff(conn, payload)
    with db_connection_sync_scope():
        result = contract_assist.run_contract_polish_for_job(payload, staff=staff)
    status = str((result or {}).get("status") or "")
    ok = status == "ready"
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE apify_jobs SET status=%s, last_error=%s, updated_at=NOW() WHERE id=%s",
                (
                    "done" if ok else "blocked",
                    None if ok else (str((result or {}).get("reason") or status or "contract_polish_failed"))[:300],
                    int(job["id"]),
                ),
            )


def _process_kol_pool_comments_collect(conn: psycopg.Connection[Any], job: dict[str, Any], payload: dict[str, Any]) -> None:
    """KOL Pool 收藏行评论采集(2026-06-12 裁令):逐帖走 collect_post_comments,泳道可见。"""
    from app.domains.comments import collector as comments_collector

    staff = _resolve_job_staff(conn, payload)
    with db_connection_sync_scope():
        result = comments_collector.run_kol_pool_comments_for_job(payload, staff=staff)
    status = str((result or {}).get("status") or "")
    ok = status == "ready"
    payload["comments_collect_result"] = {k: v for k, v in (result or {}).items() if k != "results"}
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE apify_jobs SET status=%s, last_error=%s, payload=%s::jsonb, updated_at=NOW() WHERE id=%s",
                (
                    "done" if ok else "blocked",
                    None if ok else (status or "comments_collect_failed")[:300],
                    _json(payload),
                    int(job["id"]),
                ),
            )


def _process_project_retrospective(conn: psycopg.Connection[Any], job: dict[str, Any], payload: dict[str, Any]) -> None:
    project_id = _int_or_none(payload.get("target_id") or payload.get("project_id"))
    if not project_id:
        raise ValueError("project_retrospective_aggregate payload must include target_id")
    staff = _resolve_job_staff(conn, payload)
    # retrospective_aggregate uses sqlite-compat '?' via its own get_conn(); run inside the sync scope.
    # R2 ordering: the domain writes cache(project) BEFORE we mark the job done; on no-ready/failure it
    # writes nothing to cache and we mark the job blocked (not done) so the UI/读端能区分。
    # Never touches vkpi_kol_pool / fit_score.
    with db_connection_sync_scope():
        result = project_retrospective.run_project_retrospective(int(project_id), staff=staff)
    status = str(result.get("status") or "")
    job_status = "done" if status == "ready" else "blocked"
    last_error = "" if job_status == "done" else (status or "project_retrospective_not_ready")
    payload["project_retrospective_result"] = {k: v for k, v in result.items() if k != "result"}
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE apify_jobs
                SET status=%s, last_error=NULLIF(%s, ''), payload=%s::jsonb, updated_at=NOW()
                WHERE id=%s
                """,
                (job_status, last_error[:2000], _json(payload), int(job["id"])),
            )


def _llm_budget_preflight(job: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    target_type, target_id = _target(payload)
    prompt = str(payload.get("prompt") or f"{job.get('job_type') or 'analysis'} {target_type}:{target_id}")
    with db_connection_sync_scope():
        return llm_gateway.budget_preflight(
            prompt,
            purpose="vkpi_analysis_worker",
            max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
            preferred_provider="google",
            cost_tag=LLM_BUDGET_SCOPE,
        )


def _google_allowed(preflight: dict[str, Any]) -> tuple[bool, str, float]:
    providers = preflight.get("providers") if isinstance(preflight.get("providers"), list) else []
    google = next((item for item in providers if item.get("provider") == "google"), {})
    reason = str(preflight.get("provider_gate_reason") or google.get("provider_gate_reason") or "provider_calls_blocked")
    return bool(google.get("provider_calls_allowed")), reason, float(google.get("estimated_cost_usd") or 0.0)


def _provider_allowed(preflight: dict[str, Any], provider_name: str) -> tuple[bool, str, float]:
    providers = preflight.get("providers") if isinstance(preflight.get("providers"), list) else []
    provider = next((item for item in providers if item.get("provider") == provider_name), {})
    reason = str(preflight.get("provider_gate_reason") or provider.get("provider_gate_reason") or "provider_calls_blocked")
    return bool(provider.get("provider_calls_allowed")), reason, float(provider.get("estimated_cost_usd") or 0.0)


def _log_budget_preflight_record_only(
    *,
    job: dict[str, Any],
    provider: str,
    allowed: bool,
    reason: str,
    estimated_cost: float,
    stage: str,
) -> None:
    if allowed:
        return
    logger.warning(
        "apify_jobs budget preflight would block, continuing record-only | job_id=%s provider=%s stage=%s reason=%s estimated_cost_usd=%s",
        job.get("id"),
        provider,
        stage,
        reason,
        estimated_cost,
    )


def _provider_budget_preflight(job: dict[str, Any], payload: dict[str, Any], provider: str) -> dict[str, Any]:
    target_type, target_id = _target(payload)
    prompt = str(payload.get("prompt") or f"{job.get('job_type') or 'analysis'} {target_type}:{target_id} {provider}")
    with db_connection_sync_scope():
        return llm_gateway.budget_preflight(
            prompt,
            purpose="vkpi_analysis_worker",
            max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
            preferred_provider=provider,
            cost_tag=LLM_BUDGET_SCOPE,
        )


def _load_video_evidence(conn: psycopg.Connection[Any], target_id: str) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT
              e.id,
              e.content_url,
              COALESCE(e.video_title, e.title, '') AS title,
              e.platform,
              e.view_count,
              e.like_count,
              e.comment_count,
              e.share_count,
              e.duration_seconds,
              e.publish_date,
              e.metrics_source,
              e.metrics_scraped_at,
              e.project_id,
              e.kol_pool_id,
              p.project_name,
              p.product_name,
              COALESCE(kp.handle, '') AS creator_handle,
              COALESCE(kp.display_name, '') AS creator_name,
              kp.followers,
              kp.avg_views,
              kp.engagement_rate
            FROM vkpi_kol_video_evidence e
            LEFT JOIN vkpi_kol_pool kp ON kp.id = e.kol_pool_id
            LEFT JOIN vkpi_projects p ON p.id = e.project_id
            WHERE e.id = %s
            LIMIT 1
            """,
            (_int_or_none(target_id) or 0,),
        )
        row = cur.fetchone()
    if not row:
        raise ValueError(f"video evidence not found: {target_id}")
    if not str(row.get("content_url") or "").strip():
        raise ValueError(f"video evidence has no content_url: {target_id}")
    return dict(row)


def _usage_count(metadata: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = metadata.get(key)
        if value is not None:
            parsed = _int_or_none(value)
            return parsed or 0
    return 0


def _gemini_input_cost_usd(model: str, metadata: dict[str, Any], tokens_in: int) -> float:
    model_key = str(model or "").lower()
    cached_tokens = _usage_count(metadata, "cached_content_token_count", "cachedContentTokenCount")
    uncached_tokens = max(0, tokens_in - cached_tokens)
    details = metadata.get("prompt_tokens_details")
    if not isinstance(details, list):
        details = []
    modality_counts: dict[str, int] = {}
    for item in details:
        if not isinstance(item, dict):
            continue
        modality = str(item.get("modality") or "").upper()
        modality_counts[modality] = modality_counts.get(modality, 0) + (_int_or_none(item.get("token_count")) or 0)
    if "gemini-3.1-pro" in model_key:
        rate = 4.0 if tokens_in > 200_000 else 2.0
        cached_rate = 0.40 if tokens_in > 200_000 else 0.20
        return ((uncached_tokens * rate) + (cached_tokens * cached_rate)) / 1_000_000
    if "gemini-3-flash" in model_key:
        audio = modality_counts.get("AUDIO", 0)
        return ((tokens_in - audio) * 0.50 + audio * 1.00) / 1_000_000
    if "gemini-2.5-flash" in model_key:
        audio = modality_counts.get("AUDIO", 0)
        return ((tokens_in - audio) * 0.30 + audio * 1.00) / 1_000_000
    config = llm_gateway.PROVIDER_CONFIG.get("google") or {}
    return tokens_in * float(config.get("input_cents_per_million") or 0) / 100_000_000


def _gemini_output_rate_usd_per_mtok(model: str, tokens_in: int) -> float:
    model_key = str(model or "").lower()
    if "gemini-3.1-pro" in model_key:
        return 18.0 if tokens_in > 200_000 else 12.0
    if "gemini-3-flash" in model_key:
        return 3.0
    if "gemini-2.5-flash" in model_key:
        return 2.50
    config = llm_gateway.PROVIDER_CONFIG.get("google") or {}
    return float(config.get("output_cents_per_million") or 0) / 100


def _gemini_cost(result: dict[str, Any], fallback_cost: float) -> tuple[float, str, int, int]:
    metadata = result.get("usage_metadata") if isinstance(result.get("usage_metadata"), dict) else {}
    tokens_in = _usage_count(metadata, "prompt_token_count", "promptTokenCount")
    tokens_out = _usage_count(metadata, "candidates_token_count", "candidatesTokenCount")
    tokens_out += _usage_count(metadata, "thoughts_token_count", "thoughtsTokenCount")
    if tokens_in or tokens_out:
        model = str(result.get("model") or result.get("method") or "")
        cost = _gemini_input_cost_usd(model, metadata, tokens_in)
        cost += tokens_out * _gemini_output_rate_usd_per_mtok(model, tokens_in) / 1_000_000
        return round(max(0.0, cost), 6), "gemini_usage_metadata_model_rate", tokens_in, tokens_out
    return round(max(0.0, float(fallback_cost or 0.0)), 6), "llm_gateway_budget_preflight", 0, 0


def _openai_cost(result: dict[str, Any], fallback_cost: float) -> tuple[float, str, int, int]:
    metadata = result.get("usage_metadata") if isinstance(result.get("usage_metadata"), dict) else {}
    tokens_in = _usage_count(metadata, "input_tokens", "prompt_tokens")
    tokens_out = _usage_count(metadata, "output_tokens", "completion_tokens")
    if tokens_in or tokens_out:
        model = str(result.get("model") or result.get("method") or "").lower()
        if "gpt-5.5" in model:
            cost = (tokens_in * 5.0 + tokens_out * 30.0) / 1_000_000
        else:
            config = llm_gateway.PROVIDER_CONFIG.get("openai") or {}
            input_cents = float(config.get("input_cents_per_million") or 0)
            output_cents = float(config.get("output_cents_per_million") or 0)
            cost = ((tokens_in * input_cents) + (tokens_out * output_cents)) / 100_000_000
        return round(max(0.0, cost), 6), "openai_usage_metadata_model_rate", tokens_in, tokens_out
    return round(max(0.0, float(fallback_cost or 0.0)), 6), "llm_gateway_budget_preflight", 0, 0


def _anthropic_cost(result: dict[str, Any], fallback_cost: float) -> tuple[float, str, int, int]:
    metadata = result.get("usage_metadata") if isinstance(result.get("usage_metadata"), dict) else {}
    tokens_in = _usage_count(metadata, "input_tokens")
    tokens_out = _usage_count(metadata, "output_tokens")
    if tokens_in or tokens_out:
        model = str(result.get("model") or result.get("method") or "").lower()
        if "opus" in model:
            cost = (tokens_in * 15.0 + tokens_out * 75.0) / 1_000_000
        else:
            config = llm_gateway.PROVIDER_CONFIG.get("anthropic") or {}
            input_cents = float(config.get("input_cents_per_million") or 0)
            output_cents = float(config.get("output_cents_per_million") or 0)
            cost = ((tokens_in * input_cents) + (tokens_out * output_cents)) / 100_000_000
        return round(max(0.0, cost), 6), "anthropic_usage_metadata_model_rate", tokens_in, tokens_out
    return round(max(0.0, float(fallback_cost or 0.0)), 6), "llm_gateway_budget_preflight", 0, 0


def _low_scores(scores: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for key, value in scores.items():
        if isinstance(value, (int, float)) and value <= 6:
            output.append({"dimension": key, "score": value})
    return output[:8]


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _rate(numerator: Any, denominator: Any) -> float | None:
    top = _int_or_none(numerator)
    bottom = _int_or_none(denominator)
    if top is None or bottom is None or bottom <= 0:
        return None
    return round(top / bottom, 6)


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iso_or_none(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _video_performance_context(evidence: dict[str, Any]) -> dict[str, Any]:
    views = _int_or_none(evidence.get("view_count"))
    return {
        "view_count": views,
        "like_count": _int_or_none(evidence.get("like_count")),
        "comment_count": _int_or_none(evidence.get("comment_count")),
        "share_count": _int_or_none(evidence.get("share_count")),
        "like_rate": _rate(evidence.get("like_count"), views),
        "comment_rate": _rate(evidence.get("comment_count"), views),
        "duration_seconds": _int_or_none(evidence.get("duration_seconds")),
        "publish_date": _iso_or_none(evidence.get("publish_date")),
        "metrics_source": evidence.get("metrics_source"),
        "metrics_scraped_at": _iso_or_none(evidence.get("metrics_scraped_at")),
        "account_baseline": {
            "followers": _int_or_none(evidence.get("followers")),
            "avg_views": _int_or_none(evidence.get("avg_views")),
            "engagement_rate": _float_or_none(evidence.get("engagement_rate")),
        },
        "relative_to_account_baseline_allowed": False,
        "relative_baseline_note": "followers/avg_views are often missing; use absolute performance only.",
    }


def _video_final_context(evidence: dict[str, Any]) -> dict[str, Any]:
    context = _video_performance_context(evidence)
    context["product_context"] = {
        "product_name": evidence.get("product_name"),
        "project_name": evidence.get("project_name"),
        "project_id": evidence.get("project_id"),
        "creator_handle": evidence.get("creator_handle"),
        "creator_name": evidence.get("creator_name"),
        "kol_pool_id": evidence.get("kol_pool_id"),
        "campaign_goal": "sell Viltrox lenses and validate lens proof; not to grow the KOL account",
    }
    return context


def _select_keyframe_requests(layer1: dict[str, Any], limit: int = 6) -> list[dict[str, str]]:
    timeline = layer1.get("scene_timeline") if isinstance(layer1.get("scene_timeline"), list) else []
    candidates = [
        {"timestamp": str(item.get("timestamp") or ""), "reason": str(item.get("what") or "")}
        for item in timeline
        if isinstance(item, dict) and item.get("timestamp")
    ]
    if not candidates:
        return [{"timestamp": ts, "reason": "fallback keyframe"} for ts in ["00:00", "00:15", "00:45", "01:30", "02:30", "04:30"]]
    if len(candidates) <= limit:
        return candidates
    indexes = [round(index * (len(candidates) - 1) / (limit - 1)) for index in range(limit)]
    output: list[dict[str, str]] = []
    seen: set[int] = set()
    for index in indexes:
        if index in seen:
            continue
        seen.add(index)
        output.append(candidates[index])
    return output[:limit]


def _download_youtube_for_keyframes(url: str, output_dir: str) -> dict[str, Any]:
    output: dict[str, Any] = {"success": False, "path": None, "error": None, "bytes": 0}
    out_tmpl = str(Path(output_dir) / "youtube_keyframes.%(ext)s")
    ytdlp_proxy = os.environ.get("YTDLP_PROXY", "").strip()
    cmd = [
        "yt-dlp",
        "--quiet",
        "--no-progress",
        "-f",
        "bv*[ext=mp4][height<=720]+ba[ext=m4a]/b[ext=mp4][height<=720]/best[height<=720]/best",
        "--merge-output-format",
        "mp4",
        "-o",
        out_tmpl,
    ]
    if ytdlp_proxy:
        cmd.extend(["--proxy", ytdlp_proxy])
    cmd.append(url)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        output["error"] = "youtube keyframe download timed out"
        return output
    if proc.returncode != 0:
        output["error"] = (proc.stderr or proc.stdout or "youtube keyframe download failed")[-500:]
        return output
    candidates = sorted(Path(output_dir).glob("youtube_keyframes.*"))
    if not candidates:
        output["error"] = "youtube keyframe download produced no file"
        return output
    video_path = candidates[0]
    if not video_path.exists() or video_path.stat().st_size <= 0:
        output["error"] = "youtube keyframe download produced empty file"
        return output
    output.update({"success": True, "path": str(video_path), "bytes": video_path.stat().st_size})
    return output


@contextmanager
def _extract_keyframes_for_qa(
    evidence: dict[str, Any],
    layer1: dict[str, Any],
    *,
    limit: int = 6,
    temp_prefix: str = "vkpi-keyframe-qa-video-",
) -> Iterator[dict[str, Any]]:
    keyframe_requests = _select_keyframe_requests(layer1, limit=limit)
    with tempfile.TemporaryDirectory(prefix=temp_prefix) as tmpdir:
        download = _download_youtube_for_keyframes(str(evidence.get("content_url") or ""), tmpdir)
        if not download.get("success") or not download.get("path"):
            raise RuntimeError(f"youtube_keyframe_download_failed: {download.get('error')}")
        with temporary_keyframes(str(download["path"]), keyframe_requests) as frames:
            if not frames:
                raise RuntimeError("keyframe extraction produced no frames")
            frame_meta = [
                {"timestamp": frame.get("timestamp"), "reason": frame.get("reason")}
                for frame in frames
            ]
            yield {
                "frames": frames,
                "frame_meta": frame_meta,
                "keyframe_requests": keyframe_requests,
                "download": download,
            }


@contextmanager
def _gemini_worker_overrides(payload: dict[str, Any]):
    model_override = str(payload.get("gemini_model") or WORKER_GEMINI_MODEL).strip()
    skip_subtitles = _truthy(
        payload.get("skip_subtitles", payload.get("gemini_skip_subtitles", os.environ.get("APIFY_WORKER_GEMINI_SKIP_SUBTITLES")))
    )
    with ExitStack() as stack:
        if skip_subtitles:
            stack.enter_context(patch.object(gemini_video_analyzer, "fetch_youtube_subtitles", lambda *_args, **_kwargs: ""))
        if model_override and getattr(gemini_video_analyzer, "gemini_client", None):
            original_generate = gemini_video_analyzer.gemini_client.models.generate_content

            def _forced_generate_content(*args: Any, **kwargs: Any):
                kwargs["model"] = model_override
                return original_generate(*args, **kwargs)

            stack.enter_context(patch.object(gemini_video_analyzer.gemini_client.models, "generate_content", _forced_generate_content))
        yield model_override


def _shape_gemini_result(
    *,
    job: dict[str, Any],
    evidence: dict[str, Any],
    raw: dict[str, Any],
    cost: float,
    cost_basis: str,
    preflight_cost: float,
    latency_ms: int,
    derive_method: str,
) -> dict[str, Any]:
    if derive_method in GEMINI_VIDEO_FINAL_DERIVE_METHODS:
        final = raw.get("video_analysis_final_v1") if isinstance(raw.get("video_analysis_final_v1"), dict) else {}
        model_name = str(raw.get("model") or raw.get("method") or "gemini_video")
        segments = raw.get("cost_segments") if isinstance(raw.get("cost_segments"), list) else None
        shaped: dict[str, Any] = {
            "schema_version": "video_analysis_final_v1",
            "mock": False,
            "analysis_method": derive_method,
            "job_id": job.get("id"),
            "target_type": "video",
            "target_id": str(evidence.get("id")),
            "source": {
                "url": evidence.get("content_url"),
                "title": evidence.get("title"),
                "platform": evidence.get("platform"),
                "creator_handle": evidence.get("creator_handle"),
                "creator_name": evidence.get("creator_name"),
                "project_id": evidence.get("project_id"),
                "project_name": evidence.get("project_name"),
                "product_name": evidence.get("product_name"),
                "kol_pool_id": evidence.get("kol_pool_id"),
            },
            "performance_metrics": _video_performance_context(evidence),
            "layer1_visual_content": final.get("layer1_visual_content") or {},
            "layer2_viewer_emotion": final.get("layer2_viewer_emotion") or {},
            "layer3_three_values": final.get("layer3_three_values") or {},
            "layer4_attribution": final.get("layer4_attribution") or {},
            "layer5_recommendations": final.get("layer5_recommendations") or {},
            "layer6_flags_and_scores": final.get("layer6_flags_and_scores") or {},
            "cost": {
                "recorded_cost_usd": cost,
                "cost_basis": cost_basis,
                "preflight_estimated_cost_usd": preflight_cost,
                "segments": segments
                or [
                    {
                        "stage": "single_pass",
                        "provider": "gemini",
                        "model": model_name,
                        "cost_usd": cost,
                    }
                ],
                "usage_metadata": raw.get("usage_metadata") if isinstance(raw.get("usage_metadata"), dict) else {},
                "latency_ms": latency_ms,
            },
            "raw_gemini_video": raw,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        if isinstance(raw.get("final_v1_keyframe_qa"), dict):
            shaped["keyframe_qa"] = raw.get("final_v1_keyframe_qa") or {}
            shaped["qa_pass"] = raw.get("qa_pass")
            shaped["frame_extraction"] = raw.get("frame_extraction") if isinstance(raw.get("frame_extraction"), dict) else {}
            shaped["final_v1_pass"] = raw.get("final_v1_pass") if isinstance(raw.get("final_v1_pass"), dict) else {}
        return shaped
    if derive_method in GEMINI_VIDEO_V2_DERIVE_METHODS:
        v2 = raw.get("video_analysis_v2") if isinstance(raw.get("video_analysis_v2"), dict) else {}
        layer3 = dict(v2.get("layer3_integrated_judgment") or {})
        layer3["performance_metrics"] = _video_performance_context(evidence)
        model_name = str(raw.get("model") or raw.get("method") or "gemini_video")
        segments = raw.get("cost_segments") if isinstance(raw.get("cost_segments"), list) else None
        return {
            "schema_version": "video_analysis_v2",
            "mock": False,
            "analysis_method": derive_method,
            "job_id": job.get("id"),
            "target_type": "video",
            "target_id": str(evidence.get("id")),
            "source": {
                "url": evidence.get("content_url"),
                "title": evidence.get("title"),
                "platform": evidence.get("platform"),
                "creator_handle": evidence.get("creator_handle"),
                "creator_name": evidence.get("creator_name"),
                "project_id": evidence.get("project_id"),
                "project_name": evidence.get("project_name"),
                "product_name": evidence.get("product_name"),
                "kol_pool_id": evidence.get("kol_pool_id"),
            },
            "layer1_visual_content": v2.get("layer1_visual_content") or {},
            "layer2_video_scores": v2.get("layer2_video_scores") or {},
            "layer3_integrated_judgment": layer3,
            "cost": {
                "recorded_cost_usd": cost,
                "cost_basis": cost_basis,
                "preflight_estimated_cost_usd": preflight_cost,
                "segments": segments
                or [
                    {
                        "stage": "single_pass",
                        "provider": "gemini",
                        "model": model_name,
                        "cost_usd": cost,
                    }
                ],
                "usage_metadata": raw.get("usage_metadata") if isinstance(raw.get("usage_metadata"), dict) else {},
                "latency_ms": latency_ms,
            },
            "raw_gemini_video": raw,
            "frame_extraction": raw.get("frame_extraction") if isinstance(raw.get("frame_extraction"), dict) else {},
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    quality_scores = raw.get("quality_scores") if isinstance(raw.get("quality_scores"), dict) else {}
    return {
        "mock": False,
        "analysis_method": "gemini",
        "job_id": job.get("id"),
        "target_type": "video",
        "target_id": str(evidence.get("id")),
        "source": {
            "url": evidence.get("content_url"),
            "title": evidence.get("title"),
            "platform": evidence.get("platform"),
            "creator_handle": evidence.get("creator_handle"),
            "project_id": evidence.get("project_id"),
            "kol_pool_id": evidence.get("kol_pool_id"),
        },
        "platform_algorithm_rules": {
            "content_genre": raw.get("content_genre"),
            "target_audience": raw.get("target_audience"),
            "hook_analysis": raw.get("hook_analysis"),
            "marketing_potential": raw.get("marketing_potential"),
            "brand_integration_depth": raw.get("brand_integration_depth"),
            "community_value": raw.get("community_value"),
            "quality_scores": quality_scores,
        },
        "weak_performance_reasons": {
            "quality_summary": raw.get("quality_summary"),
            "vertical_quality_notes": raw.get("vertical_quality_notes"),
            "marketing_notes": raw.get("marketing_notes"),
            "tech_floor": raw.get("tech_floor"),
            "low_scores": _low_scores(quality_scores),
        },
        "improvement_suggestions": raw.get("improvements") if isinstance(raw.get("improvements"), list) else [],
        "raw_gemini_video": raw,
        "cost": {
            "recorded_cost_usd": cost,
            "cost_basis": cost_basis,
            "preflight_estimated_cost_usd": preflight_cost,
            "usage_metadata": raw.get("usage_metadata") if isinstance(raw.get("usage_metadata"), dict) else {},
            "latency_ms": latency_ms,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _record_gemini_cost(
    *,
    job: dict[str, Any],
    payload: dict[str, Any],
    raw: dict[str, Any],
    cost: float,
    cost_basis: str,
    tokens_in: int,
    tokens_out: int,
    latency_ms: int,
    preflight_cost: float,
) -> dict[str, Any]:
    triggered_by = payload.get("triggered_by_user_id", payload.get("user_id"))
    with db_connection_sync_scope():
        return budget_guard.record_cost(
            scope=LLM_BUDGET_SCOPE,
            cron_task="vkpi_analysis_worker",
            ai_provider="gemini",
            model_name=str(raw.get("model") or raw.get("method") or "gemini_video"),
            cost_usd=cost,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            metadata={
                "status": "success" if raw.get("analyzed") else "provider_error",
                "job_id": job.get("id"),
                "target_type": payload.get("target_type"),
                "target_id": str(payload.get("target_id") or ""),
                "cost_basis": cost_basis,
                "preflight_estimated_cost_usd": preflight_cost,
                "latency_ms": latency_ms,
                "triggered_by_user_id": triggered_by,
                "error": _redact_sensitive_text(raw.get("error") or ""),
            },
            extra_scopes=["monthly_total", "single_call", "provider:gemini"],
        )


def _record_openai_cost(
    *,
    job: dict[str, Any],
    payload: dict[str, Any],
    raw: dict[str, Any],
    cost: float,
    cost_basis: str,
    tokens_in: int,
    tokens_out: int,
    latency_ms: int,
    preflight_cost: float,
) -> dict[str, Any]:
    triggered_by = payload.get("triggered_by_user_id", payload.get("user_id"))
    with db_connection_sync_scope():
        return budget_guard.record_cost(
            scope=LLM_BUDGET_SCOPE,
            cron_task="vkpi_analysis_worker",
            ai_provider="openai",
            model_name=str(raw.get("model") or raw.get("method") or "gpt-5.5"),
            cost_usd=cost,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            metadata={
                "status": "success" if raw.get("analyzed") else "provider_error",
                "job_id": job.get("id"),
                "target_type": payload.get("target_type"),
                "target_id": str(payload.get("target_id") or ""),
                "cost_basis": cost_basis,
                "preflight_estimated_cost_usd": preflight_cost,
                "latency_ms": latency_ms,
                "triggered_by_user_id": triggered_by,
                "error": _redact_sensitive_text(raw.get("error") or ""),
            },
            extra_scopes=["monthly_total", "single_call", "provider:openai"],
        )


def _record_anthropic_cost(
    *,
    job: dict[str, Any],
    payload: dict[str, Any],
    raw: dict[str, Any],
    cost: float,
    cost_basis: str,
    tokens_in: int,
    tokens_out: int,
    latency_ms: int,
    preflight_cost: float,
) -> dict[str, Any]:
    triggered_by = payload.get("triggered_by_user_id", payload.get("user_id"))
    with db_connection_sync_scope():
        return budget_guard.record_cost(
            scope=LLM_BUDGET_SCOPE,
            cron_task="vkpi_analysis_worker",
            ai_provider="anthropic",
            model_name=str(raw.get("model") or raw.get("method") or "claude-opus-4-8"),
            cost_usd=cost,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            metadata={
                "status": "success" if raw.get("analyzed") else "provider_error",
                "job_id": job.get("id"),
                "target_type": payload.get("target_type"),
                "target_id": str(payload.get("target_id") or ""),
                "cost_basis": cost_basis,
                "preflight_estimated_cost_usd": preflight_cost,
                "latency_ms": latency_ms,
                "triggered_by_user_id": triggered_by,
                "error": _redact_sensitive_text(raw.get("error") or ""),
            },
            extra_scopes=["monthly_total", "single_call", "provider:anthropic"],
        )


def _write_gemini_cache(
    conn: psycopg.Connection[Any],
    *,
    job: dict[str, Any],
    payload: dict[str, Any],
    evidence: dict[str, Any],
    raw: dict[str, Any],
    cost: float,
    cost_basis: str,
    preflight_cost: float,
    latency_ms: int,
    derive_method: str,
) -> None:
    target_type, target_id = _target(payload)
    triggered_by = payload.get("triggered_by_user_id", payload.get("user_id"))
    shaped = _shape_gemini_result(
        job=job,
        evidence=evidence,
        raw=raw,
        cost=cost,
        cost_basis=cost_basis,
        preflight_cost=preflight_cost,
        latency_ms=latency_ms,
        derive_method=derive_method,
    )
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO vkpi_analysis_cache (
                  target_type, target_id, model, derive_method, result, cost,
                  status, triggered_by_user_id, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, 'ready', %s, NOW(), NOW())
                ON CONFLICT (target_type, target_id, derive_method)
                DO UPDATE SET
                  model = EXCLUDED.model,
                  result = EXCLUDED.result,
                  cost = EXCLUDED.cost,
                  status = 'ready',
                  triggered_by_user_id = EXCLUDED.triggered_by_user_id,
                  updated_at = NOW()
                RETURNING id
                """,
                (
                    target_type,
                    target_id,
                    str(raw.get("model") or raw.get("method") or "gemini_video"),
                    derive_method,
                    _json(shaped),
                    cost,
                    _int_or_none(triggered_by),
                ),
            )
            cache_row = cur.fetchone()
            cache_id = int(cache_row[0]) if cache_row else None
            cur.execute(
                """
                UPDATE apify_jobs
                SET status='done',
                    last_error=NULL,
                    last_error_category=NULL,
                    next_retry_at=NULL,
                    updated_at=NOW()
                WHERE id=%s
                """,
                (job["id"],),
            )
    deep_result = _sync_deep_analysis_result_from_cache(
        conn,
        cache_id=cache_id,
        derive_method=derive_method,
        job_id=int(job["id"]),
    )
    account_extract_job = _enqueue_account_dossier_extract_after_final_v1(
        conn,
        job_id=int(job["id"]),
        deep_result=deep_result,
    )
    analysis_summary = _search_session_analysis_summary_from_result(
        cache_id=cache_id,
        derive_method=derive_method,
        target_type=target_type,
        target_id=target_id,
        evidence=evidence,
        result=shaped,
        cost=cost,
    )
    if analysis_summary and deep_result:
        analysis_summary["deep_result"] = deep_result
    if analysis_summary and account_extract_job:
        analysis_summary["account_dossier_extract_job"] = account_extract_job
    _sync_search_session_job(
        conn,
        int(job["id"]),
        raw_status="done",
        analysis_summary=analysis_summary,
    )


def _process_gemini_video_final_v1_keyframe_qa(
    conn: psycopg.Connection[Any],
    job: dict[str, Any],
    payload: dict[str, Any],
    evidence: dict[str, Any],
    preflight_cost: float,
) -> None:
    if _platform_from_content_url(str(evidence.get("content_url") or "")) != "youtube":
        raise RuntimeError("video_analysis_final_v1_keyframe_qa currently supports YouTube only")

    qa_model = str(payload.get("final_v1_qa_model") or FINAL_V1_KEYFRAME_QA_MODEL).strip() or FINAL_V1_KEYFRAME_QA_MODEL
    qa_preflight = _provider_budget_preflight(
        job,
        {
            **payload,
            "prompt": f"final_v1 keyframe QA video:{evidence.get('id')} model:{qa_model}",
        },
        "google",
    )
    qa_allowed, qa_reason, qa_estimated_cost = _provider_allowed(qa_preflight, "google")
    _log_budget_preflight_record_only(
        job=job,
        provider="google",
        allowed=qa_allowed,
        reason=qa_reason,
        estimated_cost=qa_estimated_cost,
        stage="keyframe_qa",
    )
    if not qa_allowed:
        # 护栏② enforce:撞 cap 不再继续——_block_job 终态(对齐 cron fallback_action=block_job)
        _block_job(
            conn,
            int(job["id"]),
            "budget_guard_blocked",
            {
                "provider": "google",
                "stage": "keyframe_qa",
                "reason_detail": qa_reason,
                "estimated_cost_usd": qa_estimated_cost,
            },
        )
        return

    started = time.monotonic()
    analysis_context = _video_final_context(evidence)
    analyzer_payload = {
        **payload,
        "gemini_final_v1_models": gemini_video_analyzer.final_v1_gemini_models(
            payload.get("gemini_final_v1_models") or FINAL_V1_GEMINI_MODELS
        ),
    }
    visual_raw = _run_gemini_analyzer_with_timeout(
        {
            **analyzer_payload,
            "mode": "youtube",
            "url": str(evidence.get("content_url") or ""),
            "title": str(evidence.get("title") or ""),
            "creator_handle": str(evidence.get("creator_handle") or ""),
            "schema_version": "final_v1",
            "performance_context": analysis_context,
        },
        job_id=job.get("id"),
        target_id=str(evidence.get("id")),
        platform="youtube",
    )
    visual_cost, visual_basis, visual_tokens_in, visual_tokens_out = _gemini_cost(visual_raw, preflight_cost)
    _record_gemini_cost(
        job=job,
        payload=payload,
        raw=visual_raw,
        cost=visual_cost,
        cost_basis=visual_basis,
        tokens_in=visual_tokens_in,
        tokens_out=visual_tokens_out,
        latency_ms=0,
        preflight_cost=preflight_cost,
    )
    if not visual_raw.get("analyzed"):
        raw_error = str(visual_raw.get("error") or "not_analyzed")
        if raw_error == "gemini_call_timeout":
            raise RuntimeError("gemini_call_timeout")
        raise RuntimeError(f"Gemini final_v1 pass failed: {raw_error}")

    final_v1 = visual_raw.get("video_analysis_final_v1") if isinstance(visual_raw.get("video_analysis_final_v1"), dict) else {}
    layer1 = final_v1.get("layer1_visual_content") if isinstance(final_v1.get("layer1_visual_content"), dict) else {}
    with _extract_keyframes_for_qa(evidence, layer1, limit=6, temp_prefix="vkpi-final-v1-qa-video-") as qa_frames:
        keyframe_requests = qa_frames["keyframe_requests"]
        frame_meta = qa_frames["frame_meta"]
        download = qa_frames["download"]
        qa_raw = asyncio.run(
            gemini_video_analyzer.analyze_final_v1_keyframe_qa(
                final_v1_result=final_v1,
                keyframes=qa_frames["frames"],
                title=str(evidence.get("title") or ""),
                performance_context=analysis_context,
                model_name=qa_model,
            )
        )

    qa_cost, qa_basis, qa_tokens_in, qa_tokens_out = _gemini_cost(qa_raw, qa_estimated_cost)
    _record_gemini_cost(
        job=job,
        payload=payload,
        raw=qa_raw,
        cost=qa_cost,
        cost_basis=qa_basis,
        tokens_in=qa_tokens_in,
        tokens_out=qa_tokens_out,
        latency_ms=0,
        preflight_cost=qa_estimated_cost,
    )
    if not qa_raw.get("analyzed"):
        raise RuntimeError(f"Gemini final_v1 keyframe QA failed: {qa_raw.get('error') or 'not_analyzed'}")

    latency_ms = int((time.monotonic() - started) * 1000)
    total_cost = round(visual_cost + qa_cost, 6)
    visual_model = str(visual_raw.get("model") or visual_raw.get("method") or "final_v1_gemini")
    combined_raw = {
        **visual_raw,
        "method": "final_v1_flash_keyframe_qa",
        "model": f"{visual_model}+{qa_model}",
        "final_v1_pass": visual_raw,
        "final_v1_keyframe_qa": qa_raw.get("final_v1_keyframe_qa") if isinstance(qa_raw.get("final_v1_keyframe_qa"), dict) else {},
        "qa_pass": qa_raw.get("qa_pass"),
        "qa_method": qa_raw.get("method"),
        "qa_model": qa_raw.get("model") or qa_model,
        "qa_usage_metadata": qa_raw.get("usage_metadata") if isinstance(qa_raw.get("usage_metadata"), dict) else {},
        "cost_segments": [
            {
                "stage": "final_v1_video_pass",
                "provider": "gemini",
                "model": visual_model,
                "cost_usd": visual_cost,
                "cost_basis": visual_basis,
                "usage_metadata": visual_raw.get("usage_metadata") if isinstance(visual_raw.get("usage_metadata"), dict) else {},
            },
            {
                "stage": "keyframe_qa_pass",
                "provider": "gemini",
                "model": qa_model,
                "cost_usd": qa_cost,
                "cost_basis": qa_basis,
                "usage_metadata": qa_raw.get("usage_metadata") if isinstance(qa_raw.get("usage_metadata"), dict) else {},
            },
        ],
        "frame_extraction": {
            "requested": keyframe_requests,
            "extracted_count": len(frame_meta),
            "frames": frame_meta,
            "download_bytes": int(download.get("bytes") or 0),
            "temporary_files_cleaned": True,
        },
    }
    _write_gemini_cache(
        conn,
        job=job,
        payload=payload,
        evidence=evidence,
        raw=combined_raw,
        cost=total_cost,
        cost_basis="gemini_final_v1_keyframe_qa_segmented_model_rate",
        preflight_cost=preflight_cost + qa_estimated_cost,
        latency_ms=latency_ms,
        derive_method=FINAL_V1_KEYFRAME_QA_DERIVE_METHOD,
    )


def _process_gemini_video_flash_pro_judge(
    conn: psycopg.Connection[Any],
    job: dict[str, Any],
    payload: dict[str, Any],
    evidence: dict[str, Any],
    preflight_cost: float,
) -> None:
    if _platform_from_content_url(str(evidence.get("content_url") or "")) != "youtube":
        raise RuntimeError("gemini_video_v2_flash_pro_judge currently supports YouTube only")
    started = time.monotonic()
    performance = _video_performance_context(evidence)
    visual_payload = {**payload, "gemini_model": "gemini-3-flash-preview"}
    with _gemini_worker_overrides(visual_payload):
        visual_raw = asyncio.run(
            gemini_video_analyzer.analyze_youtube_with_gemini(
                str(evidence.get("content_url") or ""),
                str(evidence.get("title") or ""),
                str(evidence.get("creator_handle") or ""),
                schema_version="v2",
                performance_context=performance,
            )
        )
    if visual_raw.get("analyzed"):
        visual_raw["model"] = "gemini-3-flash-preview"
        visual_raw["method"] = "gemini_direct_gemini-3-flash-preview"
    visual_cost, visual_basis, visual_tokens_in, visual_tokens_out = _gemini_cost(visual_raw, preflight_cost)
    _record_gemini_cost(
        job=job,
        payload=payload,
        raw=visual_raw,
        cost=visual_cost,
        cost_basis=visual_basis,
        tokens_in=visual_tokens_in,
        tokens_out=visual_tokens_out,
        latency_ms=0,
        preflight_cost=preflight_cost,
    )
    if not visual_raw.get("analyzed"):
        raise RuntimeError(f"Gemini visual pass failed: {visual_raw.get('error') or 'not_analyzed'}")

    v2 = visual_raw.get("video_analysis_v2") if isinstance(visual_raw.get("video_analysis_v2"), dict) else {}
    layer1 = v2.get("layer1_visual_content") if isinstance(v2.get("layer1_visual_content"), dict) else {}
    with _extract_keyframes_for_qa(evidence, layer1, limit=6, temp_prefix="vkpi-scheme2-video-") as qa_frames:
        keyframe_requests = qa_frames["keyframe_requests"]
        frame_meta = qa_frames["frame_meta"]
        download = qa_frames["download"]
        judgment_raw = asyncio.run(
            gemini_video_analyzer.analyze_v2_judgment_with_keyframes(
                layer1_visual_content=layer1,
                keyframes=qa_frames["frames"],
                title=str(evidence.get("title") or ""),
                performance_context=performance,
                model_name="gemini-3.1-pro-preview",
            )
        )
    judgment_cost, judgment_basis, judgment_tokens_in, judgment_tokens_out = _gemini_cost(judgment_raw, preflight_cost)
    _record_gemini_cost(
        job=job,
        payload=payload,
        raw=judgment_raw,
        cost=judgment_cost,
        cost_basis=judgment_basis,
        tokens_in=judgment_tokens_in,
        tokens_out=judgment_tokens_out,
        latency_ms=0,
        preflight_cost=preflight_cost,
    )
    if not judgment_raw.get("analyzed"):
        raise RuntimeError(f"Gemini keyframe judgment failed: {judgment_raw.get('error') or 'not_analyzed'}")

    latency_ms = int((time.monotonic() - started) * 1000)
    total_cost = round(visual_cost + judgment_cost, 6)
    raw = {
        **judgment_raw,
        "method": "gemini_flash_pro_judge",
        "model": "gemini-3-flash-preview+gemini-3.1-pro-preview",
        "visual_pass": visual_raw,
        "cost_segments": [
            {
                "stage": "visual_video_pass",
                "provider": "gemini",
                "model": "gemini-3-flash-preview",
                "cost_usd": visual_cost,
                "cost_basis": visual_basis,
                "usage_metadata": visual_raw.get("usage_metadata") if isinstance(visual_raw.get("usage_metadata"), dict) else {},
            },
            {
                "stage": "judgment_pass",
                "provider": "gemini",
                "model": "gemini-3.1-pro-preview",
                "cost_usd": judgment_cost,
                "cost_basis": judgment_basis,
                "usage_metadata": judgment_raw.get("usage_metadata") if isinstance(judgment_raw.get("usage_metadata"), dict) else {},
            },
        ],
        "frame_extraction": {
            "requested": keyframe_requests,
            "extracted_count": len(frame_meta),
            "frames": frame_meta,
            "download_bytes": int(download.get("bytes") or 0),
            "temporary_files_cleaned": True,
        },
    }
    _write_gemini_cache(
        conn,
        job=job,
        payload=payload,
        evidence=evidence,
        raw=raw,
        cost=total_cost,
        cost_basis="gemini_usage_metadata_segmented_model_rate",
        preflight_cost=preflight_cost,
        latency_ms=latency_ms,
        derive_method="gemini_video_v2_flash_pro_judge",
    )


def _process_gemini_video_flash_gpt55_judge(
    conn: psycopg.Connection[Any],
    job: dict[str, Any],
    payload: dict[str, Any],
    evidence: dict[str, Any],
    preflight_cost: float,
) -> None:
    if _platform_from_content_url(str(evidence.get("content_url") or "")) != "youtube":
        raise RuntimeError("gemini_video_v2_flash_gpt55_judge currently supports YouTube only")
    openai_preflight = _provider_budget_preflight(job, payload, "openai")
    openai_allowed, openai_reason, openai_estimated_cost = _provider_allowed(openai_preflight, "openai")
    _log_budget_preflight_record_only(
        job=job,
        provider="openai",
        allowed=openai_allowed,
        reason=openai_reason,
        estimated_cost=openai_estimated_cost,
        stage="openai_keyframe_judge",
    )
    if not openai_allowed:
        _block_job(
            conn,
            int(job["id"]),
            "budget_guard_blocked",
            {
                "provider": "openai",
                "stage": "openai_keyframe_judge",
                "reason_detail": openai_reason,
                "estimated_cost_usd": openai_estimated_cost,
            },
        )
        return

    started = time.monotonic()
    performance = _video_performance_context(evidence)
    visual_payload = {**payload, "gemini_model": "gemini-3-flash-preview"}
    with _gemini_worker_overrides(visual_payload):
        visual_raw = asyncio.run(
            gemini_video_analyzer.analyze_youtube_with_gemini(
                str(evidence.get("content_url") or ""),
                str(evidence.get("title") or ""),
                str(evidence.get("creator_handle") or ""),
                schema_version="v2",
                performance_context=performance,
            )
        )
    if visual_raw.get("analyzed"):
        visual_raw["model"] = "gemini-3-flash-preview"
        visual_raw["method"] = "gemini_direct_gemini-3-flash-preview"
    visual_cost, visual_basis, visual_tokens_in, visual_tokens_out = _gemini_cost(visual_raw, preflight_cost)
    _record_gemini_cost(
        job=job,
        payload=payload,
        raw=visual_raw,
        cost=visual_cost,
        cost_basis=visual_basis,
        tokens_in=visual_tokens_in,
        tokens_out=visual_tokens_out,
        latency_ms=0,
        preflight_cost=preflight_cost,
    )
    if not visual_raw.get("analyzed"):
        raise RuntimeError(f"Gemini visual pass failed: {visual_raw.get('error') or 'not_analyzed'}")

    v2 = visual_raw.get("video_analysis_v2") if isinstance(visual_raw.get("video_analysis_v2"), dict) else {}
    layer1 = v2.get("layer1_visual_content") if isinstance(v2.get("layer1_visual_content"), dict) else {}
    with _extract_keyframes_for_qa(evidence, layer1, limit=6, temp_prefix="vkpi-scheme3a-video-") as qa_frames:
        keyframe_requests = qa_frames["keyframe_requests"]
        frame_meta = qa_frames["frame_meta"]
        download = qa_frames["download"]
        judgment_raw = asyncio.run(
            gemini_video_analyzer.analyze_v2_judgment_with_openai_keyframes(
                layer1_visual_content=layer1,
                keyframes=qa_frames["frames"],
                title=str(evidence.get("title") or ""),
                performance_context=performance,
                model_name="gpt-5.5",
            )
        )
    judgment_cost, judgment_basis, judgment_tokens_in, judgment_tokens_out = _openai_cost(judgment_raw, openai_estimated_cost)
    _record_openai_cost(
        job=job,
        payload=payload,
        raw=judgment_raw,
        cost=judgment_cost,
        cost_basis=judgment_basis,
        tokens_in=judgment_tokens_in,
        tokens_out=judgment_tokens_out,
        latency_ms=0,
        preflight_cost=openai_estimated_cost,
    )
    if not judgment_raw.get("analyzed"):
        raise RuntimeError(f"OpenAI keyframe judgment failed: {judgment_raw.get('error') or 'not_analyzed'}")

    latency_ms = int((time.monotonic() - started) * 1000)
    total_cost = round(visual_cost + judgment_cost, 6)
    raw = {
        **judgment_raw,
        "method": "gemini_flash_gpt55_judge",
        "model": "gemini-3-flash-preview+gpt-5.5",
        "visual_pass": visual_raw,
        "cost_segments": [
            {
                "stage": "visual_video_pass",
                "provider": "gemini",
                "model": "gemini-3-flash-preview",
                "cost_usd": visual_cost,
                "cost_basis": visual_basis,
                "usage_metadata": visual_raw.get("usage_metadata") if isinstance(visual_raw.get("usage_metadata"), dict) else {},
            },
            {
                "stage": "judgment_pass",
                "provider": "openai",
                "model": "gpt-5.5",
                "cost_usd": judgment_cost,
                "cost_basis": judgment_basis,
                "usage_metadata": judgment_raw.get("usage_metadata") if isinstance(judgment_raw.get("usage_metadata"), dict) else {},
            },
        ],
        "frame_extraction": {
            "requested": keyframe_requests,
            "extracted_count": len(frame_meta),
            "frames": frame_meta,
            "download_bytes": int(download.get("bytes") or 0),
            "temporary_files_cleaned": True,
        },
    }
    _write_gemini_cache(
        conn,
        job=job,
        payload=payload,
        evidence=evidence,
        raw=raw,
        cost=total_cost,
        cost_basis="gemini_openai_usage_metadata_segmented_model_rate",
        preflight_cost=preflight_cost + openai_estimated_cost,
        latency_ms=latency_ms,
        derive_method="gemini_video_v2_flash_gpt55_judge",
    )


def _process_gemini_video_flash_claude_judge(
    conn: psycopg.Connection[Any],
    job: dict[str, Any],
    payload: dict[str, Any],
    evidence: dict[str, Any],
    preflight_cost: float,
) -> None:
    if _platform_from_content_url(str(evidence.get("content_url") or "")) != "youtube":
        raise RuntimeError("gemini_video_v2_flash_claude_judge currently supports YouTube only")
    anthropic_preflight = _provider_budget_preflight(job, payload, "anthropic")
    anthropic_allowed, anthropic_reason, anthropic_estimated_cost = _provider_allowed(anthropic_preflight, "anthropic")
    _log_budget_preflight_record_only(
        job=job,
        provider="anthropic",
        allowed=anthropic_allowed,
        reason=anthropic_reason,
        estimated_cost=anthropic_estimated_cost,
        stage="anthropic_keyframe_judge",
    )
    if not anthropic_allowed:
        _block_job(
            conn,
            int(job["id"]),
            "budget_guard_blocked",
            {
                "provider": "anthropic",
                "stage": "anthropic_keyframe_judge",
                "reason_detail": anthropic_reason,
                "estimated_cost_usd": anthropic_estimated_cost,
            },
        )
        return

    started = time.monotonic()
    performance = _video_performance_context(evidence)
    visual_payload = {**payload, "gemini_model": "gemini-3-flash-preview"}
    with _gemini_worker_overrides(visual_payload):
        visual_raw = asyncio.run(
            gemini_video_analyzer.analyze_youtube_with_gemini(
                str(evidence.get("content_url") or ""),
                str(evidence.get("title") or ""),
                str(evidence.get("creator_handle") or ""),
                schema_version="v2",
                performance_context=performance,
            )
        )
    if visual_raw.get("analyzed"):
        visual_raw["model"] = "gemini-3-flash-preview"
        visual_raw["method"] = "gemini_direct_gemini-3-flash-preview"
    visual_cost, visual_basis, visual_tokens_in, visual_tokens_out = _gemini_cost(visual_raw, preflight_cost)
    _record_gemini_cost(
        job=job,
        payload=payload,
        raw=visual_raw,
        cost=visual_cost,
        cost_basis=visual_basis,
        tokens_in=visual_tokens_in,
        tokens_out=visual_tokens_out,
        latency_ms=0,
        preflight_cost=preflight_cost,
    )
    if not visual_raw.get("analyzed"):
        raise RuntimeError(f"Gemini visual pass failed: {visual_raw.get('error') or 'not_analyzed'}")

    v2 = visual_raw.get("video_analysis_v2") if isinstance(visual_raw.get("video_analysis_v2"), dict) else {}
    layer1 = v2.get("layer1_visual_content") if isinstance(v2.get("layer1_visual_content"), dict) else {}
    with _extract_keyframes_for_qa(evidence, layer1, limit=6, temp_prefix="vkpi-scheme3b-video-") as qa_frames:
        keyframe_requests = qa_frames["keyframe_requests"]
        frame_meta = qa_frames["frame_meta"]
        download = qa_frames["download"]
        judgment_raw = asyncio.run(
            gemini_video_analyzer.analyze_v2_judgment_with_anthropic_keyframes(
                layer1_visual_content=layer1,
                keyframes=qa_frames["frames"],
                title=str(evidence.get("title") or ""),
                performance_context=performance,
                model_name="claude-opus-4-8",
            )
        )
    judgment_cost, judgment_basis, judgment_tokens_in, judgment_tokens_out = _anthropic_cost(judgment_raw, anthropic_estimated_cost)
    _record_anthropic_cost(
        job=job,
        payload=payload,
        raw=judgment_raw,
        cost=judgment_cost,
        cost_basis=judgment_basis,
        tokens_in=judgment_tokens_in,
        tokens_out=judgment_tokens_out,
        latency_ms=0,
        preflight_cost=anthropic_estimated_cost,
    )
    if not judgment_raw.get("analyzed"):
        raise RuntimeError(f"Anthropic keyframe judgment failed: {judgment_raw.get('error') or 'not_analyzed'}")

    latency_ms = int((time.monotonic() - started) * 1000)
    total_cost = round(visual_cost + judgment_cost, 6)
    raw = {
        **judgment_raw,
        "method": "gemini_flash_claude_judge",
        "model": "gemini-3-flash-preview+claude-opus-4-8",
        "visual_pass": visual_raw,
        "cost_segments": [
            {
                "stage": "visual_video_pass",
                "provider": "gemini",
                "model": "gemini-3-flash-preview",
                "cost_usd": visual_cost,
                "cost_basis": visual_basis,
                "usage_metadata": visual_raw.get("usage_metadata") if isinstance(visual_raw.get("usage_metadata"), dict) else {},
            },
            {
                "stage": "judgment_pass",
                "provider": "anthropic",
                "model": "claude-opus-4-8",
                "cost_usd": judgment_cost,
                "cost_basis": judgment_basis,
                "usage_metadata": judgment_raw.get("usage_metadata") if isinstance(judgment_raw.get("usage_metadata"), dict) else {},
            },
        ],
        "frame_extraction": {
            "requested": keyframe_requests,
            "extracted_count": len(frame_meta),
            "frames": frame_meta,
            "download_bytes": int(download.get("bytes") or 0),
            "temporary_files_cleaned": True,
        },
    }
    _write_gemini_cache(
        conn,
        job=job,
        payload=payload,
        evidence=evidence,
        raw=raw,
        cost=total_cost,
        cost_basis="gemini_anthropic_usage_metadata_segmented_model_rate",
        preflight_cost=preflight_cost + anthropic_estimated_cost,
        latency_ms=latency_ms,
        derive_method="gemini_video_v2_flash_claude_judge",
    )


def _process_gemini_video(
    conn: psycopg.Connection[Any],
    job: dict[str, Any],
    payload: dict[str, Any],
    preflight_cost: float,
) -> None:
    target_type, target_id = _target(payload)
    derive_method = _derive_method(payload)
    if target_type != "video":
        _block_job(conn, int(job["id"]), "unsupported_gemini_target_type", {"target_type": target_type})
        return
    evidence = _load_video_evidence(conn, target_id)
    platform = _platform_from_content_url(str(evidence.get("content_url") or ""))
    if platform == "unsupported":
        _block_job(conn, int(job["id"]), "unsupported_platform", {"source_url_host": _url_host(str(evidence.get("content_url") or ""))})
        return
    if (
        platform in {"instagram", "tiktok"}
        and derive_method not in GEMINI_VIDEO_V2_DERIVE_METHODS
        and derive_method not in GEMINI_VIDEO_FINAL_DERIVE_METHODS
    ):
        _block_job(conn, int(job["id"]), "unsupported_media_derive_method", {"platform": platform, "derive_method": derive_method})
        return
    if derive_method == FINAL_V1_KEYFRAME_QA_DERIVE_METHOD:
        _process_gemini_video_final_v1_keyframe_qa(conn, job, payload, evidence, preflight_cost)
        return
    if derive_method == "gemini_video_v2_flash_pro_judge":
        _process_gemini_video_flash_pro_judge(conn, job, payload, evidence, preflight_cost)
        return
    if derive_method == "gemini_video_v2_flash_gpt55_judge":
        _process_gemini_video_flash_gpt55_judge(conn, job, payload, evidence, preflight_cost)
        return
    if derive_method == "gemini_video_v2_flash_claude_judge":
        _process_gemini_video_flash_claude_judge(conn, job, payload, evidence, preflight_cost)
        return
    logger.info(
        "apify_jobs gemini video start | job_id=%s target_id=%s url=%s",
        job.get("id"),
        target_id,
        str(evidence.get("content_url") or "")[:120],
    )
    started = time.monotonic()
    analyzer_payload = payload
    if derive_method in GEMINI_VIDEO_FINAL_DERIVE_METHODS:
        analyzer_payload = {
            **payload,
            "gemini_final_v1_models": gemini_video_analyzer.final_v1_gemini_models(
                payload.get("gemini_final_v1_models") or FINAL_V1_GEMINI_MODELS
            ),
        }
    if platform in {"instagram", "tiktok"}:
        resolved = _resolve_video_media(evidence)
        if str(resolved.get("status") or "") == "blocked":
            _block_job(conn, int(job["id"]), str(resolved.get("reason") or "media_resolve_blocked"), resolved)
            return
        if not resolved.get("ok"):
            raise RuntimeError(f"media_resolve_failed: {resolved.get('reason') or platform}")
        with tempfile.TemporaryDirectory(prefix="vkpi-analysis-video-") as tmpdir:
            download = download_direct_video_url(
                str(resolved.get("direct_video_url") or ""),
                tmpdir,
                referer=str(evidence.get("content_url") or ""),
            )
            if not download.get("success") or not download.get("path"):
                raise RuntimeError(f"direct_video_download_failed: {download.get('error') or platform}")
            local_schema = "final_v1" if derive_method in GEMINI_VIDEO_FINAL_DERIVE_METHODS else "v2"
            analysis_context = _video_final_context(evidence) if derive_method in GEMINI_VIDEO_FINAL_DERIVE_METHODS else _video_performance_context(evidence)
            raw = _run_gemini_analyzer_with_timeout(
                {
                    **analyzer_payload,
                    "mode": "local",
                    "video_path": str(download["path"]),
                    "title": str(evidence.get("title") or ""),
                    "creator_handle": str(evidence.get("creator_handle") or ""),
                    "schema_version": local_schema,
                    "performance_context": analysis_context,
                },
                job_id=job.get("id"),
                target_id=target_id,
                platform=platform,
            )
            raw["media_resolution"] = {
                "platform": platform,
                "source_url_host": _url_host(str(evidence.get("content_url") or "")),
                "direct_video_url_host": resolved.get("direct_video_url_host"),
                "status": resolved.get("status"),
            }
            raw["local_video_input"] = {
                "download_bytes": int(download.get("bytes") or 0),
                "temporary_files_cleaned": True,
                "download_error": download.get("error"),
            }
    else:
        analysis_context = _video_final_context(evidence) if derive_method in GEMINI_VIDEO_FINAL_DERIVE_METHODS else _video_performance_context(evidence)
        raw = _run_gemini_analyzer_with_timeout(
            {
                **analyzer_payload,
                "mode": "youtube",
                "url": str(evidence.get("content_url") or ""),
                "title": str(evidence.get("title") or ""),
                "creator_handle": str(evidence.get("creator_handle") or ""),
                "schema_version": "final_v1"
                if derive_method in GEMINI_VIDEO_FINAL_DERIVE_METHODS
                else ("v2" if derive_method in GEMINI_VIDEO_V2_DERIVE_METHODS else "legacy"),
                "performance_context": analysis_context
                if derive_method in GEMINI_VIDEO_V2_DERIVE_METHODS or derive_method in GEMINI_VIDEO_FINAL_DERIVE_METHODS
                else None,
            },
            job_id=job.get("id"),
            target_id=target_id,
            platform=platform,
        )
    latency_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "apify_jobs gemini video returned | job_id=%s analyzed=%s method=%s latency_ms=%s",
        job.get("id"),
        bool(raw.get("analyzed")),
        raw.get("method"),
        latency_ms,
    )
    cost, cost_basis, tokens_in, tokens_out = _gemini_cost(raw, preflight_cost)
    ledger = _record_gemini_cost(
        job=job,
        payload=payload,
        raw=raw,
        cost=cost,
        cost_basis=cost_basis,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=latency_ms,
        preflight_cost=preflight_cost,
    )
    if not raw.get("analyzed"):
        raw_error = str(raw.get("error") or "not_analyzed")
        if raw_error == "gemini_call_timeout":
            raise RuntimeError("gemini_call_timeout")
        raise RuntimeError(f"Gemini video analysis failed: {raw_error}")
    triggered_by = payload.get("triggered_by_user_id", payload.get("user_id"))
    triggered_by_user_id = _int_or_none(triggered_by)
    shaped = _shape_gemini_result(
        job=job,
        evidence=evidence,
        raw={**raw, "ledger": ledger},
        cost=cost,
        cost_basis=cost_basis,
        preflight_cost=preflight_cost,
        latency_ms=latency_ms,
        derive_method=derive_method,
    )
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO vkpi_analysis_cache (
                  target_type, target_id, model, derive_method, result, cost,
                  status, triggered_by_user_id, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, 'ready', %s, NOW(), NOW())
                ON CONFLICT (target_type, target_id, derive_method)
                DO UPDATE SET
                  model = EXCLUDED.model,
                  result = EXCLUDED.result,
                  cost = EXCLUDED.cost,
                  status = 'ready',
                  triggered_by_user_id = EXCLUDED.triggered_by_user_id,
                  updated_at = NOW()
                RETURNING id
                """,
                (
                    target_type,
                    target_id,
                    str(raw.get("model") or raw.get("method") or "gemini_video"),
                    derive_method,
                    _json(shaped),
                    cost,
                    triggered_by_user_id,
                ),
            )
            cache_row = cur.fetchone()
            cache_id = int(cache_row[0]) if cache_row else None
            cur.execute(
                """
                UPDATE apify_jobs
                SET status='done',
                    last_error=NULL,
                    last_error_category=NULL,
                    next_retry_at=NULL,
                    updated_at=NOW()
                WHERE id=%s
                """,
                (job["id"],),
            )
    deep_result = _sync_deep_analysis_result_from_cache(
        conn,
        cache_id=cache_id,
        derive_method=derive_method,
        job_id=int(job["id"]),
    )
    account_extract_job = _enqueue_account_dossier_extract_after_final_v1(
        conn,
        job_id=int(job["id"]),
        deep_result=deep_result,
    )
    analysis_summary = _search_session_analysis_summary_from_result(
        cache_id=cache_id,
        derive_method=derive_method,
        target_type=target_type,
        target_id=target_id,
        evidence=evidence,
        result=shaped,
        cost=cost,
    )
    if analysis_summary and deep_result:
        analysis_summary["deep_result"] = deep_result
    if analysis_summary and account_extract_job:
        analysis_summary["account_dossier_extract_job"] = account_extract_job
    _sync_search_session_job(
        conn,
        int(job["id"]),
        raw_status="done",
        analysis_summary=analysis_summary,
    )


def _claim_job(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
    with conn.transaction():
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT id, job_type, payload, attempts, next_retry_at, last_error_category
                FROM apify_jobs
                WHERE status = 'queued'
                  AND (next_retry_at IS NULL OR next_retry_at <= NOW())
                ORDER BY COALESCE(next_retry_at, created_at), created_at, id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """
            )
            job = cur.fetchone()
            if not job:
                return None
            cur.execute(
                """
                UPDATE apify_jobs
                SET status='running',
                    last_error=NULL,
                    last_error_category=NULL,
                    next_retry_at=NULL,
                    started_at=NOW(),
                    updated_at=NOW()
                WHERE id=%s
                """,
                (job["id"],),
            )
            claimed = dict(job)
    _sync_search_session_job(conn, int(claimed["id"]), raw_status="running")
    return claimed


def _process_job(conn: psycopg.Connection[Any], job: dict[str, Any]) -> None:
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    if str(job.get("job_type") or "").strip().lower() == "session_advance":
        _process_session_advance(conn, job, payload)
        return
    if str(job.get("job_type") or "").strip().lower() == "smart_search_profile_advance":
        _process_smart_search_profile_advance(conn, job, payload)
        return
    if str(job.get("job_type") or "").strip().lower() == "kol_content_fit_analysis":
        _process_kol_content_fit_analysis(conn, job, payload)
        return
    if str(job.get("job_type") or "").strip().lower() == "account_dossier_extract":
        _process_account_dossier_extract(conn, job, payload)
        return
    if str(job.get("job_type") or "").strip().lower() == "project_contract_extract":
        _process_project_contract_extract(conn, job, payload)
        return
    if str(job.get("job_type") or "").strip().lower() == "project_retrospective_aggregate":
        _process_project_retrospective(conn, job, payload)
        return
    if str(job.get("job_type") or "").strip().lower() == "kol_profile_deep_crawl":
        _process_kol_profile_deep_crawl(conn, job, payload)
        return
    if str(job.get("job_type") or "").strip().lower() == "kol_pool_comments_collect":
        _process_kol_pool_comments_collect(conn, job, payload)
        return
    if str(job.get("job_type") or "").strip().lower() == "kol_outreach_draft":
        _process_kol_outreach_draft(conn, job, payload)
        return
    if str(job.get("job_type") or "").strip().lower() == "contract_invoice_extract":
        _process_contract_invoice_extract(conn, job, payload)
        return
    if str(job.get("job_type") or "").strip().lower() == "contract_polish":
        _process_contract_polish(conn, job, payload)
        return
    if str(job.get("job_type") or "").strip().lower() == "logistics_track_sync":
        _process_logistics_track_sync(conn, job, payload)
        return
    target_type, target_id = _target(payload)
    if not target_type or not target_id:
        raise ValueError("payload must include target_type and target_id")
    derive_method = _derive_method(payload)
    if derive_method != "mock":
        if target_type not in LLM_TARGET_TYPES:
            _block_job(conn, int(job["id"]), "unsupported_llm_target_type", {"target_type": target_type})
            return
        target_lock = f"{target_type}:{target_id}:{derive_method}"
        if not _advisory_lock(conn, "vkpi_analysis_worker_target", target_lock):
            _requeue_job(conn, int(job["id"]), "analysis target already in progress")
            return
        slot = _acquire_llm_slot(conn)
        try:
            if slot is None:
                _requeue_job(conn, int(job["id"]), "llm concurrency limit reached")
                return
            if _analysis_cache_exists(conn, target_type, target_id, derive_method):
                _finish_skipped(conn, int(job["id"]), "skipped_existing_analysis_cache")
                return
            preflight = _llm_budget_preflight(job, payload)
            allowed, reason, estimated_cost = _google_allowed(preflight)
            _log_budget_preflight_record_only(
                job=job,
                provider="google",
                allowed=allowed,
                reason=reason,
                estimated_cost=estimated_cost,
                stage=derive_method,
            )
            if not allowed:
                # 护栏② 主线 enforce:撞 cap 拦在 _process_gemini_video 之前(finally 正常释放 slot/lock)
                _block_job(
                    conn,
                    int(job["id"]),
                    "budget_guard_blocked",
                    {
                        "provider": "google",
                        "stage": derive_method,
                        "reason_detail": reason,
                        "estimated_cost_usd": estimated_cost,
                    },
                )
                return
            if derive_method in GEMINI_VIDEO_DERIVE_METHODS:
                _respect_gemini_qps()
                _process_gemini_video(conn, job, payload, estimated_cost)
                return
            _block_job(conn, int(job["id"]), "unsupported_llm_derive_method", {"derive_method": derive_method})
            return
        finally:
            if slot is not None:
                _advisory_unlock(conn, "vkpi_analysis_worker_llm_slot", slot)
            _advisory_unlock(conn, "vkpi_analysis_worker_target", target_lock)
    triggered_by = payload.get("triggered_by_user_id", payload.get("user_id"))
    triggered_by_user_id = int(triggered_by) if triggered_by not in (None, "") else None
    result = _mock_result(job, payload)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO vkpi_analysis_cache (
                  target_type, target_id, model, derive_method, result, cost,
                  status, triggered_by_user_id, created_at, updated_at
                )
                VALUES (%s, %s, 'mock', 'mock', %s::jsonb, 0, 'ready', %s, NOW(), NOW())
                ON CONFLICT (target_type, target_id, derive_method)
                DO UPDATE SET
                  model = EXCLUDED.model,
                  result = EXCLUDED.result,
                  cost = EXCLUDED.cost,
                  status = 'ready',
                  triggered_by_user_id = EXCLUDED.triggered_by_user_id,
                  updated_at = NOW()
                RETURNING id
                """,
                (target_type, target_id, _json(result), triggered_by_user_id),
            )
            cache_row = cur.fetchone()
            cache_id = int(cache_row[0]) if cache_row else None
            cur.execute(
                """
                UPDATE apify_jobs
                SET status='done',
                    last_error=NULL,
                    last_error_category=NULL,
                    next_retry_at=NULL,
                    updated_at=NOW()
                WHERE id=%s
                """,
                (job["id"],),
            )
    _sync_search_session_job(
        conn,
        int(job["id"]),
        raw_status="done",
        analysis_summary=_search_session_analysis_summary_from_result(
            cache_id=cache_id,
            derive_method=derive_method,
            target_type=target_type,
            target_id=target_id,
            evidence={"id": target_id},
            result=result,
            cost=0.0,
        ),
    )


def _fail_job(conn: psycopg.Connection[Any], job_id: int, exc: Exception) -> None:
    if str(exc).strip() == "gemini_call_timeout":
        message = "gemini_call_timeout"
    else:
        message = _redact_sensitive_text(f"{type(exc).__name__}: {exc}")
    category = _error_category(message)
    raw_status = "failed"
    sync_reason = message
    with conn.transaction():
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT attempts FROM apify_jobs WHERE id=%s FOR UPDATE", (job_id,))
            row = cur.fetchone() or {}
            next_attempt = int(row.get("attempts") or 0) + 1
            if category == "provider_pressure" and next_attempt < PROVIDER_RETRY_MAX_ATTEMPTS:
                delay_seconds = _provider_retry_delay_seconds(next_attempt)
                cur.execute(
                    """
                    UPDATE apify_jobs
                    SET status='queued',
                        attempts=%s,
                        last_error=%s,
                        last_error_category=%s,
                        next_retry_at=NOW() + make_interval(secs => %s),
                        updated_at=NOW()
                    WHERE id=%s
                    RETURNING next_retry_at
                    """,
                    (next_attempt, message, category, delay_seconds, job_id),
                )
                retry_row = cur.fetchone() or {}
                raw_status = "queued"
                sync_reason = _provider_retry_reason(message, next_retry_at=retry_row.get("next_retry_at"))
                logger.warning(
                    "apify_jobs provider pressure retry scheduled | id=%s attempt=%s delay_seconds=%s next_retry_at=%s",
                    job_id,
                    next_attempt,
                    delay_seconds,
                    retry_row.get("next_retry_at"),
                )
            else:
                cur.execute(
                    """
                    UPDATE apify_jobs
                    SET status='failed',
                        attempts=%s,
                        last_error=%s,
                        last_error_category=%s,
                        next_retry_at=NULL,
                        updated_at=NOW()
                    WHERE id=%s
                    """,
                    (next_attempt, message, category, job_id),
                )
    _sync_search_session_job(conn, job_id, raw_status=raw_status, reason=sync_reason)


def _heartbeat_running_job(job_id: int, stop_signal: threading.Event) -> None:
    while not stop_signal.wait(RUNNING_HEARTBEAT_SECONDS):
        try:
            with psycopg.connect(DB_RUNTIME_URL, autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE apify_jobs SET updated_at=NOW() WHERE id=%s AND status='running'",
                        (job_id,),
                    )
        except Exception as exc:
            logger.warning("apify_jobs running heartbeat failed | id=%s error=%s", job_id, exc)


@contextmanager
def _running_job_heartbeat(job_id: int):
    stop_signal = threading.Event()
    thread = threading.Thread(
        target=_heartbeat_running_job,
        args=(job_id, stop_signal),
        name=f"apify-job-heartbeat-{job_id}",
        daemon=True,
    )
    thread.start()
    try:
        yield
    finally:
        stop_signal.set()
        thread.join(timeout=2)


def _reclaim_stale_running_jobs(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
    with conn.transaction():
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                UPDATE apify_jobs
                SET
                  status = CASE WHEN attempts < %(max_attempts)s THEN 'queued' ELSE 'failed' END,
                  attempts = attempts + 1,
                  last_error = CASE
                    WHEN attempts < %(max_attempts)s THEN 'stale_running_reclaimed: requeued after worker heartbeat expired'
                    ELSE 'stale_running_reclaimed: max attempts reached'
                  END,
                  last_error_category = 'stale_running',
                  next_retry_at = NULL,
                  updated_at = NOW()
                WHERE status='running'
                  AND updated_at < NOW() - make_interval(secs => %(stale_seconds)s)
                RETURNING id, status, attempts, payload, last_error
                """,
                {"max_attempts": MAX_JOB_ATTEMPTS, "stale_seconds": STALE_RECLAIM_SECONDS},
            )
            rows = [dict(row) for row in cur.fetchall()]
    for row in rows:
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        logger.warning(
            "apify_jobs stale running reclaimed | id=%s target_id=%s status=%s attempts=%s",
            row.get("id"),
            payload.get("target_id"),
            row.get("status"),
            row.get("attempts"),
        )
        try:
            _sync_search_session_job(
                conn,
                int(row.get("id")),
                raw_status=str(row.get("status") or "queued"),
                reason=str(row.get("last_error") or "stale_running_reclaimed"),
            )
        except Exception as exc:
            logger.warning("search session stale job sync failed | job_id=%s error=%s", row.get("id"), exc)
    return rows


def _adopt_recent_provider_pressure_failures(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
    if PROVIDER_RETRY_ADOPT_WINDOW_MINUTES <= 0:
        return []
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, attempts, last_error, updated_at, payload
            FROM apify_jobs
            WHERE status='failed'
              AND attempts < %(max_attempts)s
              AND updated_at >= NOW() - make_interval(mins => %(window_minutes)s)
            ORDER BY updated_at DESC, id DESC
            LIMIT 25
            """,
            {
                "max_attempts": PROVIDER_RETRY_MAX_ATTEMPTS,
                "window_minutes": PROVIDER_RETRY_ADOPT_WINDOW_MINUTES,
            },
        )
        candidates = [dict(row) for row in cur.fetchall()]
    adopted: list[dict[str, Any]] = []
    for row in candidates:
        message = str(row.get("last_error") or "")[:2000]
        if _error_category(message) != "provider_pressure":
            continue
        attempts = int(row.get("attempts") or 0)
        delay_seconds = _provider_retry_delay_seconds(attempts or 1)
        with conn.transaction():
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    UPDATE apify_jobs
                    SET status='queued',
                        last_error=%s,
                        last_error_category='provider_pressure',
                        next_retry_at=NOW() + make_interval(secs => %s),
                        updated_at=NOW()
                    WHERE id=%s
                      AND status='failed'
                    RETURNING id, status, attempts, payload, last_error, last_error_category, next_retry_at
                    """,
                    (
                        _provider_retry_reason(message),
                        delay_seconds,
                        int(row["id"]),
                    ),
                )
                updated = cur.fetchone()
        if not updated:
            continue
        adopted_row = dict(updated)
        adopted.append(adopted_row)
        payload = adopted_row.get("payload") if isinstance(adopted_row.get("payload"), dict) else {}
        logger.warning(
            "apify_jobs adopted provider pressure failure | id=%s target_id=%s attempts=%s delay_seconds=%s next_retry_at=%s",
            adopted_row.get("id"),
            payload.get("target_id"),
            adopted_row.get("attempts"),
            delay_seconds,
            adopted_row.get("next_retry_at"),
        )
        try:
            _sync_search_session_job(
                conn,
                int(adopted_row["id"]),
                raw_status="queued",
                reason=str(adopted_row.get("last_error") or "provider_pressure_retry_scheduled"),
            )
        except Exception as exc:
            logger.warning("search session adopted retry sync failed | job_id=%s error=%s", adopted_row.get("id"), exc)
    return adopted


def run_worker() -> None:
    if not DB_RUNTIME_URL:
        raise RuntimeError("DATABASE_URL is required for apify_jobs worker")
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    logger.info(
        "apify_jobs worker started | poll_seconds=%s stale_minutes=%s resolve_timeout_sec=%s gemini_timeout_sec=%s llm_concurrency=%s gemini_qps=%s gemini_min_interval_sec=%s provider_retry_max_attempts=%s provider_retry_base_sec=%s provider_retry_max_delay_sec=%s provider_retry_adopt_window_min=%s",
        POLL_SECONDS,
        STALE_RUNNING_MINUTES,
        MEDIA_RESOLVE_TIMEOUT_SECONDS,
        GEMINI_CALL_TIMEOUT_SECONDS,
        LLM_CONCURRENCY_LIMIT,
        GEMINI_QPS_LIMIT,
        GEMINI_MIN_INTERVAL_SECONDS,
        PROVIDER_RETRY_MAX_ATTEMPTS,
        PROVIDER_RETRY_BASE_SECONDS,
        PROVIDER_RETRY_MAX_DELAY_SECONDS,
        PROVIDER_RETRY_ADOPT_WINDOW_MINUTES,
    )
    try:
        with psycopg.connect(DB_RUNTIME_URL, autocommit=True) as conn:
            _reclaim_stale_running_jobs(conn)
            _adopt_recent_provider_pressure_failures(conn)
            last_reclaim = time.monotonic()
            while not _stop_event.is_set():
                if time.monotonic() - last_reclaim >= STALE_RECLAIM_POLL_SECONDS:
                    _reclaim_stale_running_jobs(conn)
                    _adopt_recent_provider_pressure_failures(conn)
                    last_reclaim = time.monotonic()
                job = _claim_job(conn)
                if not job:
                    _stop_event.wait(POLL_SECONDS)
                    continue
                if _stop_event.is_set():
                    _requeue_job(conn, int(job["id"]), "worker stop requested before processing")
                    break
                try:
                    with _running_job_heartbeat(int(job["id"])):
                        _process_job(conn, job)
                    logger.info("apify_jobs job done | id=%s", job["id"])
                except Exception as exc:
                    logger.error(
                        "apify_jobs job failed | id=%s category=%s error=%s",
                        job.get("id"),
                        _error_category(str(exc)),
                        _redact_sensitive_text(f"{type(exc).__name__}: {exc}"),
                    )
                    _fail_job(conn, int(job["id"]), exc)
    finally:
        close_db_runtime_sync()
        logger.info("apify_jobs worker stopped")


if __name__ == "__main__":
    run_worker()
