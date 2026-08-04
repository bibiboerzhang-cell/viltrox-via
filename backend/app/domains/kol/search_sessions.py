"""Unified KOL Pool search-session state.

This module records smart URL/profile/text recall orchestration state. It only
writes the session tables introduced by migration 103 and must not update
vkpi_kol_pool scoring fields.

Schema constants, serde helpers, history operations, item persistence, and
attach-result builders live in focused ``search_sessions_*`` modules. This
module remains the compatibility facade for existing imports and monkeypatches.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.kol.pool_common import _mask_email
from app.domains.kol.contact_access import mask_contact_payload

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
from app.domains.kol.search_sessions_schema import (
    PENDING_ENRICHMENT_STATUSES as _PENDING_ENRICHMENT_STATUSES,
    REACH_GATED_ITEM_TYPES as _REACH_GATED_ITEM_TYPES,
    TERMINAL_SESSION_STATUSES as _TERMINAL_SESSION_STATUSES,
)
from app.domains.kol.search_progress_contract import (
    observe_worker_health,
    project_search_progress,
)
from app.domains.kol.search_sessions_history import (
    archive_history_session as _archive_history_session,
    archive_history_sessions as _archive_history_sessions,
    list_history as _list_history,
    restore_history_session as _restore_history_session,
)
from app.domains.kol.search_sessions_items import (
    _update_session as _update_session_impl,
    _upsert_item as _upsert_item_impl,
    get_session_item as _get_session_item,
    mark_items_profile_cancelled as _mark_items_profile_cancelled,
    mark_items_profile_queued as _mark_items_profile_queued,
    mark_items_profile_running as _mark_items_profile_running,
    record_items as _record_items,
    update_item_profile_execution as _update_item_profile_execution,
)

# Re-export attach-result builders (behavior-preserving move).
from app.domains.kol.search_sessions_attach import (
    _link_job_payloads,
    _session_status_from_url_result,
    _url_result_item,
    attach_new_discovery_result as _attach_new_discovery_result,
    attach_recall_result as _attach_recall_result,
    attach_url_result as _attach_url_result,
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


def _enrichment_preview_status(item: dict[str, Any], key: str, *, ready: bool) -> str:
    if ready:
        return "ready"
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    profile_execute = payload.get("profile_execute") if isinstance(payload.get("profile_execute"), dict) else {}
    explicit = profile_execute.get(key) if isinstance(profile_execute.get(key), dict) else {}
    explicit_status = _text(explicit.get("status")).lower()
    if explicit_status in _PENDING_ENRICHMENT_STATUSES:
        return "pending"
    if explicit_status in {"ok", "ready", "done"}:
        return "ready"
    if explicit_status in {"no_contacts", "no_commenters", "no_posts", "no_raw", "not_found"}:
        return "empty"
    if explicit_status in {"error", "failed", "partial"}:
        return "partial"
    job = payload.get("profile_advance_job") if isinstance(payload.get("profile_advance_job"), dict) else {}
    item_status = _text(item.get("status")).lower()
    job_status = _text(job.get("status")).lower()
    if item_status in {"queued", "running"} or job_status in _PENDING_ENRICHMENT_STATUSES:
        return "pending"
    return "missing"


def _refresh_enrichment_queue_states(conn: Any, items: list[dict[str, Any]]) -> None:
    job_ids: set[int] = set()
    for item in items:
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        profile_execute = payload.get("profile_execute") if isinstance(payload.get("profile_execute"), dict) else {}
        for key in ("contact_enrichment", "audience_enrichment"):
            enrichment = profile_execute.get(key) if isinstance(profile_execute.get(key), dict) else {}
            job_id = _int_or_none(enrichment.get("job_id"))
            if job_id:
                job_ids.add(job_id)
    if not job_ids:
        return
    placeholders = ",".join(["?"] * len(job_ids))
    try:
        rows = conn.execute(
            f"SELECT id, status FROM apify_jobs WHERE id IN ({placeholders})",
            tuple(sorted(job_ids)),
        ).fetchall()
    except Exception:
        logger.warning("search_sessions.enrichment_job_lookup_failed", exc_info=True)
        return
    job_statuses = {int(dict(row)["id"]): _text(dict(row).get("status")).lower() for row in rows}
    for item in items:
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        profile_execute = payload.get("profile_execute") if isinstance(payload.get("profile_execute"), dict) else {}
        for key in ("contact_enrichment", "audience_enrichment"):
            enrichment = profile_execute.get(key) if isinstance(profile_execute.get(key), dict) else None
            if enrichment is None:
                continue
            queue_status = job_statuses.get(_int_or_none(enrichment.get("job_id")) or 0)
            if not queue_status:
                continue
            enrichment["queue_status"] = queue_status
            if _text(enrichment.get("status")).lower() in _PENDING_ENRICHMENT_STATUSES:
                if queue_status == "done":
                    enrichment["status"] = "empty"
                elif queue_status in {"failed", "blocked", "cancelled"}:
                    enrichment["status"] = "partial"


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


def _attach_progress_contract(
    session: dict[str, Any],
    items: list[dict[str, Any]],
    *,
    worker_health: dict[str, Any],
) -> dict[str, Any]:
    """Attach the same live, read-only progress projection on every session API."""

    contract = project_search_progress(session, items, worker_health=worker_health)
    session["progress_contract"] = contract
    session["worker_health"] = dict(worker_health)
    summary = _dict(session.get("result_summary")).copy()
    summary["progress_contract"] = contract
    session["result_summary"] = summary
    return session


def _session_items_by_id(conn: Any, session_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {int(session_id): [] for session_id in session_ids}
    if not session_ids:
        return grouped
    placeholders = ",".join(["?"] * len(session_ids))
    rows = conn.execute(
        f"""
        SELECT *
        FROM vkpi_kol_search_session_items
        WHERE session_id IN ({placeholders})
        ORDER BY session_id, rank NULLS LAST, id
        """,
        tuple(session_ids),
    ).fetchall()
    for row in rows:
        item = _row_to_item(row)
        grouped.setdefault(int(item.get("session_id") or 0), []).append(item)
    return grouped


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
    session = _row_to_session(row)
    return _attach_progress_contract(
        session,
        [],
        worker_health=observe_worker_health(conn),
    )


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
    sessions = [_row_to_session(row) for row in rows]
    grouped = _session_items_by_id(
        conn,
        [int(session["id"]) for session in sessions if _int_or_none(session.get("id"))],
    )
    worker_health = observe_worker_health(conn)
    for session in sessions:
        _attach_progress_contract(
            session,
            grouped.get(int(session.get("id") or 0), []),
            worker_health=worker_health,
        )
    return {
        "status": "ready",
        "count": len(sessions),
        "items": sessions,
        "worker_health": worker_health,
    }


def list_history(
    *,
    limit: int = 20,
    status: str = "",
    query_type: str = "",
    item_limit: int = 5,
    staff: dict[str, Any] | None = None,
    scope_to_staff: bool = True,
    archived: bool = False,
) -> dict[str, Any]:
    """Return recent search sessions with compact item previews for history UI.

    每个人的记录不能串:默认按 created_by=当前登录人作用域过滤(scope_to_staff),
    不同员工互不串记录。actor 取不到时返回空,绝不回退成全员记录。
    """
    return _list_history(
        limit=limit,
        status=status,
        query_type=query_type,
        item_limit=item_limit,
        staff=staff,
        scope_to_staff=scope_to_staff,
        archived=archived,
        get_conn_fn=get_conn,
        apply_reach_display_gate_fn=_apply_reach_display_gate,
        mask_contact_payload_fn=mask_contact_payload,
    )


def get_session(
    session_id: int,
    *,
    staff: dict[str, Any] | None = None,
    scope_to_staff: bool = False,
) -> dict[str, Any]:
    conn = get_conn()
    params: list[Any] = [int(session_id)]
    where = ["id=?"]
    if scope_to_staff:
        actor_id = _staff_user_id(staff)
        if not actor_id:
            raise LookupError(f"search session not found: {session_id}")
        where.append("created_by=?")
        params.append(actor_id)
    row = conn.execute(
        f"SELECT * FROM vkpi_kol_search_sessions WHERE {' AND '.join(where)}",
        tuple(params),
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
    _refresh_enrichment_queue_states(conn, items)
    # 名字全局一致(2026-07-03 用户点名):部分物化路径的 item payload 不带 display_name,
    # 前端只好显示 handle(YT 时是一串频道 ID)。读端统一回填:凡带 kol_pool_id 且 payload
    # 缺名字的,批量查池表补 display_name —— 一处修好,校验中/已有库/新发现全部受益。
    _profile_ids = sorted({
        int(it["kol_pool_id"]) for it in items
        if it.get("kol_pool_id") and isinstance(it.get("payload"), dict)
    })
    if _profile_ids:
        try:
            _ph = ",".join(["?"] * len(_profile_ids))
            _profile_rows = conn.execute(
                f"""
                SELECT id, display_name, email, contact_channels, other_contacts_json,
                       audience_estimated_json
                FROM vkpi_kol_pool
                WHERE id IN ({_ph})
                """,
                tuple(_profile_ids),
            ).fetchall()
            _profiles = {int(dict(row)["id"]): dict(row) for row in _profile_rows}
            for it in items:
                _kid = it.get("kol_pool_id")
                if _kid and isinstance(it.get("payload"), dict):
                    _profile = _profiles.get(int(_kid), {})
                    _nm = str(_profile.get("display_name") or "").strip()
                    if _nm and not str(it["payload"].get("display_name") or "").strip():
                        it["payload"]["display_name"] = _nm
                    _email = str(_profile.get("email") or "").strip()
                    _channels = _loads(_profile.get("contact_channels"), {})
                    _other_contacts = _loads(_profile.get("other_contacts_json"), [])
                    _contact_count = len(_other_contacts) if isinstance(_other_contacts, list) else 0
                    if isinstance(_channels, (dict, list)):
                        _contact_count += len(_channels)
                    _contact_ready = bool(_email or _contact_count)
                    it["payload"]["contact_preview"] = {
                        "status": _enrichment_preview_status(
                            it,
                            "contact_enrichment",
                            ready=_contact_ready,
                        ),
                        "email": _mask_email(_email),
                        "channel_count": _contact_count,
                        "contact_masked": True,
                        "async": not _contact_ready,
                    }
                    _audience = _loads(_profile.get("audience_estimated_json"), {})
                    _audience_ready = isinstance(_audience, dict) and bool(_audience)
                    it["payload"]["audience_preview"] = {
                        "status": _enrichment_preview_status(
                            it,
                            "audience_enrichment",
                            ready=_audience_ready,
                        ),
                        "method": _text(_audience.get("method")) if isinstance(_audience, dict) else "",
                        "confidence": _audience.get("confidence") if isinstance(_audience, dict) else None,
                        "sample_size": _audience.get("sample_size") if isinstance(_audience, dict) else None,
                        "async": not _audience_ready,
                    }
        except Exception:
            logger.warning("search_sessions.profile_preview_backfill_failed", exc_info=True)
    for item in items:
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else None
        if payload is None:
            continue
        if not isinstance(payload.get("contact_preview"), dict):
            payload["contact_preview"] = {
                "status": _enrichment_preview_status(item, "contact_enrichment", ready=False),
                "email": "",
                "channel_count": 0,
                "contact_masked": True,
                "async": True,
            }
        if not isinstance(payload.get("audience_preview"), dict):
            payload["audience_preview"] = {
                "status": _enrichment_preview_status(item, "audience_enrichment", ready=False),
                "method": "",
                "confidence": None,
                "sample_size": None,
                "async": True,
            }
    _attach_progress_contract(
        session,
        items,
        worker_health=observe_worker_health(conn),
    )
    # 触达展示闸(第二道闸落点①):会话项按 pool 现值实时重判——补全回填 followers 后,
    # 低触达行(如 kol_pool 12297,2 粉)从「全网新发现/库内已有」消失;followers 未知折叠为
    # 「分析中 ×N」。counts/count 按可见项重算,隐藏计数走 reach_floor_display 诚实透出。
    items, reach_counts = _apply_reach_display_gate(conn, items)
    for item in items:
        if isinstance(item.get("payload"), dict):
            item["payload"] = mask_contact_payload(item["payload"])
    session["items"] = items
    session["count"] = len(items)
    session["counts"] = _item_counts(items)
    session["reach_floor_display"] = reach_counts
    return session


def archive_history_session(
    session_id: int,
    *,
    staff: dict[str, Any] | None,
    reason: str = "user_removed",
) -> dict[str, Any]:
    """Soft-archive one terminal search session owned by the current staff member."""
    return _archive_history_session(
        session_id,
        staff=staff,
        reason=reason,
        get_conn_fn=get_conn,
    )


def restore_history_session(
    session_id: int,
    *,
    staff: dict[str, Any] | None,
) -> dict[str, Any]:
    """Restore one archived search session to the current staff member's history."""
    return _restore_history_session(
        session_id,
        staff=staff,
        get_conn_fn=get_conn,
    )


