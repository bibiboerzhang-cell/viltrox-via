"""V-KPI background task handlers."""
from __future__ import annotations

import asyncio
from typing import Any

from app.domains.kol import pool as kol_pool
from app.domains import media as media_cache
import app.domains.sync.refresh_tier as refresh_tier
import app.domains.tasks.enqueue as task_enqueue
from app.core.logging import get_logger
from app.domains import channels
from app.domains.channels import refill as channel_refill
from app.platform.apify_budget import (
    APIFY_BUDGET_SCOPE,
    ApifyBudgetBlocked,
    ApifyBudgetDecision,
)

_worker_logger = get_logger(__name__)


TERMINAL_STATUSES = {"done", "partial_done", "failed", "prefilter_rejected", "cancelled", "timeout"}


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _status_ok(value: Any) -> bool:
    return str(value or "").strip().lower() in {"ok", "success", "done", "synced"}


def _is_apify_budget_blocked_result(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    status = str(result.get("sync_status") or result.get("provider_status") or result.get("status") or "").strip().lower()
    code = str(result.get("code") or "").strip().lower()
    message = str(result.get("message") or result.get("error") or "").strip().lower()
    return status == "budget_blocked" or code == "apify_budget_hard_stop" or "apify_budget_hard_stop" in message


def _raise_if_apify_budget_blocked(result: Any, *, operation: str) -> None:
    if not _is_apify_budget_blocked_result(result):
        return
    raise ApifyBudgetBlocked(
        ApifyBudgetDecision(
            allowed=False,
            scope=APIFY_BUDGET_SCOPE,
            estimated_cost_usd=float(result.get("estimated_cost_usd") or 0.001),
            reason=str(result.get("reason") or "hard_stop_or_projected_cap"),
            operation=operation,
            actor_id=str(result.get("actor_id") or ""),
            platform=str(result.get("platform") or ""),
            source="redis_worker",
        )
    )


async def _is_terminal(queue, task_id: str) -> bool:
    current = await queue.get_status(task_id)
    return str((current or {}).get("status") or "").lower() in TERMINAL_STATUSES


def _revalidate_kol_refresh_target(payload: dict[str, Any]) -> dict[str, Any] | None:
    from app.db.connection import get_conn
    from app.domains.kol.my_kol_paid_action_access import (
        FENCE_KEY,
        MyKolPaidActionError,
        revalidate_target_fence,
    )

    has_fence = isinstance(payload.get(FENCE_KEY), dict)
    reason = str(payload.get("reason") or "").strip().lower()
    if not has_fence:
        if reason.startswith("manual_"):
            raise MyKolPaidActionError("my_kol_paid_action_fence_required", 403)
        return None
    return revalidate_target_fence(
        get_conn(), payload, expected_action="kol_pool_refresh"
    )


async def process_vkpi_official_channel_sync_job(queue, raw_job: dict) -> None:
    task_id = str(raw_job.get("task_id") or "")
    payload = raw_job.get("payload") or {}
    channel_id = _int(payload.get("channel_id"))
    max_posts = max(1, min(1000, _int(payload.get("max_posts"), 12)))
    staff = payload.get("staff") if isinstance(payload.get("staff"), dict) else {}
    item_key = f"channel:{channel_id or 'unknown'}"

    if not task_id or not channel_id:
        await queue.set_status(task_id, "failed", error_message="channel_id required", stage="vkpi_official_channel_sync")
        return

    if await _is_terminal(queue, task_id):
        return

    if task_enqueue.task_cancel_requested(task_id):
        task_enqueue.upsert_task_item(task_id, item_key, status="cancelled")
        await queue.set_status(task_id, "cancelled", summary="任务已取消", stage="cancelled")
        return

    task_enqueue.upsert_task_item(task_id, item_key, status="pending")
    task_enqueue.upsert_task_item(task_id, item_key, status="running")
    await queue.set_status(
        task_id,
        "running",
        stage="vkpi_official_channel_sync",
        summary=f"同步官方账号 {channel_id}",
        progress_pct=10,
        progress_text="正在同步官方账号快照",
    )

    loop = asyncio.get_running_loop()

    def progress_callback(pct: int, text: str) -> None:
        future = asyncio.run_coroutine_threadsafe(
            queue.set_status(
                task_id,
                "running",
                stage="vkpi_official_channel_sync",
                summary=text,
                progress_pct=pct,
                progress_text=text,
            ),
            loop,
        )
        future.result(timeout=10)

    def cancel_check() -> bool:
        return task_enqueue.task_cancel_requested(task_id)

    try:
        channel = channels.get_channel(channel_id, staff=staff)["channel"]
        result = await asyncio.to_thread(
            channel_refill.sync_channel_snapshot,
            channel,
            staff=staff,
            max_posts=max_posts,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )
    except channel_refill.CancelledException:
        task_enqueue.upsert_task_item(task_id, item_key, status="cancelled")
        await queue.set_status(
            task_id,
            "cancelled",
            summary="任务已取消",
            stage="cancelled",
            progress_pct=100,
            progress_text="官方账号同步已取消",
        )
        return
    except Exception as exc:
        if await _is_terminal(queue, task_id):
            return
        message = f"{type(exc).__name__}: {str(exc)[:500]}"
        task_enqueue.upsert_task_item(task_id, item_key, status="failed", error=message)
        # 2026-07-18 体检修:凡在 _mark 之前炸掉,渠道行保持上次成功态+空错误,
        # 断更完全不可诊断——异常路径也回写渠道行的失败态。
        try:
            from app.db.connection import get_conn

            conn = get_conn()
            conn.execute(
                "UPDATE vkpi_employee_channels SET last_sync_status='error', last_sync_error=?, last_sync_at=NOW() WHERE id=?",
                (message[:500], int(channel_id)),
            )
            conn.commit()
        except Exception:
            # 回写失败不掩盖原始异常;任务表已带 message 可诊断。
            _worker_logger.debug("official_channel_sync.error_markback_failed", exc_info=True)
        await queue.set_status(
            task_id,
            "failed",
            error_message=message,
            stage="vkpi_official_channel_sync",
            progress_pct=100,
            progress_text="官方账号同步失败",
        )
        return

    # A paid provider denial is terminal and must reach worker_main so the
    # stream message goes to DLQ.  Returning here would let the generic worker
    # ACK it as a successful handler completion.
    if _is_apify_budget_blocked_result(result):
        message = str(result.get("message") or result.get("error") or "Apify provider budget blocked")[:500]
        task_enqueue.upsert_task_item(task_id, item_key, status="failed", error=message, result=result)
        await queue.set_status(
            task_id,
            "failed",
            error_message=message,
            stage="budget_blocked",
            result_json=result,
            progress_pct=100,
            progress_text="Apify 预算硬停，未发起网络请求",
        )
        _raise_if_apify_budget_blocked(result, operation="vkpi_official_channel_sync")

    if await _is_terminal(queue, task_id):
        return

    if task_enqueue.task_cancel_requested(task_id):
        task_enqueue.upsert_task_item(task_id, item_key, status="cancelled", result=result)
        await queue.set_status(task_id, "cancelled", summary="任务已取消", stage="cancelled", result_json=result)
        return

    task_enqueue.upsert_task_item(task_id, item_key, status="done", result=result)
    await queue.set_status(
        task_id,
        "done",
        summary=str(result.get("message") or "官方账号同步完成"),
        stage="vkpi_official_channel_sync",
        result_json=result,
        progress_pct=100,
        progress_text="官方账号同步完成",
    )


async def process_vkpi_kol_pool_on_demand_refresh_job(queue, raw_job: dict) -> None:
    task_id = str(raw_job.get("task_id") or "")
    payload = raw_job.get("payload") or {}
    kol_pool_id = _int(payload.get("kol_pool_id"))
    max_posts = max(1, min(3, _int(payload.get("max_posts"), 1)))
    staff = payload.get("staff") if isinstance(payload.get("staff"), dict) else {}
    item_key = f"kol_pool:{kol_pool_id or 'unknown'}"

    if not task_id or not kol_pool_id:
        await queue.set_status(task_id, "failed", error_message="kol_pool_id required", stage="vkpi_kol_pool_on_demand_refresh")
        return

    if await _is_terminal(queue, task_id):
        return

    if task_enqueue.task_cancel_requested(task_id):
        task_enqueue.upsert_task_item(task_id, item_key, status="cancelled")
        await queue.set_status(task_id, "cancelled", summary="任务已取消", stage="cancelled")
        return

    from app.domains.kol.my_kol_paid_action_access import MyKolPaidActionError

    try:
        actor = await asyncio.to_thread(_revalidate_kol_refresh_target, payload)
    except MyKolPaidActionError as exc:
        await queue.set_status(
            task_id,
            "failed",
            error_message=exc.code,
            stage="authorization_blocked",
            result_json={
                "status": "blocked",
                "reason": exc.code,
                "provider_calls_performed": False,
            },
            progress_pct=100,
            progress_text="KOL 刷新授权已失效",
        )
        return
    if actor is not None:
        staff = actor

    task_enqueue.upsert_task_item(task_id, item_key, status="pending")
    task_enqueue.upsert_task_item(task_id, item_key, status="running")
    await queue.set_status(
        task_id,
        "running",
        stage="vkpi_kol_pool_on_demand_refresh",
        summary=f"按需刷新 KOL Pool {kol_pool_id}",
        progress_pct=10,
        progress_text="KOL 按需刷新任务启动",
    )

    try:
        result = await asyncio.to_thread(kol_pool.enrich_item, kol_pool_id, max_posts=max_posts, staff=staff)
        status = str(result.get("sync_status") or result.get("provider_status") or "unknown").strip().lower()
        refresh_tier.mark_kol_refreshed(kol_pool_id, status=status or "unknown")
    except Exception as exc:
        if await _is_terminal(queue, task_id):
            return
        message = f"{type(exc).__name__}: {str(exc)[:500]}"
        refresh_tier.mark_kol_refreshed(kol_pool_id, status="error")
        task_enqueue.upsert_task_item(task_id, item_key, status="failed", error=message)
        await queue.set_status(
            task_id,
            "failed",
            error_message=message,
            stage="vkpi_kol_pool_on_demand_refresh",
            progress_pct=100,
            progress_text="KOL 按需刷新失败",
        )
        return

    if _is_apify_budget_blocked_result(result):
        message = str(result.get("message") or result.get("error") or "Apify provider budget blocked")[:500]
        refresh_tier.mark_kol_refreshed(kol_pool_id, status="budget_blocked")
        task_enqueue.upsert_task_item(task_id, item_key, status="failed", error=message, result=result)
        await queue.set_status(
            task_id,
            "failed",
            error_message=message,
            stage="budget_blocked",
            result_json=result,
            progress_pct=100,
            progress_text="Apify 预算硬停，未发起网络请求",
        )
        _raise_if_apify_budget_blocked(result, operation="vkpi_kol_pool_on_demand_refresh")

    if await _is_terminal(queue, task_id):
        return

    if task_enqueue.task_cancel_requested(task_id):
        task_enqueue.upsert_task_item(task_id, item_key, status="cancelled", result=result)
        await queue.set_status(task_id, "cancelled", summary="任务已取消", stage="cancelled", result_json=result)
        return

    terminal_status = "done" if _status_ok(result.get("sync_status") or result.get("provider_status")) else "partial_done"
    task_enqueue.upsert_task_item(task_id, item_key, status=terminal_status, result=result)
    await queue.set_status(
        task_id,
        terminal_status,
        summary=str(result.get("message") or "KOL 按需刷新完成"),
        stage="vkpi_kol_pool_on_demand_refresh",
        result_json=result,
        progress_pct=100,
        progress_text="KOL 按需刷新完成",
    )


async def process_vkpi_video_cache_job(queue, raw_job: dict) -> None:
    task_id = str(raw_job.get("task_id") or "")
    payload = raw_job.get("payload") or {}
    platform = str(payload.get("platform") or "").strip().lower()
    video_id = str(payload.get("video_id") or "").strip()
    source_url = str(payload.get("source_url") or payload.get("url") or "").strip()
    force_refresh = bool(payload.get("force_refresh"))
    item_key = f"video:{platform or 'unknown'}:{video_id or 'unknown'}"

    if not task_id or not platform or not video_id:
        await queue.set_status(task_id, "failed", error_message="platform and video_id required", stage="vkpi_video_cache")
        return

    if await _is_terminal(queue, task_id):
        return

    if task_enqueue.task_cancel_requested(task_id):
        task_enqueue.upsert_task_item(task_id, item_key, status="cancelled")
        await queue.set_status(task_id, "cancelled", summary="任务已取消", stage="cancelled")
        return

    task_enqueue.upsert_task_item(task_id, item_key, status="pending")
    task_enqueue.upsert_task_item(task_id, item_key, status="running")
    await queue.set_status(
        task_id,
        "running",
        stage="vkpi_video_cache",
        summary=f"缓存视频 {platform}:{video_id}",
        progress_pct=10,
        progress_text="视频缓存任务启动",
    )

    loop = asyncio.get_running_loop()

    def progress_callback(pct: int, text: str) -> None:
        future = asyncio.run_coroutine_threadsafe(
            queue.set_status(
                task_id,
                "running",
                stage="vkpi_video_cache",
                summary=text,
                progress_pct=pct,
                progress_text=text,
            ),
            loop,
        )
        future.result(timeout=10)

    def cancel_check() -> bool:
        return task_enqueue.task_cancel_requested(task_id)

    try:
        result = await asyncio.to_thread(
            media_cache.cache_video_for_item,
            platform,
            video_id,
            source_url,
            force_refresh,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )
    except media_cache.VideoCacheCancelled:
        task_enqueue.upsert_task_item(task_id, item_key, status="cancelled")
        await queue.set_status(
            task_id,
            "cancelled",
            summary="视频缓存已取消",
            stage="cancelled",
            progress_pct=100,
            progress_text="视频缓存已取消",
        )
        return
    except Exception as exc:
        if await _is_terminal(queue, task_id):
            return
        message = f"{type(exc).__name__}: {str(exc)[:500]}"
        task_enqueue.upsert_task_item(task_id, item_key, status="failed", error=message)
        await queue.set_status(
            task_id,
            "failed",
            error_message=message,
            stage="vkpi_video_cache",
            progress_pct=100,
            progress_text="视频缓存失败",
        )
        return

    if await _is_terminal(queue, task_id):
        return

    if task_enqueue.task_cancel_requested(task_id):
        task_enqueue.upsert_task_item(task_id, item_key, status="cancelled", result=result)
        await queue.set_status(task_id, "cancelled", summary="视频缓存已取消", stage="cancelled", result_json=result)
        return

    status = str(result.get("status") or "").lower()
    if status == "failed":
        message = str(result.get("error") or result.get("reason") or "video cache failed")
        task_enqueue.upsert_task_item(task_id, item_key, status="failed", result=result, error=message)
        await queue.set_status(
            task_id,
            "failed",
            error_message=message,
            stage="vkpi_video_cache",
            result_json=result,
            progress_pct=100,
            progress_text="视频缓存失败",
        )
        return

    if result.get("skipped"):
        task_enqueue.upsert_task_item(task_id, item_key, status="skipped", result=result)
        await queue.set_status(
            task_id,
            "done",
            summary=str(result.get("skip_reason") or "视频缓存已跳过"),
            stage="vkpi_video_cache",
            result_json=result,
            progress_pct=100,
            progress_text="视频缓存已跳过",
        )
        return

    task_enqueue.upsert_task_item(task_id, item_key, status="done", result=result)
    await queue.set_status(
        task_id,
        "done",
        summary="视频缓存完成",
        stage="vkpi_video_cache",
        result_json=result,
        progress_pct=100,
        progress_text="视频缓存完成",
    )
