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

from datetime import datetime
from typing import Any

from app.db.connection import get_conn, table_exists
from app.domains.access import scope
from app.domains.projects import observation_window_open, observation_window_scans
from app.core.logging import get_logger

logger = get_logger(__name__)


# 窗口「活动」态(去重以此为准):这三态视为还在等内容,不应重复开窗。
_WINDOW_ACTIVE_STATUSES = ("pending", "scanning", "matched")

# 内容帖子复核动作 → 仅置本表 status,绝不连带改业务表。
_VALID_POST_ACTIONS = {"matched", "rejected", "needs_review"}


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


def _emit_event(event_type: str, **kw: Any) -> None:
    """cut3 · 业务主干事件埋点(best-effort,失败不影响主流程,零触 viltrox_fit_score)。"""
    try:
        from app.domains.platform import event_ledger

        event_ledger.emit(event_type, source="observation_windows", **kw)
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        pass


# 履约 content post 走规范路径可追溯(10D,additive)的来源标记。
# vkpi_kol_video_evidence.source 列是 VARCHAR(20) → 这两个常量必须 <=20 字符。
_CONTENT_POST_EVIDENCE_SOURCE = "content_post"  # 12 字符,安全。
_CONTENT_POST_EVIDENCE_TYPE = "video"


def _ensure_workflow_evidence_for_post(
    conn: Any,
    *,
    project_id: int,
    kol_pool_id: int | None,
    content_url: str,
    platform: str,
    title: str,
    published_at: Any,
    view_count: int,
    like_count: int,
    comment_count: int,
) -> int | None:
    """为一条 content post 写/取一条可追溯的 workflow evidence(vkpi_kol_video_evidence)。

    这是 10D 履约闭环「content post 走规范路径可追溯」的 evidence 侧:手录/抓取的内容落
    canonical content_posts 表时,**同时**在真证据表登记一条按 URL 可追的 evidence,返回其 id
    供回填 vkpi_project_content_posts.evidence_id。

    additive 红线:
    - 只 INSERT/UPSERT vkpi_kol_video_evidence 这一行;绝不写 viltrox_fit_score / rule_v0,
      绝不改 project.stage / assignment.stage / cost_ledger。
    - kol_pool_id 是 evidence 表 NOT NULL 列且 FK→vkpi_kol_pool:无 kol_pool_id 或该 KOL 不存在
      时诚实跳过(返回 None),绝不伪造、绝不报错中断主流程。
    - ON CONFLICT(content_url) 幂等:同 URL 已有 evidence(如 scan 路径先落)→ 复用其 id,
      不覆盖既有抓取来源的 metrics(用 COALESCE 仅补空),只保证 project_id 挂上。
    - compat:? 占位、bool 写 True、INTERVAL/% 不出现在 SQL 串里。
    """
    kpid = _nullable_int(kol_pool_id)
    if kpid is None:
        return None
    if not table_exists("vkpi_kol_video_evidence") or not table_exists("vkpi_kol_pool"):
        return None
    # FK 守卫:kol_pool 行不存在则跳过(evidence.kol_pool_id NOT NULL + FK 会炸)。
    pool_row = conn.execute(
        "SELECT id FROM vkpi_kol_pool WHERE id = ? LIMIT 1",
        (kpid,),
    ).fetchone()
    if pool_row is None:
        return None

    # published_at 是 TIMESTAMPTZ;evidence.posted_at 是 DATE。取日期部分(诚实从宽)。
    posted_date = None
    if isinstance(published_at, datetime):
        posted_date = published_at.date()

    source_ref = f"content_post:{int(project_id)}"
    cursor = conn.execute(
        """
        INSERT INTO vkpi_kol_video_evidence
            (kol_pool_id, project_id, content_url, platform, video_title, title,
             posted_at, view_count, like_count, comment_count,
             evidence_type, source, source_ref, confidence, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'medium', ?)
        ON CONFLICT (content_url) DO UPDATE SET
            project_id = COALESCE(vkpi_kol_video_evidence.project_id, excluded.project_id),
            video_title = CASE WHEN vkpi_kol_video_evidence.video_title IS NULL
                               OR vkpi_kol_video_evidence.video_title = ''
                               THEN excluded.video_title ELSE vkpi_kol_video_evidence.video_title END,
            posted_at = COALESCE(vkpi_kol_video_evidence.posted_at, excluded.posted_at),
            updated_at = NOW()
        RETURNING id
        """,
        (
            kpid,
            int(project_id),
            content_url,
            str(platform or ""),
            str(title or ""),
            str(title or ""),
            posted_date,
            int(view_count or 0),
            int(like_count or 0),
            int(comment_count or 0),
            _CONTENT_POST_EVIDENCE_TYPE,
            _CONTENT_POST_EVIDENCE_SOURCE,
            source_ref,
            True,
        ),
    )
    ev_row = cursor.fetchone()
    if ev_row is None:
        return None
    try:
        return int(dict(ev_row)["id"])
    except (TypeError, ValueError, KeyError):
        return None