def archive_history_sessions(*, staff: dict[str, Any] | None) -> dict[str, Any]:
    """Archive all terminal history rows owned by staff, preserving active work."""
    return _archive_history_sessions(staff=staff, get_conn_fn=get_conn)


def get_session_item(session_id: int, item_id: int) -> dict[str, Any]:
    return _get_session_item(session_id, item_id, get_conn_fn=get_conn)


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


def _attached_result_count(result: dict[str, Any]) -> int:
    items = result.get("items")
    if isinstance(items, list) and items:
        return len(items)
    buckets = result.get("buckets") if isinstance(result.get("buckets"), dict) else {}
    bucket_count = sum(len(value) for value in buckets.values() if isinstance(value, list))
    return bucket_count if bucket_count else len(items or [])


def _persist_attached_status(
    session_id: int,
    recorded: dict[str, Any],
    *,
    status: str,
    result_state: str,
) -> dict[str, Any]:
    normalized = _normalize_status(status)
    if _text(recorded.get("status")).lower() == normalized:
        return recorded
    updated = update_session_result_summary(
        int(session_id),
        status=normalized,
        summary_patch={"result_state": result_state},
    )
    recorded["status"] = updated.get("status") or normalized
    if isinstance(recorded.get("result_summary"), dict):
        recorded["result_summary"]["result_state"] = result_state
    return recorded


