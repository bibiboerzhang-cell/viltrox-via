"""Static guard: Claude model ids must be sourced from config, never bare-pinned.

Rationale (audit cc_full_audit 2026-06-10): the deprecated date-stamped Claude id
``claude-sonnet-4-20250514`` (retiring 2026-06-15) was hard-coded across 10 call
sites, bypassing ``app.core.config.CLAUDE_MODEL`` (= ``claude-sonnet-4-6``). On the
retire date those call sites would 404 and silently degrade scoring. This test
prevents that regression class.

Pure filesystem scan: imports only the stdlib, touches no app module and opens no
DB connection, so it is safe to run in isolation against any environment.
"""
from __future__ import annotations

import re
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1] / "backend" / "app"

# core/config.py is the single sanctioned place to define the default model id.
ALLOWLIST = {APP_ROOT / "core" / "config.py"}

# Split so this test file itself never trips a repo-wide grep for the literal.
RETIRING_ID = "claude-sonnet-4-" + "20250514"
# Any date-stamped sonnet-4 pin (>=6 trailing digits). The sanctioned id is the
# undated "claude-sonnet-4-6", which this pattern does not match.
DATED_SONNET4 = re.compile(r"claude-sonnet-4-\d{6,}")


def _py_files():
    return [p for p in APP_ROOT.rglob("*.py") if "__pycache__" not in p.parts]


def test_retiring_claude_id_fully_eradicated():
    offenders = []
    for path in _py_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        if RETIRING_ID not in text:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if RETIRING_ID in line:
                offenders.append(f"{path.relative_to(APP_ROOT)}:{lineno}")
    assert not offenders, (
        "Retiring Claude model id must not appear under backend/app; route through "
        "app.core.config.CLAUDE_MODEL. Offenders: " + ", ".join(offenders)
    )


def test_no_date_stamped_sonnet4_literal_outside_config():
    offenders = []
    for path in _py_files():
        if path in ALLOWLIST:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for lineno, line in enumerate(text.splitlines(), 1):
            if DATED_SONNET4.search(line):
                offenders.append(f"{path.relative_to(APP_ROOT)}:{lineno}")
    assert not offenders, (
        "Date-stamped claude-sonnet-4 ids must come from app.core.config, not bare "
        "literals at call sites. Offenders: " + ", ".join(offenders)
    )
