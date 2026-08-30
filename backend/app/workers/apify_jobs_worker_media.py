"""媒体解析 / 子进程分析器 / R2 回灌簇,从 apify_jobs_worker.py 整簇 move 出来。

函数体逐字不变 → 行为必然不变;原文件 re-export 兜住所有调用点。
超时常量(GEMINI_CALL_*/MEDIA_RESOLVE_*)从 config 叶子顶部 import
(2026-08-30 拆 import 期环:原底部回环 import 已删,worker 只 re-export)。红线:本簇零 fit 写。
"""
from __future__ import annotations

import hashlib
import os
import re
import signal
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from psycopg.rows import dict_row

from app.core.logging import get_logger
from app.domains.media.cache import cache_local_video_file
from app.domains.media.cache_core import MAX_VIDEO_BYTES, VIDEO_CACHE_DIR
from app.domains.kol.url_deep_crawl_helpers import _video_id as _content_url_video_id
from app.services.media.resolution_state import (
    needs_secondary_video_probe,
    stamp_video_media_resolution,
)
from app.workers.apify_jobs_worker_helpers import (
    _json,
    _parse_last_json_stdout,
    _platform_from_content_url,
    _redact_sensitive_text,
    _url_host,
)
from app.workers.apify_jobs_worker_media_resolver import resolve_video_media
# 超时常量来自 config 叶子(原 worker 底部回环 import 转正到顶部,2026-08-30 拆环)。
from app.workers.apify_jobs_worker_config import (
    GEMINI_CALL_TERMINATE_GRACE_SECONDS,
    GEMINI_CALL_TIMEOUT_SECONDS,
    MEDIA_RESOLVE_TIMEOUT_SECONDS,
)


logger = get_logger(__name__)

# C1:子进程 stderr 尾巴落 diagnostics 的长度上限(跨车道契约 V→O:≤500 字、脱敏)。
CHILD_STDERR_TAIL_CHARS = 500
_GOOGLE_API_KEY_RE = re.compile(r"AIza[0-9A-Za-z_\-]{20,}")
_LONG_SECRET_RE = re.compile(r"\b(sk|hf|ghp|xox[abp]|apify_api)[-_][A-Za-z0-9_\-]{16,}\b")


def child_stderr_tail(stderr: Any, *, limit: int = CHILD_STDERR_TAIL_CHARS) -> str:
    """子进程 stderr 尾巴:脱敏(URL userinfo / token / key / bearer / Google key 形态)后截尾 ≤ limit。"""

    text = str(stderr or "")
    if not text:
        return ""
    tail = text[-(limit * 4):]
    tail = _GOOGLE_API_KEY_RE.sub("AIza***", tail)
    tail = _LONG_SECRET_RE.sub(lambda m: f"{m.group(1)}-***", tail)
    tail = _redact_sensitive_text(tail, limit=len(tail) + 16)
    return tail[-limit:]