def attach_recall_result(session_id: int, result: dict[str, Any]) -> dict[str, Any]:
    recorded = _attach_recall_result(int(session_id), result)
    upstream_status = _text(result.get("status")).lower()
    item_count = len(recorded.get("items") or [])
    if "items" not in recorded:
        item_count = _attached_result_count(result)
    if upstream_status == "failed":
        desired, state = "failed", "failed"
    elif upstream_status == "partial":
        desired, state = "partial", "partial"
    elif item_count <= 0:
        desired, state = "partial", "empty"
    else:
        desired, state = "ready", "ready"
    if bool(result.get("_session_pipeline_running")):
        progress = _dict(result.get("_session_progress"))
        updated = update_session_result_summary(
            int(session_id),
            status="running",
            summary_patch={"result_state": state, "phase": "base", "progress": progress},
        )
        recorded["status"] = updated.get("status") or "running"
        return recorded
    return _persist_attached_status(int(session_id), recorded, status=desired, result_state=state)


def attach_new_discovery_result(session_id: int, result: dict[str, Any]) -> dict[str, Any]:
    recorded = _attach_new_discovery_result(int(session_id), result)
    upstream_status = _text(result.get("status")).lower()
    recorded_count = len(recorded.get("items") or [])
    if upstream_status == "failed":
        desired, state = ("partial", "partial") if recorded_count else ("failed", "failed")
    elif upstream_status == "partial":
        desired, state = "partial", "partial"
    elif upstream_status == "empty" and recorded_count <= 0:
        desired, state = "partial", "empty"
    else:
        desired, state = _text(recorded.get("status")) or "ready", upstream_status or "ready"
    if bool(result.get("_session_pipeline_running")):
        progress = _dict(result.get("_session_progress"))
        updated = update_session_result_summary(
            int(session_id),
            status="running",
            summary_patch={"result_state": state, "phase": "base", "progress": progress},
        )
        recorded["status"] = updated.get("status") or "running"
        return recorded
    return _persist_attached_status(int(session_id), recorded, status=desired, result_state=state)


