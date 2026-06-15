"""Trackable / recoverable state for the synchronous KOL lookup path.

The lookup endpoint runs ``scan_account``/``analyze_account`` inline inside the
request instead of going through the async worker queue, so nothing landed in
``job_execution_ledger`` (the table the 任务进度 sidebar reads) and the result
vanished on page navigation.

This module wraps that inline run with two honest side records:

1. A ``vkpi_kol_search_session`` (migration 103) — durable result/terminal state
   so navigation can re-fetch what happened (query_type ``text_recall`` |
   ``url_profile``, source ``kol_lookup``).
2. A ``job_execution_ledger`` row (migration 009) with ``job_type='kol_lookup'``
   so ``TaskProgressBoard`` / ``/task-queue/compact`` surfaces the live stage
   (search → thinking → summarizing) and any failure reason.

It NEVER touches scoring (``viltrox_fit_score``) and never fabricates data: stage
transitions and terminal status mirror what actually ran. All tracking writes are
best-effort — a tracking failure must not break the lookup itself.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.access import scope
from app.domains.kol import search_sessions

logger = get_logger(__name__)

LEDGER_JOB_TYPE = "kol_lookup"
SESSION_SOURCE = "kol_lookup"

# stage 名沿用 queue_view STAGE_LABELS：search/thinking/summarizing。
STAGE_SEARCH = "search"
STAGE_THINKING = "thinking"
STAGE_SUMMARIZING = "summarizing"


def _utcnow() -> str:
    from app.services.jobs.queue_common import utcnow

    return utcnow()


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _looks_like_url(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return text.startswith("http://") or text.startswith("https://") or "/" in text and "." in text


def query_type_for(body: dict[str, Any]) -> str:
    """Derive the session query_type the same way the lookup input is shaped.

    A profile/handle URL pasted into the lookup box → ``url_profile``; a plain
    platform+handle text recall → ``text_recall``. Defaults to ``text_recall``.
    """
    raw_url = body.get("url") or body.get("handle_or_url") or ""
    if _looks_like_url(raw_url):
        return "url_profile"
    return "text_recall"


def query_text_for(body: dict[str, Any], result: dict[str, Any] | None = None) -> str:
    query = (result or {}).get("query") if isinstance(result, dict) else None
    if isinstance(query, dict):
        platform = str(query.get("platform") or "").strip()
        handle = str(query.get("handle") or "").strip()
        if platform and handle:
            return f"{platform}/{handle}"
        if handle:
            return handle
    parts = [
        str(body.get("platform") or "").strip(),
        str(body.get("handle") or body.get("handle_or_url") or body.get("url") or "").strip(),
    ]
    text = "/".join(part for part in parts if part)
    return text[:180]


class LookupTracker:
    """Best-effort session + ledger recorder for one synchronous lookup run."""

    def __init__(self, *, body: dict[str, Any], staff: dict[str, Any] | None) -> None:
        self._body = dict(body or {})
        self._staff = staff
        self.session_id: int | None = None
        self.task_id: str = f"kol_lookup_{uuid.uuid4().hex[:24]}"
        self._stage: str = STAGE_SEARCH
        # ledger.user_id 取登录用户(与 enqueue._created_user_id 同口径),无则退 staff_id。
        self._user_id = _int((staff or {}).get("user_id")) or _int(scope.actor_staff_id(staff))
        self._ledger_open = False

    # --- session ---------------------------------------------------------
    def open(self) -> int | None:
        query_type = query_type_for(self._body)
        try:
            session = search_sessions.create_session(
                query_text=query_text_for(self._body),
                query_type=query_type,
                source=SESSION_SOURCE,
                input_payload={
                    "platform": self._body.get("platform"),
                    "handle": self._body.get("handle") or self._body.get("handle_or_url") or self._body.get("url"),
                    "scan_account": bool(self._body.get("scan_account") or self._body.get("scan_if_missing")),
                    "product_sku": self._body.get("product_sku"),
                    "task_id": self.task_id,
                },
                status="running",
                staff=self._staff,
            )
            self.session_id = _int(session.get("id")) or None
        except Exception as exc:  # tracking must not break lookup
            logger.warning("kol_lookup session create failed: %s", exc)
            self.session_id = None
        self._open_ledger(query_type)
        return self.session_id

    def set_query_text(self, result: dict[str, Any]) -> None:
        """Refresh durable query_text once the resolved kol/query is known."""
        if not self.session_id:
            return
        text = query_text_for(self._body, result)
        if not text:
            return
        try:
            conn = get_conn()
            conn.execute(
                "UPDATE vkpi_kol_search_sessions SET query_text=?, updated_at=NOW() WHERE id=?",
                (text, int(self.session_id)),
            )
            conn.commit()
        except Exception as exc:
            logger.warning("kol_lookup query_text update failed: %s", exc)

    # --- stage transitions ----------------------------------------------
    def stage(self, stage: str) -> None:
        self._stage = stage
        self._update_ledger(status="processing", stage=stage)

    # --- terminal --------------------------------------------------------
    def finish(
        self,
        *,
        status: str,
        result: dict[str, Any],
        reason: str = "",
        summary: dict[str, Any] | None = None,
    ) -> None:
        """Persist terminal session state + close the ledger row.

        ``status`` is one of ``ready`` | ``failed`` | ``partial`` (session
        vocabulary). The ledger maps these to ``done`` | ``failed`` |
        ``partial_done``.
        """
        session_summary = dict(summary or {})
        session_summary.setdefault("kind", "kol_lookup")
        session_summary.setdefault("task_id", self.task_id)
        if reason:
            session_summary["reason"] = reason
        if self.session_id:
            try:
                search_sessions.update_session_result_summary(
                    int(self.session_id),
                    status=status,
                    summary_patch=session_summary,
                )
            except Exception as exc:
                logger.warning("kol_lookup session finalize failed: %s", exc)
        ledger_status = {
            "ready": "done",
            "partial": "partial_done",
            "failed": "failed",
        }.get(status, "done")
        self._close_ledger(
            status=ledger_status,
            reason=reason,
            summary=session_summary,
            result=result if isinstance(result, dict) else {},
        )

    # --- ledger internals ------------------------------------------------
    def _open_ledger(self, query_type: str) -> None:
        payload = {
            "source": "vkpi",
            "search_session_id": str(self.session_id) if self.session_id else "",
            "search_session_stage": self._stage,
            "query_type": query_type,
            "platform": self._body.get("platform"),
            "handle": self._body.get("handle") or self._body.get("handle_or_url") or self._body.get("url"),
            "user_id": self._user_id,
            "staff_id": _int(scope.actor_staff_id(self._staff)),
            "created_by_user_id": self._user_id,
        }
        now = _utcnow()
        try:
            conn = get_conn()
            conn.execute(
                """
                INSERT INTO job_execution_ledger
                  (task_id, job_type, submission_id, user_id, status, payload_json,
                   retry_count, stage, summary, created_at, updated_at, started_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.task_id,
                    LEDGER_JOB_TYPE,
                    0,
                    self._user_id,
                    "processing",
                    _json(payload),
                    0,
                    self._stage,
                    query_text_for(self._body),
                    now,
                    now,
                    now,
                ),
            )
            conn.commit()
            self._ledger_open = True
        except Exception as exc:
            logger.warning("kol_lookup ledger open failed: %s", exc)
            self._ledger_open = False

    def _update_ledger(self, *, status: str, stage: str) -> None:
        if not self._ledger_open:
            return
        try:
            conn = get_conn()
            conn.execute(
                """
                UPDATE job_execution_ledger
                SET status=?, stage=?, updated_at=?
                WHERE task_id=?
                """,
                (status, stage, _utcnow(), self.task_id),
            )
            conn.commit()
        except Exception as exc:
            logger.warning("kol_lookup ledger stage update failed: %s", exc)

    def _close_ledger(
        self,
        *,
        status: str,
        reason: str,
        summary: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        if not self._ledger_open:
            return
        now = _utcnow()
        final_stage = STAGE_SUMMARIZING if status in {"done", "partial_done"} else self._stage
        # 只回写本 lookup 真产物,绝不含 viltrox_fit_score。
        result_payload = {
            "session_id": self.session_id,
            "scan_result": result.get("scan_result"),
            "analysis_result": result.get("analysis_result"),
            "kol_id": (result.get("kol") or {}).get("id") if isinstance(result.get("kol"), dict) else None,
        }
        try:
            conn = get_conn()
            conn.execute(
                """
                UPDATE job_execution_ledger
                SET status=?, stage=?, summary=?, error_message=?, result_json=?,
                    updated_at=?, finished_at=?
                WHERE task_id=?
                """,
                (
                    status,
                    final_stage,
                    str(summary.get("kind") or "kol_lookup"),
                    str(reason or ""),
                    _json(result_payload),
                    now,
                    now,
                    self.task_id,
                ),
            )
            conn.commit()
        except Exception as exc:
            logger.warning("kol_lookup ledger close failed: %s", exc)
