"""Scheduler overrides permitted only in an explicit local/test runtime."""
from __future__ import annotations

import os


def local_scheduler_force_enable() -> bool:
    from app.core import config

    requested = os.environ.get("OPS_SCHEDULER_FORCE_ENABLE", "").strip().lower()
    runtime = os.environ.get("ENVIRONMENT", config.ENVIRONMENT).strip().lower()
    production_mode = os.environ.get("V2_PRODUCTION_MODE", "").strip().lower()
    return (
        requested in {"1", "true", "yes", "on"}
        and not config.IS_PRODUCTION
        and production_mode not in {"1", "true", "yes", "on"}
        and runtime in {"local", "test", "testing", "dev", "development"}
    )
