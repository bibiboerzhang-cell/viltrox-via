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

from app.db.connection import get_conn
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
