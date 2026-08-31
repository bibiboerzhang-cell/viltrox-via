"""Runtime binding shared by the Redis queue facade and its collaborators.

The collaborator modules must observe live attributes on ``queue.py`` because
tests and operational probes patch that public facade.  Importing the facade
back from every collaborator created a real package cycle, though.  The facade
therefore binds its already-imported module object once, and collaborators read
that object through this acyclic registry.
"""
from __future__ import annotations

import sys
from types import ModuleType


_QUEUE_FACADE: ModuleType | None = None
_EXPECTED_FACADE = "app.services.jobs.queue"


def bind_queue_facade(module: ModuleType) -> None:
    """Bind the canonical facade without copying any patchable attributes."""

    if module.__name__ != _EXPECTED_FACADE:
        raise RuntimeError(f"unexpected queue facade: {module.__name__}")
    global _QUEUE_FACADE
    _QUEUE_FACADE = module


def queue_facade() -> ModuleType:
    """Return the live facade, including after this registry is reloaded."""

    facade = _QUEUE_FACADE or sys.modules.get(_EXPECTED_FACADE)
    if facade is None:
        raise RuntimeError("Redis queue facade is not bound")
    if not isinstance(facade, ModuleType):
        raise RuntimeError("Redis queue facade binding is invalid")
    return facade


__all__ = ["bind_queue_facade", "queue_facade"]
