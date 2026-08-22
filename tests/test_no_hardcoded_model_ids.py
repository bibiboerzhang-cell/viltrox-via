"""Static guard: Claude model ids must be sourced from config, never bare-pinned.

Rationale (audit cc_full_audit 2026-06-10): the deprecated date-stamped Claude id
``claude-sonnet-4-20250514`` (retiring 2026-06-15) was hard-coded across 10 call
sites, bypassing ``app.core.config.CLAUDE_MODEL`` (now ``claude-sonnet-5``). On the
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


# ── 2026-08-22 模型升级刀:退役 id 不得再以字符串字面出现在 core/ 之外 ──
# 单一真源 = core/config.py(默认值)+ core/model_registry.py(注册)+
# core/model_pricing.py(价)+ platform/models/runtime.py(精确目录,旧 id 保留给
# prod env pin / 历史台账回算)。调用点一律经 config/registry 取 id。
# 字面拆开拼接,防本文件自己被仓库级 grep 命中。
RETIRED_DEFAULT_IDS = (
    "claude-sonnet-" + "4-6",
    "claude-opus-" + "4-7",
    "gemini-3-flash-" + "preview",
    "gemini-3.1-pro-" + "preview",
)
RETIRED_ID_ALLOWLIST_DIRS = (APP_ROOT / "core",)
RETIRED_ID_ALLOWLIST_FILES = {APP_ROOT / "platform" / "models" / "runtime.py"}


def _quoted(literal: str) -> re.Pattern[str]:
    escaped = re.escape(literal)
    return re.compile(r"""(["'])[^"'\n]*""" + escaped + r"""[^"'\n]*\1""")


def test_retired_default_model_ids_are_not_string_literals_outside_core():
    patterns = {literal: _quoted(literal) for literal in RETIRED_DEFAULT_IDS}
    offenders = []
    for path in _py_files():
        if path in RETIRED_ID_ALLOWLIST_FILES:
            continue
        if any(allowed in path.parents for allowed in RETIRED_ID_ALLOWLIST_DIRS):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if not any(literal in text for literal in RETIRED_DEFAULT_IDS):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for literal, pattern in patterns.items():
                if pattern.search(line):
                    offenders.append(f"{path.relative_to(APP_ROOT)}:{lineno} ({literal})")
    assert not offenders, (
        "Retired default model ids must not be quoted literals outside backend/app/core "
        "(and the exact pricing catalog); source them from app.core.config / "
        "app.core.model_registry. Offenders: " + ", ".join(offenders)
    )
