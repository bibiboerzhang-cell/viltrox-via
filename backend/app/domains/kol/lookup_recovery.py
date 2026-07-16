"""Navigation-recovery read for a KOL lookup search session.

The lookup endpoint returns ``search_session_id`` immediately. After the user
navigates away and back, the client re-fetches the durable state here: the
``vkpi_kol_search_session`` terminal status/reason/summary plus the matching
``kol_lookup`` ledger row's live stage. Read-only; never mutates anything and
never touches scoring.
"""
from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.kol import search_sessions
from app.domains.kol.lookup_tracking import LEDGER_JOB_TYPE

logger = get_logger(__name__)


def _loads(raw: Any, default: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    try:
        parsed = json.loads(raw or "")
    except Exception:
        return default
    return parsed if parsed is not None else default


def _ledger_for_session(session: dict[str, Any]) -> dict[str, Any]:
    """Find the kol_lookup ledger row recorded for this session, if any.

    The tracker stores ``task_id`` in both the session input_payload and the
    result_summary; we resolve the ledger by that task_id so the recovery view
    can surface the live stage/error the 任务进度 board shows.
    """
    input_payload = session.get("input_payload") if isinstance(session.get("input_payload"), dict) else {}
    result_summary = session.get("result_summary") if isinstance(session.get("result_summary"), dict) else {}
    task_id = str(input_payload.get("task_id") or result_summary.get("task_id") or "").strip()
    if not task_id:
        return {}
    try:
        row = get_conn().execute(
            """
            SELECT task_id, job_type, status, stage, summary, error_message, result_json,
                   created_at, updated_at, started_at, finished_at
            FROM job_execution_ledger
            WHERE task_id=? AND job_type=?
            """,
            (task_id, LEDGER_JOB_TYPE),
        ).fetchone()
    except Exception as exc:
        logger.warning("kol_lookup recovery ledger read failed: %s", exc)
        return {}
    if not row:
        return {}
    data = dict(row)
    return {
        "task_id": data.get("task_id"),
        "status": data.get("status"),
        "stage": data.get("stage"),
        "summary": data.get("summary"),
        "error": data.get("error_message") or "",
        "result": _loads(data.get("result_json"), {}),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "started_at": data.get("started_at"),
        "finished_at": data.get("finished_at"),
    }


def recover_session(session_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the durable lookup state for one session.

    Raises ``LookupError`` if the session does not exist. ``staff`` is accepted
    for parity with the route guard; visibility is already enforced by the tab
    permission and the session is the actor's own lookup record.
    """
    session = search_sessions.get_session(int(session_id))
    result_summary = session.get("result_summary") if isinstance(session.get("result_summary"), dict) else {}
    payload = {
        "status": "ready",
        "session_id": int(session_id),
        "session": session,
        "terminal_status": session.get("status"),
        "reason": result_summary.get("reason") or "",
        "summary": result_summary,
        "ledger": _ledger_for_session(session),
    }
    from app.domains.kol.contact_access import authorize_plaintext_contacts, mask_contact_payload

    reveal = authorize_plaintext_contacts(
        staff,
        resource_type="kol_lookup",
        resource_id=int(session_id),
        page_path=f"/kols/lookup/sessions/{int(session_id)}",
        metadata={"surface": "lookup_recovery"},
    )
    return payload if reveal else mask_contact_payload(payload)
