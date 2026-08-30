"""观察窗口扫描与收口责任。

该模块是 ``observation_windows`` 的内部实现层：只承接窗口列表、
签收开窗、到期收口、内容扫描和匹配帖回链。公开调用仍经
``observation_windows`` 兼容门面，由门面显式注入连接、RBAC、表检测和
候选帖写入回调，以保留现有测试替身与事务边界。

红线不变：扫描只物化待人核的窗口/候选和本窗口的扫描元数据；
不改 project/assignment/cost，不碰 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable


_WINDOW_SCANNABLE_STATUSES = ("pending", "scanning", "matched")


def list_windows(
    staff: dict[str, Any] | None = None,
    status: str = "pending",
    project_id: int | None = None,
    *,
    get_conn_fn: Callable[[], Any],
    nullable_int_fn: Callable[[Any], int | None],
    row_to_window_fn: Callable[[Any], dict[str, Any]],
    scope_module: Any,
) -> dict[str, Any]:
    """纯 SELECT 列观察窗口，RBAC 经项目 scope 收口。"""
    conn = get_conn_fn()
    where_parts: list[str] = []
    params: list[Any] = []

    status_key = str(status or "").strip().lower()
    if status_key and status_key != "all":
        where_parts.append("w.status = ?")
        params.append(status_key)

    pid = nullable_int_fn(project_id)
    if pid is not None:
        where_parts.append("w.project_id = ?")
        params.append(pid)

    scope_sql, scope_params = scope_module.project_filter("p", staff)
    if scope_sql:
        where_parts.append(scope_sql)
        params.extend(scope_params)

    where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
    rows = conn.execute(
        f"""
        SELECT w.*, p.project_name, p.product_name
        FROM vkpi_project_content_observation_windows w
        JOIN vkpi_projects p ON p.id = w.project_id
        {where_clause}
        ORDER BY w.ends_at ASC, w.id ASC
        LIMIT 500
        """,
        tuple(params),
    ).fetchall()
    items = [row_to_window_fn(row) for row in rows]
    return {
        "status": "ok",
        "count": len(items),
        "items": items,
        "filter_status": status_key or "all",
        "scope_mode": scope_module.scope_context(staff)["scope_mode"],
        "note": "观察窗口列表;空=无 delivered shipment 开窗(物流断流,诚实)。",
    }


def scan_delivered_into_windows(
    staff: dict[str, Any] | None = None,
    days_overdue: int = 7,
    *,
    project_id: int | None = None,
    get_conn_fn: Callable[[], Any],
    nullable_int_fn: Callable[[Any], int | None],
    open_window_fn: Callable[..., dict[str, Any]],
    scope_module: Any,
) -> dict[str, Any]:
    """扫已签收派单并为其创建待人核观察窗口。"""
    days = max(0, min(int(days_overdue or 7), 90))
    target_project_id = nullable_int_fn(project_id)
    if project_id is not None and (target_project_id is None or target_project_id <= 0):
        return {"status": "error", "error": "project_id must be a positive integer"}
    cutoff = datetime.utcnow() - timedelta(days=days)
    conn = get_conn_fn()

    try:
        conn.execute("SELECT assignment_id FROM vkpi_shipments WHERE 1=0")
        has_assignment_col = True
    except Exception:
        has_assignment_col = False

    scope_sql, scope_params = scope_module.project_filter("p", staff)
    scope_clause = f"AND {scope_sql}" if scope_sql else ""
    target_clause = "AND s.project_id = ?" if target_project_id is not None else ""
    target_params: tuple[Any, ...] = (target_project_id,) if target_project_id is not None else ()
    assignment_select = "s.assignment_id AS assignment_id," if has_assignment_col else "NULL AS assignment_id,"
    rows = conn.execute(
        f"""
        SELECT s.id AS shipment_id, s.project_id AS project_id,
               {assignment_select} s.delivered_at AS delivered_at
        FROM vkpi_shipments s
        JOIN vkpi_projects p ON p.id = s.project_id
        WHERE s.delivered_at IS NOT NULL
          AND s.delivered_at < ?
          {target_clause}
          {scope_clause}
        ORDER BY s.delivered_at ASC
        LIMIT 500
        """,
        (cutoff, *target_params, *scope_params),
    ).fetchall()

    fanout_stages = ("device_sent", "shipped", "arrived", "received", "delivered", "content_posted")
    created: list[int] = []
    skipped_existing = 0
    scanned_projects = 0
    seen_projects: set[int] = set()
    for raw_row in rows:
        row = dict(raw_row)
        row_project_id = row.get("project_id")
        delivered_at = row.get("delivered_at")
        if not row_project_id or delivered_at is None:
            continue
        if int(row_project_id) not in seen_projects:
            seen_projects.add(int(row_project_id))
            scanned_projects += 1
        shipment_assignment = row.get("assignment_id")
        if shipment_assignment:
            assignment_row = conn.execute(
                "SELECT kol_pool_id FROM vkpi_project_kol_assignments WHERE id=?",
                (int(shipment_assignment),),
            ).fetchone()
            targets: list[tuple[int | None, int | None]] = [
                (int(shipment_assignment), (dict(assignment_row) if assignment_row else {}).get("kol_pool_id"))
            ]
        else:
            placeholders = ",".join("?" for _ in fanout_stages)
            assignments = conn.execute(
                f"""
                SELECT id AS assignment_id, kol_pool_id
                FROM vkpi_project_kol_assignments
                WHERE project_id = ? AND LOWER(COALESCE(stage,'')) IN ({placeholders})
                """,
                (int(row_project_id), *fanout_stages),
            ).fetchall()
            targets = (
                [(dict(item).get("assignment_id"), dict(item).get("kol_pool_id")) for item in assignments]
                if assignments
                else [(None, None)]
            )
        for assignment_id, kol_pool_id in targets:
            result = open_window_fn(
                project_id=int(row_project_id),
                assignment_id=assignment_id,
                kol_pool_id=kol_pool_id,
                delivered_at=delivered_at,
                staff=staff,
            )
            if result.get("status") == "created":
                window = result.get("window") or {}
                if window.get("id") is not None:
                    created.append(int(window["id"]))
            elif result.get("status") == "skipped":
                skipped_existing += 1

    return {
        "status": "ok",
        "days_overdue": days,
        "project_id": target_project_id,
        "scanned_projects": scanned_projects,
        "created": created,
        "skipped_existing": skipped_existing,
        "scope_mode": scope_module.scope_context(staff)["scope_mode"],
        "note": "只为人 CREATE 待核观察窗口,零自动裁决;created=[] 多因无 delivered shipment(物流断流,诚实)。",
    }


def close_expired_windows(
    staff: dict[str, Any] | None = None,
    *,
    grace_days: int = 3,
    get_conn_fn: Callable[[], Any],
) -> dict[str, Any]:
    """收口已过宽限期的活动观察窗口。"""
    del staff
    days = max(0, min(int(grace_days or 0), 30))
    conn = get_conn_fn()
    closed_cur = conn.execute(
        """
        UPDATE vkpi_project_content_observation_windows
        SET status='closed', updated_at=NOW()
        WHERE status IN ('pending','scanning','matched')
          AND matched_content_post_id IS NOT NULL
          AND ends_at IS NOT NULL
          AND ends_at < NOW() - (? * INTERVAL '1 day')
        """,
        (days,),
    )
    closed = int(getattr(closed_cur, "rowcount", 0) or 0)
    missing_cur = conn.execute(
        """
        UPDATE vkpi_project_content_observation_windows
        SET status='content_missing', updated_at=NOW()
        WHERE status IN ('pending','scanning')
          AND matched_content_post_id IS NULL
          AND ends_at IS NOT NULL
          AND ends_at < NOW() - (? * INTERVAL '1 day')
        """,
        (days,),
    )
    missing = int(getattr(missing_cur, "rowcount", 0) or 0)
    conn.commit()
    return {"status": "ok", "closed": closed, "content_missing": missing, "grace_days": days}


def mark_window_scanned(conn: Any, window_id: int, matched: bool) -> None:
    """只在本窗口行记一次扫描痕迹。"""
    now = datetime.utcnow()
    if matched:
        conn.execute(
            """
            UPDATE vkpi_project_content_observation_windows
            SET scan_count = scan_count + 1, last_scan_at = ?, status = 'matched', updated_at = ?
            WHERE id = ?
            """,
            (now, now, int(window_id)),
        )
    else:
        conn.execute(
            """
            UPDATE vkpi_project_content_observation_windows
            SET scan_count = scan_count + 1, last_scan_at = ?,
                status = CASE WHEN status = 'pending' THEN 'scanning' ELSE status END,
                updated_at = ?
            WHERE id = ?
            """,
            (now, now, int(window_id)),
        )


def scan_windows_for_content(
    staff: dict[str, Any] | None = None,
    max_windows: int = 200,
    min_scan_interval_minutes: int = 60,
    *,
    get_conn_fn: Callable[[], Any],
    mark_window_scanned_fn: Callable[[Any, int, bool], None],
    table_exists_fn: Callable[[str], bool],
    record_candidate_fn: Callable[..., dict[str, Any]],
    scope_module: Any,
) -> dict[str, Any]:
    """对活动窗口在真证据表找内容并物化为待人核候选。"""
    if not table_exists_fn("vkpi_project_content_observation_windows") or not table_exists_fn(
        "vkpi_kol_video_evidence"
    ):
        return {
            "status": "ok",
            "scanned_windows": 0,
            "rate_limited": 0,
            "created_posts": [],
            "skipped_dupes": 0,
            "schema_ready": False,
            "scope_mode": scope_module.scope_context(staff)["scope_mode"],
            "note": "观察窗口/证据表缺失(迁移 128/085 未应用)→ 空扫描(诚实)。",
        }

    conn = get_conn_fn()
    safe_max = max(1, min(int(max_windows or 200), 500))
    interval = max(0, min(int(min_scan_interval_minutes or 0), 24 * 60))
    scan_cutoff = datetime.utcnow() - timedelta(minutes=interval) if interval else None

    scope_sql, scope_params = scope_module.project_filter("p", staff)
    scope_clause = f"AND {scope_sql}" if scope_sql else ""
    status_placeholders = ",".join(["?"] * len(_WINDOW_SCANNABLE_STATUSES))
    rows = conn.execute(
        f"""
        SELECT w.id, w.project_id, w.assignment_id, w.kol_pool_id, w.starts_at, w.last_scan_at
        FROM vkpi_project_content_observation_windows w
        JOIN vkpi_projects p ON p.id = w.project_id
        WHERE w.status IN ({status_placeholders})
          {scope_clause}
        ORDER BY w.ends_at ASC, w.id ASC
        LIMIT ?
        """,
        (*_WINDOW_SCANNABLE_STATUSES, *scope_params, safe_max),
    ).fetchall()

    scanned_windows = 0
    rate_limited = 0
    created_posts: list[int] = []
    skipped_dupes = 0
    for raw_row in rows:
        win = dict(raw_row)
        window_id = int(win.get("id") or 0)
        row_project_id = win.get("project_id")
        if not window_id or not row_project_id:
            continue
        last_scan = win.get("last_scan_at")
        if scan_cutoff is not None and isinstance(last_scan, datetime):
            if last_scan.replace(tzinfo=None) >= scan_cutoff:
                rate_limited += 1
                continue
        kol_pool_id = win.get("kol_pool_id")
        starts_at = win.get("starts_at")

        evidence_where = ["e.project_id = ?", "e.is_active = TRUE"]
        evidence_params: list[Any] = [int(row_project_id)]
        if kol_pool_id is not None:
            evidence_where.append("e.kol_pool_id = ?")
            evidence_params.append(int(kol_pool_id))
        if isinstance(starts_at, datetime):
            evidence_where.append("(e.posted_at IS NULL OR e.posted_at >= ?)")
            evidence_params.append(starts_at.date())

        evidence_rows = conn.execute(
            f"""
            SELECT e.id, e.kol_pool_id, e.content_url, e.platform, e.video_title,
                   e.posted_at, e.view_count, e.like_count, e.comment_count
            FROM vkpi_kol_video_evidence e
            WHERE {' AND '.join(evidence_where)}
            ORDER BY e.posted_at DESC NULLS LAST, e.id DESC
            LIMIT 50
            """,
            tuple(evidence_params),
        ).fetchall()

        matched_any = False
        for evidence in evidence_rows:
            evidence_data = dict(evidence)
            content_url = str(evidence_data.get("content_url") or "").strip()
            if not content_url:
                continue
            posted_at = evidence_data.get("posted_at")
            published_iso = str(posted_at) if posted_at is not None else None
            result = record_candidate_fn(
                project_id=int(row_project_id),
                assignment_id=win.get("assignment_id"),
                kol_pool_id=(
                    evidence_data.get("kol_pool_id")
                    if evidence_data.get("kol_pool_id") is not None
                    else kol_pool_id
                ),
                post={
                    "content_url": content_url,
                    "platform": evidence_data.get("platform") or "",
                    "title": evidence_data.get("video_title") or "",
                    "published_at": published_iso,
                    "view_count": evidence_data.get("view_count") or 0,
                    "like_count": evidence_data.get("like_count") or 0,
                    "comment_count": evidence_data.get("comment_count") or 0,
                    "evidence_id": evidence_data.get("id"),
                    "match_reason": "observation_scan: kol_video_evidence in window",
                    "matched_terms": "viltrox_evidence",
                },
                staff=staff,
            )
            result_status = result.get("status")
            if result_status == "created":
                post = result.get("post") or {}
                if post.get("id") is not None:
                    created_posts.append(int(post["id"]))
                matched_any = True
            elif result_status == "skipped":
                skipped_dupes += 1
                matched_any = True

        mark_window_scanned_fn(conn, window_id, matched_any)
        scanned_windows += 1

    conn.commit()
    return {
        "status": "ok",
        "scanned_windows": scanned_windows,
        "rate_limited": rate_limited,
        "created_posts": created_posts,
        "skipped_dupes": skipped_dupes,
        "scope_mode": scope_module.scope_context(staff)["scope_mode"],
        "note": (
            "内容观测扫描:对活动窗口在真证据表(vkpi_kol_video_evidence)找内容→物化候选,零自动裁决;"
            "created_posts=[] 多因 KOL 尚未发布内容或无窗口(诚实)。"
        ),
    }


def find_post_for_window(
    conn: Any,
    *,
    project_id: int,
    assignment_id: int | None,
    kol_pool_id: int | None,
    nullable_int_fn: Callable[[Any], int | None],
    table_exists_fn: Callable[[str], bool],
) -> int | None:
    """为活动窗口在已落库候选里挑一条最佳匹配帖。"""
    pid = int(project_id or 0)
    if pid <= 0 or not table_exists_fn("vkpi_project_content_posts"):
        return None
    aid = nullable_int_fn(assignment_id)
    kpid = nullable_int_fn(kol_pool_id)
    where_parts = ["project_id = ?", "status <> 'rejected'"]
    params: list[Any] = [pid]
    if aid is None:
        where_parts.append("assignment_id IS NULL")
    else:
        where_parts.append("assignment_id = ?")
        params.append(aid)
    if kpid is None:
        where_parts.append("kol_pool_id IS NULL")
    else:
        where_parts.append("kol_pool_id = ?")
        params.append(kpid)
    row = conn.execute(
        f"""
        SELECT id FROM vkpi_project_content_posts
        WHERE {' AND '.join(where_parts)}
        ORDER BY
            CASE WHEN status IN ('matched', 'retrospective_ready') THEN 0 ELSE 1 END ASC,
            view_count DESC,
            id DESC
        LIMIT 1
        """,
        tuple(params),
    ).fetchone()
    if row is None:
        return None
    return int(dict(row)["id"])


def scan_windows_backfill_matched_post(
    staff: dict[str, Any] | None = None,
    max_windows: int = 500,
    *,
    emit_event_fn: Callable[..., None],
    find_post_fn: Callable[..., int | None],
    get_conn_fn: Callable[[], Any],
    scope_module: Any,
    table_exists_fn: Callable[[str], bool],
) -> dict[str, Any]:
    """把已有内容候选幂等回填到尚未挂帖的活动观察窗口。"""
    if not table_exists_fn("vkpi_project_content_observation_windows") or not table_exists_fn(
        "vkpi_project_content_posts"
    ):
        return {
            "status": "ok",
            "scanned_windows": 0,
            "backfilled_windows": [],
            "unmatched": 0,
            "schema_ready": False,
            "scope_mode": scope_module.scope_context(staff)["scope_mode"],
            "note": "观察窗口/内容帖表缺失(迁移 128 未应用)→ 空回填(诚实)。",
        }

    conn = get_conn_fn()
    safe_max = max(1, min(int(max_windows or 500), 1000))

    scope_sql, scope_params = scope_module.project_filter("p", staff)
    scope_clause = f"AND {scope_sql}" if scope_sql else ""
    status_placeholders = ",".join(["?"] * len(_WINDOW_SCANNABLE_STATUSES))
    rows = conn.execute(
        f"""
        SELECT w.id, w.project_id, w.assignment_id, w.kol_pool_id
        FROM vkpi_project_content_observation_windows w
        JOIN vkpi_projects p ON p.id = w.project_id
        WHERE w.status IN ({status_placeholders})
          AND w.matched_content_post_id IS NULL
          {scope_clause}
        ORDER BY w.ends_at ASC, w.id ASC
        LIMIT ?
        """,
        (*_WINDOW_SCANNABLE_STATUSES, *scope_params, safe_max),
    ).fetchall()

    scanned_windows = 0
    backfilled_windows: list[int] = []
    unmatched = 0
    now = datetime.utcnow()
    for raw_row in rows:
        win = dict(raw_row)
        window_id = int(win.get("id") or 0)
        row_project_id = win.get("project_id")
        if not window_id or not row_project_id:
            continue
        scanned_windows += 1
        post_id = find_post_fn(
            conn,
            project_id=int(row_project_id),
            assignment_id=win.get("assignment_id"),
            kol_pool_id=win.get("kol_pool_id"),
        )
        if post_id is None:
            unmatched += 1
            continue
        cursor = conn.execute(
            """
            UPDATE vkpi_project_content_observation_windows
            SET matched_content_post_id = ?, status = 'matched', updated_at = ?
            WHERE id = ? AND matched_content_post_id IS NULL
            """,
            (int(post_id), now, window_id),
        )
        if int(getattr(cursor, "rowcount", 0) or 0) > 0:
            backfilled_windows.append(window_id)
            emit_event_fn(
                "observation.window_backfilled",
                entity_type="observation_window",
                entity_id=window_id,
                payload={"project_id": int(row_project_id), "matched_content_post_id": int(post_id)},
            )

    conn.commit()
    return {
        "status": "ok",
        "scanned_windows": scanned_windows,
        "backfilled_windows": backfilled_windows,
        "unmatched": unmatched,
        "scope_mode": scope_module.scope_context(staff)["scope_mode"],
        "note": (
            "批量回填:把已落库内容候选挂回活动窗口 matched_content_post_id(只增不覆盖,幂等);"
            "unmatched 窗口=暂无匹配候选(诚实跳过,不建窗不造数据)。"
        ),
    }
