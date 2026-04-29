"""
services/audit_log.py — admin action audit logging

Used by all admin routers via record_admin_action(). Writes to admin_audit_log
table. Safe for any action — best-effort (doesn't throw on DB errors).
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import Request

from app.core.logging import get_logger
from app.db.connection import get_conn

logger = get_logger(__name__)


def record_admin_action(
    *,
    actor: Any,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    detail: dict | None = None,
    request: Request | None = None,
) -> None:
    """
    Log an admin action. Best-effort — never raises.
    `actor` is expected to be the value returned by require_admin. In the
    current viltrox-2.0 backend this is a dict, not an object.
    """
    try:
        conn = get_conn()
        if isinstance(actor, dict):
            actor_id = actor.get("id")
            actor_name = (
                actor.get("name")
                or actor.get("email")
                or actor.get("creator_code")
                or actor.get("handle")
                or f"user:{actor_id}"
            )
        else:
            actor_id = getattr(actor, "id", None)
            actor_name = (
                getattr(actor, "name", None)
                or getattr(actor, "email", None)
                or getattr(actor, "creator_code", None)
                or getattr(actor, "handle", None)
                or f"user:{actor_id}"
            )

        ip_address = None
        user_agent = None
        if request is not None:
            try:
                ip_address = (
                    request.headers.get("x-forwarded-for", "").split(",")[0].strip()
                    or (request.client.host if request.client else None)
                )
                user_agent = request.headers.get("user-agent", "")[:500]
            except Exception:
                logger.debug("admin_audit.request_context_failed", exc_info=True)

        conn.execute(
            """INSERT INTO admin_audit_log
                (actor_id, actor_name, action, target_type, target_id,
                 detail_json, ip_address, user_agent)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                actor_id, actor_name, action,
                target_type, target_id,
                json.dumps(detail or {}, default=str),
                ip_address, user_agent,
            ),
        )
        conn.commit()
    except Exception as e:
        # Audit logs failing should never block the admin action itself
        logger.warning("audit_log write failed (action=%s): %s", action, e)
