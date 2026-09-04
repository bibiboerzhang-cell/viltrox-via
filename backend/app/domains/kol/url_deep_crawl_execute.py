"""URL deep-crawl execute + profile-build cluster (从 url_deep_crawl.py 抽出,行为不变).

包含 execute 路径(profile / 新老创作者 video / 代表作 / 历史视频)与其依赖的
profile 抓取 / 组装 / 落库 / run 记录 helper。函数体逐字搬运,调用点由 url_deep_crawl
re-export 兜住(behavior-preserving move)。

循环导入纪律:本模块绝不在顶层 import url_deep_crawl;唯一回指原文件的运行时引用
(_classified_from_creator_identity)在函数体内 lazy import。ClassifiedUrl 仅作类型注解,
from __future__ annotations 已字符串化,运行时无需导入(TYPE_CHECKING 下声明)。

红线:LLM 绝不写 viltrox_fit_score;本模块零 fit 写,只透传上游 *_changed_ids。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.tasks.apify_idempotency import active_job_idempotency_key, enqueue_active_apify_job
from app.domains.kol import url_deep_crawl_execute_profile_data as profile_data
from app.domains.kol.pool_common import (
    _json,
    _table_columns,
)
from app.domains.kol.profile_basics import write_kol_profile_basics
from app.domains.kol.url_deep_crawl_helpers import (
    _compact_enqueue_result,
    _compact_profile_write_result,
    _compact_video_evidence_result,
    _fit_changed_ids,
    _has_matchable_creator_identity,
    _load_json,
    _max_posts,
    _parse_date,
    _profile_exclude_video_urls,
    _profile_history_video_limit,
    _profile_representative_video_limit,
    _profile_should_enqueue_representative_videos,
    _profile_should_materialize_history_videos,
    _public_profile_data,
    _public_video_metadata,
    _raw_profile_backfilled_at,
    _video_execute_mode,
    _video_metadata_date,
)
from app.domains.kol.url_deep_crawl_execute_profile_videos import (  # noqa: F401  re-export 兜调用点(含私有名)
    _execute_profile_history_video_evidence,
    _execute_profile_representative_video_analysis,
)
from app.domains.kol.url_deep_crawl_execute_video_flows import (  # noqa: F401  re-export 兜调用点(含私有名)
    _cache_video_flow_url,
    _execute_existing_creator_video_flow,
    _execute_new_creator_video_flow,
)
from app.domains.kol.url_deep_crawl_video_meta import (
    _filter_incremental_profile_videos,
    _profile_representative_video_metadata,
)
from app.domains.kol.video_analysis_enqueue import _enqueue_final_v1_video_analysis
from app.domains.kol.video_evidence import ensure_video_evidence_from_url

if TYPE_CHECKING:
    from app.domains.kol.url_deep_crawl import ClassifiedUrl

logger = get_logger("viltrox.domains.kol.url_deep_crawl_execute")


def _revalidate_maintenance_profile_target(
    body: dict[str, Any],
    *,
    conn: Any,
    resolved_kol_pool_id: int | None,
    lock_target: bool = False,
    provider_calls_performed: bool | None = None,
) -> None:
    """Keep a system refresh on its selected row across provider latency."""

    if body.get("maintenance_refresh") is not True:
        return
    from app.domains.kol import url_deep_crawl_queue
    from app.domains.kol.video_tracking import VideoTrackingError

    try:
        current = url_deep_crawl_queue._revalidate_maintenance_target_fence(  # noqa: SLF001
            body,
            conn=conn,
            lock_target=lock_target,
        )
        if int((current or {}).get("kol_pool_id") or 0) != int(resolved_kol_pool_id or 0):
            raise VideoTrackingError("maintenance_refresh_target_drifted", 409)
    except VideoTrackingError as exc:
        if isinstance(provider_calls_performed, bool):
            exc.provider_calls_performed = provider_calls_performed
        raise


def _verify_maintenance_crawl_identity(
    body: dict[str, Any],
    crawl: dict[str, Any],
) -> None:
    """Reject a provider result whose native account id differs from the DB fence."""

    if body.get("maintenance_refresh") is not True:
        return
    from app.domains.kol import url_deep_crawl_queue
    from app.domains.kol.video_tracking import VideoTrackingError

    if str(crawl.get("status") or "").lower() not in {"ok", "synced"}:
        return
    fence = body.get("maintenance_target_fence")
    expected = (
        dict(fence.get("stable_native_ids"))
        if isinstance(fence, dict) and isinstance(fence.get("stable_native_ids"), dict)
        else {}
    )
    expected_handle = (
        str(fence.get("stable_handle") or "")
        if isinstance(fence, dict)
        else ""
    )
    try:
        observed = url_deep_crawl_queue._stable_profile_native_ids(  # noqa: SLF001
            str(fence.get("platform") or ""),
            crawl.get("profile_payload"),
        )
        observed_handle = url_deep_crawl_queue._stable_profile_handle(  # noqa: SLF001
            str(fence.get("platform") or ""),
            crawl.get("profile_payload"),
        )
    except ValueError as exc:
        raise VideoTrackingError(
            "maintenance_refresh_provider_identity_mismatch",
            409,
        ) from exc
    if any(observed.get(field) != value for field, value in expected.items()):
        raise VideoTrackingError("maintenance_refresh_provider_identity_mismatch", 409)
    youtube_channel_equivalent = (
        str(fence.get("platform") or "") == "youtube"
        and observed_handle == str(expected.get("channel_id") or "")
        and observed.get("channel_id") == expected.get("channel_id")
    )
    if (
        observed_handle
        and observed_handle != expected_handle
        and not youtube_channel_equivalent
    ):
        raise VideoTrackingError("maintenance_refresh_provider_identity_mismatch", 409)
    if not expected and (not expected_handle or observed_handle != expected_handle):
        raise VideoTrackingError("maintenance_refresh_provider_identity_mismatch", 409)


def _verify_maintenance_profile_data_identity(
    body: dict[str, Any],
    profile_data: dict[str, Any],
) -> None:
    """Validate, then freeze, generated identity fields to the durable target."""

    if body.get("maintenance_refresh") is not True:
        return
    from app.domains.kol import url_deep_crawl_queue
    from app.domains.kol.video_tracking import VideoTrackingError

    fence = body.get("maintenance_target_fence")
    if not isinstance(fence, dict):
        raise VideoTrackingError("maintenance_refresh_target_fence_invalid", 403)
    platform = str(fence.get("platform") or "").strip().lower()
    expected_handle = str(fence.get("stable_handle") or "")
    expected_url = str(fence.get("canonical_profile_url") or "")
    actual_platform = str(profile_data.get("platform") or "").strip().lower()
    actual_handle = url_deep_crawl_queue._normalise_handle(  # noqa: SLF001
        platform,
        profile_data.get("handle"),
    )
    actual_url = url_deep_crawl_queue._canonical_url(  # noqa: SLF001
        str(profile_data.get("profile_url") or "")
    )
    expected_native = (
        dict(fence.get("stable_native_ids"))
        if isinstance(fence.get("stable_native_ids"), dict)
        else {}
    )
    youtube_channel_id = str(expected_native.get("channel_id") or "")
    observed_native: dict[str, str] = {}
    observed_url_channel_id = ""
    if platform == "youtube" and youtube_channel_id:
        from app.services.verification.viltrox_official import (
            detect_platform_from_profile_url,
            extract_handle_from_profile_url,
        )

        try:
            observed_native = url_deep_crawl_queue._stable_profile_native_ids(  # noqa: SLF001
                platform,
                profile_data.get("raw_platform_data"),
            )
        except ValueError as exc:
            raise VideoTrackingError(
                "maintenance_refresh_provider_identity_mismatch",
                409,
            ) from exc
        if detect_platform_from_profile_url(actual_url) == "youtube":
            observed_url_channel_id = url_deep_crawl_queue._normalise_handle(  # noqa: SLF001
                platform,
                extract_handle_from_profile_url(actual_url, platform),
            )
            if not observed_url_channel_id.startswith("UC"):
                observed_url_channel_id = ""
    youtube_channel_equivalent = (
        platform == "youtube"
        and youtube_channel_id
        and actual_handle == youtube_channel_id
        and youtube_channel_id in expected_url
    )
    youtube_url_equivalent = (
        platform == "youtube"
        and observed_url_channel_id == youtube_channel_id
        and observed_native.get("channel_id") == youtube_channel_id
    )
    if (
        actual_platform != platform
        or (actual_url != expected_url and not youtube_url_equivalent)
        or not actual_handle
        or (actual_handle != expected_handle and not youtube_channel_equivalent)
    ):
        raise VideoTrackingError("maintenance_refresh_provider_identity_mismatch", 409)

    # Maintenance updates metrics/raw evidence only.  Preserve the selected
    # row's already-validated public identity instead of rewriting a readable
    # YouTube handle to its /channel/UC... locator.
    profile_data["handle"] = expected_handle
    profile_data["profile_url"] = expected_url


def _execute_profile_flow(
    classified: ClassifiedUrl,
    matches: list[dict[str, Any]],
    body: dict[str, Any],
) -> dict[str, Any]:
    from app.domains.kol.video_tracking import VideoTrackingError

    if body.get("maintenance_refresh") is True:
        # Durable payloads are evidence, not authority.  Maintenance is a
        # bounded profile refresh: one history item, no representative AI job
        # and no account follow-up spawned from this paid crawl.
        body = {
            **body,
            "max_posts": 1,
            "suppress_final_v1": True,
            "suppress_profile_followups": True,
        }
    started = time.monotonic()
    target = _profile_target(classified)
    max_posts = _max_posts(body)
    kol_pool_id = int(matches[0]["kol_pool_id"]) if len(matches) == 1 else None
    operation = "update" if kol_pool_id else "insert"
    incremental_state = _profile_incremental_state(kol_pool_id, force_full=_profile_force_full_history(body))
    conn = get_conn()

    # URL matching is mutable state.  Require it to still resolve to the exact
    # pool row selected by the scheduler immediately before paid provider I/O.
    _revalidate_maintenance_profile_target(
        body,
        conn=conn,
        resolved_kol_pool_id=kol_pool_id,
        provider_calls_performed=False,
    )
    crawl = _crawl_profile_basics(
        classified, target=target, max_posts=max_posts, since=str(body.get("since") or "").strip()
    )
    # The provider call may take minutes.  Recheck once more before the first
    # crawl-run/profile/evidence write so a merge or identity change cannot
    # redirect the result to another row or create a new row.
    try:
        _revalidate_maintenance_profile_target(
            body,
            conn=conn,
            resolved_kol_pool_id=kol_pool_id,
            lock_target=True,
            provider_calls_performed=True,
        )
        _verify_maintenance_crawl_identity(body, crawl)
    except VideoTrackingError as exc:
        exc.provider_calls_performed = True
        raise
    if str(crawl.get("status") or "").lower() not in {"ok", "synced"}:
        run_id = _record_deep_crawl_run(
            conn,
            kol_pool_id=kol_pool_id,
            source_url=classified.normalized_url,
            url_type="profile",
            mode=str(body.get("mode") or "profile_only"),
            status="failed",
            dry_run=False,
            summary={
                "reason": "profile_crawl_not_ready",
                "crawl_status": crawl.get("status"),
                "elapsed_ms": crawl.get("elapsed_ms"),
                "provider_source": crawl.get("provider_source"),
            },
        )
        return {
            "status": "crawl_failed",
            "operation": operation,
            "kol_pool_id": kol_pool_id,
            "target": target,
            "crawl_status": crawl.get("status"),
            "provider_source": crawl.get("provider_source"),
            "run_id": run_id,
            "crawl_performed": True,
            "business_tables_written": bool(run_id),
            "viltrox_fit_score_changed_ids": [],
            "viltrox_fit_score_untouched": True,
        }

    profile_data = _profile_data_from_crawl(
        classified,
        crawl,
        existing_match=matches[0] if matches else {},
        max_posts=max_posts,
    )
    try:
        _verify_maintenance_profile_data_identity(body, profile_data)
    except VideoTrackingError as exc:
        exc.provider_calls_performed = True
        raise
    write_result = write_kol_profile_basics(
        kol_pool_id,
        profile_data,
        dry_run=False,
        conn=conn,
        suppress_contact_acquisition=body.get("suppress_contact_acquisition") is True,
        suppress_avatar_landing=body.get("suppress_avatar_landing") is True,
        suppress_reach_floor_regate=body.get("maintenance_refresh") is True,
    )
    written_kol_pool_id = int(write_result.get("kol_pool_id") or kol_pool_id or 0) or None
    # write_kol_profile_basics commits and releases the first post-provider
    # lock.  Reacquire before any representative/history evidence stage.
    _revalidate_maintenance_profile_target(
        body,
        conn=conn,
        resolved_kol_pool_id=written_kol_pool_id,
        lock_target=True,
        provider_calls_performed=True,
    )
    representative_video_analysis = _execute_profile_representative_video_analysis(
        conn,
        classified=classified,
        kol_pool_id=written_kol_pool_id,
        crawl=crawl,
        body=body,
        incremental_state=incremental_state,
    )
    history_video_evidence = _execute_profile_history_video_evidence(
        conn,
        classified=classified,
        kol_pool_id=written_kol_pool_id,
        crawl=crawl,
        body=body,
        incremental_state=incremental_state,
    )
    worker_touched = bool(representative_video_analysis.get("worker_touched"))
    account_dossier_extract_job = None
    if written_kol_pool_id and not worker_touched and body.get("suppress_profile_followups") is not True:
        account_dossier_extract_job = _enqueue_account_dossier_extract_followup(
            conn,
            kol_pool_id=written_kol_pool_id,
            source="kol_url_profile_flow",
            trigger=str(representative_video_analysis.get("status") or "profile_ready_no_video_job"),
            source_url=classified.normalized_url,
            query_text=f"profile account dossier - {target}",
        )
    changed_ids = sorted(
        set(
            _fit_changed_ids(write_result)
            + _fit_changed_ids(representative_video_analysis)
            + _fit_changed_ids(history_video_evidence)
            + _fit_changed_ids(account_dossier_extract_job or {})
        )
    )
    # Profile/evidence writers commit independently and release their row
    # lock.  Reacquire immediately before the durable run receipt so a merge
    # cannot attach that receipt to a target changed after the last write.
    _revalidate_maintenance_profile_target(
        body,
        conn=conn,
        resolved_kol_pool_id=written_kol_pool_id,
        lock_target=True,
        provider_calls_performed=True,
    )
    run_id = _record_deep_crawl_run(
        conn,
        kol_pool_id=written_kol_pool_id,
        source_url=classified.normalized_url,
        url_type="profile",
        mode=str(body.get("mode") or "profile_only"),
        status="ready",
        dry_run=False,
        summary={
            "operation": operation,
            "target": target,
            "crawl_status": crawl.get("status"),
            "provider_source": crawl.get("provider_source"),
            "elapsed_ms": crawl.get("elapsed_ms"),
            "fields_written": write_result.get("fields_written"),
            "viltrox_fit_score_changed_ids": write_result.get("viltrox_fit_score_changed_ids"),
            "representative_video_analysis": representative_video_analysis,
            "history_video_evidence": history_video_evidence,
            "account_dossier_extract_job": account_dossier_extract_job,
            "incremental_state": incremental_state,
        },
    )
    return {
        "status": "ready",
        "operation": operation,
        "kol_pool_id": written_kol_pool_id,
        "target": target,
        "max_posts": max_posts,
        "profile_data": _public_profile_data(profile_data),
        "write_result": {
            "fields_written": write_result.get("fields_written"),
            "ignored_fields": write_result.get("ignored_fields"),
            "missing_columns": write_result.get("missing_columns"),
            "viltrox_fit_score_changed_ids": write_result.get("viltrox_fit_score_changed_ids"),
            "viltrox_fit_score_untouched": write_result.get("viltrox_fit_score_untouched"),
        },
        "representative_video_analysis": representative_video_analysis,
        "history_video_evidence": history_video_evidence,
        "account_dossier_extract_job": account_dossier_extract_job,
        "run_id": run_id,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "crawl_performed": True,
        "business_tables_written": True,
        "worker_touched": worker_touched or bool(account_dossier_extract_job and account_dossier_extract_job.get("status") == "queued"),
        "provider_source": crawl.get("provider_source"),
        "crawl_status": crawl.get("status"),
        "viltrox_fit_score_changed_ids": changed_ids,
        "viltrox_fit_score_untouched": not changed_ids,
    }


def _profile_force_full_history(body: dict[str, Any]) -> bool:
    """强制全量历史:绕过 last_video_at 增量截断,把已爬过 KOL 的全部视频重新 materialize。
    用于「该用户全部视频都分析」(account_deep 重跑),不改任何评分字段。"""
    return bool((body or {}).get("force_full_history") or (body or {}).get("ignore_incremental"))


def _profile_incremental_state(kol_pool_id: int | None, *, force_full: bool = False) -> dict[str, Any]:
    if force_full:
        return {
            "enabled": True,
            "kol_pool_id": int(kol_pool_id) if kol_pool_id else None,
            "last_video_at": "",
            "profile_backfilled_at": "",
            "mode": "force_full_history",
        }
    if not kol_pool_id:
        return {
            "enabled": True,
            "kol_pool_id": None,
            "last_video_at": "",
            "profile_backfilled_at": "",
            "mode": "full_first_profile_crawl",
        }
    conn = get_conn()
    columns = _table_columns(conn, "vkpi_kol_pool")
    selected = ["id"]
    for column in ("last_video_at", "raw_platform_data"):
        if column in columns:
            selected.append(column)
    row = conn.execute(
        f"SELECT {', '.join(selected)} FROM vkpi_kol_pool WHERE id=?",
        (int(kol_pool_id),),
    ).fetchone()
    if not row:
        return {
            "enabled": True,
            "kol_pool_id": int(kol_pool_id),
            "last_video_at": "",
            "profile_backfilled_at": "",
            "mode": "missing_existing_profile",
        }
    row_dict = dict(row)
    raw_payload = _load_json(row_dict.get("raw_platform_data"))
    profile_backfilled_at = _raw_profile_backfilled_at(raw_payload)
    last_video_at = _parse_date(row_dict.get("last_video_at")) or ""
    return {
        "enabled": True,
        "kol_pool_id": int(kol_pool_id),
        "last_video_at": last_video_at,
        "profile_backfilled_at": profile_backfilled_at,
        "mode": "incremental_after_last_video" if last_video_at else "full_no_last_video_at",
    }


def _video_creator_resolved(video_flow: dict[str, Any]) -> bool:
    identity = video_flow.get("creator_identity") if isinstance(video_flow, dict) else {}
    return isinstance(identity, dict) and _has_matchable_creator_identity(identity)


def _profile_classified_from_video_flow(
    classified: ClassifiedUrl,
    video_flow: dict[str, Any],
) -> ClassifiedUrl | None:
    # lazy import 避免与 url_deep_crawl 循环导入(_classified_from_creator_identity 留在原文件)。
    from app.domains.kol.url_deep_crawl import _classified_from_creator_identity

    identity = video_flow.get("creator_identity") if isinstance(video_flow, dict) else {}
    if not isinstance(identity, dict):
        return None
    return _classified_from_creator_identity(classified, identity)


def _profile_data_for_new_video_creator(
    profile_classified: ClassifiedUrl,
    crawl: dict[str, Any],
    video_flow: dict[str, Any],
    *,
    max_posts: int,
) -> dict[str, Any]:
    identity = video_flow.get("creator_identity") if isinstance(video_flow.get("creator_identity"), dict) else {}
    metadata = video_flow.get("video_metadata") if isinstance(video_flow.get("video_metadata"), dict) else {}
    profile_data = _profile_data_from_crawl(profile_classified, crawl, existing_match={}, max_posts=max_posts)
    now = datetime.now(timezone.utc).isoformat()

    profile_data["platform"] = profile_classified.platform
    profile_data["handle"] = profile_classified.handle or profile_classified.channel_id
    profile_data["profile_url"] = profile_data.get("profile_url") or profile_classified.normalized_url
    if not profile_data.get("avatar_url") and identity.get("avatar_url"):
        profile_data["avatar_url"] = identity.get("avatar_url")
    if not profile_data.get("followers") and identity.get("followers") is not None:
        profile_data["followers"] = identity.get("followers")
    if not profile_data.get("bio") and identity.get("bio"):
        profile_data["bio"] = identity.get("bio")
    profile_data["profile_backfilled_at"] = now
    if not profile_data.get("last_video_at"):
        profile_data["last_video_at"] = _video_metadata_date(metadata)

    raw_payload = _load_json(profile_data.get("raw_platform_data"))
    if not isinstance(raw_payload, dict):
        raw_payload = {}
    raw_payload["video_url_creator_bootstrap"] = {
        "method": "url_deep_crawl_video_new_creator_v1",
        "source_url": metadata.get("content_url") or "",
        "video_title": metadata.get("title") or "",
        "video_id": metadata.get("video_id") or metadata.get("id") or "",
        "creator_identity": identity,
        "profile_backfilled_at": now,
    }
    profile_data["raw_platform_data"] = _json(raw_payload)
    return profile_data


def _enqueue_account_dossier_extract_followup(
    conn: Any,
    *,
    kol_pool_id: int | None,
    source: str,
    trigger: str,
    source_url: str,
    query_text: str = "",
) -> dict[str, Any] | None:
    if not kol_pool_id:
        return None
    existing = conn.execute(
        """
        SELECT id, job_type, status, created_at, updated_at
        FROM apify_jobs
        WHERE job_type='account_dossier_extract'
          AND status IN ('queued', 'running')
          AND payload->>'target_type'='kol_pool'
          AND payload->>'target_id'=?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (str(int(kol_pool_id)),),
    ).fetchone()
    if existing:
        return {
            "status": "already_queued" if existing["status"] == "queued" else "already_running",
            "job": {
                "id": existing["id"],
                "job_type": existing["job_type"],
                "status": existing["status"],
                "created_at": existing["created_at"],
                "updated_at": existing["updated_at"],
            },
            "kol_pool_id": int(kol_pool_id),
            "viltrox_fit_score_changed_ids": [],
            "viltrox_fit_score_untouched": True,
        }
    payload = {
        "target_type": "kol_pool",
        "target_id": str(int(kol_pool_id)),
        "derive_method": "kol_account_dossier_extract_v1",
        "analysis_kind": "profile_llm",
        "source": source,
        "trigger": trigger,
        "source_url": source_url,
        "query_text": query_text or f"account dossier - kol_pool #{int(kol_pool_id)}",
    }
    row, inserted = enqueue_active_apify_job(
        conn,
        job_type="account_dossier_extract",
        payload=payload,
        idempotency_key=active_job_idempotency_key("account_dossier_extract", int(kol_pool_id)),
    )
    try:
        conn.commit()
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        pass
    return {
        "status": "queued" if inserted else ("already_running" if row.get("status") == "running" else "already_queued"),
        "job": row,
        "kol_pool_id": int(kol_pool_id),
        "viltrox_fit_score_changed_ids": [],
        "viltrox_fit_score_untouched": True,
    }


