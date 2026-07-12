"""Unified KOL Pool search-session state.

This module records smart URL/profile/text recall orchestration state. It only
writes the session tables introduced by migration 103 and must not update
vkpi_kol_pool scoring fields.

Pure serde/normalization helpers live in ``search_sessions_serde`` and the
attach-result builders live in ``search_sessions_attach``; both are re-exported
below so all existing call sites keep importing from ``search_sessions``.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn

logger = get_logger(__name__)

# Re-export pure serde/normalization helpers (behavior-preserving move).
from app.domains.kol.search_sessions_serde import (
    ITEM_STATUSES,
    SESSION_QUERY_TYPES,
    SESSION_STATUSES,
    _compact_flow,
    _compact_video_batch_flow,
    _dict,
    _float_or_none,
    _int_or_none,
    _item_counts,
    _json_dumps,
    _jsonable,
    _list,
    _loads,
    _normalize_query_type,
    _normalize_status,
    _row_to_item,
    _row_to_session,
    _staff_user_id,
    _text,
)

# Re-export attach-result builders (behavior-preserving move).
from app.domains.kol.search_sessions_attach import (
    _link_job_payloads,
    _session_status_from_url_result,
    _url_result_item,
    attach_new_discovery_result,
    attach_recall_result,
    attach_url_result,
)

# 触达门槛读端展示闸(2026-07-12 第二道闸,kol_pool 12297 两粉号案):会话项是搜索时的快照,
# 档案补全回填 followers 后快照不会自己变——读端按 pool 现值实时重判。判据复用
# discovery_filters 单一真源;只挡展示,绝不改写会话项/池行。
from app.domains.kol.discovery_filters import (  # noqa: E402
    LOW_REACH_FLAG_LIKE_PATTERN,
    _reach_display_state,
    _reach_floor_enabled,
    _reach_floor_min_followers,
)

# 展示闸适用的会话项类型:推荐/发现面的候选(用户显式贴 URL 的分析项 url_video/url_profile
# 不闸——那是用户点名要看的,非推荐)。
_REACH_GATED_ITEM_TYPES = {"new_creator", "existing_kol", "recall_candidate"}


def _reach_gate_pool_rows(
    conn: Any,
    ids: list[int],
    pairs: list[tuple[str, str]],
) -> tuple[dict[int, dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    """批量取会话项对应的 pool 行现值(id 直查 + new_creator 按 platform/handle 反查)。

    new_creator 会话项 kol_pool_id 恒 NULL(设计不变量,见 approve_session 注释),但发现已
    自动入库 → 按 (platform, lower(handle)) 反查现值。返回 (by_id, by_pair);查询失败抛给调用方。
    """
    by_id: dict[int, dict[str, Any]] = {}
    by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    select_cols = (
        "SELECT id, platform, handle, followers, avg_views, avg_comments, engagement_rate, "
        "(raw_platform_data LIKE ?) AS low_reach_flagged FROM vkpi_kol_pool"
    )
    if ids:
        placeholders = ",".join(["?"] * len(ids))
        rows = conn.execute(
            f"{select_cols} WHERE id IN ({placeholders})",
            (LOW_REACH_FLAG_LIKE_PATTERN, *ids),
        ).fetchall()
        for row in rows:
            data = dict(row)
            by_id[int(data["id"])] = data
    if pairs:
        clauses = " OR ".join(["(lower(platform)=? AND lower(handle)=?)"] * len(pairs))
        params: list[Any] = [LOW_REACH_FLAG_LIKE_PATTERN]
        for platform, handle in pairs:
            params.extend([platform, handle])
        rows = conn.execute(f"{select_cols} WHERE {clauses}", tuple(params)).fetchall()
        for row in rows:
            data = dict(row)
            key = (str(data.get("platform") or "").lower(), str(data.get("handle") or "").lower())
            by_pair[key] = data
    return by_id, by_pair


def _item_reach_pair(item: dict[str, Any]) -> tuple[str, str] | None:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    platform = str(payload.get("platform") or "").strip().lower()
    handle = str(payload.get("handle") or "").strip().lstrip("@").lower()
    if platform and handle:
        return (platform, handle)
    return None


def _apply_reach_display_gate(
    conn: Any,
    items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """会话读端触达展示闸:按 pool 现值三态过滤推荐/发现面会话项(第二道闸落点①)。

    - low_reach:followers 已知 < 门槛/互动实测全零/补全后 low_reach 标 → 不展示(计数折叠);
    - unknown:followers 未知(分析中,「分析后再 po」)→ 不展示(计数折叠;补全回填达标后
      同一会话再读自动放出——快照不变,变的是 pool 现值);
    - ok:展示。pool 行缺时退回会话项 payload 实时判据(payload 也未知 → 归分析中)。
    fail-open:池查询异常 → 原样返回全部项(过滤器绝不当故障放大器),计数带 error 标。
    红线:零写库;落库≠推荐——池行/会话项都保留,只挡本展示出口。
    """
    counts: dict[str, Any] = {
        "enabled": _reach_floor_enabled(),
        "min_followers": _reach_floor_min_followers(),
        "hidden_low_reach": 0,
        "hidden_analyzing": 0,
        "by_type": {},
    }
    if not items or not _reach_floor_enabled():
        return items, counts
    gated_idx = {
        i for i, item in enumerate(items)
        if str(item.get("item_type") or "") in _REACH_GATED_ITEM_TYPES
    }
    if not gated_idx:
        return items, counts
    ids = sorted({
        int(items[i]["kol_pool_id"]) for i in gated_idx
        if _int_or_none(items[i].get("kol_pool_id"))
    })
    pairs = sorted({
        pair for i in gated_idx
        if not _int_or_none(items[i].get("kol_pool_id")) and (pair := _item_reach_pair(items[i]))
    })
    try:
        by_id, by_pair = _reach_gate_pool_rows(conn, ids, pairs)
    except Exception:
        logger.warning("reach display gate skipped(fail-open 不误杀)", exc_info=True)
        counts["error"] = "pool_lookup_failed"
        return items, counts

    visible: list[dict[str, Any]] = []
    for i, item in enumerate(items):
        if i not in gated_idx:
            visible.append(item)
            continue
        pool_row: dict[str, Any] | None = None
        pool_id = _int_or_none(item.get("kol_pool_id"))
        if pool_id:
            pool_row = by_id.get(int(pool_id))
        else:
            pair = _item_reach_pair(item)
            if pair:
                pool_row = by_pair.get(pair)
        # pool 现值优先(补全回填后的真值);池行缺 → 退回会话项 payload 快照实时判据。
        candidate = pool_row if pool_row is not None else (
            item.get("payload") if isinstance(item.get("payload"), dict) else {}
        )
        state = _reach_display_state(candidate)
        if state == "ok":
            visible.append(item)
            continue
        bucket = "hidden_low_reach" if state == "low_reach" else "hidden_analyzing"
        counts[bucket] += 1
        type_key = str(item.get("item_type") or "unknown")
        type_counts = counts["by_type"].setdefault(type_key, {"hidden_low_reach": 0, "hidden_analyzing": 0})
        type_counts[bucket] += 1
    return visible, counts


def create_session(
    *,
    query_text: str,
    query_type: str = "unknown",
    source: str = "smart_kol_input",
    input_payload: dict[str, Any] | None = None,
    status: str = "planned",
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    conn = get_conn()
    row = conn.execute(
        """
        INSERT INTO vkpi_kol_search_sessions
          (query_text, query_type, source, status, created_by, input_payload_json, result_summary_json)
        VALUES (?, ?, ?, ?, ?, ?::jsonb, '{}'::jsonb)
        RETURNING *
        """,
        (
            _text(query_text),
            _normalize_query_type(query_type),
            _text(source) or "smart_kol_input",
            _normalize_status(status),
            _staff_user_id(staff),
            _json_dumps(input_payload or {}),
        ),
    ).fetchone()
    conn.commit()
    return _row_to_session(row)


def list_sessions(*, limit: int = 20, status: str = "") -> dict[str, Any]:
    safe_limit = max(1, min(int(limit or 20), 100))
    normalized_status = _normalize_status(status) if status else ""
    conn = get_conn()
    if normalized_status:
        rows = conn.execute(
            """
            SELECT *
            FROM vkpi_kol_search_sessions
            WHERE status=?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (normalized_status, safe_limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT *
            FROM vkpi_kol_search_sessions
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
    return {
        "status": "ready",
        "count": len(rows),
        "items": [_row_to_session(row) for row in rows],
    }


def list_history(
    *,
    limit: int = 20,
    status: str = "",
    query_type: str = "",
    item_limit: int = 5,
    staff: dict[str, Any] | None = None,
    scope_to_staff: bool = True,
) -> dict[str, Any]:
    """Return recent search sessions with compact item previews for history UI.

    每个人的记录不能串:默认按 created_by=当前登录人作用域过滤(scope_to_staff),
    不同员工互不串记录。actor 取不到时不过滤(回退看全部,避免登录态异常致空)。
    """
    safe_limit = max(1, min(int(limit or 20), 50))
    safe_item_limit = max(0, min(int(item_limit or 5), 10))
    normalized_status = _normalize_status(status) if status else ""
    normalized_query_type = _normalize_query_type(query_type) if query_type else ""

    where: list[str] = []
    params: list[Any] = []
    if normalized_status:
        where.append("status=?")
        params.append(normalized_status)
    if normalized_query_type:
        where.append("query_type=?")
        params.append(normalized_query_type)
    actor_id = _staff_user_id(staff) if scope_to_staff else None
    if actor_id:
        where.append("created_by=?")
        params.append(actor_id)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    conn = get_conn()
    rows = conn.execute(
        f"""
        SELECT *
        FROM vkpi_kol_search_sessions
        {where_sql}
        ORDER BY updated_at DESC, id DESC
        LIMIT ?
        """,
        (*params, safe_limit),
    ).fetchall()
    sessions = [_row_to_session(row) for row in rows]
    if not sessions:
        return {
            "status": "ready",
            "count": 0,
            "items": [],
            "filters": {
                "status": normalized_status,
                "query_type": normalized_query_type,
                "limit": safe_limit,
                "item_limit": safe_item_limit,
            },
        }

    session_ids = [int(session["id"]) for session in sessions if _int_or_none(session.get("id"))]
    placeholders = ", ".join(["?"] * len(session_ids))
    item_rows = conn.execute(
        f"""
        SELECT *
        FROM vkpi_kol_search_session_items
        WHERE session_id IN ({placeholders})
        ORDER BY session_id, rank NULLS LAST, id
        """,
        tuple(session_ids),
    ).fetchall()

    grouped: dict[int, list[dict[str, Any]]] = {int(session_id): [] for session_id in session_ids}
    for row in item_rows:
        item = _row_to_item(row)
        grouped.setdefault(int(item.get("session_id") or 0), []).append(item)

    history_items: list[dict[str, Any]] = []
    for session in sessions:
        session_id = int(session["id"])
        all_items = grouped.get(session_id, [])
        # 触达展示闸(第二道闸落点①,与 get_session 同口径):历史面板的 items_preview 也是
        # 前端「全网新发现/召回」展示来源(restoreSession 直接吃),同样按 pool 现值过滤。
        all_items, reach_counts = _apply_reach_display_gate(conn, all_items)
        counts = _item_counts(all_items)
        preview_items = all_items[:safe_item_limit] if safe_item_limit else []
        active_items = [
            item
            for item in all_items
            if _text(item.get("status")) in {"queued", "running", "already_queued"}
        ]
        result_summary = _dict(session.get("result_summary"))
        history_items.append(
            {
                **session,
                "item_count": len(all_items),
                "items_preview": preview_items,
                "active_items": active_items[:3],
                "counts": counts,
                "reach_floor_display": reach_counts,
                "summary": {
                    "kind": result_summary.get("kind"),
                    "platform": result_summary.get("platform"),
                    "url_type": result_summary.get("url_type"),
                    "in_pool": result_summary.get("in_pool"),
                    "items_written": result_summary.get("items_written"),
                    "matched_kol_pool_id": result_summary.get("matched_kol_pool_id"),
                    "viltrox_fit_score_untouched": result_summary.get("viltrox_fit_score_untouched"),
                },
            }
        )

    return {
        "status": "ready",
        "count": len(history_items),
        "items": history_items,
        "filters": {
            "status": normalized_status,
            "query_type": normalized_query_type,
            "limit": safe_limit,
            "item_limit": safe_item_limit,
        },
    }


def get_session(session_id: int) -> dict[str, Any]:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM vkpi_kol_search_sessions WHERE id=?",
        (int(session_id),),
    ).fetchone()
    if not row:
        raise LookupError(f"search session not found: {session_id}")
    item_rows = conn.execute(
        """
        SELECT *
        FROM vkpi_kol_search_session_items
        WHERE session_id=?
        ORDER BY rank NULLS LAST, id
        """,
        (int(session_id),),
    ).fetchall()
    session = _row_to_session(row)
    items = [_row_to_item(item) for item in item_rows]
    # 名字全局一致(2026-07-03 用户点名):部分物化路径的 item payload 不带 display_name,
    # 前端只好显示 handle(YT 时是一串频道 ID)。读端统一回填:凡带 kol_pool_id 且 payload
    # 缺名字的,批量查池表补 display_name —— 一处修好,校验中/已有库/新发现全部受益。
    _need_name_ids = sorted({
        int(it["kol_pool_id"]) for it in items
        if it.get("kol_pool_id")
        and isinstance(it.get("payload"), dict)
        and not str(it["payload"].get("display_name") or it["payload"].get("channel_name") or "").strip()
    })
    if _need_name_ids:
        try:
            _ph = ",".join(["?"] * len(_need_name_ids))
            _name_rows = conn.execute(
                f"SELECT id, display_name FROM vkpi_kol_pool WHERE id IN ({_ph})",
                tuple(_need_name_ids),
            ).fetchall()
            _names = {
                int(dict(r)["id"]): str(dict(r)["display_name"] or "").strip()
                for r in _name_rows
            }
            for it in items:
                _kid = it.get("kol_pool_id")
                if _kid and isinstance(it.get("payload"), dict):
                    _nm = _names.get(int(_kid), "")
                    if _nm and not str(it["payload"].get("display_name") or "").strip():
                        it["payload"]["display_name"] = _nm
        except Exception:
            logger.warning("search_sessions.display_name_backfill_failed", exc_info=True)
    # 触达展示闸(第二道闸落点①):会话项按 pool 现值实时重判——补全回填 followers 后,
    # 低触达行(如 kol_pool 12297,2 粉)从「全网新发现/库内已有」消失;followers 未知折叠为
    # 「分析中 ×N」。counts/count 按可见项重算,隐藏计数走 reach_floor_display 诚实透出。
    items, reach_counts = _apply_reach_display_gate(conn, items)
    session["items"] = items
    session["count"] = len(items)
    session["counts"] = _item_counts(items)
    session["reach_floor_display"] = reach_counts
    return session