def _attach_child_diagnostics(
    raw: dict[str, Any],
    *,
    stderr: Any,
    returncode: Any,
    job_id: Any,
    target_id: str,
    platform: str,
    reason: str,
) -> dict[str, Any]:
    """C1:子进程失败(非零退出 / 无 JSON / 结果带 error)时把 stderr 尾巴放进
    ``raw["diagnostics"]["child_stderr_tail"]`` 并 warning 一行——此前异常只留在子进程 stderr,
    父进程只有超时才记,线上「Gemini video analysis failed: ...」永远查不到真因。"""

    tail = child_stderr_tail(stderr)
    diagnostics = raw.get("diagnostics") if isinstance(raw.get("diagnostics"), dict) else {}
    diagnostics["child_stderr_tail"] = tail
    diagnostics["child_returncode"] = returncode
    diagnostics["child_failure_reason"] = str(reason or "")[:80]
    raw["diagnostics"] = diagnostics
    logger.warning(
        "gemini analyzer child failed | job_id=%s target_id=%s platform=%s reason=%s returncode=%s error=%s stderr_tail=%s",
        job_id,
        target_id,
        platform,
        reason,
        returncode,
        _redact_sensitive_text(str(raw.get("error") or ""), limit=200),
        tail.replace("\n", " | ")[-300:],
    )
    return raw


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cached_video_asset_rows(
    conn: Any,
    evidence: dict[str, Any],
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Find cached bytes for one evidence without mutating cache state.

    Exact source URL is the primary identity.  ``external_id`` is a bounded
    compatibility fallback because older cache writers stored the provider
    download URL as ``source_url`` while keeping the public IG/TikTok native id.
    """

    content_url = str(evidence.get("content_url") or "").strip()
    platform = _platform_from_content_url(content_url)
    if platform not in {"instagram", "tiktok"} or not content_url:
        return []
    parsed = urlparse(content_url)
    native_id = _content_url_video_id(
        platform,
        (parsed.hostname or "").lower(),
        parsed.path,
        parsed.query,
    )
    source_hash = hashlib.sha256(content_url.encode("utf-8")).hexdigest()
    try:
        # Worker connections run in autocommit mode.  The explicit transaction
        # makes a lookup error self-contained so the resolver fallback receives
        # a usable connection rather than an aborted transaction.
        with conn.transaction():
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT
                      id,
                      platform,
                      external_id,
                      source_url,
                      digest,
                      checksum,
                      content_type,
                      size_bytes,
                      storage_backend,
                      local_path,
                      r2_key,
                      updated_at
                    FROM vkpi_media_cache_assets
                    WHERE media_kind='video'
                      AND status='cached'
                      AND (
                        source_url_hash=%s
                        OR source_url=%s
                        OR (
                          %s <> ''
                          AND platform=%s
                          AND external_id=%s
                        )
                      )
                    ORDER BY
                      CASE
                        WHEN source_url_hash=%s THEN 0
                        WHEN source_url=%s THEN 1
                        WHEN external_id=%s THEN 2
                        ELSE 3
                      END,
                      updated_at DESC,
                      id DESC
                    LIMIT %s
                    """,
                    (
                        source_hash,
                        content_url,
                        native_id,
                        platform,
                        native_id,
                        source_hash,
                        content_url,
                        native_id,
                        max(1, min(int(limit or 8), 24)),
                    ),
                )
                return [dict(row) for row in cur.fetchall()]
    except Exception as exc:
        # Cache is an optimization.  Missing schema/read failures must not turn
        # a previously valid provider fallback into a terminal worker failure.
        logger.info(
            "video media cache lookup unavailable | platform=%s error_type=%s",
            platform,
            type(exc).__name__,
        )
        return []


def _validated_cached_video_file(path: Path, asset: dict[str, Any]) -> dict[str, Any]:
    content_type = str(asset.get("content_type") or "").split(";", 1)[0].strip().lower()
    if content_type and not (
        content_type.startswith("video/")
        or content_type == "application/octet-stream"
    ):
        return {"ok": False, "reason": "media_cache_content_type_invalid"}
    if not path.is_file():
        return {"ok": False, "reason": "media_cache_file_missing"}
    size_bytes = int(path.stat().st_size)
    expected_size = int(asset.get("size_bytes") or 0)
    if size_bytes <= 0 or size_bytes > MAX_VIDEO_BYTES:
        return {"ok": False, "reason": "media_cache_size_invalid"}
    if expected_size > 0 and size_bytes != expected_size:
        return {"ok": False, "reason": "media_cache_size_mismatch"}
    checksum = str(asset.get("checksum") or "").strip().lower()
    if checksum.startswith("sha256:"):
        checksum = checksum.split(":", 1)[1]
    if not _SHA256_RE.fullmatch(checksum):
        return {"ok": False, "reason": "media_cache_checksum_missing"}
    actual_checksum = _sha256_path(path)
    if actual_checksum != checksum:
        return {"ok": False, "reason": "media_cache_checksum_mismatch"}
    return {
        "ok": True,
        "bytes": size_bytes,
        "checksum": actual_checksum,
        "content_type": content_type or "video/mp4",
    }