_crawler_for = profile_data._crawler_for


def _crawl_profile_basics(
    classified: ClassifiedUrl,
    *,
    target: str,
    max_posts: int,
    since: str = "",
) -> dict[str, Any]:
    return profile_data._crawl_profile_basics(
        classified,
        target=target,
        max_posts=max_posts,
        since=since,
        crawler_factory=_crawler_for,
    )


def _crawl_youtube_profile_basics(
    crawler: Any,
    classified: ClassifiedUrl,
    *,
    target: str,
    max_posts: int,
    since_text: str,
) -> tuple[Any, dict[str, Any], list[dict[str, Any]]]:
    return profile_data._crawl_youtube_profile_basics(
        crawler,
        classified,
        target=target,
        max_posts=max_posts,
        since_text=since_text,
    )


def _youtube_profile_channel_id(
    profile_payload: Any,
    classified: ClassifiedUrl,
) -> str:
    return profile_data._youtube_profile_channel_id(profile_payload, classified)


def _dict_video_items(values: Any) -> list[dict[str, Any]]:
    return profile_data._dict_video_items(values)


def _crawl_generic_profile_basics(
    crawler: Any,
    *,
    target: str,
    max_posts: int,
    since_text: str,
) -> tuple[Any, list[dict[str, Any]]]:
    return profile_data._crawl_generic_profile_basics(
        crawler,
        target=target,
        max_posts=max_posts,
        since_text=since_text,
    )


def _content_video_items(items: Any) -> list[dict[str, Any]]:
    return profile_data._content_video_items(items)


def _nested_profile_video_items(
    profile_obj: dict[str, Any],
) -> list[dict[str, Any]]:
    return profile_data._nested_profile_video_items(profile_obj)


def _crawl_payload_status(profile_payload: Any, videos_payload: Any) -> str:
    return profile_data._crawl_payload_status(profile_payload, videos_payload)


def _crawl_provider_source(profile_payload: Any, videos_payload: Any) -> str:
    return profile_data._crawl_provider_source(profile_payload, videos_payload)


_profile_data_from_crawl = profile_data._profile_data_from_crawl
_record_deep_crawl_run = profile_data._record_deep_crawl_run
_profile_target = profile_data._profile_target
_identity_profile_data = profile_data._identity_profile_data
