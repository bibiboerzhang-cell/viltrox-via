"""Safe payload merge projections for provider-job worker checkpoints."""
from __future__ import annotations

from typing import Any


def provider_job_payload_delta(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop stale search-session fields before merging a claimed snapshot.

    Session attachers may add item lineage after a worker claims its row.  A
    progress or terminal checkpoint must therefore merge only worker-owned
    fields into the database's current payload instead of overwriting newer
    session relationships with the old claim snapshot.
    """

    return {
        str(key): value
        for key, value in dict(payload or {}).items()
        if not str(key).startswith("search_session_")
    }


__all__ = ["provider_job_payload_delta"]