def _link_post_to_observation_window(
    conn: Any,
    *,
    project_id: int,
    assignment_id: int | None,
    kol_pool_id: int | None,
    post_id: int,
) -> int | None:
    """把刚落库的 content post 挂到对应的活动观察窗口(set matched_content_post_id)。

    这是 10D 履约闭环「可追溯」的 window 侧:content post → observation_window → workflow_evidence
    形成可追链路。匹配口径与 open_window_for_delivered 去重同款(project + assignment + kol_pool,
    NULL 维度用 IS NULL),挑最新的活动窗口(pending/scanning/matched)。

    additive 红线:
    - 只 UPDATE 该窗口行的 matched_content_post_id(仅当其当前为 NULL,不覆盖既有匹配)
      + 把 status 抬到 'matched'(展示态,仍待人确认)+ updated_at;绝不改
      project/assignment/cost/viltrox_fit_score。
    - 找不到活动窗口 → 诚实返回 None(手录内容可能先于开窗;不强行建窗,保持零自动裁决)。
    - compat:? 占位、bool 写 True。
    """
    pid = int(project_id or 0)
    if pid <= 0 or not post_id:
        return None
    if not table_exists("vkpi_project_content_observation_windows"):
        return None

    aid = _nullable_int(assignment_id)
    kpid = _nullable_int(kol_pool_id)
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
    status_placeholders = ",".join(["?"] * len(_WINDOW_ACTIVE_STATUSES))
    where_parts.append(f"status IN ({status_placeholders})")
    params.extend(_WINDOW_ACTIVE_STATUSES)

    win_row = conn.execute(
        f"""
        SELECT id FROM vkpi_project_content_observation_windows
        WHERE {' AND '.join(where_parts)}
        ORDER BY id DESC
        LIMIT 1
        """,
        tuple(params),
    ).fetchone()
    if win_row is None:
        return None
    window_id = int(dict(win_row)["id"])

    now = datetime.utcnow()
    conn.execute(
        """
        UPDATE vkpi_project_content_observation_windows
        SET matched_content_post_id = COALESCE(matched_content_post_id, ?),
            status = 'matched',
            updated_at = ?
        WHERE id = ?
        """,
        (int(post_id), now, window_id),
    )
    return window_id


def open_window_for_delivered_in_transaction(
    conn: Any,
    project_id: int,
    assignment_id: int | None,
    kol_pool_id: int | None,
    delivered_at: Any,
    staff: dict[str, Any] | None = None,
    *,
    source_shipment_id: int | None = None,
) -> dict[str, Any]:
    """Compatibility export for the no-commit exact-write primitive."""
    return observation_window_open.open_window_for_delivered_in_transaction(
        conn,
        project_id,
        assignment_id,
        kol_pool_id,
        delivered_at,
        staff,
        source_shipment_id=source_shipment_id,
    )