def attach_url_result(session_id: int, result: dict[str, Any]) -> dict[str, Any]:
    recorded = _attach_url_result(int(session_id), result)
    desired = _session_status_from_url_result(result)
    state = desired
    if result.get("url_type") not in {"profile", "video"}:
        # 不支持的平台链接(如 bilibili):诚实终态,不再挂「部分完成」假装还在跑。
        # result_state=unsupported 供前端历史卡显示「不支持」而非笼统的失败。
        desired, state = "failed", "unsupported"
    return _persist_attached_status(int(session_id), recorded, status=desired, result_state=state)


def update_item_profile_execution(
    session_id: int,
    item_id: int,
    *,
    profile_result: dict[str, Any],
) -> dict[str, Any]:
    """Persist profile-crawl execution result for a discovery item."""
    return _update_item_profile_execution(
        session_id,
        item_id,
        profile_result=profile_result,
        get_conn_fn=get_conn,
        update_session_fn=_update_session,
    )


def mark_items_profile_queued(
    session_id: int,
    *,
    item_ids: list[int],
    job_id: int,
    reason: str = "session_advance_queued",
    plan_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Mark selected discovery items as queued for one session-advance job."""
    return _mark_items_profile_queued(
        session_id,
        item_ids=item_ids,
        job_id=job_id,
        reason=reason,
        plan_items=plan_items,
        get_conn_fn=get_conn,
    )


def mark_items_profile_running(
    session_id: int,
    *,
    job_id: int,
    reason: str = "session_advance_running",
) -> dict[str, Any]:
    """Mark queued discovery items as running when the worker claims the job."""
    return _mark_items_profile_running(
        session_id,
        job_id=job_id,
        reason=reason,
        get_conn_fn=get_conn,
    )


def mark_items_profile_cancelled(
    session_id: int,
    *,
    job_ids: list[int],
    reason: str = "session_advance_cancelled_by_user",
) -> dict[str, Any]:
    """Mark queued items as retryable after their queued session-advance job is blocked."""
    return _mark_items_profile_cancelled(
        session_id,
        job_ids=job_ids,
        reason=reason,
        get_conn_fn=get_conn,
    )


def _update_session(
    conn: Any,
    session_id: int,
    *,
    status: str,
    summary: dict[str, Any],
) -> None:
    _update_session_impl(conn, session_id, status=status, summary=summary)


def _upsert_item(conn: Any, session_id: int, item: dict[str, Any]) -> dict[str, Any]:
    return _upsert_item_impl(conn, session_id, item)


def record_items(
    session_id: int,
    items: list[dict[str, Any]],
    *,
    status: str = "ready",
    summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    recorded = _record_items(
        session_id,
        items,
        status=status,
        summary=summary,
        get_conn_fn=get_conn,
        upsert_item_fn=_upsert_item,
        update_session_fn=_update_session,
    )
    session_view = {
        "id": int(session_id),
        "status": _normalize_status(status),
        "result_summary": _dict(summary),
    }
    worker_health = observe_worker_health(get_conn())
    recorded["progress_contract"] = project_search_progress(
        session_view,
        recorded.get("items") or [],
        worker_health=worker_health,
    )
    recorded["worker_health"] = worker_health
    return recorded


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
