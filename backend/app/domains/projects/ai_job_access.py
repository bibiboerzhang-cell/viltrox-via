"""Durable authorization and identity fences for project AI jobs.

Request-time authorization is not durable: a queued actor may be suspended or
lose project/event access, and a queued file or prompt can be replaced before a
worker runs.  This module seals the execution identity at enqueue time and
revalidates live access immediately before provider-capable work.

Server-scheduled project retrospectives use an opaque in-process capability.
Persisted server claims are signed; a JSON body cannot opt itself into that
mode.  Legacy user jobs without a valid fence fail closed in the worker.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import IS_PRODUCTION, JWT_SECRET
from app.core.permissions import check_tab_permission
from app.core.security import user_status_allows_auth
from app.db.connection import get_conn
from app.domains.access import scope


FENCE_KEY = "project_ai_access_fence"
FENCE_VERSION = 1
FILE_IDENTITY_KEY = "source_file_identity"

INVOICE_EXTRACT = "invoice_extract"
CONTRACT_POLISH = "contract_polish"
PROJECT_RETROSPECTIVE = "project_retrospective"
SUPPORTED_ACTIONS = frozenset({INVOICE_EXTRACT, CONTRACT_POLISH, PROJECT_RETROSPECTIVE})


class ProjectAiAccessError(RuntimeError):
    """Stable terminal failure for a durable project-AI authorization check."""

    def __init__(self, code: str, status_code: int = 403):
        super().__init__(code)
        self.code = str(code)
        self.status_code = int(status_code)


@dataclass(frozen=True)
class ServerProjectAiCapability:
    """Opaque process-issued capability; caller-provided dictionaries are invalid."""

    action: str
    project_id: int
    signature: str


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _signing_secret() -> bytes:
    explicit = _text(os.environ.get("VKPI_PROJECT_AI_JOB_FENCE_SECRET"))
    if explicit:
        return explicit.encode("utf-8")
    if IS_PRODUCTION:
        return _text(JWT_SECRET).encode("utf-8")
    return b"vkpi-local-project-ai-job-fence-v1-development-only"


def _signature(value: Any) -> str:
    return hmac.new(_signing_secret(), _canonical(value).encode("utf-8"), hashlib.sha256).hexdigest()


def _signed(claim: dict[str, Any]) -> dict[str, Any]:
    unsigned = {key: value for key, value in claim.items() if key != "signature"}
    return {**unsigned, "signature": _signature(unsigned)}


def _valid_signature(claim: dict[str, Any]) -> bool:
    supplied = _text(claim.get("signature"))
    unsigned = {key: value for key, value in claim.items() if key != "signature"}
    return bool(supplied) and hmac.compare_digest(supplied, _signature(unsigned))


def capture_file_identity(path: Path, *, root: Path) -> dict[str, Any]:
    """Return a stable, non-absolute identity and reject files changing mid-hash."""

    resolved_root = root.resolve()
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(resolved_root).as_posix()
    except ValueError as exc:
        raise ProjectAiAccessError("project_ai_file_outside_evidence_root", 409) from exc
    try:
        before = resolved.stat()
    except OSError as exc:
        raise ProjectAiAccessError("project_ai_file_missing", 409) from exc
    if before.st_size <= 0 or not resolved.is_file():
        raise ProjectAiAccessError("project_ai_file_missing", 409)
    digest = hashlib.sha256()
    try:
        with resolved.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        after = resolved.stat()
    except OSError as exc:
        raise ProjectAiAccessError("project_ai_file_missing", 409) from exc
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ProjectAiAccessError("project_ai_file_changed_during_hash", 409)
    return {
        "relative_path": relative,
        "size": int(after.st_size),
        "mtime_ns": int(after.st_mtime_ns),
        "sha256": digest.hexdigest(),
    }


def _target(payload: dict[str, Any], action: str) -> tuple[str, str]:
    project_id = _int(payload.get("project_id"))
    event_id = _text(payload.get("event_id"))
    if action == INVOICE_EXTRACT:
        if bool(project_id) == bool(event_id):
            raise ProjectAiAccessError("invoice_target_invalid", 409)
        return ("project", str(project_id)) if project_id else ("event", event_id)
    if action in {CONTRACT_POLISH, PROJECT_RETROSPECTIVE} and project_id > 0 and not event_id:
        return "project", str(project_id)
    raise ProjectAiAccessError("project_ai_target_invalid", 409)


def _binding(payload: dict[str, Any], action: str) -> dict[str, Any]:
    target_type, target_id = _target(payload, action)
    common = {
        "action": action,
        "target_type": target_type,
        "target_id": target_id,
        "payload_target_type": _text(payload.get("target_type")),
        "payload_target_id": _text(payload.get("target_id")),
        "staff_id": _int(payload.get("staff_id")) or None,
        "user_id": _int(payload.get("triggered_by_user_id")) or None,
        "derive_method": _text(payload.get("derive_method")),
    }
    if action == INVOICE_EXTRACT:
        identity = payload.get(FILE_IDENTITY_KEY)
        if not isinstance(identity, dict):
            raise ProjectAiAccessError("invoice_file_identity_required", 409)
        return {
            **common,
            "extract_key": _text(payload.get("extract_key")),
            "file_url": _text(payload.get("file_url")).split("?", 1)[0],
            "file_name": _text(payload.get("file_name")),
            "file_identity": identity,
        }
    if action == CONTRACT_POLISH:
        fields = payload.get("fields")
        if not isinstance(fields, dict) or not fields:
            raise ProjectAiAccessError("contract_fields_required", 409)
        return {
            **common,
            "polish_key": _text(payload.get("polish_key")),
            "template_key": _text(payload.get("template_key")),
            "fields_sha256": _digest(fields),
        }
    return {**common, "analysis_kind": _text(payload.get("analysis_kind"))}


def issue_server_project_ai_capability(*, action: str, project_id: int) -> ServerProjectAiCapability:
    """Mint an internal-only capability for a scheduler-owned retrospective."""

    action_text = _text(action).lower()
    project = _int(project_id)
    if action_text != PROJECT_RETROSPECTIVE or project <= 0:
        raise ProjectAiAccessError("server_project_ai_capability_unsupported")
    claims = {"version": FENCE_VERSION, "action": action_text, "project_id": project}
    return ServerProjectAiCapability(action_text, project, _signature(claims))


def _valid_server_capability(
    capability: ServerProjectAiCapability | None,
    *,
    action: str,
    project_id: int,
) -> bool:
    if not isinstance(capability, ServerProjectAiCapability):
        return False
    claims = {
        "version": FENCE_VERSION,
        "action": _text(capability.action).lower(),
        "project_id": _int(capability.project_id),
    }
    return bool(
        action == PROJECT_RETROSPECTIVE
        and claims["action"] == action
        and claims["project_id"] == int(project_id)
        and hmac.compare_digest(_text(capability.signature), _signature(claims))
    )


def build_job_fence(
    payload: dict[str, Any],
    *,
    action: str,
    staff: dict[str, Any] | None,
    server_capability: ServerProjectAiCapability | None = None,
) -> dict[str, Any]:
    """Authorize enqueue and seal the immutable actor/target/input binding."""

    action_text = _text(action).lower()
    if action_text not in SUPPORTED_ACTIONS:
        raise ProjectAiAccessError("project_ai_action_unsupported")
    target_type, target_id = _target(payload, action_text)
    server_owned = _valid_server_capability(
        server_capability,
        action=action_text,
        project_id=_int(target_id) if target_type == "project" else 0,
    )
    if server_capability is not None and not server_owned:
        raise ProjectAiAccessError("server_project_ai_capability_invalid")
    if server_owned:
        if _int(payload.get("staff_id")) or _int(payload.get("triggered_by_user_id")):
            raise ProjectAiAccessError("server_project_ai_actor_invalid")
    else:
        if not check_tab_permission(staff or {}, "vkpi", "write"):
            raise ProjectAiAccessError("project_ai_write_permission_required")
        if target_type == "project":
            scope.assert_project_access(int(target_id), staff, write=True)
        else:
            scope.assert_event_access(target_id, staff, write=True)
        if _int(payload.get("staff_id")) <= 0 or _int(payload.get("triggered_by_user_id")) <= 0:
            raise ProjectAiAccessError("project_ai_actor_identity_required")
    _assert_target_exists(get_conn(), target_type, target_id)
    claim = {
        "version": FENCE_VERSION,
        "mode": "server_owned" if server_owned else "user",
        "action": action_text,
        "target_type": target_type,
        "target_id": target_id,
        "binding": _binding(payload, action_text),
    }
    return _signed(claim)


def _active_actor(conn: Any, *, staff_id: int, user_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT s.*, u.status AS user_status
        FROM staff s JOIN users u ON u.id=s.user_id
        WHERE s.id=? LIMIT 1
        """,
        (int(staff_id),),
    ).fetchone()
    if not row:
        raise ProjectAiAccessError("project_ai_actor_inactive")
    actor = dict(row)
    if _int(actor.get("user_id")) != int(user_id):
        raise ProjectAiAccessError("project_ai_actor_changed")
    if actor.get("active") not in (True, 1, "1") or _text(actor.get("suspended_at")):
        raise ProjectAiAccessError("project_ai_actor_inactive")
    if not user_status_allows_auth(actor.get("user_status"), production=True):
        raise ProjectAiAccessError("project_ai_actor_inactive")
    if not check_tab_permission(actor, "vkpi", "write"):
        raise ProjectAiAccessError("project_ai_permission_revoked")
    return actor


