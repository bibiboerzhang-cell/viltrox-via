"""履约「观察窗口 + 内容帖子候选」应用层(additive,零自动裁决)。

物流签收(vkpi_shipments.delivered_at)落地后,人/扫描器为该派单开一个观察窗口
(starts = delivered + 7d、ends = delivered + 45d),等内容(短视频/帖子)出现供人核对;
扫到的疑似内容以「候选」入 vkpi_project_content_posts 等人复核。

本模块只做两件事:CREATE「待人看」的窗口/候选 + READ。绝不自动改业务状态——
不写 vkpi_projects.stage/closed_at、不写 vkpi_project_kol_assignments.stage、
不写 vkpi_cost_ledger、不碰 viltrox_fit_score / rule_v0。窗口/候选的 status 流转
也只动自己这张表的这一行,绝不连带改项目/派单/费用。

红线对齐(与 fulfillment_tasks.py / fulfillment_observation.py 同款):
- 仅写 vkpi_project_content_observation_windows(128)、vkpi_project_content_posts(129)。
- RBAC 经 scope.project_filter("p", staff) 收口(own-only 员工只见自己负责/创建项目;管理层全见)。
- psycopg `?` 占位;日期 cutoff 在 Python 算后作参数传(避开 % / INTERVAL 翻译层脆弱)。
- 去重先查后插(NULL 维度用 IS NULL 比较)。
- scan_delivered_into_windows 是唯一「自动」入口:只为派单 CREATE 待人核窗口,绝不裁决;
  当前 vkpi_shipments 多无 delivered_at → created=[] 是诚实结果(物流断流)。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from app.db.connection import get_conn, table_exists
from app.domains.access import scope


# 窗口开启偏移:签收后 7 天起观察、45 天止(与迁移注释一致)。
_WINDOW_START_OFFSET_DAYS = 7
_WINDOW_END_OFFSET_DAYS = 45

# 窗口「活动」态(去重以此为准):这三态视为还在等内容,不应重复开窗。
_WINDOW_ACTIVE_STATUSES = ("pending", "scanning", "matched")

# 内容帖子复核动作 → 仅置本表 status,绝不连带改业务表。
_VALID_POST_ACTIONS = {"matched", "rejected", "needs_review"}


def _dump_json(value: Any) -> str:
    """规整成 JSON 文本(metadata_json 是 TEXT 列,与 124/126 系迁移同款存法)。"""
    if value is None:
        return "{}"
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return "{}"


def _nullable_int(value: Any) -> int | None:
    if value in (None, "", 0, "0"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _stringify_ts(item: dict[str, Any], cols: tuple[str, ...]) -> dict[str, Any]:
    for col in cols:
        val = item.get(col)
        if val is not None and not isinstance(val, str):
            item[col] = str(val)
    return item


def _row_to_window(row: Any) -> dict[str, Any]:
    item = dict(row)
    raw = item.get("metadata_json")
    if isinstance(raw, str):
        try:
            item["metadata_json"] = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            item["metadata_json"] = {}
    return _stringify_ts(item, ("starts_at", "ends_at", "last_scan_at", "created_at", "updated_at"))


def _row_to_post(row: Any) -> dict[str, Any]:
    item = dict(row)
    conf = item.get("match_confidence")
    if conf is not None:
        try:
            item["match_confidence"] = float(conf)
        except (TypeError, ValueError):
            pass
    return _stringify_ts(item, ("published_at", "created_at", "updated_at"))


def open_window_for_delivered(
    project_id: int,
    assignment_id: int | None,
    kol_pool_id: int | None,
    delivered_at: Any,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """为一笔已签收派单开观察窗口(starts = delivered + 7d、ends = delivered + 45d,status=pending)。

    去重:同 (project_id, assignment_id, kol_pool_id) 已有活动窗口(pending/scanning/matched)则跳过。
    红线:只写 vkpi_project_content_observation_windows。绝不改 project/assignment/cost 状态。
    """
    pid = int(project_id or 0)
    if pid <= 0:
        return {"status": "error", "error": "project_id required"}

    delivered = delivered_at
    if isinstance(delivered, str):
        try:
            delivered = datetime.fromisoformat(delivered.replace("Z", "+00:00"))
        except ValueError:
            delivered = None
    if not isinstance(delivered, datetime):
        return {"status": "error", "error": "valid delivered_at required"}

    aid = _nullable_int(assignment_id)
    kpid = _nullable_int(kol_pool_id)
    starts_at = delivered + timedelta(days=_WINDOW_START_OFFSET_DAYS)
    ends_at = delivered + timedelta(days=_WINDOW_END_OFFSET_DAYS)

    conn = get_conn()
    # 先查后插兜底:NULL 维度用 IS NULL 比较(SQL 里 NULL = NULL 不成立)。
    status_placeholders = ",".join(["?"] * len(_WINDOW_ACTIVE_STATUSES))
    where_parts = ["project_id = ?"]
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
    where_parts.append(f"status IN ({status_placeholders})")
    params.extend(_WINDOW_ACTIVE_STATUSES)

    existing = conn.execute(
        f"""
        SELECT id FROM vkpi_project_content_observation_windows
        WHERE {' AND '.join(where_parts)}
        LIMIT 1
        """,
        tuple(params),
    ).fetchone()
    if existing is not None:
        return {"status": "skipped", "reason": "duplicate_active_window", "window_id": int(dict(existing)["id"])}

    metadata = _dump_json(
        {
            "delivered_at": str(delivered),
            "opened_by_staff_id": scope.actor_staff_id(staff) or None,
            "window_offset_days": [_WINDOW_START_OFFSET_DAYS, _WINDOW_END_OFFSET_DAYS],
        }
    )
    cursor = conn.execute(
        """
        INSERT INTO vkpi_project_content_observation_windows
            (project_id, assignment_id, kol_pool_id, starts_at, ends_at, status, metadata_json)
        VALUES (?, ?, ?, ?, ?, 'pending', ?)
        RETURNING *
        """,
        (pid, aid, kpid, starts_at, ends_at, metadata),
    )
    row = cursor.fetchone()
    conn.commit()
    return {"status": "created", "window": _row_to_window(row)}


def list_windows(
    staff: dict[str, Any] | None = None,
    status: str = "pending",
    project_id: int | None = None,
) -> dict[str, Any]:
    """纯 SELECT 列观察窗口。RBAC 经 scope.project_filter("p", staff) 收口(own-only / 管理层全见)。"""
    conn = get_conn()
    where_parts: list[str] = []
    params: list[Any] = []

    status_key = str(status or "").strip().lower()
    if status_key and status_key != "all":
        where_parts.append("w.status = ?")
        params.append(status_key)

    pid = _nullable_int(project_id)
    if pid is not None:
        where_parts.append("w.project_id = ?")
        params.append(pid)

    scope_sql, scope_params = scope.project_filter("p", staff)
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
    items = [_row_to_window(r) for r in rows]
    return {
        "status": "ok",
        "count": len(items),
        "items": items,
        "filter_status": status_key or "all",
        "scope_mode": scope.scope_context(staff)["scope_mode"],
        "note": "观察窗口列表;空=无 delivered shipment 开窗(物流断流,诚实)。",
    }


def record_content_candidate(
    project_id: int,
    assignment_id: int | None,
    kol_pool_id: int | None,
    post: dict[str, Any] | None,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """插一条 candidate 内容帖子。去重:同 (project_id, content_url) 已存在则跳过。

    红线:只写 vkpi_project_content_posts(status 恒为 'candidate' 入库,等人复核)。
    match_confidence/match_reason 仅供人参考,绝不据此自动改任何业务状态。
    """
    pid = int(project_id or 0)
    if pid <= 0:
        return {"status": "error", "error": "project_id required"}
    data = post or {}
    content_url = str(data.get("content_url") or "").strip()
    if not content_url:
        return {"status": "error", "error": "content_url required"}

    aid = _nullable_int(assignment_id)
    kpid = _nullable_int(kol_pool_id)
    evidence_id = _nullable_int(data.get("evidence_id"))

    conn = get_conn()
    # 先查后插兜底(迁移 unique(project_id, content_url) 是首道闸)。
    existing = conn.execute(
        """
        SELECT id FROM vkpi_project_content_posts
        WHERE project_id = ? AND content_url = ?
        LIMIT 1
        """,
        (pid, content_url),
    ).fetchone()
    if existing is not None:
        return {"status": "skipped", "reason": "duplicate_content_url", "post_id": int(dict(existing)["id"])}

    try:
        confidence = float(data.get("match_confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(confidence, 1.0))

    def _int0(key: str) -> int:
        try:
            return int(data.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    published_at = data.get("published_at")
    if isinstance(published_at, str) and published_at:
        try:
            published_at = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        except ValueError:
            published_at = None
    elif not isinstance(published_at, datetime):
        published_at = None

    cursor = conn.execute(
        """
        INSERT INTO vkpi_project_content_posts
            (project_id, assignment_id, kol_pool_id, evidence_id, platform, content_url,
             title, caption, published_at, view_count, like_count, comment_count,
             matched_terms, match_confidence, match_reason, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'candidate')
        RETURNING *
        """,
        (
            pid,
            aid,
            kpid,
            evidence_id,
            str(data.get("platform") or ""),
            content_url,
            str(data.get("title") or ""),
            str(data.get("caption") or ""),
            published_at,
            _int0("view_count"),
            _int0("like_count"),
            _int0("comment_count"),
            str(data.get("matched_terms") or ""),
            confidence,
            str(data.get("match_reason") or ""),
        ),
    )
    row = cursor.fetchone()
    conn.commit()
    return {"status": "created", "post": _row_to_post(row)}


def list_content_posts(
    staff: dict[str, Any] | None = None,
    status: str = "candidate",
    project_id: int | None = None,
) -> dict[str, Any]:
    """纯 SELECT 列内容帖子候选。RBAC 经 scope.project_filter("p", staff) 收口。"""
    conn = get_conn()
    where_parts: list[str] = []
    params: list[Any] = []

    status_key = str(status or "").strip().lower()
    if status_key and status_key != "all":
        where_parts.append("c.status = ?")
        params.append(status_key)

    pid = _nullable_int(project_id)
    if pid is not None:
        where_parts.append("c.project_id = ?")
        params.append(pid)

    scope_sql, scope_params = scope.project_filter("p", staff)
    if scope_sql:
        where_parts.append(scope_sql)
        params.extend(scope_params)

    where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
    rows = conn.execute(
        f"""
        SELECT c.*, p.project_name, p.product_name
        FROM vkpi_project_content_posts c
        JOIN vkpi_projects p ON p.id = c.project_id
        {where_clause}
        ORDER BY c.created_at DESC, c.id DESC
        LIMIT 500
        """,
        tuple(params),
    ).fetchall()
    items = [_row_to_post(r) for r in rows]
    return {
        "status": "ok",
        "count": len(items),
        "items": items,
        "filter_status": status_key or "all",
        "scope_mode": scope.scope_context(staff)["scope_mode"],
        "note": "内容帖子候选列表;空=无窗口/无扫到内容(诚实)。",
    }


def review_content_post(
    post_id: int,
    action: str,
    staff: dict[str, Any] | None = None,
    note: str = "",
) -> dict[str, Any]:
    """人工复核一条内容帖子候选 → 仅置本帖子行 status(matched/rejected/needs_review)。

    红线:绝不连带改 project/assignment/cost/复盘——只动 vkpi_project_content_posts 这一行。
    RBAC:own-only 员工只能复核自己可见(负责/创建项目下)的帖子;管理层全可复核。
    """
    cid = int(post_id or 0)
    if cid <= 0:
        return {"status": "error", "error": "post_id required"}
    act = str(action or "").strip().lower()
    if act not in _VALID_POST_ACTIONS:
        return {"status": "error", "error": f"invalid action: {act}"}

    conn = get_conn()
    # RBAC 收口:复用 project_filter,确认 actor 能看到这条帖子所属项目,再放行复核。
    scope_sql, scope_params = scope.project_filter("p", staff)
    scope_clause = f"AND {scope_sql}" if scope_sql else ""
    target = conn.execute(
        f"""
        SELECT c.id
        FROM vkpi_project_content_posts c
        JOIN vkpi_projects p ON p.id = c.project_id
        WHERE c.id = ? {scope_clause}
        """,
        (cid, *scope_params),
    ).fetchone()
    if target is None:
        return {"status": "error", "error": "post not found or out of scope"}

    updated_at = datetime.utcnow()
    cursor = conn.execute(
        """
        UPDATE vkpi_project_content_posts
        SET status = ?, match_reason = CASE WHEN ? <> '' THEN ? ELSE match_reason END, updated_at = ?
        WHERE id = ?
        RETURNING *
        """,
        (act, str(note or ""), str(note or ""), updated_at, cid),
    )
    row = cursor.fetchone()
    conn.commit()
    return {"status": "ok", "action": act, "post": _row_to_post(row)}


def advance_matched_posts_to_retrospective_ready(
    project_id: int,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """R12 · 把项目下 status='matched' 的内容帖批量推进到 'retrospective_ready'。

    红线:只 UPDATE vkpi_project_content_posts.status,绝不连带改 project.stage / assignment /
    cost / viltrox_fit_score。幂等:只动 matched 行(已 retrospective_ready 不重复)。
    RBAC:own-only 员工只能推进自己可见项目的帖子;管理层全可(复用 project_filter)。
    """
    pid = int(project_id or 0)
    if pid <= 0:
        return {"status": "error", "error": "project_id required"}
    conn = get_conn()
    scope_sql, scope_params = scope.project_filter("p", staff)
    scope_clause = f"AND {scope_sql}" if scope_sql else ""
    visible = conn.execute(
        f"SELECT p.id FROM vkpi_projects p WHERE p.id = ? {scope_clause}",
        (pid, *scope_params),
    ).fetchone()
    if visible is None:
        return {"status": "error", "error": "project not found or out of scope"}
    updated_at = datetime.utcnow()
    cursor = conn.execute(
        """
        UPDATE vkpi_project_content_posts
        SET status = 'retrospective_ready', updated_at = ?
        WHERE project_id = ? AND status = 'matched'
        """,
        (updated_at, pid),
    )
    conn.commit()
    advanced = int(getattr(cursor, "rowcount", 0) or 0)
    return {"status": "ok", "project_id": pid, "advanced_count": advanced}


def scan_delivered_into_windows(
    staff: dict[str, Any] | None = None,
    days_overdue: int = 7,
) -> dict[str, Any]:
    """唯一的「自动」入口:扫已签收派单(vkpi_shipments.delivered_at NOT NULL)→ 为其开观察窗口。

    红线:这不是裁决——只为人 CREATE「待人核」的观察窗口,绝不自动改项目/派单/费用状态。
    去重交给 open_window_for_delivered(同 project/assignment/KOL 已有活动窗口则跳过)。

    派单关联:vkpi_shipments 无 assignment 外键,按 project_id 下的派单 fan-out
    (同项目多派单各开一窗,assignment_id/kol_pool_id 取自 vkpi_project_kol_assignments;
    项目下零派单时退化为项目级单窗 assignment_id=NULL)。
    当前 vkpi_shipments 多无 delivered_at 数据 → created=[] 是诚实结果(物流断流)。
    """
    days = max(0, min(int(days_overdue or 7), 90))
    cutoff = datetime.utcnow() - timedelta(days=days)
    conn = get_conn()

    scope_sql, scope_params = scope.project_filter("p", staff)
    scope_clause = f"AND {scope_sql}" if scope_sql else ""
    # 每个项目取最早 delivered_at(同 due_list 口径);只读,不改 shipments。
    rows = conn.execute(
        f"""
        SELECT p.id AS project_id, MIN(s.delivered_at) AS delivered_at
        FROM vkpi_shipments s
        JOIN vkpi_projects p ON p.id = s.project_id
        WHERE s.delivered_at IS NOT NULL
          AND s.delivered_at < ?
          {scope_clause}
        GROUP BY p.id
        ORDER BY MIN(s.delivered_at) ASC
        LIMIT 500
        """,
        (cutoff, *scope_params),
    ).fetchall()

    created: list[int] = []
    skipped_existing = 0
    scanned_projects = 0
    for r in rows:
        row = dict(r)
        project_id = row.get("project_id")
        delivered_at = row.get("delivered_at")
        if not project_id or delivered_at is None:
            continue
        scanned_projects += 1
        # 项目下派单 fan-out;零派单退化为项目级单窗(assignment_id/kol_pool_id=NULL)。
        assignments = conn.execute(
            """
            SELECT id AS assignment_id, kol_pool_id
            FROM vkpi_project_kol_assignments
            WHERE project_id = ?
            """,
            (int(project_id),),
        ).fetchall()
        targets: list[tuple[int | None, int | None]] = (
            [(dict(a).get("assignment_id"), dict(a).get("kol_pool_id")) for a in assignments]
            if assignments
            else [(None, None)]
        )
        for assignment_id, kol_pool_id in targets:
            result = open_window_for_delivered(
                project_id=int(project_id),
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
        "scanned_projects": scanned_projects,
        "created": created,
        "skipped_existing": skipped_existing,
        "scope_mode": scope.scope_context(staff)["scope_mode"],
        "note": "只为人 CREATE 待核观察窗口,零自动裁决;created=[] 多因无 delivered shipment(物流断流,诚实)。",
    }


# 「活动」窗口(等内容、可被内容扫描覆盖)的状态集合;closed/content_missing 不再扫。
_WINDOW_SCANNABLE_STATUSES = ("pending", "scanning", "matched")


def _mark_window_scanned(conn: Any, window_id: int, matched: bool) -> None:
    """只在本窗口行上记一次扫描痕迹(scan_count+1、last_scan_at、可选 status→scanning)。

    红线:只动 vkpi_project_content_observation_windows 自己这一行的扫描元数据;
    绝不改 project/assignment/cost。matched=True 时把窗口 status 抬到 'matched'(展示态,
    仍待人确认),否则保持原 status 但翻到 'scanning' 以示已扫过(只在 pending→scanning)。
    """
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
) -> dict[str, Any]:
    """内容观测扫描(content-observation-scan)的实际任务体 —— 对到期/活动窗口找真内容。

    对每个活动观察窗口(pending/scanning/matched),在 **已有的真实证据表**
    vkpi_kol_video_evidence 里找该项目(且若窗口绑定 KOL 则匹配 kol_pool_id)在窗口
    starts_at 之后发布、is_active 的视频证据;命中即以「候选」materialize 进
    vkpi_project_content_posts(经 record_content_candidate,unique(project_id,content_url)
    去重),并在窗口行记一次扫描痕迹(scan_count/last_scan_at/status)。

    红线(与 scan_delivered_into_windows 同款,零自动裁决):
    - 只「物化观测」:读真证据 → CREATE 候选 + 记本窗口扫描元数据。绝不写
      vkpi_projects.stage/closed_at、vkpi_project_kol_assignments.stage、vkpi_cost_ledger,
      绝不碰 viltrox_fit_score / rule_v0。候选恒以 status='candidate' 入库等人复核。
    - 数据源是系统内已落地的 KOL 视频证据(非新爬虫/非 LLM):无证据 → created=[] 是
      诚实结果(KOL 尚未发布 Viltrox 内容,或物流断流没开窗)。
    - rate-limit:跳过 last_scan_at 在 min_scan_interval_minutes 内刚扫过的窗口(幂等友好,
      避免调度高频重复扫同一窗;命中去重又由 unique 约束兜底)。
    """
    # schema 守卫:窗口/证据表缺失(迁移 128 未跑)→ 诚实返回空,绝不报错也不编造。
    if not table_exists("vkpi_project_content_observation_windows") or not table_exists(
        "vkpi_kol_video_evidence"
    ):
        return {
            "status": "ok",
            "scanned_windows": 0,
            "rate_limited": 0,
            "created_posts": [],
            "skipped_dupes": 0,
            "schema_ready": False,
            "scope_mode": scope.scope_context(staff)["scope_mode"],
            "note": "观察窗口/证据表缺失(迁移 128/085 未应用)→ 空扫描(诚实)。",
        }

    conn = get_conn()
    safe_max = max(1, min(int(max_windows or 200), 500))
    interval = max(0, min(int(min_scan_interval_minutes or 0), 24 * 60))
    scan_cutoff = datetime.utcnow() - timedelta(minutes=interval) if interval else None

    scope_sql, scope_params = scope.project_filter("p", staff)
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
    for r in rows:
        win = dict(r)
        window_id = int(win.get("id") or 0)
        project_id = win.get("project_id")
        if not window_id or not project_id:
            continue
        # rate-limit:本窗口在间隔内刚扫过就跳过(幂等 + 避免高频重复)。
        last_scan = win.get("last_scan_at")
        if scan_cutoff is not None and isinstance(last_scan, datetime):
            if last_scan.replace(tzinfo=None) >= scan_cutoff:
                rate_limited += 1
                continue
        kol_pool_id = win.get("kol_pool_id")
        starts_at = win.get("starts_at")

        ev_where = ["e.project_id = ?", "e.is_active = TRUE"]
        ev_params: list[Any] = [int(project_id)]
        if kol_pool_id is not None:
            ev_where.append("e.kol_pool_id = ?")
            ev_params.append(int(kol_pool_id))
        if isinstance(starts_at, datetime):
            # 只认窗口开启之后发布的内容(posted_at 是 DATE;用 >= 起始日,诚实从宽)。
            ev_where.append("(e.posted_at IS NULL OR e.posted_at >= ?)")
            ev_params.append(starts_at.date())

        evidence_rows = conn.execute(
            f"""
            SELECT e.id, e.kol_pool_id, e.content_url, e.platform, e.video_title,
                   e.posted_at, e.view_count, e.like_count, e.comment_count
            FROM vkpi_kol_video_evidence e
            WHERE {' AND '.join(ev_where)}
            ORDER BY e.posted_at DESC NULLS LAST, e.id DESC
            LIMIT 50
            """,
            tuple(ev_params),
        ).fetchall()

        matched_any = False
        for ev in evidence_rows:
            evd = dict(ev)
            content_url = str(evd.get("content_url") or "").strip()
            if not content_url:
                continue
            posted_at = evd.get("posted_at")
            published_iso = str(posted_at) if posted_at is not None else None
            result = record_content_candidate(
                project_id=int(project_id),
                assignment_id=win.get("assignment_id"),
                kol_pool_id=evd.get("kol_pool_id") if evd.get("kol_pool_id") is not None else kol_pool_id,
                post={
                    "content_url": content_url,
                    "platform": evd.get("platform") or "",
                    "title": evd.get("video_title") or "",
                    "published_at": published_iso,
                    "view_count": evd.get("view_count") or 0,
                    "like_count": evd.get("like_count") or 0,
                    "comment_count": evd.get("comment_count") or 0,
                    "evidence_id": evd.get("id"),
                    "match_reason": "observation_scan: kol_video_evidence in window",
                    "matched_terms": "viltrox_evidence",
                },
                staff=staff,
            )
            status = result.get("status")
            if status == "created":
                post = result.get("post") or {}
                if post.get("id") is not None:
                    created_posts.append(int(post["id"]))
                matched_any = True
            elif status == "skipped":
                skipped_dupes += 1
                matched_any = True  # URL 已是该项目候选 → 视为命中(窗口确有内容)。

        _mark_window_scanned(conn, window_id, matched=matched_any)
        scanned_windows += 1

    conn.commit()
    return {
        "status": "ok",
        "scanned_windows": scanned_windows,
        "rate_limited": rate_limited,
        "created_posts": created_posts,
        "skipped_dupes": skipped_dupes,
        "scope_mode": scope.scope_context(staff)["scope_mode"],
        "note": (
            "内容观测扫描:对活动窗口在真证据表(vkpi_kol_video_evidence)找内容→物化候选,零自动裁决;"
            "created_posts=[] 多因 KOL 尚未发布内容或无窗口(诚实)。"
        ),
    }