def _trusted_local_cache_file(value: Any) -> Path | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        path = Path(raw).resolve(strict=True)
        cache_root = VIDEO_CACHE_DIR.resolve(strict=False)
        if not path.is_file() or not path.is_relative_to(cache_root):
            return None
        return path
    except (OSError, RuntimeError, ValueError):
        return None


def _materialize_cached_video_media(
    conn: Any,
    evidence: dict[str, Any],
    output_dir: str,
) -> dict[str, Any]:
    """Copy/download a validated cached video into the worker temp directory.

    The returned path is always under ``output_dir`` and is therefore covered
    by the existing TemporaryDirectory cleanup boundary.  R2 object keys and
    filesystem paths never escape in the public result/log payload.
    """

    content_url = str(evidence.get("content_url") or "").strip()
    platform = _platform_from_content_url(content_url)
    candidates = _cached_video_asset_rows(conn, evidence)
    if not candidates:
        return {
            "ok": False,
            "status": "miss",
            "reason": "media_cache_miss",
            "cache_hit": False,
            "cache_candidate_count": 0,
            "platform": platform,
        }
    destination = Path(output_dir) / "cached_video_input.mp4"
    failure_reasons: list[str] = []
    for asset in candidates:
        destination.unlink(missing_ok=True)
        source = ""
        local_path = _trusted_local_cache_file(asset.get("local_path"))
        try:
            if local_path is not None:
                shutil.copyfile(local_path, destination)
                source = "local_cache"
            elif str(asset.get("r2_key") or "").strip():
                from app.services.media.r2 import download_file

                download_file(str(asset.get("r2_key") or ""), str(destination))
                source = "r2_cache"
            else:
                failure_reasons.append("media_cache_bytes_unavailable")
                continue
        except Exception as exc:
            destination.unlink(missing_ok=True)
            failure_reasons.append(f"media_cache_{source or 'read'}_failed:{type(exc).__name__}")
            continue
        validation = _validated_cached_video_file(destination, asset)
        if not validation.get("ok"):
            destination.unlink(missing_ok=True)
            failure_reasons.append(str(validation.get("reason") or "media_cache_validation_failed"))
            continue
        return stamp_video_media_resolution(
            {
                "ok": True,
                "status": "ready",
                "reason": "media_cache_hit",
                "cache_hit": True,
                "cache_source": source,
                "cache_asset_id": int(asset.get("id") or 0) or None,
                "cache_storage_backend": str(asset.get("storage_backend") or ""),
                "cache_candidate_count": len(candidates),
                "platform": platform,
                "source_url_host": _url_host(content_url),
                "direct_video_url": "",
                "direct_video_url_host": "",
                "path": str(destination),
                "bytes": int(validation.get("bytes") or 0),
                "content_type": str(validation.get("content_type") or "video/mp4"),
                "checksum": str(validation.get("checksum") or ""),
                "scraped_ok": False,
                "provider_calls_performed": False,
            },
            scrape_success=False,
            media_resolved=True,
            downloadable=True,
            confirmed_non_video=False,
        )
    return {
        "ok": False,
        "status": "miss",
        "reason": "media_cache_invalid",
        "cache_hit": False,
        "cache_candidate_count": len(candidates),
        "cache_failure_reasons": failure_reasons[:3],
        "platform": platform,
    }


