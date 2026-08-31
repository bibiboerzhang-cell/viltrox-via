"""Read-only worker-fleet proof used by queue capacity admission."""
from __future__ import annotations

from typing import Any


def worker_fleet_snapshot() -> dict[str, Any]:
    """Read one release-aligned heartbeat snapshot with a bounded DB lease."""

    from app.db.connection import db_connection_sync_reusing_scope
    from app.workers.redis_worker_health import redis_worker_fleet_health
    from app.workers.redis_worker_runtime import _release_sha

    with db_connection_sync_reusing_scope():
        release_sha = _release_sha()
        snapshot = redis_worker_fleet_health(release_sha)
    return {**snapshot, "capacity_release_sha": release_sha}
