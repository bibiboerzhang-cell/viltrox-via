"""Persistent apify_jobs worker with mock analysis and LLM brake controls."""
from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
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
from app.platform import llm_gateway
from app.services.media.video_download import download_direct_video_url
from app.services.media.video_keyframes import temporary_keyframes
from app.services.ai.analyzers import gemini_video as gemini_video_analyzer


logger = get_logger(__name__)
POLL_SECONDS = float(os.environ.get("APIFY_WORKER_POLL_SECONDS", "2"))
MEDIA_RESOLVE_TIMEOUT_SECONDS = max(10, int(os.environ.get("APIFY_WORKER_MEDIA_RESOLVE_TIMEOUT_SEC", "90")))
GEMINI_CALL_TIMEOUT_SECONDS = max(30, int(os.environ.get("APIFY_WORKER_GEMINI_CALL_TIMEOUT_SEC", "300")))
GEMINI_CALL_TERMINATE_GRACE_SECONDS = max(1, int(os.environ.get("APIFY_WORKER_GEMINI_CALL_TERMINATE_GRACE_SEC", "5")))
STALE_RUNNING_MINUTES = max(1, int(os.environ.get("APIFY_WORKER_STALE_RUNNING_MINUTES", "10")))
STALE_RECLAIM_SECONDS = STALE_RUNNING_MINUTES * 60
STALE_RECLAIM_POLL_SECONDS = max(30, int(os.environ.get("APIFY_WORKER_STALE_RECLAIM_POLL_SECONDS", "60")))
RUNNING_HEARTBEAT_SECONDS = max(10, int(os.environ.get("APIFY_WORKER_RUNNING_HEARTBEAT_SECONDS", "30")))
MAX_JOB_ATTEMPTS = max(1, int(os.environ.get("APIFY_WORKER_MAX_ATTEMPTS", "2")))
LLM_BUDGET_SCOPE = os.environ.get("APIFY_WORKER_LLM_BUDGET_SCOPE", "cron:vkpi_analysis_worker")
LLM_CONCURRENCY_LIMIT = max(1, min(2, int(os.environ.get("APIFY_WORKER_LLM_CONCURRENCY", "1"))))
LLM_MAX_OUTPUT_TOKENS = int(os.environ.get("APIFY_WORKER_LLM_MAX_OUTPUT_TOKENS", "1200"))
LLM_TARGET_TYPES = {"video", "contract"}
GEMINI_VIDEO_V2_DERIVE_METHODS = {
    "gemini_video_v2",
    "gemini_video_v2_pro_single",
    "gemini_video_v2_flash_pro_judge",
    "gemini_video_v2_flash_gpt55_judge",
    "gemini_video_v2_flash_claude_judge",
}
GEMINI_VIDEO_FINAL_DERIVE_METHODS = {"video_analysis_final_v1"}
GEMINI_VIDEO_DERIVE_METHODS = {"gemini", *GEMINI_VIDEO_V2_DERIVE_METHODS, *GEMINI_VIDEO_FINAL_DERIVE_METHODS}
WORKER_GEMINI_MODEL = os.environ.get("APIFY_WORKER_GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
FINAL_V1_GEMINI_MODELS = gemini_video_analyzer.final_v1_gemini_models()
_stop_event = threading.Event()


def _request_stop(_signum: int, _frame: Any) -> None:
    _stop_event.set()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _int_or_none(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed


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


def _finish_skipped(conn: psycopg.Connection[Any], job_id: int, reason: str) -> None:
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE apify_jobs SET status='done', last_error=%s, updated_at=NOW() WHERE id=%s",
                (reason[:2000], job_id),
            )


def _block_job(conn: psycopg.Connection[Any], job_id: int, reason: str, detail: dict[str, Any] | None = None) -> None:
    payload = {"reason": reason, **(detail or {})}
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE apify_jobs SET status='blocked', last_error=%s, updated_at=NOW() WHERE id=%s",
                (_json(payload)[:2000], job_id),
            )


