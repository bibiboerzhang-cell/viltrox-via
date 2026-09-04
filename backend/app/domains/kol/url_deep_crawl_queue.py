"""Queue/worker entry layer for KOL profile deep crawl.

Behavior-preserving extraction from url_deep_crawl.py: the enqueue + worker
runner cluster lives here and is re-exported from the parent module so every
internal reference and external import path stays unchanged. The parent's
``dry_run_url_deep_crawl`` is imported lazily inside the runner to avoid a
circular import (parent re-exports from this module).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime
from app.domains.kol import url_route_plan
from app.domains.kol import (
    url_deep_crawl_maintenance_fence as maintenance_fence,
    url_deep_crawl_queue_runtime as queue_runtime,
    url_deep_crawl_site_scan as site_scan,
)
from app.domains.kol.url_deep_crawl_helpers import (
    _canonical_url,
    _normalise_handle,
    _video_id,
)
from app.domains.tasks.apify_idempotency import active_job_idempotency_key, enqueue_active_apify_job

logger = get_logger("viltrox.domains.kol.url_deep_crawl")


# ── 队列铁律(2026-06-12 裁令:所有 LLM 搜索都要进左侧队列)──
DEEP_CRAWL_JOB_TYPE = "kol_profile_deep_crawl"
MAINTENANCE_REFRESH_TASK_KEY = maintenance_fence.MAINTENANCE_REFRESH_TASK_KEY
MAINTENANCE_TARGET_FENCE_KIND = maintenance_fence.MAINTENANCE_TARGET_FENCE_KIND
MAINTENANCE_REFRESH_TIMEZONE = maintenance_fence.MAINTENANCE_REFRESH_TIMEZONE
PROFILE_DEEP_CRAWL_MODES = {"auto", "profile_with_video", "account_deep"}
TARGET_WRITE_FENCE_TERMINAL_CODES = frozenset(
    {
        "staff_identity_required",
        "kol_pool_not_found",
        "kol_pool_duplicate_not_writable",
        "my_kol_video_write_forbidden",
        "kol_profile_url_missing",
        "kol_profile_url_mismatch",
        "kol_profile_identity_invalid",
        "kol_profile_identity_mismatch",
        "kol_profile_write_fence_invalid",
        "kol_profile_target_drifted",
        "kol_profile_actor_revoked",
        "kol_profile_actor_changed",
        "kol_profile_write_permission_revoked",
        "kol_content_monitor_fence_invalid",
        "kol_content_monitor_cancelled",
        "kol_content_monitor_target_drifted",
        "maintenance_refresh_target_fence_invalid",
        "maintenance_refresh_target_not_found",
        "maintenance_refresh_target_merged",
        "maintenance_refresh_target_drifted",
        "maintenance_refresh_target_identity_invalid",
        "maintenance_refresh_provider_identity_mismatch",
    }
)
_PLATFORM_VIDEO_ID_PATTERNS = {
    "instagram": re.compile(r"^[A-Za-z0-9_-]{3,96}$"),
    "tiktok": re.compile(r"^[0-9]{5,32}$"),
    "youtube": re.compile(r"^[A-Za-z0-9_-]{3,128}$"),
}


def _content_url_video_id(platform: Any, content_url: Any) -> str:
    """Safely extract a platform-native video id from a public content URL."""

    platform_key = str(platform or "").strip().lower()
    pattern = _PLATFORM_VIDEO_ID_PATTERNS.get(platform_key)
    if pattern is None:
        return ""
    try:
        parsed = urlparse(str(content_url or "").strip())
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    host = parsed.hostname.lower().removeprefix("www.")
    allowed_host = (
        (platform_key == "instagram" and (host == "instagram.com" or host.endswith(".instagram.com")))
        or (platform_key == "tiktok" and (host == "tiktok.com" or host.endswith(".tiktok.com")))
        or (
            platform_key == "youtube"
            and (host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com"))
        )
    )
    if not allowed_host:
        return ""
    native_id = _video_id(platform_key, host, parsed.path, parsed.query)
    return native_id if pattern.fullmatch(native_id) else ""


def _video_cache_key(platform: Any, content_url: Any) -> str:
    """Return only a verified native id; never create new evidence-id keys."""

    return _content_url_video_id(platform, content_url)


def _profile_deep_crawl_mode(value: Any, *, legacy_default: bool = False) -> str:
    mode = str(value or "").strip()
    if not mode and legacy_default:
        return "account_deep"
    if mode not in PROFILE_DEEP_CRAWL_MODES:
        raise ValueError("unsupported profile deep-crawl mode")
    return mode


def _representative_video_limit(value: Any, *, legacy_default: bool = False) -> int:
    if value in (None, "") and legacy_default:
        return 1
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError("representative_video_limit must be an integer") from None
    if parsed < 1 or parsed > 3:
        raise ValueError("representative_video_limit must be between 1 and 3")
    return parsed


def _maintenance_refresh_batch_block_reason(
    payload: dict[str, Any] | None,
    *,
    as_of: datetime | None = None,
) -> str:
    return maintenance_fence._maintenance_refresh_batch_block_reason(
        payload,
        as_of=as_of,
    )


def _maintenance_refresh_execution_block_reason(
    payload: dict[str, Any] | None = None,
) -> str:
    return maintenance_fence._maintenance_refresh_execution_block_reason(
        payload,
        get_connection=get_conn,
        batch_block_reason=_maintenance_refresh_batch_block_reason,
    )


def profile_deep_crawl_is_fresh(kol_pool_id: int | None, *, max_age_hours: int = 24) -> bool:
    """Avoid paying for the same automatic URL refresh repeatedly."""
    if not kol_pool_id:
        return False
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max(1, int(max_age_hours)))).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        row = get_conn().execute(
            """
            SELECT 1
            FROM vkpi_kol_url_deep_crawl_runs
            WHERE kol_pool_id=? AND status='ready' AND created_at>=?
            LIMIT 1
            """,
            (int(kol_pool_id), cutoff),
        ).fetchone()
        return bool(row)
    except Exception:
        logger.warning("profile deep-crawl freshness check failed kol_pool_id=%s", kol_pool_id, exc_info=True)
        return False


def _profile_target_row(
    conn: Any,
    kol_pool_id: int,
    *,
    for_update: bool = False,
    include_raw_platform_data: bool = False,
) -> dict[str, Any]:
    return maintenance_fence._profile_target_row(
        conn,
        kol_pool_id,
        for_update=for_update,
        postgres_runtime=is_postgres_runtime() if for_update else False,
        include_raw_platform_data=include_raw_platform_data,
    )


_validated_profile_identity = maintenance_fence._validated_profile_identity


def _build_target_write_fence(
    conn: Any,
    *,
    kol_pool_id: int,
    submitted_url: str,
    staff: dict[str, Any] | None,
) -> dict[str, Any]:
    """Validate My-KOL row ownership before a paid job can be inserted."""

    from app.domains.kol import video_tracking

    actor_id = video_tracking._assert_target_writable(  # noqa: SLF001 - shared row-write boundary
        conn,
        kol_pool_id=int(kol_pool_id),
        staff=staff,
    )
    row = _profile_target_row(conn, int(kol_pool_id))
    identity = _validated_profile_identity(row, submitted_url)
    return {
        "version": 1,
        "kol_pool_id": int(kol_pool_id),
        "staff_id": int(actor_id),
        "user_id": int((staff or {}).get("user_id") or 0) or None,
        **identity,
    }


_build_maintenance_target_fence = maintenance_fence._build_maintenance_target_fence
_stable_profile_native_ids = maintenance_fence._stable_profile_native_ids
_stable_profile_handle = maintenance_fence._stable_profile_handle
_validated_maintenance_profile_identity = (
    maintenance_fence._validated_maintenance_profile_identity
)


def _revalidate_maintenance_target_fence(
    payload: dict[str, Any],
    *,
    conn: Any | None = None,
    lock_target: bool = False,
) -> dict[str, Any] | None:
    return maintenance_fence._revalidate_maintenance_target_fence(
        payload,
        conn=conn,
        lock_target=lock_target,
        get_connection=get_conn,
        postgres_runtime=is_postgres_runtime() if lock_target else False,
    )


def _revalidate_target_write_fence(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Fail closed immediately before provider execution for fenced UI jobs."""

    fence = payload.get("target_write_fence")
    if not isinstance(fence, dict):
        # Legacy/background jobs use their existing scheduler authorization.
        # Only the My-KOL paid action opts into this durable actor/target fence.
        return None

    from app.domains.kol import video_tracking
    from app.domains.kol.video_metric_refresh import authorize_video_metric_refresh_actor

    try:
        kol_pool_id = int(payload.get("kol_pool_id") or 0)
        fenced_kol_pool_id = int(fence.get("kol_pool_id") or 0)
        staff_id = int(fence.get("staff_id") or 0)
        fenced_user_id = int(fence.get("user_id") or 0)
    except (TypeError, ValueError):
        raise video_tracking.VideoTrackingError("kol_profile_write_fence_invalid", 403) from None
    if kol_pool_id <= 0 or kol_pool_id != fenced_kol_pool_id or staff_id <= 0:
        raise video_tracking.VideoTrackingError("kol_profile_target_drifted", 409)

    conn = get_conn()
    current_staff, actor_error = authorize_video_metric_refresh_actor(
        conn,
        staff_id=staff_id,
        kol_pool_id=kol_pool_id,
    )
    if current_staff is None:
        error_code = {
            "video_refresh_actor_permission_revoked": "kol_profile_write_permission_revoked",
            "video_refresh_target_permission_revoked": "my_kol_video_write_forbidden",
        }.get(actor_error, "kol_profile_actor_revoked")
        raise video_tracking.VideoTrackingError(error_code, 403)
    current_user_id = int(current_staff.get("user_id") or 0)
    if fenced_user_id and current_user_id != fenced_user_id:
        raise video_tracking.VideoTrackingError("kol_profile_actor_changed", 403)
    # ``authorize_video_metric_refresh_actor`` has already re-run the same
    # active-user, vkpi:write and row-level ownership gate used by durable
    # video refreshes, including suspended staff and a removed favorite.
    row = _profile_target_row(conn, kol_pool_id)
    current_identity = _validated_profile_identity(row, str(payload.get("url") or ""))
    if (
        current_identity["canonical_profile_url"]
        != str(fence.get("canonical_profile_url") or "")
        or current_identity["platform"] != str(fence.get("platform") or "")
        or current_identity["stable_identity_key"]
        != str(fence.get("stable_identity_key") or "")
    ):
        raise video_tracking.VideoTrackingError("kol_profile_target_drifted", 409)
    # Even an equivalent alternate spelling is not forwarded.  The provider
    # always receives the latest database canonical URL after the identity and
    # TOCTOU checks above have passed.
    payload["url"] = current_identity["canonical_profile_url"]
    # Subscription cancellation/generation is checked last, immediately before
    # the provider-facing profile flow. A paused or replaced subscription is a
    # terminal authorization decision and must never retry into spend.
    from app.domains.kol import content_monitoring

    content_monitoring.revalidate_monitor_fence(payload, conn=conn)
    return current_staff


