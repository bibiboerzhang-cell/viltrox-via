"""MY KOL 观察清单总览(波 C·C5,纯读、零 LLM、零 provider、零写入)。

用户原话:「我是否可以特别给某些 kol 增加一个观察列表跟进进度」。
落点不造新表:观察清单 = 既有 员工分组(vkpi_staff_groups)的组级共享 KOL
(permissions.shared_kol_pool_ids;EditGroupModal 存的 kol_pool_id 数组),
分组展开时已把这些 KOL 铺成 vkpi_kol_pool_members 行 → 组员天然可读。

一张表一眼看进度,组内每个 KOL 一行:
  追踪视频数(vkpi_kol_video_metric_tracking)/ 最近快照时间 / 7d 播放增量
  (vkpi_content_metric_snapshots,口径复用 video_tracking_trends.build_tracked_item:
  legacy_current_only 不算实测,只有 1 个样本 = 样本不足)/ 深析完成比
  (口径复用 video_analysis_enqueue.account_video_analysis_progress:最近 20 条证据
  × ready cache / 最新任务态,这里改成一次批量 SQL 避免 N+1)/ 待办失败数 /
  镜头家族 top3(vkpi_kol_lens_evidence)/ 最近活动时间。

诚实空态:tracking_state ∈ {tracked, untracked, no_videos};views_delta_7d 样本不足
给 null + views_delta_7d_reason;组级 untracked_count / no_videos_count /
empty_reason;总览级 favorites_not_in_any_group。
权限:管理层(scope.can_view_all)看全部分组;其余只看本人所在(member_ids 含本人)
或本人创建(created_by)的分组。
SQL 全 ? 占位;不用字面 % / LIKE;时间窗在 Python 侧算,不用 interval。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from app.domains.access import scope
from app.domains.content_metric_snapshots import _parse_timestamp
from app.domains.kol import video_analysis_enqueue as _vae
from app.domains.kol import video_tracking_trends as _trends
from app.domains.staff_groups import service as staff_groups_service


CONTRACT = "my_kol_watchlist_overview_v1"
DELTA_WINDOW = "7d"
LENS_TOP_N = 3
MAX_KOLS_PER_GROUP = 100
MAX_GROUPS = 200
MAX_TRACKED_VIDEOS_PER_GROUP = 600

_DEEP_KEYS = ("completed", "in_progress", "failed", "not_requested")


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _text(value: Any) -> str:
    return str(value or "").strip()


def _iso(value: Any) -> str | None:
    parsed = _parse_timestamp(value) if not isinstance(value, datetime) else value
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def _max_ts(*values: Any) -> datetime | None:
    parsed = [ts for ts in (_parse_timestamp(v) for v in values) if ts is not None]
    return max(parsed) if parsed else None


def _placeholders(ids: Iterable[int]) -> tuple[str, tuple[int, ...]]:
    clean = tuple(sorted({_int(v) for v in ids if _int(v) > 0}))
    return ", ".join("?" for _ in clean), clean


# ── 分组可见性 ────────────────────────────────────────────────────────────────


def group_visible_to(group: dict[str, Any], staff: dict[str, Any] | None) -> bool:
    """管理层全可见;否则本人在 member_ids 里或本人是 created_by。"""

    if scope.can_view_all(staff):
        return True
    actor = scope.actor_staff_id(staff)
    if actor <= 0:
        return False
    if _int(group.get("created_by")) == actor:
        return True
    return any(_int(member) == actor for member in (group.get("member_ids") or []))


def _group_kol_ids(group: dict[str, Any]) -> list[int]:
    return staff_groups_service._shared_kol_ids(group.get("permissions"))


def list_visible_groups(conn: Any, staff: dict[str, Any] | None, *, limit: int = MAX_GROUPS) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM vkpi_staff_groups ORDER BY updated_at DESC, created_at DESC LIMIT ?",
        (max(1, min(_int(limit) or MAX_GROUPS, 500)),),
    ).fetchall()
    groups = [staff_groups_service._group_row(row) for row in rows]
    return [group for group in groups if group_visible_to(group, staff)]


def load_group(conn: Any, group_id: str, staff: dict[str, Any] | None) -> dict[str, Any]:
    """LookupError = 组不存在;PermissionError = 不是本人分组。"""

    row = conn.execute("SELECT * FROM vkpi_staff_groups WHERE id = ?", (_text(group_id),)).fetchone()
    if not row:
        raise LookupError("group not found")
    group = staff_groups_service._group_row(row)
    if not group_visible_to(group, staff):
        raise PermissionError("not a member of this group")
    return group


# ── 批量取数(每段一次 SQL,不按 KOL 循环) ─────────────────────────────────────


def _kol_base_rows(conn: Any, kol_ids: list[int]) -> dict[int, dict[str, Any]]:
    marks, params = _placeholders(kol_ids)
    if not params:
        return {}
    rows = conn.execute(
        f"""
        SELECT p.id, COALESCE(NULLIF(p.display_name, ''), p.handle, '') AS display_name,
               COALESCE(p.handle, '') AS handle, LOWER(COALESCE(p.platform, '')) AS platform,
               COALESCE(p.profile_url, '') AS profile_url,
               (SELECT COUNT(*) FROM vkpi_kol_video_evidence e
                 WHERE e.kol_pool_id = p.id AND e.is_active IS NOT FALSE) AS video_evidence_total,
               (SELECT MAX(e.published_at_norm) FROM vkpi_kol_video_evidence e
                 WHERE e.kol_pool_id = p.id AND e.is_active IS NOT FALSE) AS latest_video_published_at
        FROM vkpi_kol_pool p
        WHERE p.id IN ({marks})
        """,
        params,
    ).fetchall()
    return {_int(dict(r)["id"]): dict(r) for r in rows}


def _tracking_rows(conn: Any, kol_ids: list[int], *, cap: int) -> tuple[list[dict[str, Any]], bool]:
    marks, params = _placeholders(kol_ids)
    if not params:
        return [], False
    rows = [
        dict(r)
        for r in conn.execute(
            _trends._TRACKING_SELECT
            + f"""
        WHERE e.kol_pool_id IN ({marks}) AND e.is_active IS NOT FALSE
        ORDER BY t.updated_at DESC, t.evidence_id DESC
        LIMIT ?
        """,
            params + (int(cap) + 1,),
        ).fetchall()
    ]
    truncated = len(rows) > cap
    return rows[:cap], truncated


def _snapshot_aggregates(conn: Any, kol_ids: list[int]) -> dict[int, dict[str, Any]]:
    marks, params = _placeholders(kol_ids)
    if not params:
        return {}
    rows = conn.execute(
        f"""
        SELECT e.kol_pool_id,
               MAX(CASE WHEN s.status = 'success' THEN s.fetched_at END) AS last_success_at,
               MAX(CASE WHEN s.status IN ('success', 'failed') THEN s.fetched_at END) AS last_attempt_at,
               COUNT(CASE WHEN s.status = 'success' THEN 1 END) AS success_samples,
               COUNT(CASE WHEN s.status = 'legacy_current_only' THEN 1 END) AS legacy_baseline_videos
        FROM vkpi_content_metric_snapshots s
        JOIN vkpi_kol_video_evidence e ON e.id = s.evidence_id
        WHERE e.kol_pool_id IN ({marks})
        GROUP BY e.kol_pool_id
        """,
        params,
    ).fetchall()
    return {_int(dict(r)["kol_pool_id"]): dict(r) for r in rows}


def _deep_analysis_by_kol(conn: Any, kol_ids: list[int]) -> dict[int, dict[str, Any]]:
    """account_video_analysis_progress 的批量版:每 KOL 最近 N 条证据 × cache/job 态。"""

    marks, params = _placeholders(kol_ids)
    limit = int(_vae.KOL_DEEP_ANALYSIS_VIDEO_LIMIT)
    out: dict[int, dict[str, Any]] = {
        kid: {"completed": 0, "in_progress": 0, "failed": 0, "not_requested": 0,
              "scope_videos": 0, "scope_limit": limit, "last_job_at": None, "last_cache_at": None}
        for kid in params
    }
    if not params:
        return out
    scoped = conn.execute(
        f"""
        SELECT kol_pool_id, id AS evidence_id
        FROM (
            SELECT e.kol_pool_id, e.id,
                   ROW_NUMBER() OVER (PARTITION BY e.kol_pool_id ORDER BY e.id DESC) AS rn
            FROM vkpi_kol_video_evidence e
            WHERE e.kol_pool_id IN ({marks})
              AND (e.is_active IS NULL OR e.is_active = TRUE)
        ) ranked
        WHERE rn <= ?
        """,
        params + (limit,),
    ).fetchall()
    scope_ids: list[int] = []
    owner_of: dict[int, int] = {}
    for raw in scoped:
        row = dict(raw)
        eid = _int(row.get("evidence_id"))
        if eid > 0:
            scope_ids.append(eid)
            owner_of[eid] = _int(row.get("kol_pool_id"))
    if not scope_ids:
        return out
    id_marks = ", ".join("?" for _ in scope_ids)
    text_ids = tuple(str(eid) for eid in scope_ids)
    cache_rows = conn.execute(
        f"""
        SELECT target_id, updated_at
        FROM vkpi_analysis_cache
        WHERE target_type = 'video' AND derive_method = ? AND status = 'ready'
          AND target_id IN ({id_marks})
        """,
        (_vae.FINAL_V1_DERIVE_METHOD, *text_ids),
    ).fetchall()
    cache_by_id = {_text(dict(r)["target_id"]): dict(r) for r in cache_rows}
    job_rows = conn.execute(
        f"""
        SELECT DISTINCT ON (payload->>'target_id')
               status, updated_at, payload->>'target_id' AS target_id
        FROM apify_jobs
        WHERE job_type = 'video'
          AND payload->>'derive_method' = ?
          AND payload->>'target_type' = 'video'
          AND payload->>'target_id' IN ({id_marks})
        ORDER BY payload->>'target_id', created_at DESC, id DESC
        """,
        (_vae.FINAL_V1_DERIVE_METHOD, *text_ids),
    ).fetchall()
    job_by_id = {_text(dict(r)["target_id"]): dict(r) for r in job_rows}
    for eid in scope_ids:
        bucket = out[owner_of[eid]]
        bucket["scope_videos"] += 1
        key = str(eid)
        cache = cache_by_id.get(key)
        job = job_by_id.get(key) or {}
        job_status = _text(job.get("status")).lower()
        if cache:
            bucket["completed"] += 1
            bucket["last_cache_at"] = _max_ts(bucket["last_cache_at"], cache.get("updated_at"))
        elif job_status in _vae.PROGRESS_ACTIVE_STATES:
            bucket["in_progress"] += 1
        elif job_status in _vae.PROGRESS_FAILED_STATES:
            bucket["failed"] += 1
        else:
            bucket["not_requested"] += 1
        if job:
            bucket["last_job_at"] = _max_ts(bucket["last_job_at"], job.get("updated_at"))
    return out


def _lens_families_by_kol(conn: Any, kol_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    marks, params = _placeholders(kol_ids)
    out: dict[int, list[dict[str, Any]]] = {kid: [] for kid in params}
    if not params:
        return out
    rows = conn.execute(
        f"""
        SELECT kol_pool_id, lens_key, display_name, category_main, resolution, mentions, videos
        FROM (
            SELECT kol_pool_id, lens_key,
                   MAX(display_name) AS display_name,
                   MAX(category_main) AS category_main,
                   MIN(resolution) AS resolution,
                   SUM(mention_count) AS mentions,
                   COUNT(DISTINCT evidence_id) AS videos,
                   ROW_NUMBER() OVER (
                       PARTITION BY kol_pool_id
                       ORDER BY SUM(mention_count) DESC, COUNT(DISTINCT evidence_id) DESC, lens_key
                   ) AS rn
            FROM vkpi_kol_lens_evidence
            WHERE kol_pool_id IN ({marks}) AND lens_key <> ''
            GROUP BY kol_pool_id, lens_key
        ) ranked
        WHERE rn <= ?
        ORDER BY kol_pool_id, rn
        """,
        params + (LENS_TOP_N,),
    ).fetchall()
    for raw in rows:
        row = dict(raw)
        out.setdefault(_int(row.get("kol_pool_id")), []).append({
            "lens_key": _text(row.get("lens_key")),
            "display_name": _text(row.get("display_name")) or _text(row.get("lens_key")),
            "category_main": _text(row.get("category_main")),
            "resolution": _text(row.get("resolution")),
            "mentions": _int(row.get("mentions")),
            "videos": _int(row.get("videos")),
        })
    return out


# ── 组装 ─────────────────────────────────────────────────────────────────────


def _delta_7d(items: list[dict[str, Any]]) -> dict[str, Any]:
    """聚合一组追踪视频的 7d 播放增量:ready/partial 的才计入;一个都没有 = 样本不足。"""

    deltas: list[int] = []
    partial = 0
    for item in items:
        window = (item.get("windows") or {}).get(DELTA_WINDOW, {}).get("views") or {}
        if window.get("status") in {"ready", "partial"} and window.get("delta") is not None:
            deltas.append(int(window["delta"]))
            partial += 1 if window.get("status") == "partial" else 0
    if not items:
        return {"value": None, "reason": "no_tracked_videos", "videos": 0, "status": None}
    if not deltas:
        measured = any((item.get("tracking") or {}).get("history") != "never_measured" for item in items)
        return {"value": None, "reason": "insufficient_samples" if measured else "never_measured", "videos": 0, "status": None}
    return {
        "value": sum(deltas),
        "reason": None,
        "videos": len(deltas),
        "status": "partial" if partial == len(deltas) else "ready",
    }


def build_kol_rows(
    conn: Any,
    kol_ids: list[int],
    *,
    now: datetime | None = None,
    tracked_cap: int = MAX_TRACKED_VIDEOS_PER_GROUP,
) -> dict[str, Any]:
    """一组 KOL 的观察行(批量 SQL:基础 1 + 追踪 1 + 快照 1 + 快照聚合 1 + 深析 3 + 镜头 1 + 调度闸 1)。"""

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    ordered_ids: list[int] = []
    for kid in kol_ids:
        if _int(kid) > 0 and _int(kid) not in ordered_ids:
            ordered_ids.append(_int(kid))
    base = _kol_base_rows(conn, ordered_ids)
    tracking_rows, tracked_truncated = _tracking_rows(conn, ordered_ids, cap=tracked_cap)
    gate = _trends.scheduler_gate(conn)
    snapshots = _trends._snapshots_for(conn, (row.get("evidence_id") for row in tracking_rows))
    tracked_by_kol: dict[int, list[dict[str, Any]]] = {kid: [] for kid in ordered_ids}
    for row in tracking_rows:
        item = _trends.build_tracked_item(
            row, snapshots.get(_int(row.get("evidence_id")), []), now=current, gate_enabled=gate["enabled"],
        )
        tracked_by_kol.setdefault(_int(row.get("kol_pool_id")), []).append(item)
    snapshot_agg = _snapshot_aggregates(conn, ordered_ids)
    deep = _deep_analysis_by_kol(conn, ordered_ids)
    lenses = _lens_families_by_kol(conn, ordered_ids)

    rows: list[dict[str, Any]] = []
    missing: list[int] = []
    for kid in ordered_ids:
        info = base.get(kid)
        if info is None:
            missing.append(kid)
            continue
        items = tracked_by_kol.get(kid, [])
        active = [it for it in items if it["tracking"]["status"] == "active"]
        paused = [it for it in items if it["tracking"]["status"] != "active"]
        metric_failures = sum(
            1 for it in items if it.get("last_attempt") and it["last_attempt"].get("status") == "failed"
        )
        agg = snapshot_agg.get(kid, {})
        deep_row = deep.get(kid) or {}
        deep_counts = {key: _int(deep_row.get(key)) for key in _DEEP_KEYS}
        scope_videos = _int(deep_row.get("scope_videos"))
        video_total = _int(info.get("video_evidence_total"))
        if items:
            tracking_state = "tracked"
        elif video_total > 0:
            tracking_state = "untracked"
        else:
            tracking_state = "no_videos"
        last_activity = _max_ts(
            agg.get("last_attempt_at"),
            deep_row.get("last_job_at"),
            deep_row.get("last_cache_at"),
            info.get("latest_video_published_at"),
        )
        delta = _delta_7d(items)
        rows.append({
            "kol_pool_id": kid,
            "display_name": _text(info.get("display_name")),
            "handle": _text(info.get("handle")),
            "platform": _text(info.get("platform")),
            "profile_url": _text(info.get("profile_url")),
            "tracking_state": tracking_state,
            "video_evidence_total": video_total,
            "tracked_videos": len(active),
            "tracked_paused": len(paused),
            "last_snapshot_at": _iso(agg.get("last_success_at")),
            "last_metric_attempt_at": _iso(agg.get("last_attempt_at")),
            "snapshot_samples": _int(agg.get("success_samples")),
            "legacy_baseline_videos": _int(agg.get("legacy_baseline_videos")),
            "views_delta_7d": delta["value"],
            "views_delta_7d_reason": delta["reason"],
            "views_delta_7d_videos": delta["videos"],
            "views_delta_7d_status": delta["status"],
            "deep_analysis": {
                **deep_counts,
                "scope_videos": scope_videos,
                "scope_limit": _int(deep_row.get("scope_limit")) or int(_vae.KOL_DEEP_ANALYSIS_VIDEO_LIMIT),
                "completion_ratio": (
                    round(deep_counts["completed"] / scope_videos, 4) if scope_videos > 0 else None
                ),
            },
            "open_failures": deep_counts["failed"] + metric_failures,
            "open_failures_breakdown": {"deep_analysis": deep_counts["failed"], "metric_refresh": metric_failures},
            "lens_families": lenses.get(kid, []),
            "latest_video_published_at": _iso(info.get("latest_video_published_at")),
            "last_activity_at": _iso(last_activity),
        })
    return {
        "rows": rows,
        "missing_kol_ids": missing,
        "tracked_truncated": tracked_truncated,
        "scheduler": gate,
        "generated_at": _iso(current),
    }


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    deep_total = {key: sum(_int(row["deep_analysis"].get(key)) for row in rows) for key in _DEEP_KEYS}
    scope_videos = sum(_int(row["deep_analysis"].get("scope_videos")) for row in rows)
    deltas = [row["views_delta_7d"] for row in rows if row.get("views_delta_7d") is not None]
    tracked_rows = [row for row in rows if row["tracking_state"] == "tracked"]
    return {
        "kol_count": len(rows),
        "tracked_count": len(tracked_rows),
        "untracked_count": sum(1 for row in rows if row["tracking_state"] == "untracked"),
        "no_videos_count": sum(1 for row in rows if row["tracking_state"] == "no_videos"),
        "tracked_videos_total": sum(_int(row["tracked_videos"]) for row in rows),
        "tracked_paused_total": sum(_int(row["tracked_paused"]) for row in rows),
        "last_snapshot_at": _iso(_max_ts(*(row["last_snapshot_at"] for row in rows))),
        "views_delta_7d": sum(deltas) if deltas else None,
        "views_delta_7d_reason": (
            None if deltas else (
                "no_tracked_videos" if not tracked_rows
                else "never_measured" if all(_int(row.get("snapshot_samples")) == 0 for row in tracked_rows)
                else "insufficient_samples"
            )
        ),
        "views_delta_7d_kols": len(deltas),
        "deep_analysis": {
            **deep_total,
            "scope_videos": scope_videos,
            "completion_ratio": round(deep_total["completed"] / scope_videos, 4) if scope_videos > 0 else None,
        },
        "open_failures": sum(_int(row["open_failures"]) for row in rows),
        "last_activity_at": _iso(_max_ts(*(row["last_activity_at"] for row in rows))),
        "empty_reason": None if rows else "no_kols_in_group",
    }


def _group_head(group: dict[str, Any]) -> dict[str, Any]:
    return {
        "group_id": _text(group.get("id")),
        "name": _text(group.get("name")),
        "description": _text(group.get("description")),
        "member_count": len(group.get("member_ids") or []),
        "updated_at": _iso(group.get("updated_at")),
    }


def group_overview(
    conn: Any,
    *,
    group_id: str,
    staff: dict[str, Any] | None,
    now: datetime | None = None,
    kol_limit: int = MAX_KOLS_PER_GROUP,
) -> dict[str, Any]:
    """GET /my-kol/groups/{group_id}/watch-overview 的主体(调用方把 LookupError/PermissionError 映射 404/403)。"""

    group = load_group(conn, group_id, staff)
    kol_ids = _group_kol_ids(group)
    bound = max(1, min(_int(kol_limit) or MAX_KOLS_PER_GROUP, MAX_KOLS_PER_GROUP))
    scoped_ids = kol_ids[:bound]
    built = build_kol_rows(conn, scoped_ids, now=now)
    return {
        "contract": CONTRACT,
        "read_only": True,
        "generated_at": built["generated_at"],
        "group": _group_head(group),
        "summary": summarize_rows(built["rows"]),
        "items": built["rows"],
        "kol_ids_configured": len(kol_ids),
        "kols_truncated": len(kol_ids) > bound,
        "missing_kol_ids": built["missing_kol_ids"],
        "tracked_truncated": built["tracked_truncated"],
        "scheduler": built["scheduler"],
        "window": DELTA_WINDOW,
        "empty_reason": None if built["rows"] else "no_kols_in_group",
    }


def _favorites_not_in_groups(conn: Any, staff: dict[str, Any] | None, grouped_ids: set[int]) -> int | None:
    actor = scope.actor_staff_id(staff)
    if actor <= 0:
        return None
    rows = conn.execute(
        "SELECT kol_pool_id FROM vkpi_kol_pool_favorites WHERE staff_id = ?", (actor,),
    ).fetchall()
    return sum(1 for r in rows if _int(dict(r).get("kol_pool_id")) not in grouped_ids)


def watch_overview(
    conn: Any,
    *,
    staff: dict[str, Any] | None,
    now: datetime | None = None,
    group_limit: int = MAX_GROUPS,
) -> dict[str, Any]:
    """GET /my-kol/watch-overview 的主体:本人全部分组各一张汇总卡(不展开 KOL 行)。"""

    groups = list_visible_groups(conn, staff, limit=group_limit)
    all_ids: list[int] = []
    per_group_ids: list[tuple[dict[str, Any], list[int]]] = []
    for group in groups:
        ids = _group_kol_ids(group)[:MAX_KOLS_PER_GROUP]
        per_group_ids.append((group, ids))
        all_ids.extend(ids)
    # 所有分组的 KOL 一次批量取数,再按组切分(同一 KOL 在多组重复出现时只算一次 SQL)。
    built = build_kol_rows(conn, all_ids, now=now, tracked_cap=MAX_TRACKED_VIDEOS_PER_GROUP * 2)
    row_by_id = {row["kol_pool_id"]: row for row in built["rows"]}
    cards: list[dict[str, Any]] = []
    for group, ids in per_group_ids:
        rows = [row_by_id[kid] for kid in ids if kid in row_by_id]
        cards.append({
            **_group_head(group),
            "kol_ids": ids,
            "summary": summarize_rows(rows),
        })
    all_rows = list(row_by_id.values())
    grouped_ids = set(row_by_id)
    if not groups:
        empty_reason: str | None = "no_groups"
    elif not all_rows:
        empty_reason = "no_kols_in_groups"
    else:
        empty_reason = None
    return {
        "contract": CONTRACT,
        "read_only": True,
        "generated_at": built["generated_at"],
        "viewer_scope": scope.scope_context(staff),
        "groups": cards,
        "totals": {
            **summarize_rows(all_rows),
            "empty_reason": empty_reason,
            "group_count": len(groups),
            "groups_with_kols": sum(1 for card in cards if card["summary"]["kol_count"] > 0),
            "favorites_not_in_any_group": _favorites_not_in_groups(conn, staff, grouped_ids),
        },
        "missing_kol_ids": built["missing_kol_ids"],
        "tracked_truncated": built["tracked_truncated"],
        "scheduler": built["scheduler"],
        "window": DELTA_WINDOW,
        "empty_reason": empty_reason,
    }


__all__ = [
    "CONTRACT",
    "MAX_KOLS_PER_GROUP",
    "build_kol_rows",
    "group_overview",
    "group_visible_to",
    "list_visible_groups",
    "load_group",
    "summarize_rows",
    "watch_overview",
]
