#!/usr/bin/env python3
"""P4.4B contract smoke: Discover search UI must not look stuck or overflow.

This is a static UI contract guard. Real browser QA is still required for the
click path, but these checks prevent regressions in the exact issues reported:
- existing KOL cards should load detail state instead of only filling input
- missing KOL + no auto-create should show an explicit warning
- existing KOL card text should not overlap avatar/logo area
"""
from stdout_utils import out as stdout_out
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DISCOVER = ROOT / "frontend/src/components/vkpi/pages/DiscoverPage.tsx"
CSS = ROOT / "frontend/src/components/vkpi/VkpiDashboard.css"


def require(path: Path, needle: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if needle not in text:
        raise AssertionError(f"missing {label}: {needle}")


def main() -> None:
    require(DISCOVER, "function searchNeedle", "URL-to-handle search normalization")
    require(DISCOVER, "function existingKolToLookupResult", "existing KOL immediate detail payload")
    require(DISCOVER, "setLookupResult(existingKolToLookupResult(kol))", "existing KOL click loads detail before refresh")
    require(DISCOVER, "未勾选“新红人自动建档”", "missing-KOL no-auto-create warning copy")
    require(DISCOVER, "messageTone === 'error' ? 'is-error' : messageTone === 'warn'", "warning/error visual class")
    require(DISCOVER, "void chooseExistingKol(kol)", "async existing-KOL click handler")

    require(CSS, ".vkpi-existing-kol-row .vkpi-avatar", "existing KOL avatar grid placement")
    require(CSS, "grid-template-columns: 42px minmax(0, 1fr)", "existing KOL text column minmax")
    require(CSS, "text-overflow: ellipsis", "existing KOL overflow clipping")
    require(CSS, ".vkpi-inline-message.is-warn", "warning inline message style")
    require(CSS, ".vkpi-info-block strong { overflow-wrap: anywhere", "long result value wrapping")

    stdout_out("VKPI_P4_4B_DISCOVER_SEARCH_UI_CONTRACT_OK")


if __name__ == "__main__":
    main()
