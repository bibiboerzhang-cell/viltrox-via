"""Uniform authorization policy wrappers (A3, 2026-06-14).

Routers across the V-KPI backend currently hand-roll the same shape every time
they need to gate a project/event read or write::

    from app.domains.access import scope
    try:
        scope.assert_project_access(project_id, staff, write=True)
    except scope.ScopeDenied:
        raise HTTPException(status_code=403, detail="...")

That pattern is repeated in many routers with slightly different detail strings,
exception variable names, and (occasionally) a missing ``write=`` flag. This
module collapses the call site to a single named helper per (resource, action)
so the intent reads at a glance and the ``write`` flag can never be forgotten:

    from app.domains.access import policy
    policy.require_project_write(project_id, staff)   # raises ScopeDenied if denied

Design rules (read before extending):

- **Thin, not clever.** Each helper delegates straight to ``scope.assert_*``.
  All the real authz decisions (own/assigned/creator, shared member viewer vs
  editor, ``is_public`` read-widen, ``restricted`` shadowing, ``can_view_all``
  for owner/admin/manager) stay in ``scope.py`` — the single source of truth.
  This wrapper adds **zero** new policy; it only renames and re-raises.

- **Consistent failure.** Helpers re-raise ``scope.ScopeDenied`` (re-exported
  here as ``ScopeDenied`` for convenience) with a uniform, resource-tagged
  message. Routers should let it bubble to the app's existing ScopeDenied →
  HTTP 403 mapping (or map it themselves) instead of catching a bare
  ``PermissionError``. We deliberately do **not** raise ``HTTPException`` here
  so the helpers stay framework-agnostic and unit-testable without FastAPI.

- **ADDITIVE adoption only.** This module is provided as an opt-in convenience.
  It does **not** rewire any existing router — adopting it is a separate,
  per-router change to avoid a churn-heavy blast radius. Intended adoption:
  replace a hand-rolled ``try/except scope.ScopeDenied`` block with the matching
  ``require_*`` call; behavior is identical (same underlying ``assert_*``), only
  the boilerplate disappears.

- **Import-safe.** No DB calls, no FastAPI imports, no side effects at import
  time. Safe to import from anywhere ``scope`` is importable.
"""
from __future__ import annotations

from typing import Any

from app.domains.access import scope

# Re-export so adopters can ``from app.domains.access import policy`` and catch
# ``policy.ScopeDenied`` without also importing ``scope`` directly.
ScopeDenied = scope.ScopeDenied

__all__ = [
    "ScopeDenied",
    "require_project_read",
    "require_projects_read",
    "require_project_write",
    "require_event_read",
    "require_event_write",
]


def _reraise(resource: str, action: str, exc: scope.ScopeDenied) -> "ScopeDenied":
    """Normalize any ScopeDenied from the scope layer into a consistent message.

    Keeps the resource/action tag uniform across routers so logs and HTTP 403
    bodies read the same regardless of which call site denied.
    """
    return ScopeDenied(f"{resource} {action} denied")


def require_project_read(target_id: int, staff: dict[str, Any] | None) -> None:
    """Gate a project READ. Raises ``ScopeDenied`` if ``staff`` may not read it.

    Allowed (delegated to ``scope.assert_project_access``): owner/admin/manager
    (can_view_all), assigned/creator, shared member (viewer or editor), and a
    non-restricted ``is_public`` project. Denied otherwise.
    """
    try:
        scope.assert_project_access(int(target_id), staff, write=False)
    except scope.ScopeDenied as exc:
        raise _reraise("project", "read", exc) from exc


def require_projects_read(target_ids: list[int] | tuple[int, ...] | set[int], staff: dict[str, Any] | None) -> None:
    """Gate a bounded project set without repeating the scalar DB reads."""
    try:
        scope.assert_project_access_many(target_ids, staff, write=False)
    except scope.ScopeDenied as exc:
        raise _reraise("project", "read", exc) from exc


def require_project_write(target_id: int, staff: dict[str, Any] | None) -> None:
    """Gate a project WRITE. Raises ``ScopeDenied`` if ``staff`` may not write it.

    Stricter than read: ``is_public`` does not grant write, and a shared
    ``viewer`` member is read-only. Delegates to ``scope.assert_project_access``
    with ``write=True``.
    """
    try:
        scope.assert_project_access(int(target_id), staff, write=True)
    except scope.ScopeDenied as exc:
        raise _reraise("project", "write", exc) from exc


def require_event_read(target_id: str, staff: dict[str, Any] | None) -> None:
    """Gate an event READ. Raises ``ScopeDenied`` if ``staff`` may not read it.

    Allowed (delegated to ``scope.assert_event_access``): owner/admin/manager
    (can_view_all), event owner, team member, shared member (viewer or editor),
    and a public (``is_public``) event. Denied otherwise.
    """
    try:
        scope.assert_event_access(str(target_id), staff, write=False)
    except scope.ScopeDenied as exc:
        raise _reraise("event", "read", exc) from exc


def require_event_write(target_id: str, staff: dict[str, Any] | None) -> None:
    """Gate an event WRITE. Raises ``ScopeDenied`` if ``staff`` may not write it.

    Stricter than read: ``is_public`` does not grant write, and a shared
    ``viewer`` member is read-only. A holder of ``vkpi:write`` who is neither
    owner/team/editor-member nor can_view_all is denied. Delegates to
    ``scope.assert_event_access`` with ``write=True``.
    """
    try:
        scope.assert_event_access(str(target_id), staff, write=True)
    except scope.ScopeDenied as exc:
        raise _reraise("event", "write", exc) from exc