def _assert_target_exists(conn: Any, target_type: str, target_id: str) -> None:
    if target_type == "project":
        row = conn.execute("SELECT id FROM vkpi_projects WHERE id=? LIMIT 1", (int(target_id),)).fetchone()
    else:
        row = conn.execute("SELECT id FROM vkpi_events WHERE id=? LIMIT 1", (target_id,)).fetchone()
    if not row:
        raise ProjectAiAccessError(f"{target_type}_ai_target_removed", 409)


def revalidate_job_fence(
    payload: dict[str, Any],
    *,
    action: str,
    conn: Any | None = None,
    file_path: Path | None = None,
    file_root: Path | None = None,
) -> dict[str, Any]:
    """Fail closed on actor, scope, input or file drift immediately before AI work."""

    action_text = _text(action).lower()
    fence = payload.get(FENCE_KEY)
    if not isinstance(fence, dict):
        raise ProjectAiAccessError("project_ai_fence_missing")
    if _int(fence.get("version")) != FENCE_VERSION or not _valid_signature(fence):
        raise ProjectAiAccessError("project_ai_fence_invalid")
    if _text(fence.get("action")).lower() != action_text:
        raise ProjectAiAccessError("project_ai_action_drifted", 409)
    current_binding = _binding(payload, action_text)
    if not hmac.compare_digest(_canonical(fence.get("binding")), _canonical(current_binding)):
        raise ProjectAiAccessError("project_ai_payload_drifted", 409)
    target_type, target_id = _target(payload, action_text)
    if fence.get("target_type") != target_type or _text(fence.get("target_id")) != target_id:
        raise ProjectAiAccessError("project_ai_target_drifted", 409)
    db = conn or get_conn()
    mode = _text(fence.get("mode"))
    if mode == "server_owned":
        if action_text != PROJECT_RETROSPECTIVE or target_type != "project":
            raise ProjectAiAccessError("server_project_ai_fence_invalid")
        actor: dict[str, Any] = {"id": None, "user_id": None, "server_owned": True}
    elif mode == "user":
        binding = current_binding
        staff_id = _int(binding.get("staff_id"))
        user_id = _int(binding.get("user_id"))
        if staff_id <= 0 or user_id <= 0:
            raise ProjectAiAccessError("project_ai_actor_identity_required")
        actor = _active_actor(db, staff_id=staff_id, user_id=user_id)
        try:
            if target_type == "project":
                scope.assert_project_access(int(target_id), actor, write=True)
            else:
                scope.assert_event_access(target_id, actor, write=True)
        except scope.ScopeDenied as exc:
            raise ProjectAiAccessError(f"{target_type}_ai_permission_revoked") from exc
    else:
        raise ProjectAiAccessError("project_ai_fence_invalid")
    _assert_target_exists(db, target_type, target_id)
    if action_text == INVOICE_EXTRACT:
        if file_path is None or file_root is None:
            raise ProjectAiAccessError("invoice_file_identity_required", 409)
        current_identity = capture_file_identity(file_path, root=file_root)
        if not hmac.compare_digest(
            _canonical(payload.get(FILE_IDENTITY_KEY)), _canonical(current_identity)
        ):
            raise ProjectAiAccessError("invoice_file_identity_drifted", 409)
    return actor


def blocked_result(exc: ProjectAiAccessError, *, provider_called: bool = False) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": exc.code,
        "provider_calls_performed": bool(provider_called),
        "retryable": False,
    }