def _resolve_cached_or_provider_video(
    conn: Any,
    evidence: dict[str, Any],
    output_dir: str,
) -> dict[str, Any]:
    """Prefer durable cached bytes, then preserve the existing resolver path."""

    cached = _materialize_cached_video_media(conn, evidence, output_dir)
    if cached.get("ok"):
        return cached
    resolved = _resolve_video_media(evidence)
    resolved["cache_hit"] = False
    resolved["provider_calls_performed"] = True
    resolved["cache_lookup_reason"] = str(cached.get("reason") or "media_cache_miss")
    resolved["cache_candidate_count"] = int(cached.get("cache_candidate_count") or 0)
    if cached.get("cache_failure_reasons"):
        resolved["cache_failure_reasons"] = list(cached["cache_failure_reasons"])
    resolved = stamp_video_media_resolution(resolved)
    # yt-dlp 兜底改按结构化状态接线,不再依赖可漂移的 error suffix。
    # 只有「帖子元数据抓成功 + 未得到视频输入 + 尚未确认纯图」才复核;
    # 抓取本身失败保持可重试,已确认图文则终态收口。
    if not resolved.get("ok") and str(resolved.get("platform") or "") in ("instagram", "tiktok"):
        if needs_secondary_video_probe(resolved):
            from app.workers.apify_jobs_worker_ytdlp_fallback import ytdlp_fallback_resolve

            resolved = ytdlp_fallback_resolve(evidence, output_dir, apify_resolved=resolved)
    return resolved


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
    """Resolve media in the claimed worker process, preserving its fence.

    The old subprocess boundary silently discarded the ContextVar installed by
    ``_execute_claimed_job``.  Consequently ``call_apify_actor`` correctly
    rejected the child and the media pipeline misreported a normal IG/TikTok
    job as an empty scrape.  The provider lifecycle itself is already bounded
    by ``timeout_secs`` + ``wait_secs`` and persists the run id before waiting,
    so no process kill is needed here.
    """
    import asyncio

    from app.platform.apify_budget import current_apify_execution_context
    from app.services.scraping import apify as apify_scraper

    if current_apify_execution_context() is None:
        return {
            "scraped_ok": False,
            "error": "durable_execution_context_required",
            "_context_required": True,
        }
    try:
        return asyncio.run(
            apify_scraper.scrape_with_apify(
                content_url,
                platform,
                timeout_secs=MEDIA_RESOLVE_TIMEOUT_SECONDS,
            )
        )
    except TimeoutError:
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
    except Exception as exc:
        logger.warning(
            "apify media resolve failed | platform=%s source_host=%s error=%s",
            platform,
            _url_host(content_url),
            type(exc).__name__,
        )
        return {
            "scraped_ok": False,
            "error": f"{type(exc).__name__}: {str(exc)[:800]}",
        }


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
        # analyze_youtube_with_gemini 住在 gemini_video_youtube,按自己模块的全局名取字幕函数
        from app.services.ai.analyzers import gemini_video_youtube as _yt
        stack.enter_context(patch.object(_yt, "fetch_youtube_subtitles", lambda *_args, **_kwargs: ""))
    if model_override and getattr(gemini_video_analyzer, "gemini_client", None):
        original_generate = gemini_video_analyzer.gemini_client.models.generate_content

        def _forced_generate_content(*args, **kwargs):
            kwargs["model"] = model_override
            return original_generate(*args, **kwargs)

        stack.enter_context(patch.object(gemini_video_analyzer.gemini_client.models, "generate_content", _forced_generate_content))
    return stack, model_override


def _scope_checkpoint_for(payload):
    # Signed final_v1 children re-read the durable actor/target fence between
    # every paid or external stage (subtitles, direct URL attempts, download,
    # File API upload, each model attempt).  Local-evaluation jobs carry no
    # durable actor and are validated by capability in the parent instead.
    if str(payload.get("derive_method") or "").strip().lower() != "video_analysis_final_v1":
        return None
    if payload.get("local_evaluation") is True:
        return None

    def _checkpoint(stage):
        import json

        from app.db import connection as db_connection
        from app.db.connection import db_connection_sync_scope
        from app.workers.apify_jobs_worker_paid_scope import revalidate_paid_job_scope

        # 子进程 payload 是执行超集(mode/video_path/llm_context/gemini_models…都不在
        # 围栏合同里),拿它算指纹必判 payload_drifted(2026-08-24 事故)。复验对象必须是
        # 落库 payload——围栏保护的本体,顺带把执行中途的行篡改一并抓住。
        try:
            job_id = int(payload.get("job_id"))
        except (TypeError, ValueError):
            raise gemini_video_analyzer.AnalysisScopeRevoked(
                "fence_job_identity_missing", stage=stage
            ) from None
        with db_connection_sync_scope():
            row = db_connection.get_conn().execute(
                "SELECT payload FROM apify_jobs WHERE id=?", (job_id,)
            ).fetchone()
        durable = dict(row).get("payload") if row else None
        if isinstance(durable, str):
            try:
                durable = json.loads(durable)
            except ValueError:
                durable = None
        if not isinstance(durable, dict):
            raise gemini_video_analyzer.AnalysisScopeRevoked(
                "fence_durable_payload_unavailable", stage=stage
            )
        _action, reason, _actor = revalidate_paid_job_scope(
            durable, "video", connection_scope=db_connection_sync_scope
        )
        if reason:
            raise gemini_video_analyzer.AnalysisScopeRevoked(reason, stage=stage)

    return _checkpoint


