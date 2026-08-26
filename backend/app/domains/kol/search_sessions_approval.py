"""Session-scoped approval for KOL recall candidates.

Approval is a security boundary: a request may only select pool rows already
materialized as recall candidates on the owned session.  Strict local sessions
additionally require the persisted server qualification proof to have passed —
with exactly one named exception, the "we never crawled this creator" bucket.

That bucket is reported outside the 30-person target, so the "occupying a slot
implies selectable" rule does not force it open; it is opened on purpose all the
same.  A row that is returned to the operator, labelled, and then impossible to
act on is worse than one that was never returned, and every other gate
(account quality / followers / market / language / profile type / platform /
relevance) has already passed for these rows.  Freshness itself is not relaxed:
stale, future-dated, non-video and unauditable rows never reach this branch.
The picks are recorded separately in the approval summary, so the audit shows
which of them the operator knowingly took with activity still unverified.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import re
from typing import Any

from app.db.connection import get_conn
from app.domains.kol.profile_recall_activity_gate import (
    DEFERRED_ACTIVITY_STATUS,
    UNKNOWN_ACTIVITY_REASON,
    deferred_activity_proof,
)
from app.domains.kol.search_sessions_serde import (
    _dict,
    _int_or_none,
    _json_dumps,
    _list,
    _loads,
    _row_to_session,
    _sanitize_session_payload,
    _staff_user_id,
    _text,
)


GetConn = Callable[[], Any]
_APPROVABLE_RECALL_STATUSES = {"matched", "ready", "already_analyzed", "partial"}
_STRICT_LOCAL_SCHEMA = "smart_local_qualified_v2"
_STRICT_GATE_SCHEMA = "smart_local_gate_evidence_v2"
_STRICT_GATE_FIELDS = (
    "account_quality",
    "followers",
    "activity",
    "market",
    "language",
    "profile_type",
    "platform",
    "relevance",
)


def _strict_local_session(session_row: dict[str, Any]) -> bool:
    summary = _loads(session_row.get("result_summary_json"), {})
    qualification = _dict(_dict(summary).get("local_qualification"))
    policy = _dict(qualification.get("policy"))
    return bool(
        _text(qualification.get("schema")) == _STRICT_LOCAL_SCHEMA
        and _int_or_none(policy.get("policy_version")) == 2
        and policy.get("server_owned") is True
    )


def _strict_online_contract(session_row: dict[str, Any]) -> dict[str, Any]:
    summary = _loads(session_row.get("result_summary_json"), {})
    qualification = _dict(_dict(summary).get("online_qualification"))
    if not (
        _text(qualification.get("schema")) == "smart_online_net_new_qualified_v1"
        and _int_or_none(qualification.get("policy_version")) == 1
        and qualification.get("server_owned") is True
        and qualification.get("terminal") is True
        and qualification.get("snapshot_complete") is True
        and _int_or_none(qualification.get("target_count")) == 30
        and _text(qualification.get("snapshot_id"))
        and (_int_or_none(qualification.get("snapshot_revision")) or 0) >= 1
    ):
        return {}
    return qualification


def _strict_online_session(session_row: dict[str, Any]) -> bool:
    return bool(_strict_online_contract(session_row))


def _strict_gate_passed(value: Any) -> bool:
    proof = _dict(value)
    relevance = _dict(proof.get("relevance"))
    return bool(
        _text(proof.get("schema")) == _STRICT_GATE_SCHEMA
        and proof.get("passed") is True
        and all(_dict(proof.get(field)).get("passed") is True for field in _STRICT_GATE_FIELDS)
        and bool(_list(relevance.get("evidence")))
    )


def _strict_gate_deferred(value: Any) -> bool:
    """活跃度未知(从未抓到过视频证据)的候选:除活跃度外每道闸都已通过。

    与 ``_strict_gate_passed`` 同一份证据、同一套字段来源;区别只在活跃度这一
    项是「没抓过」而不是「不合格」。陈旧 / 未来时间 / 非视频 / 无可审计链接的
    行都进不来,天数阈值也不在这里读——它们在服务端出证据时就已经判过。
    """
    proof = _dict(value)
    relevance = _dict(proof.get("relevance"))
    return bool(
        _text(proof.get("schema")) == _STRICT_GATE_SCHEMA
        and deferred_activity_proof(proof)
        and bool(_list(relevance.get("evidence")))
    )


def _candidate_sets(
    conn: Any,
    session_id: int,
    *,
    require_passed_proof: bool,
    online_contract: dict[str, Any],
) -> tuple[set[int], set[int], set[int]]:
    """Return (session IDs, approvable IDs, activity-unknown IDs), pool-backed.

    The third set is a strict subset of the second: it names the approvable rows
    whose only open gate is "we never crawled a video for this creator".
    """
    rows = conn.execute(
        """
        SELECT i.kol_pool_id, i.item_type, i.status, i.payload_json
        FROM vkpi_kol_search_session_items i
        JOIN vkpi_kol_pool p ON p.id=i.kol_pool_id
        WHERE i.session_id=?
          AND i.item_type IN ('recall_candidate', 'online_qualified_candidate')
          AND i.kol_pool_id IS NOT NULL
        """,
        (int(session_id),),
    ).fetchall()
    session_ids: set[int] = set()
    approvable_ids: set[int] = set()
    activity_unknown_ids: set[int] = set()
    for raw in rows:
        row = dict(raw)
        kol_pool_id = _int_or_none(row.get("kol_pool_id"))
        if not kol_pool_id:
            continue
        session_ids.add(kol_pool_id)
        if _text(row.get("status")).lower() not in _APPROVABLE_RECALL_STATUSES:
            continue
        item_type = _text(row.get("item_type") or "recall_candidate")
        payload = _loads(row.get("payload_json"), {})
        payload = _dict(payload)
        proof = _dict(payload.get("qualification_evidence"))
        if item_type == "online_qualified_candidate":
            if not (
                online_contract
                and payload.get("origin_lane") == "online"
                and payload.get("source") == "platform_discovery_strict"
                and payload.get("qualification_status") == "accepted"
                and _strict_gate_passed(proof)
                and _int_or_none(proof.get("kol_pool_id")) == kol_pool_id
                and re.fullmatch(r"[0-9a-f]{64}", _text(payload.get("canonical_fingerprint")))
                and proof.get("canonical_fingerprint") == payload.get("canonical_fingerprint")
                and payload.get("snapshot_id") == online_contract.get("snapshot_id")
                and proof.get("snapshot_id") == online_contract.get("snapshot_id")
                and _int_or_none(payload.get("snapshot_revision")) == _int_or_none(online_contract.get("snapshot_revision"))
                and _int_or_none(proof.get("snapshot_revision")) == _int_or_none(online_contract.get("snapshot_revision"))
                and 1 <= (_int_or_none(payload.get("server_rank")) or 0) <= 30
                and _int_or_none(proof.get("server_rank")) == _int_or_none(payload.get("server_rank"))
                and 1 <= (_int_or_none(payload.get("global_unique_rank")) or 0) <= 60
                and _int_or_none(proof.get("global_unique_rank")) == _int_or_none(payload.get("global_unique_rank"))
            ):
                continue
        elif require_passed_proof and not _strict_gate_passed(proof):
            # 唯一放行的非通过分支:活跃度未知桶。它不占 30 人目标数,但
            # 必须能被勾选——否则界面上就是一批看得见、点不动的死行。
            if not _strict_gate_deferred(proof):
                continue
            activity_unknown_ids.add(kol_pool_id)
        approvable_ids.add(kol_pool_id)
    return session_ids, approvable_ids, activity_unknown_ids


def approve_session(
    session_id: int,
    *,
    kol_pool_ids: list[Any],
    staff: dict[str, Any] | None = None,
    get_conn_fn: GetConn | None = None,
) -> dict[str, Any]:
    actor_id = _staff_user_id(staff)
    if not actor_id:
        raise LookupError(f"search session not found: {session_id}")
    conn = (get_conn_fn or get_conn)()
    row = conn.execute(
        "SELECT * FROM vkpi_kol_search_sessions WHERE id=? AND created_by=?",
        (int(session_id), int(actor_id)),
    ).fetchone()
    if not row:
        raise LookupError(f"search session not found: {session_id}")
    session_row = dict(row)

    requested: list[int] = []
    seen: set[int] = set()
    for raw in _list(kol_pool_ids):
        parsed = _int_or_none(raw)
        if parsed and parsed not in seen:
            seen.add(parsed)
            requested.append(parsed)

    strict_local = _strict_local_session(session_row)
    online_contract = _strict_online_contract(session_row)
    strict_online = bool(online_contract)
    session_ids, approvable_ids, activity_unknown_ids = _candidate_sets(
        conn,
        int(session_id),
        require_passed_proof=strict_local,
        online_contract=online_contract,
    )
    accepted = [kol_pool_id for kol_pool_id in requested if kol_pool_id in approvable_ids]
    accepted_activity_unknown = [
        kol_pool_id for kol_pool_id in accepted if kol_pool_id in activity_unknown_ids
    ]
    skipped_not_in_session = [kol_pool_id for kol_pool_id in requested if kol_pool_id not in session_ids]
    skipped_failed_qualification = [
        kol_pool_id
        for kol_pool_id in requested
        if kol_pool_id in session_ids and kol_pool_id not in approvable_ids
    ]
    skipped = [kol_pool_id for kol_pool_id in requested if kol_pool_id not in approvable_ids]

    summary = _loads(session_row.get("result_summary_json"), {})
    if not isinstance(summary, dict):
        summary = {}
    approved_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    summary["approval"] = {
        "approved_kol_ids": accepted,
        "approved_count": len(accepted),
        "skipped_not_in_session": skipped_not_in_session,
        "skipped_failed_qualification": skipped_failed_qualification,
        # 活跃度未知的选择单独记账:批准记录里看得出哪几个人是在「还没抓到
        # 视频证据」的状态下被操作员主动收下的,不与合格者混为一谈。
        "approved_activity_unknown_ids": accepted_activity_unknown,
        "approved_activity_unknown_count": len(accepted_activity_unknown),
        "approved_activity_unknown_status": DEFERRED_ACTIVITY_STATUS,
        "approved_activity_unknown_reason": UNKNOWN_ACTIVITY_REASON,
        "strict_local_proof_required": strict_local,
        "strict_online_proof_required": strict_online,
        "approved_by": actor_id,
        "approved_at": approved_at,
        "viltrox_fit_score_untouched": True,
    }
    summary = _sanitize_session_payload(summary)
    updated = conn.execute(
        """
        UPDATE vkpi_kol_search_sessions
        SET approved_kol_ids=?::jsonb,
            result_summary_json=?::jsonb,
            updated_at=NOW()
        WHERE id=? AND created_by=?
        RETURNING *
        """,
        (_json_dumps(accepted), _json_dumps(summary), int(session_id), int(actor_id)),
    ).fetchone()
    if not updated:
        raise LookupError(f"search session not found: {session_id}")
    conn.commit()
    session = _row_to_session(updated)
    session.update(
        {
            "approved_count": len(accepted),
            "skipped_not_in_pool": skipped,
            "skipped_not_in_session": skipped_not_in_session,
            "skipped_failed_qualification": skipped_failed_qualification,
            "approved_activity_unknown_ids": accepted_activity_unknown,
            "approved_activity_unknown_count": len(accepted_activity_unknown),
        }
    )
    return session