def _active_profile_job(
    conn: Any,
    *,
    clean_url: str,
    content_monitor_fence: dict[str, Any] | None,
    maintenance_refresh: bool = False,
) -> dict[str, Any]:
    """Return only an active job that can safely satisfy this exact request.

    A monitoring subscription owns its completion receipt.  Reusing an
    unrelated one-shot crawl for the same public URL would bind the
    subscription to a job whose payload has no monitor fence, so its terminal
    callback could never update the subscription.  Conversely, a one-shot
    request must not observe a monitoring job as its own active request.
    """

    if content_monitor_fence is None:
        row = conn.execute(
            """
            SELECT id FROM apify_jobs
            WHERE job_type=? AND status IN ('queued','running')
              AND payload->>'url'=?
              AND payload->'content_monitor_fence' IS NULL
              AND (
                    ? IS TRUE
                    OR LOWER(CAST(COALESCE(payload->>'maintenance_refresh', 'false') AS TEXT))
                       NOT IN ('true', '1')
                  )
            ORDER BY id DESC
            LIMIT 1
            """,
            (DEEP_CRAWL_JOB_TYPE, clean_url, bool(maintenance_refresh)),
        ).fetchone()
        return dict(row) if row else {}

    identity = (
        int(content_monitor_fence["version"]),
        int(content_monitor_fence["subscription_id"]),
        int(content_monitor_fence["staff_id"]),
        int(content_monitor_fence["kol_pool_id"]),
        int(content_monitor_fence["generation"]),
    )
    row = conn.execute(
        """
        SELECT id FROM apify_jobs
        WHERE job_type=? AND status IN ('queued','running')
          AND CAST(payload->'content_monitor_fence'->>'version' AS TEXT)=?
          AND CAST(payload->'content_monitor_fence'->>'subscription_id' AS TEXT)=?
          AND CAST(payload->'content_monitor_fence'->>'staff_id' AS TEXT)=?
          AND CAST(payload->'content_monitor_fence'->>'kol_pool_id' AS TEXT)=?
          AND CAST(payload->'content_monitor_fence'->>'generation' AS TEXT)=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (DEEP_CRAWL_JOB_TYPE, *(str(value) for value in identity)),
    ).fetchone()
    return dict(row) if row else {}


def _profile_job_idempotency_key(
    clean_url: str,
    content_monitor_fence: dict[str, Any] | None,
    *,
    maintenance_refresh: bool = False,
) -> str:
    if content_monitor_fence is None:
        key_scope = (
            f"{DEEP_CRAWL_JOB_TYPE}.maintenance"
            if maintenance_refresh
            else DEEP_CRAWL_JOB_TYPE
        )
        return active_job_idempotency_key(key_scope, clean_url)
    return active_job_idempotency_key(
        f"{DEEP_CRAWL_JOB_TYPE}.content-monitor",
        int(content_monitor_fence["version"]),
        int(content_monitor_fence["subscription_id"]),
        int(content_monitor_fence["staff_id"]),
        int(content_monitor_fence["kol_pool_id"]),
        int(content_monitor_fence["generation"]),
    )


# Keep the historical queue-module patch points alive after the site-scan split.
# Tests and callers may replace these guards; the wrappers deliberately resolve
# them at call time so a replacement still governs the real scan path.
_robots_allows = site_scan._robots_allows
_site_already_scanned = site_scan._site_already_scanned
_save_site_contacts = site_scan._save_site_contacts


def _scan_site_contacts(conn: Any, url: str, kol_pool_id: int) -> dict[str, Any]:
    return site_scan._scan_site_contacts(
        conn,
        url,
        kol_pool_id,
        robots_allows=_robots_allows,
        site_already_scanned=_site_already_scanned,
        save_site_contacts=_save_site_contacts,
    )


def _divert_off_crawler_url(
    conn: Any,
    clean_url: str,
    *,
    kol_pool_id: int | None,
    monitored: bool,
) -> dict[str, Any] | None:
    return site_scan._divert_off_crawler_url(
        conn,
        clean_url,
        kol_pool_id=kol_pool_id,
        monitored=monitored,
        scan_site_contacts=_scan_site_contacts,
    )


def _canonical_enqueued_profile_url(
    clean_url: str,
    target_write_fence: dict[str, Any] | None,
) -> str:
    if target_write_fence is not None:
        return str(target_write_fence["canonical_profile_url"])
    if url_route_plan.plan_url_route_from_url(clean_url).route == url_route_plan.ROUTE_PROFILE:
        return _canonical_url(clean_url) or clean_url
    return clean_url


def enqueue_profile_deep_crawl_job(
    url: str,
    *,
    kol_pool_id: int | None = None,
    max_posts: int = 3,
    mode: str = "account_deep",
    representative_video_limit: int = 1,
    staff: dict[str, Any] | None = None,
    search_session_id: int | None = None,
    source: str = "kol_profile_deep_crawl",
    queue_lane: str = "interactive",
    enforce_target_write: bool = False,
    content_monitor_fence: dict[str, Any] | None = None,
    suppress_final_v1: bool = False,
    suppress_contact_followup: bool = False,
    suppress_profile_followups: bool = False,
    maintenance_refresh: bool = False,
    maintenance_batch_date: str = "",
    since_iso: str = "",
) -> dict[str, Any]:
    """把账号深爬 execute 入 apify_jobs 队列(泳道可见),替代同步 HTTP 内爬。

    幂等:普通任务仅复用同 URL 的普通 active 任务;内容监控仅复用
    同 subscription/staff/KOL/generation 围栏的 active 任务。

    ``since_iso``(可选,ISO 日期 YYYY-MM-DD):发布时间下限,只在抓取器支持日期
    截断的平台生效,其余平台由客户端按发布时间筛。**它既不抬 max_posts,也不改
    幂等口径**——同 URL 已有在途任务时仍然并入那一条(窗口以先入队的为准),由调
    用方如实回执「已有一次在跑,本次并入」,而不是各起一条去翻倍花钱。
    """
    conn = get_conn()
    clean_url = str(url or "").strip()
    if not clean_url:
        raise ValueError("url required")
    maintenance_target_fence: dict[str, Any] | None = None
    if maintenance_refresh:
        try:
            maintenance_target_id = int(kol_pool_id or 0)
        except (TypeError, ValueError):
            maintenance_target_id = 0
        if maintenance_target_id <= 0:
            raise ValueError("kol_pool_id required for maintenance refresh")
        maintenance_target_fence = _build_maintenance_target_fence(
            conn,
            kol_pool_id=maintenance_target_id,
            submitted_url=clean_url,
        )
        clean_url = str(maintenance_target_fence["canonical_profile_url"])
    target_write_fence: dict[str, Any] | None = None
    if enforce_target_write:
        try:
            target_id = int(kol_pool_id or 0)
        except (TypeError, ValueError):
            target_id = 0
        if target_id <= 0:
            raise ValueError("kol_pool_id required")
        target_write_fence = _build_target_write_fence(
            conn,
            kol_pool_id=target_id,
            submitted_url=clean_url,
            staff=staff,
        )
        # Provider input is always the database-backed canonical locator, not
        # a request-supplied spelling/query variant.
        clean_url = str(target_write_fence["canonical_profile_url"])
    if content_monitor_fence is not None:
        if target_write_fence is None:
            from app.domains.kol.video_tracking import VideoTrackingError

            raise VideoTrackingError("kol_content_monitor_fence_invalid", 403)
        from app.domains.kol import content_monitoring

        content_monitor_fence = content_monitoring.validate_monitor_fence_for_enqueue(
            conn,
            content_monitor_fence,
            target_write_fence=target_write_fence,
        )
    diverted = _divert_off_crawler_url(
        conn, clean_url, kol_pool_id=kol_pool_id, monitored=content_monitor_fence is not None
    )
    if diverted is not None:
        return diverted
    clean_url = _canonical_enqueued_profile_url(clean_url, target_write_fence)
    normalized_mode = _profile_deep_crawl_mode(mode)
    normalized_representative_limit = _representative_video_limit(representative_video_limit)
    normalized_queue_lane = str(queue_lane or "interactive").strip().lower()
    if normalized_queue_lane not in {"interactive", "batch"}:
        raise ValueError("queue_lane must be interactive or batch")
    active = _active_profile_job(
        conn,
        clean_url=clean_url,
        content_monitor_fence=content_monitor_fence,
        maintenance_refresh=maintenance_refresh,
    )
    if active:
        return {"status": "already_queued", "job_id": int(active["id"])}
    payload = {
        "queue_lane": normalized_queue_lane,
        "url": clean_url,
        "kol_pool_id": int(kol_pool_id) if kol_pool_id else None,
        "max_posts": max(1, min(12, int(max_posts or 3))),
        "mode": normalized_mode,
        "representative_video_limit": normalized_representative_limit,
        # 泳道 label=kind+query_text,kind 已是「账号分析」——query_text 只留 URL,
        # 否则显示成"账号分析 · 账号分析 · url"(2026-06-12 截图案)。
        "query_text": clean_url[:96],
        "target_type": "kol_profile",
        # target_id=泳道点击回跳 MY KOL 的定位键(2026-06-12 裁令:从哪发起回哪去)
        "target_id": int(kol_pool_id) if kol_pool_id else None,
        "triggered_by_user_id": (staff or {}).get("user_id"),
        "staff_id": (staff or {}).get("id") or (staff or {}).get("staff_id"),
        "search_session_id": int(search_session_id) if search_session_id else None,
        "source": str(source or "kol_profile_deep_crawl")[:80],
    }
    if target_write_fence is not None:
        payload["target_write_fence"] = target_write_fence
    if maintenance_target_fence is not None:
        payload["maintenance_target_fence"] = maintenance_target_fence
    if content_monitor_fence is not None:
        payload["content_monitor_fence"] = content_monitor_fence
        payload["monitoring_window"] = {"kind": "recent_posts", "max_posts": 12, "full_history": False}
    if suppress_final_v1:
        payload["suppress_final_v1"] = True
    if suppress_contact_followup:
        payload["suppress_contact_followup"] = True
    if suppress_profile_followups:
        payload["suppress_profile_followups"] = True
    if maintenance_refresh:
        payload["maintenance_refresh"] = True
        normalized_batch_date = str(maintenance_batch_date or "").strip()[:10]
        if normalized_batch_date:
            payload["maintenance_batch_date"] = normalized_batch_date
    normalized_since = str(since_iso or "").strip()[:32]
    if normalized_since:
        payload["since"] = normalized_since
    job, inserted = enqueue_active_apify_job(
        conn,
        job_type=DEEP_CRAWL_JOB_TYPE,
        payload=payload,
        idempotency_key=_profile_job_idempotency_key(
            clean_url,
            content_monitor_fence,
            maintenance_refresh=maintenance_refresh,
        ),
    )
    conn.commit()
    return {"status": "queued" if inserted else "already_queued", "job_id": int(job["id"])}


def enqueue_stored_video_analysis_job(
    *,
    kol_pool_id: int,
    evidence_id: int,
    staff: dict[str, Any] | None = None,
    search_session_id: int | None = None,
    search_session_item_id: int | None = None,
    source: str = "kol_url_video_existing_evidence",
    local_evaluation: bool = False,
) -> dict[str, Any]:
    """Queue only final_v1 for already-owned evidence; never crawl a profile.

    The smart URL route already resolved the native video identity and its KOL
    from local evidence. Sending that case through ``kol_profile_deep_crawl``
    both duplicated work and rejected ``mode=video_deep`` before insertion.
    """

    from app.domains.kol.video_analysis_enqueue import _enqueue_final_v1_video_analysis

    result = _enqueue_final_v1_video_analysis(
        get_conn(),
        kol_pool_id=int(kol_pool_id),
        evidence_id=int(evidence_id),
        staff=staff,
        source=str(source or "kol_url_video_existing_evidence")[:80],
        batch="on_demand",
        commit=True,
        search_session_id=int(search_session_id) if search_session_id else None,
        search_session_item_id=(
            int(search_session_item_id) if search_session_item_id else None
        ),
        local_evaluation=bool(local_evaluation),
    )
    job = result.get("job") if isinstance(result.get("job"), dict) else {}
    return {
        **result,
        "job_id": int(job.get("id") or 0) or None,
    }


def run_profile_deep_crawl_for_job(payload: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """worker 入口:执行账号深爬(与 HTTP execute 同一内核 dry_run_url_deep_crawl execute=True)。

    新任务原样执行 API 写入的模式与代表视频数。旧任务没有这两个字段时继续使用
    account_deep + 1，避免历史队列升级后改变行为。
    """
    from app.domains.kol.url_deep_crawl import dry_run_url_deep_crawl

    # This is intentionally the first durable-worker action.  If the actor,
    # permission, ownership, target or canonical profile URL changed while the
    # job waited, no provider-facing crawl function is reached.
    is_content_monitoring = isinstance(payload.get("content_monitor_fence"), dict)
    is_maintenance_refresh = payload.get("maintenance_refresh") is True
    from app.domains.kol.video_tracking import VideoTrackingError

    try:
        if is_maintenance_refresh:
            block_reason = _maintenance_refresh_execution_block_reason(payload)
            if block_reason:
                logger.warning(
                    "maintenance refresh blocked before provider url=%s reason=%s",
                    str(payload.get("url") or "")[:160],
                    block_reason,
                )
                return {
                    "status": block_reason,
                    "reason": block_reason,
                    "provider_calls_performed": False,
                    "llm_calls_performed": False,
                    "viltrox_fit_score_untouched": True,
                }
            _revalidate_maintenance_target_fence(payload)

        revalidated_staff = _revalidate_target_write_fence(payload)
    except VideoTrackingError as exc:
        # Every fence check above runs before dry_run_url_deep_crawl can reach
        # a provider.  Persist that phase truth on terminalization.
        exc.provider_calls_performed = False
        raise
    if revalidated_staff is not None:
        staff = revalidated_staff

    body = {
        "url": str(payload.get("url") or ""),
        "execute": True,
        "mode": _profile_deep_crawl_mode(payload.get("mode"), legacy_default=True),
        "max_posts": 1 if is_maintenance_refresh else (payload.get("max_posts") or 3),
        "representative_video_limit": _representative_video_limit(
            payload.get("representative_video_limit"),
            legacy_default=True,
        ),
        "source": str(payload.get("source") or "queue:kol_profile_deep_crawl"),
        "suppress_final_v1": (
            is_maintenance_refresh or payload.get("suppress_final_v1") is True
        ),
        "suppress_profile_followups": (
            is_maintenance_refresh or payload.get("suppress_profile_followups") is True
        ),
        "maintenance_refresh": is_maintenance_refresh,
        "suppress_contact_acquisition": (
            is_maintenance_refresh or payload.get("suppress_contact_followup") is True
        ),
        "suppress_avatar_landing": is_maintenance_refresh,
        # 发布时间下限:老任务没有这个键 → "" → 与升级前逐字节同行为。
        "since": str(payload.get("since") or ""),
    }
    if is_maintenance_refresh:
        body["kol_pool_id"] = int(payload.get("kol_pool_id") or 0)
        body["maintenance_target_fence"] = dict(payload["maintenance_target_fence"])
    if revalidated_staff is not None:
        body["paid_action_staff"] = {
            key: revalidated_staff.get(key)
            for key in (
                "id",
                "user_id",
                "role",
                "is_owner",
                "permissions",
                "permissions_json",
                "active",
                "suspended_at",
            )
            if key in revalidated_staff
        }
        body["enforce_target_write"] = True
    result = dry_run_url_deep_crawl(body)
    # 队列路径不经 HTTP 路由的 _attach_smart_url_session——session 必须在此自建,
    # 否则任务完成后 payload 无 search_session_id,泳道「最近完成」按规则将其滤掉(一闪而过案)。
    if not is_content_monitoring and not is_maintenance_refresh:
        queue_runtime.attach_search_session(
            payload=payload,
            body=body,
            result=result,
            staff=staff,
        )
    # 媒体进 R2(2026-06-12 裁令:"理论都是在 R2 然后回传"):深爬产出的 evidence
    # 缩略图(cache_image)与非 YT 平台视频(cache_video_for_item,YT 走 embed 不缓存)
    # 就地喂缓存——失败不毁任务(媒体缓存属增强,非主链)。
    kol_pool_id = payload.get("kol_pool_id")
    if kol_pool_id and not is_content_monitoring and not is_maintenance_refresh:
        queue_runtime.warm_media_cache(
            kol_pool_id=kol_pool_id,
            get_connection=get_conn,
            video_cache_key=_video_cache_key,
        )
    return result