def open_window_for_delivered(
    project_id: int,
    assignment_id: int | None,
    kol_pool_id: int | None,
    delivered_at: Any,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compatibility wrapper that commits the existing ordinary write path."""
    conn = get_conn()
    result = open_window_for_delivered_in_transaction(
        conn, project_id, assignment_id, kol_pool_id, delivered_at, staff,
    )
    conn.commit()
    if result.get("status") == "created":
        window = result.get("window") or {}
        _emit_event(
            "observation.window_created",
            entity_type="observation_window",
            entity_id=window.get("id"),
            payload={"project_id": int(project_id), "kol_pool_id": _nullable_int(kol_pool_id)},
        )
    return result


def list_windows(
    staff: dict[str, Any] | None = None,
    status: str = "pending",
    project_id: int | None = None,
) -> dict[str, Any]:
    """兼容门面：纯 SELECT 列观察窗口。"""
    return observation_window_scans.list_windows(
        staff=staff,
        status=status,
        project_id=project_id,
        get_conn_fn=get_conn,
        nullable_int_fn=_nullable_int,
        row_to_window_fn=_row_to_window,
        scope_module=scope,
    )


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
    post = _row_to_post(row)
    post_id = _nullable_int(post.get("id"))

    # 10D 履约闭环「走规范路径可追溯」(additive,与上面 INSERT 同事务,一次性 commit):
    # 1) 若调用方没带 evidence_id,补登一条可追 URL 的 workflow evidence 并回填 evidence_id;
    # 2) 把这条 content post 挂到对应活动观察窗口(set matched_content_post_id)。
    # 链路:content_post -> evidence_id -> vkpi_kol_video_evidence(URL) +
    #      observation_window.matched_content_post_id -> content_post。任一环失败都 best-effort
    #      吞掉(不回滚已落库的 candidate),绝不触 viltrox_fit_score / 业务状态。
    linked_evidence_id = evidence_id
    linked_window_id: int | None = None
    if post_id is not None:
        if linked_evidence_id is None:
            try:
                new_ev_id = _ensure_workflow_evidence_for_post(
                    conn,
                    project_id=pid,
                    kol_pool_id=kpid,
                    content_url=content_url,
                    platform=str(data.get("platform") or ""),
                    title=str(data.get("title") or ""),
                    published_at=published_at,
                    view_count=_int0("view_count"),
                    like_count=_int0("like_count"),
                    comment_count=_int0("comment_count"),
                )
            except Exception:
                logger.warning("workflow_evidence backfill failed (additive, suppressed)", exc_info=True)
                new_ev_id = None
            if new_ev_id is not None:
                conn.execute(
                    "UPDATE vkpi_project_content_posts SET evidence_id = ?, updated_at = ? WHERE id = ?",
                    (int(new_ev_id), datetime.utcnow(), int(post_id)),
                )
                linked_evidence_id = new_ev_id
                post["evidence_id"] = int(new_ev_id)
        try:
            linked_window_id = _link_post_to_observation_window(
                conn,
                project_id=pid,
                assignment_id=aid,
                kol_pool_id=kpid,
                post_id=int(post_id),
            )
        except Exception:
            logger.warning("observation_window link failed (additive, suppressed)", exc_info=True)
            linked_window_id = None

    conn.commit()
    _emit_event("content.detected", entity_type="content_post", entity_id=post.get("id"),
                payload={
                    "project_id": pid,
                    "kol_pool_id": kpid,
                    "content_url": content_url,
                    "evidence_id": linked_evidence_id,
                    "observation_window_id": linked_window_id,
                })
    return {
        "status": "created",
        "post": post,
        "evidence_id": linked_evidence_id,
        "observation_window_id": linked_window_id,
    }


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


def matched_content_posts_for_retrospective(
    project_id: int,
    conn: Any | None = None,
) -> list[dict[str, Any]]:
    """复盘聚合用:取项目下「人已确认匹配」的履约内容帖(status in matched/retrospective_ready)。

    这是「复盘口径诚实化」的履约内容侧:retrospective_aggregate 原本只读 video_analysis_final_v1
    视频证据,与 matched content_posts 解耦(会在 0 帖 0 窗口的项目上产出,高估履约成熟度)。
    本函数把人工确认的真实履约内容也喂进复盘,二者都计入。

    纯只读:SELECT content post 并 LEFT JOIN 其关联 evidence。content_posts 的三项指标是
    legacy NOT NULL DEFAULT 0,不能区分“真 0”与“未采”;因此只在 evidence 有抓取时间/
    metrics source/非 content_post 来源时投影为 observed_evidence,否则对复盘返回 NULL。
    绝不写任何表、绝不碰 viltrox_fit_score。
    无 RBAC 收口(调用方 retrospective 已按 project_id 定向,worker 侧无 staff 上下文);
    口径与按曝光降序选取一致,供聚合侧 Top-N 截断。
    """
    pid = int(project_id or 0)
    if pid <= 0:
        return []
    own_conn = conn if conn is not None else get_conn()
    if not table_exists("vkpi_project_content_posts"):
        return []
    rows = own_conn.execute(
        """
        WITH matched AS (
            SELECT c.id, c.project_id, c.assignment_id, c.kol_pool_id, c.evidence_id,
                   c.platform, c.content_url, c.title, c.caption, c.published_at,
                   c.view_count AS legacy_view_count,
                   c.like_count AS legacy_like_count,
                   c.comment_count AS legacy_comment_count,
                   e.view_count AS evidence_view_count,
                   e.like_count AS evidence_like_count,
                   e.comment_count AS evidence_comment_count,
                   c.match_reason, c.status,
                   CASE
                       WHEN e.id IS NOT NULL AND (
                           e.metrics_scraped_at IS NOT NULL
                           OR NULLIF(BTRIM(COALESCE(e.metrics_source, '')), '') IS NOT NULL
                           OR e.scraped_at IS NOT NULL
                           OR NULLIF(BTRIM(COALESCE(e.scrape_source, '')), '') IS NOT NULL
                           OR LOWER(COALESCE(e.source, '')) <> 'content_post'
                       ) THEN 'observed_evidence'
                       WHEN e.id IS NOT NULL THEN 'linked_unobserved'
                       ELSE 'legacy_unobserved'
                   END AS metric_observation_status,
                   COALESCE(
                       NULLIF(BTRIM(COALESCE(e.metrics_source, '')), ''),
                       NULLIF(BTRIM(COALESCE(e.scrape_source, '')), ''),
                       NULLIF(BTRIM(COALESCE(e.source, '')), '')
                   ) AS metric_observation_source
            FROM vkpi_project_content_posts c
            LEFT JOIN vkpi_kol_video_evidence e
              ON e.id = c.evidence_id AND e.is_active IS NOT FALSE
            WHERE c.project_id = ? AND c.status IN ('matched', 'retrospective_ready')
        )
        SELECT *,
               CASE WHEN metric_observation_status = 'observed_evidence'
                    THEN evidence_view_count ELSE NULL END AS view_count,
               CASE WHEN metric_observation_status = 'observed_evidence'
                    THEN evidence_like_count ELSE NULL END AS like_count,
               CASE WHEN metric_observation_status = 'observed_evidence'
                    THEN evidence_comment_count ELSE NULL END AS comment_count
        FROM matched
        ORDER BY view_count DESC NULLS LAST, id ASC
        """,
        (pid,),
    ).fetchall()
    return [_row_to_post(r) for r in rows]


def review_content_post(
    post_id: int,
    action: str,
    staff: dict[str, Any] | None = None,
    note: str = "",
) -> dict[str, Any]:
    """人工复核一条内容帖子候选 → 置本帖子行 status(matched/rejected/needs_review)。

    履约后半链做实(R-fulfill):action='matched' 时,**额外** backfill 对应活动观察窗口的
    matched_content_post_id(让人工确认的候选真正挂回窗口,链路 content_post -> window 闭合),
    使前端「窗口/匹配帖」一栏能反映真匹配,复盘聚合也能据此纳入已匹配履约内容。

    红线:只动 vkpi_project_content_posts(本帖行 status)+ 仅在 matched 时回填该窗口行的
    matched_content_post_id/status(展示态)。绝不改 project.stage/closed_at/assignment.stage/
    cost_ledger/viltrox_fit_score。回填复用 _link_post_to_observation_window(同款匹配口径,
    仅当窗口 matched_content_post_id 为 NULL 才写,不覆盖既有匹配)。
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
    # 同时取出帖子的 (project_id, assignment_id, kol_pool_id) 供 matched 时回填窗口。
    scope_sql, scope_params = scope.project_filter("p", staff)
    scope_clause = f"AND {scope_sql}" if scope_sql else ""
    target = conn.execute(
        f"""
        SELECT c.id, c.project_id, c.assignment_id, c.kol_pool_id, c.status AS prior_status
        FROM vkpi_project_content_posts c
        JOIN vkpi_projects p ON p.id = c.project_id
        WHERE c.id = ? {scope_clause}
        """,
        (cid, *scope_params),
    ).fetchone()
    if target is None:
        return {"status": "error", "error": "post not found or out of scope"}
    target_row = dict(target)

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

    # 履约后半链做实:人工确认 matched → 把帖子挂回对应活动观察窗口(set matched_content_post_id)。
    # best-effort:回填失败不回滚已落库的 status 改动(帖已 matched,仍可手动再触发)。
    linked_window_id: int | None = None
    if act == "matched":
        try:
            linked_window_id = _link_post_to_observation_window(
                conn,
                project_id=int(target_row.get("project_id") or 0),
                assignment_id=target_row.get("assignment_id"),
                kol_pool_id=target_row.get("kol_pool_id"),
                post_id=cid,
            )
        except Exception:
            logger.warning("review matched -> window backfill failed (additive, suppressed)", exc_info=True)
            linked_window_id = None

    conn.commit()
    if act == "matched":
        _emit_event("content.matched", entity_type="content_post", entity_id=cid,
                    payload={"project_id": target_row.get("project_id"),
                             "observation_window_id": linked_window_id})
    return {
        "status": "ok",
        "action": act,
        "post": _row_to_post(row),
        "observation_window_id": linked_window_id,
        "previous_status": str(target_row.get("prior_status") or ""),
        "state_changed": str(target_row.get("prior_status") or "") != act,
    }


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
    if advanced > 0:
        _emit_event("project.retrospective_ready", entity_type="project", entity_id=pid,
                    payload={"advanced_count": advanced})
    return {"status": "ok", "project_id": pid, "advanced_count": advanced}


def scan_delivered_into_windows(
    staff: dict[str, Any] | None = None,
    days_overdue: int = 7,
    *,
    project_id: int | None = None,
) -> dict[str, Any]:
    """兼容门面：扫已签收派单并创建待人核观察窗口。"""
    return observation_window_scans.scan_delivered_into_windows(
        staff=staff,
        days_overdue=days_overdue,
        project_id=project_id,
        get_conn_fn=get_conn,
        nullable_int_fn=_nullable_int,
        open_window_fn=open_window_for_delivered,
        scope_module=scope,
    )


def close_expired_windows(
    staff: dict[str, Any] | None = None,
    *,
    grace_days: int = 3,
) -> dict[str, Any]:
    """兼容门面：收口已过宽限期的活动观察窗口。"""
    return observation_window_scans.close_expired_windows(
        staff=staff,
        grace_days=grace_days,
        get_conn_fn=get_conn,
    )


def _mark_window_scanned(conn: Any, window_id: int, matched: bool) -> None:
    """兼容导出：只记本窗口的扫描痕迹。"""
    observation_window_scans.mark_window_scanned(conn, window_id, matched)


def scan_windows_for_content(
    staff: dict[str, Any] | None = None,
    max_windows: int = 200,
    min_scan_interval_minutes: int = 60,
) -> dict[str, Any]:
    """兼容门面：对活动窗口在真证据表找内容并物化为待人核候选。"""
    return observation_window_scans.scan_windows_for_content(
        staff=staff,
        max_windows=max_windows,
        min_scan_interval_minutes=min_scan_interval_minutes,
        get_conn_fn=get_conn,
        mark_window_scanned_fn=_mark_window_scanned,
        table_exists_fn=table_exists,
        record_candidate_fn=record_content_candidate,
        scope_module=scope,
    )


def _find_post_for_window(
    conn: Any,
    *,
    project_id: int,
    assignment_id: int | None,
    kol_pool_id: int | None,
) -> int | None:
    """兼容导出：为活动窗口挑选最佳已落库内容候选。"""
    return observation_window_scans.find_post_for_window(
        conn,
        project_id=project_id,
        assignment_id=assignment_id,
        kol_pool_id=kol_pool_id,
        nullable_int_fn=_nullable_int,
        table_exists_fn=table_exists,
    )


def scan_windows_backfill_matched_post(
    staff: dict[str, Any] | None = None,
    max_windows: int = 500,
) -> dict[str, Any]:
    """兼容门面：把已落库候选幂等回填到尚未挂帖的活动观察窗口。"""
    return observation_window_scans.scan_windows_backfill_matched_post(
        staff=staff,
        max_windows=max_windows,
        emit_event_fn=_emit_event,
        find_post_fn=_find_post_for_window,
        get_conn_fn=get_conn,
        scope_module=scope,
        table_exists_fn=table_exists,
    )
