"""Industry data domain facade."""

from app.domains.industry.data import *  # noqa: F401,F403
from app.domains.industry.snapshot_collector import (  # noqa: F401
    collect_account_snapshot,
    provider_gate,
    sync_enabled_accounts,
)
from app.domains.industry.snapshot_kpis import (  # noqa: F401
    SNAPSHOT_FIELDS,
    calculate_kpis,
)

