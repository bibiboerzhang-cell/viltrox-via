#!/usr/bin/env python3
"""Static smoke for P3.1E account crawl/API status gate.

A previously synced account must not be shown as API-blocked just because the
platform settings row still has last_test_status=not_configured. The settings
status is a setup hint, while account sync/last_successful_at is authoritative
for an already-refreshed profile.
"""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "frontend/src/components/vkpi/pages/data-analysis/profile/ProfileDashboard.tsx"
DRAWER = ROOT / "frontend/src/components/vkpi/pages/data-analysis/drawers/AccountDrawer.tsx"


def require_contains(path: Path, needle: str) -> None:
    text = path.read_text()
    if needle not in text:
        raise AssertionError(f"{path.name} missing expected marker: {needle}")


def main() -> None:
    for path in (PROFILE, DRAWER):
        require_contains(path, "function statusReady")
        require_contains(path, "function accountHasSuccessfulSync")
        require_contains(path, "last_successful_at")
        require_contains(path, "最近同步成功")

    require_contains(PROFILE, "const accountSyncReady = accountHasSuccessfulSync(account)")
    require_contains(PROFILE, "const apiReady = !paidPlatform || accountSyncReady || statusReady(platformTestStatus)")
    require_contains(PROFILE, "setting.last_test_status")

    require_contains(DRAWER, "const accountSyncReady = accountHasSuccessfulSync(account)")
    require_contains(DRAWER, "const apiReady = accountSyncReady || statusReady(apiStatus)")
    require_contains(DRAWER, "const apiDetail = accountSyncReady")

    print("VKPI_P3_1E_ACCOUNT_STATUS_GATE_SMOKE_OK")


if __name__ == "__main__":
    main()