async def _run(payload):
    mode = str(payload.get("mode") or "").strip()
    checkpoint = _scope_checkpoint_for(payload)
    if mode == "local":
        return await gemini_video_analyzer.analyze_local_video_with_gemini(
            str(payload.get("video_path") or ""),
            str(payload.get("title") or ""),
            str(payload.get("creator_handle") or ""),
            schema_version=str(payload.get("schema_version") or "v2"),
            performance_context=payload.get("performance_context"),
            final_v1_models=payload.get("gemini_final_v1_models"),
            models=payload.get("gemini_models"),
            llm_context=payload.get("llm_context"),
            authorization_checkpoint=checkpoint,
        )
    if mode == "youtube":
        return await gemini_video_analyzer.analyze_youtube_with_gemini(
            str(payload.get("url") or ""),
            str(payload.get("title") or ""),
            str(payload.get("creator_handle") or ""),
            schema_version=str(payload.get("schema_version") or "legacy"),
            performance_context=payload.get("performance_context"),
            final_v1_models=payload.get("gemini_final_v1_models"),
            models=payload.get("gemini_models"),
            llm_context=payload.get("llm_context"),
            authorization_checkpoint=checkpoint,
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
                "stdout_tail": _redact_sensitive_text(str(stdout or "")[-500:], limit=500),
                "stderr_tail": child_stderr_tail(stderr),
            },
            "diagnostics": {
                "child_stderr_tail": child_stderr_tail(stderr),
                "child_returncode": None,
                "child_failure_reason": "timeout",
            },
        }
    parsed = _parse_last_json_stdout(stdout)
    if not parsed:
        return _attach_child_diagnostics(
            {
                "analyzed": False,
                "method": "gemini_worker_subprocess",
                "error": f"gemini_child_no_json: {child_stderr_tail(stderr or stdout, limit=1000)}",
                "provider_subprocess": {"returncode": proc.returncode},
            },
            stderr=stderr,
            returncode=proc.returncode,
            job_id=job_id,
            target_id=target_id,
            platform=platform,
            reason="no_json",
        )
    raw = parsed.get("raw") if isinstance(parsed.get("raw"), dict) else {}
    if proc.returncode != 0 and not raw.get("error"):
        raw["error"] = f"gemini_child_exit:{proc.returncode}: {child_stderr_tail(stderr or stdout, limit=1000)}"
    raw["provider_subprocess"] = {
        "returncode": proc.returncode,
        "timeout": False,
        "stderr_tail": child_stderr_tail(stderr),
    }
    if proc.returncode != 0 or raw.get("error") or not raw.get("analyzed"):
        _attach_child_diagnostics(
            raw,
            stderr=stderr,
            returncode=proc.returncode,
            job_id=job_id,
            target_id=target_id,
            platform=platform,
            reason=(
                "nonzero_exit" if proc.returncode != 0
                else "result_error" if raw.get("error")
                else "not_analyzed"
            ),
        )
    return raw


def _resolve_video_media(evidence: dict[str, Any]) -> dict[str, Any]:
    return resolve_video_media(
        evidence,
        apify_configured=bool(os.environ.get("APIFY_TOKEN", "").strip()),
        scrape_with_timeout=_scrape_with_apify_timeout,
    )