def get_session_item(session_id: int, item_id: int) -> dict[str, Any]:
    conn = get_conn()
    row = conn.execute(
        """
        SELECT *
        FROM vkpi_kol_search_session_items
        WHERE session_id=? AND id=?
        """,
        (int(session_id), int(item_id)),
    ).fetchone()
    if not row:
        raise LookupError(f"search session item not found: session={session_id} item={item_id}")
    return _row_to_item(row)


def approve_session(
    session_id: int,
    *,
    kol_pool_ids: list[Any],
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """R1:人审锁定该会话里要推进合作的候选 KOL → 写 approved_kol_ids(R2 据此建项目草案)。

    只接受真实存在的 kol_pool_id(校验 vkpi_kol_pool 存在性,绝不写任意 id);去重保序;replace
    语义(本次选择即最终锁定集)。校验口径用「池中存在」而非「会话项含该 id」——因全网新发现
    new_creator 入池后会话项 kol_pool_id 仍为 NULL,若按会话项交集会把这些真候选全误杀。
    审计落 result_summary_json.approval(谁/何时/接受几个/跳过几个)。绝不写 vkpi_kol_pool /
    viltrox_fit_score / rule_v0,只读池做存在性校验 + 写本会话 approved_kol_ids + summary 两处。
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM vkpi_kol_search_sessions WHERE id=?",
        (int(session_id),),
    ).fetchone()
    if not row:
        raise LookupError(f"search session not found: {session_id}")

    requested: list[int] = []
    seen: set[int] = set()
    for raw in _list(kol_pool_ids):
        parsed = _int_or_none(raw)
        if parsed and parsed not in seen:
            seen.add(parsed)
            requested.append(parsed)

    # 存在性校验:只接受真实在池的 kol_pool_id(只读 vkpi_kol_pool,绝不写)。
    valid_ids: set[int] = set()
    if requested:
        placeholders = ",".join("?" for _ in requested)
        pool_rows = conn.execute(
            f"SELECT id FROM vkpi_kol_pool WHERE id IN ({placeholders})",
            requested,
        ).fetchall()
        valid_ids = {int(dict(r)["id"]) for r in pool_rows if dict(r).get("id")}

    accepted = [kid for kid in requested if kid in valid_ids]
    skipped = [kid for kid in requested if kid not in valid_ids]

    # 审计合并进 result_summary_json.approval(沿用既有 jsonb 合并;不另加列)。
    summary = _loads(dict(row).get("result_summary_json"), {})
    if not isinstance(summary, dict):
        summary = {}
    approved_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    summary["approval"] = {
        "approved_kol_ids": accepted,
        "approved_count": len(accepted),
        "skipped_not_in_pool": skipped,
        "approved_by": _staff_user_id(staff),
        "approved_at": approved_at,
        "viltrox_fit_score_untouched": True,
    }

    updated = conn.execute(
        """
        UPDATE vkpi_kol_search_sessions
        SET approved_kol_ids=?::jsonb,
            result_summary_json=?::jsonb,
            updated_at=NOW()
        WHERE id=?
        RETURNING *
        """,
        (_json_dumps(accepted), _json_dumps(summary), int(session_id)),
    ).fetchone()
    conn.commit()
    session = _row_to_session(updated)
    session["approved_count"] = len(accepted)
    session["skipped_not_in_pool"] = skipped
    return session


def update_session_result_summary(
    session_id: int,
    *,
    status: str,
    summary_patch: dict[str, Any],
) -> dict[str, Any]:
    """Merge a small orchestration summary into one search session."""

    conn = get_conn()
    row = conn.execute(
        "SELECT result_summary_json FROM vkpi_kol_search_sessions WHERE id=?",
        (int(session_id),),
    ).fetchone()
    if not row:
        raise LookupError(f"search session not found: {session_id}")
    summary = _loads(dict(row).get("result_summary_json"), {})
    if not isinstance(summary, dict):
        summary = {}
    summary.update(_dict(summary_patch))
    updated = conn.execute(
        """
        UPDATE vkpi_kol_search_sessions
        SET status=?,
            result_summary_json=?::jsonb,
            updated_at=NOW()
        WHERE id=?
        RETURNING *
        """,
        (_normalize_status(status), _json_dumps(summary), int(session_id)),
    ).fetchone()
    conn.commit()
    return _row_to_session(updated)


def update_item_profile_execution(
    session_id: int,
    item_id: int,
    *,
    profile_result: dict[str, Any],
) -> dict[str, Any]:
    """Persist profile-crawl execution result for a discovery item."""
    conn = get_conn()
    row = conn.execute(
        """
        SELECT *
        FROM vkpi_kol_search_session_items
        WHERE session_id=? AND id=?
        """,
        (int(session_id), int(item_id)),
    ).fetchone()
    if not row:
        raise LookupError(f"search session item not found: session={session_id} item={item_id}")
    current = _row_to_item(row)
    payload = _dict(current.get("payload")).copy()
    profile_flow = _dict(profile_result.get("profile_flow"))
    status_text = _text(profile_flow.get("status") or profile_result.get("status")).lower()
    next_status = "ready" if status_text == "ready" else "failed" if "failed" in status_text or status_text in {"crawl_failed", "unsupported"} else "partial"
    kol_pool_id = _int_or_none(
        profile_flow.get("kol_pool_id")
        or profile_result.get("matched_kol_pool_id")
        or current.get("kol_pool_id")
    )
    payload["profile_execute"] = {
        "status": status_text or next_status,
        "kol_pool_id": kol_pool_id,
        "operation": profile_flow.get("operation"),
        "run_id": profile_flow.get("run_id"),
        "profile_data": profile_flow.get("profile_data"),
        "write_result": profile_flow.get("write_result"),
        "representative_video_analysis": profile_flow.get("representative_video_analysis"),
        "viltrox_fit_score_changed_ids": profile_flow.get("viltrox_fit_score_changed_ids") or profile_result.get("viltrox_fit_score_changed_ids") or [],
        "viltrox_fit_score_untouched": profile_flow.get("viltrox_fit_score_untouched") if "viltrox_fit_score_untouched" in profile_flow else profile_result.get("viltrox_fit_score_untouched"),
    }
    updated = conn.execute(
        """
        UPDATE vkpi_kol_search_session_items
        SET status=?,
            stage='profile',
            kol_pool_id=COALESCE(?, kol_pool_id),
            payload_json=?::jsonb,
            updated_at=NOW()
        WHERE session_id=? AND id=?
        RETURNING *
        """,
        (
            next_status,
            kol_pool_id,
            _json_dumps(payload),
            int(session_id),
            int(item_id),
        ),
    ).fetchone()

    session_row = conn.execute(
        "SELECT result_summary_json FROM vkpi_kol_search_sessions WHERE id=?",
        (int(session_id),),
    ).fetchone()
    summary = _loads(dict(session_row).get("result_summary_json") if session_row else "{}", {})
    if not isinstance(summary, dict):
        summary = {}
    profile_materialization = _dict(summary.get("profile_materialization")).copy()
    profile_materialization.update(
        {
            "last_item_id": int(item_id),
            "last_status": next_status,
            "last_kol_pool_id": kol_pool_id,
            "viltrox_fit_score_untouched": payload["profile_execute"].get("viltrox_fit_score_untouched"),
        }
    )
    summary["profile_materialization"] = profile_materialization
    session_status = "partial" if next_status == "failed" else "ready"
    _update_session(conn, int(session_id), status=session_status, summary=summary)
    conn.commit()
    return _row_to_item(updated)


def mark_items_profile_queued(
    session_id: int,
    *,
    item_ids: list[int],
    job_id: int,
    reason: str = "session_advance_queued",
    plan_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Mark selected discovery items as queued for one session-advance job."""

    safe_item_ids = sorted({int(item_id) for item_id in item_ids if _int_or_none(item_id)})
    safe_job_id = _int_or_none(job_id)
    if not safe_item_ids or not safe_job_id:
        return {
            "status": "ready",
            "session_id": int(session_id),
            "job_id": safe_job_id,
            "updated_count": 0,
            "items": [],
        }

    plan_by_item_id = {
        _int_or_none(item.get("item_id")): item
        for item in (plan_items or [])
        if isinstance(item, dict) and _int_or_none(item.get("item_id"))
    }
    placeholders = ", ".join(["?"] * len(safe_item_ids))
    conn = get_conn()
    rows = conn.execute(
        f"""
        SELECT *
        FROM vkpi_kol_search_session_items
        WHERE session_id=?
          AND id IN ({placeholders})
        ORDER BY rank NULLS LAST, id
        """,
        (int(session_id), *safe_item_ids),
    ).fetchall()

    queued_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    updated_items: list[dict[str, Any]] = []
    for row in rows:
        current = _row_to_item(row)
        item_id = int(current["id"])
        payload = _dict(current.get("payload")).copy()
        payload["profile_advance_job"] = {
            "status": "queued",
            "job_id": safe_job_id,
            "queued_at": queued_at,
            "reason": _text(reason) or "session_advance_queued",
            "plan": _dict(plan_by_item_id.get(item_id)).get("plan"),
            "viltrox_fit_score_untouched": True,
        }
        updated = conn.execute(
            """
            UPDATE vkpi_kol_search_session_items
            SET status='queued',
                stage='profile',
                job_id=?,
                payload_json=?::jsonb,
                updated_at=NOW()
            WHERE session_id=? AND id=?
            RETURNING *
            """,
            (
                safe_job_id,
                _json_dumps(payload),
                int(session_id),
                item_id,
            ),
        ).fetchone()
        updated_items.append(_row_to_item(updated))

    conn.commit()
    return {
        "status": "ready",
        "session_id": int(session_id),
        "job_id": safe_job_id,
        "updated_count": len(updated_items),
        "items": updated_items,
    }


def mark_items_profile_running(
    session_id: int,
    *,
    job_id: int,
    reason: str = "session_advance_running",
) -> dict[str, Any]:
    """Mark queued discovery items as running when the worker claims the job."""

    safe_job_id = _int_or_none(job_id)
    if not safe_job_id:
        return {
            "status": "ready",
            "session_id": int(session_id),
            "job_id": safe_job_id,
            "updated_count": 0,
            "items": [],
        }

    conn = get_conn()
    rows = conn.execute(
        """
        SELECT *
        FROM vkpi_kol_search_session_items
        WHERE session_id=? AND job_id=? AND status='queued'
        ORDER BY rank NULLS LAST, id
        """,
        (int(session_id), safe_job_id),
    ).fetchall()
    running_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    updated_items: list[dict[str, Any]] = []
    for row in rows:
        current = _row_to_item(row)
        payload = _dict(current.get("payload")).copy()
        profile_advance_job = _dict(payload.get("profile_advance_job")).copy()
        profile_advance_job.update(
            {
                "status": "running",
                "job_id": safe_job_id,
                "running_at": running_at,
                "reason": _text(reason) or "session_advance_running",
                "viltrox_fit_score_untouched": True,
            }
        )
        payload["profile_advance_job"] = profile_advance_job
        updated = conn.execute(
            """
            UPDATE vkpi_kol_search_session_items
            SET status='running',
                stage='profile',
                payload_json=?::jsonb,
                updated_at=NOW()
            WHERE session_id=? AND id=?
            RETURNING *
            """,
            (
                _json_dumps(payload),
                int(session_id),
                int(current["id"]),
            ),
        ).fetchone()
        updated_items.append(_row_to_item(updated))

    conn.commit()
    return {
        "status": "ready",
        "session_id": int(session_id),
        "job_id": safe_job_id,
        "updated_count": len(updated_items),
        "items": updated_items,
    }


def mark_items_profile_cancelled(
    session_id: int,
    *,
    job_ids: list[int],
    reason: str = "session_advance_cancelled_by_user",
) -> dict[str, Any]:
    """Mark queued items as retryable after their queued session-advance job is blocked."""

    safe_job_ids = sorted({int(job_id) for job_id in job_ids if _int_or_none(job_id)})
    if not safe_job_ids:
        return {
            "status": "ready",
            "session_id": int(session_id),
            "updated_count": 0,
            "items": [],
        }

    placeholders = ", ".join(["?"] * len(safe_job_ids))
    conn = get_conn()
    rows = conn.execute(
        f"""
        SELECT *
        FROM vkpi_kol_search_session_items
        WHERE session_id=?
          AND job_id IN ({placeholders})
          AND status='queued'
        ORDER BY rank NULLS LAST, id
        """,
        (int(session_id), *safe_job_ids),
    ).fetchall()

    cancelled_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    updated_items: list[dict[str, Any]] = []
    for row in rows:
        current = _row_to_item(row)
        payload = _dict(current.get("payload")).copy()
        profile_advance_job = _dict(payload.get("profile_advance_job")).copy()
        profile_advance_job.update(
            {
                "status": "cancelled",
                "cancelled_at": cancelled_at,
                "reason": _text(reason) or "session_advance_cancelled_by_user",
                "viltrox_fit_score_untouched": True,
            }
        )
        payload["profile_advance_job"] = profile_advance_job
        updated = conn.execute(
            """
            UPDATE vkpi_kol_search_session_items
            SET status='skipped',
                stage='identified',
                payload_json=?::jsonb,
                updated_at=NOW()
            WHERE session_id=? AND id=?
            RETURNING *
            """,
            (
                _json_dumps(payload),
                int(session_id),
                int(current["id"]),
            ),
        ).fetchone()
        updated_items.append(_row_to_item(updated))

    conn.commit()
    return {
        "status": "ready",
        "session_id": int(session_id),
        "updated_count": len(updated_items),
        "items": updated_items,
    }


def _update_session(
    conn: Any,
    session_id: int,
    *,
    status: str,
    summary: dict[str, Any],
) -> None:
    conn.execute(
        """
        UPDATE vkpi_kol_search_sessions
        SET status=?,
            result_summary_json=?::jsonb,
            updated_at=NOW()
        WHERE id=?
        """,
        (_normalize_status(status), _json_dumps(summary), int(session_id)),
    )


def _upsert_item(conn: Any, session_id: int, item: dict[str, Any]) -> dict[str, Any]:
    dedupe_key = _text(item.get("dedupe_key")) or f"item:{_text(item.get('item_type'))}:{_text(item.get('source_url'))}:{_text(item.get('kol_pool_id'))}"
    row = conn.execute(
        """
        INSERT INTO vkpi_kol_search_session_items
          (session_id, dedupe_key, item_type, status, stage, rank, score, kol_pool_id, evidence_id, job_id, source_url, payload_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?::jsonb)
        ON CONFLICT (session_id, dedupe_key) DO UPDATE
        SET item_type=EXCLUDED.item_type,
            status=EXCLUDED.status,
            stage=EXCLUDED.stage,
            rank=COALESCE(EXCLUDED.rank, vkpi_kol_search_session_items.rank),
            score=COALESCE(EXCLUDED.score, vkpi_kol_search_session_items.score),
            kol_pool_id=COALESCE(EXCLUDED.kol_pool_id, vkpi_kol_search_session_items.kol_pool_id),
            evidence_id=COALESCE(EXCLUDED.evidence_id, vkpi_kol_search_session_items.evidence_id),
            job_id=COALESCE(EXCLUDED.job_id, vkpi_kol_search_session_items.job_id),
            source_url=COALESCE(NULLIF(EXCLUDED.source_url, ''), vkpi_kol_search_session_items.source_url),
            payload_json=EXCLUDED.payload_json,
            updated_at=NOW()
        RETURNING *
        """,
        (
            int(session_id),
            dedupe_key,
            _text(item.get("item_type")) or "unknown",
            _normalize_status(item.get("status"), item=True),
            _text(item.get("stage")) or "identified",
            _int_or_none(item.get("rank")),
            _float_or_none(item.get("score")),
            _int_or_none(item.get("kol_pool_id")),
            _int_or_none(item.get("evidence_id")),
            _int_or_none(item.get("job_id")),
            _text(item.get("source_url")),
            _json_dumps(item.get("payload") or {}),
        ),
    ).fetchone()
    return _row_to_item(row)


def record_items(
    session_id: int,
    items: list[dict[str, Any]],
    *,
    status: str = "ready",
    summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM vkpi_kol_search_sessions WHERE id=?",
        (int(session_id),),
    ).fetchone()
    if not existing:
        raise LookupError(f"search session not found: {session_id}")
    written = [_upsert_item(conn, int(session_id), item) for item in items]
    _update_session(conn, int(session_id), status=status, summary=summary or {"items_written": len(written)})
    conn.commit()
    return {
        "status": "ready",
        "session_id": int(session_id),
        "items_written": len(written),
        "items": written,
    }


def ensure_session_for_result(
    *,
    session_id: int | None,
    create: bool,
    query_text: str,
    query_type: str,
    source: str,
    input_payload: dict[str, Any] | None = None,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if session_id:
        return get_session(int(session_id))
    if create:
        return create_session(
            query_text=query_text,
            query_type=query_type,
            source=source,
            input_payload=input_payload or {},
            status="planned",
            staff=staff,
        )
    return None