def _requeue_job(conn: psycopg.Connection[Any], job_id: int, reason: str) -> None:
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE apify_jobs SET status='queued', last_error=%s, updated_at=NOW() WHERE id=%s",
                (reason[:2000], job_id),
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
        return {
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
                "segments": [
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
                "error": raw.get("error") or "",
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
                "error": raw.get("error") or "",
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
                "error": raw.get("error") or "",
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
            cur.execute(
                "UPDATE apify_jobs SET status='done', last_error=NULL, updated_at=NOW() WHERE id=%s",
                (job["id"],),
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
    if not openai_allowed:
        _block_job(
            conn,
            int(job["id"]),
            openai_reason,
            {"estimated_cost_usd": openai_estimated_cost, "budget_scope": LLM_BUDGET_SCOPE, "provider": "openai"},
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
    if not anthropic_allowed:
        _block_job(
            conn,
            int(job["id"]),
            anthropic_reason,
            {"estimated_cost_usd": anthropic_estimated_cost, "budget_scope": LLM_BUDGET_SCOPE, "provider": "anthropic"},
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
            cur.execute(
                "UPDATE apify_jobs SET status='done', last_error=NULL, updated_at=NOW() WHERE id=%s",
                (job["id"],),
            )


def _claim_job(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
    with conn.transaction():
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT id, job_type, payload
                FROM apify_jobs
                WHERE status = 'queued'
                ORDER BY created_at, id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """
            )
            job = cur.fetchone()
            if not job:
                return None
            cur.execute(
                "UPDATE apify_jobs SET status='running', updated_at=NOW() WHERE id=%s",
                (job["id"],),
            )
            return dict(job)


def _process_job(conn: psycopg.Connection[Any], job: dict[str, Any]) -> None:
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
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
            if not allowed:
                _block_job(conn, int(job["id"]), reason, {"estimated_cost_usd": estimated_cost, "budget_scope": LLM_BUDGET_SCOPE})
                return
            if derive_method in GEMINI_VIDEO_DERIVE_METHODS:
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
                """,
                (target_type, target_id, _json(result), triggered_by_user_id),
            )
            cur.execute(
                "UPDATE apify_jobs SET status='done', last_error=NULL, updated_at=NOW() WHERE id=%s",
                (job["id"],),
            )


def _fail_job(conn: psycopg.Connection[Any], job_id: int, exc: Exception) -> None:
    if str(exc).strip() == "gemini_call_timeout":
        message = "gemini_call_timeout"
    else:
        message = f"{type(exc).__name__}: {exc}"[:2000]
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE apify_jobs
                SET status='failed', attempts=attempts+1, last_error=%s, updated_at=NOW()
                WHERE id=%s
                """,
                (message, job_id),
            )


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
    return rows


def run_worker() -> None:
    if not DB_RUNTIME_URL:
        raise RuntimeError("DATABASE_URL is required for apify_jobs worker")
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    logger.info(
        "apify_jobs worker started | poll_seconds=%s stale_minutes=%s resolve_timeout_sec=%s",
        POLL_SECONDS,
        STALE_RUNNING_MINUTES,
        MEDIA_RESOLVE_TIMEOUT_SECONDS,
    )
    try:
        with psycopg.connect(DB_RUNTIME_URL, autocommit=True) as conn:
            _reclaim_stale_running_jobs(conn)
            last_reclaim = time.monotonic()
            while not _stop_event.is_set():
                if time.monotonic() - last_reclaim >= STALE_RECLAIM_POLL_SECONDS:
                    _reclaim_stale_running_jobs(conn)
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
                    logger.exception("apify_jobs job failed | id=%s", job.get("id"))
                    _fail_job(conn, int(job["id"]), exc)
    finally:
        close_db_runtime_sync()
        logger.info("apify_jobs worker stopped")


if __name__ == "__main__":
    run_worker()
