"""A synthetic browser-fixture ID is not a blanket secret-scan exemption."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / ".gitleaks.toml"
DESCRIPTION = "Exact synthetic browser-fixture dedupe identifier, not a credential"
FIXTURE_PATH = "frontend/tests/browser/system-optimization/fixture-state.ts"
FIXTURE_VALUE = "synthetic-fixture-900001"


def _allowlist() -> dict[str, object]:
    config = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    matches = [
        item for item in config["allowlists"]
        if item.get("description") == DESCRIPTION
    ]
    assert len(matches) == 1
    return matches[0]


def test_fixture_exception_is_an_exact_rule_path_and_secret_conjunction() -> None:
    assert _allowlist() == {
        "description": DESCRIPTION,
        "targetRules": ["generic-api-key"],
        "condition": "AND",
        "regexTarget": "secret",
        "regexes": [r"^synthetic-fixture-900001$"],
        "paths": [
            r"^frontend/tests/browser/system-optimization/fixture-state\.ts$",
        ],
    }


@pytest.mark.parametrize(
    ("rule", "path", "value", "allowed"),
    [
        ("generic-api-key", FIXTURE_PATH, FIXTURE_VALUE, True),
        ("aws-access-token", FIXTURE_PATH, FIXTURE_VALUE, False),
        ("generic-api-key", "other.ts", FIXTURE_VALUE, False),
        ("generic-api-key", "prefix/" + FIXTURE_PATH, FIXTURE_VALUE, False),
        ("generic-api-key", FIXTURE_PATH + ".backup", FIXTURE_VALUE, False),
        ("generic-api-key", FIXTURE_PATH.upper(), FIXTURE_VALUE, False),
        ("generic-api-key", FIXTURE_PATH, "synthetic-fixture-900002", False),
        ("generic-api-key", FIXTURE_PATH, "prefix-" + FIXTURE_VALUE, False),
        ("generic-api-key", FIXTURE_PATH, FIXTURE_VALUE + "-suffix", False),
        ("generic-api-key", FIXTURE_PATH, FIXTURE_VALUE + "\n", False),
        ("generic-api-key", FIXTURE_PATH, "", False),
    ],
)
def test_fixture_exception_requires_every_exact_dimension(
    rule: str, path: str, value: str, allowed: bool,
) -> None:
    entry = _allowlist()
    # Exact TOML structure is asserted above; fullmatch checks its bounded values.
    matches = (
        rule in entry["targetRules"]
        and any(re.fullmatch(pattern, path) for pattern in entry["paths"])
        and any(re.fullmatch(pattern, value) for pattern in entry["regexes"])
    )
    assert bool(matches) is allowed


@pytest.mark.parametrize(
    ("path", "value", "expected_findings"),
    [
        (FIXTURE_PATH, FIXTURE_VALUE, 0),
        ("other.ts", FIXTURE_VALUE, 1),
        ("prefix/" + FIXTURE_PATH, FIXTURE_VALUE, 1),
        (FIXTURE_PATH + ".backup", FIXTURE_VALUE, 1),
        (FIXTURE_PATH, "synthetic-fixture-900002", 1),
        (
            FIXTURE_PATH,
            "fixture-" + base64.urlsafe_b64encode(
                hashlib.sha256(b"not-a-real-credential").digest(),
            ).decode("ascii").rstrip("="),
            1,
        ),
    ],
    ids=["exact-id", "other-file", "prefix-path", "suffix-path", "other-id", "synthetic-token"],
)
def test_real_gitleaks_keeps_other_paths_and_credential_shaped_values_detectable(
    tmp_path: Path, path: str, value: str, expected_findings: int,
) -> None:
    executable = shutil.which("gitleaks")
    if executable is None:
        pytest.skip("local gitleaks unavailable; the TOML contract still runs")
    scan_root = tmp_path / "input"
    source = scan_root / path
    source.parent.mkdir(parents=True)
    source.write_text(
        'const fixture = { dedupe_key: "' + value + '" };\n', encoding="utf-8",
    )
    report = tmp_path / "redacted-findings.json"
    result = subprocess.run(
        [
            executable, "dir", ".", "--config", str(CONFIG),
            "--redact=100", "--no-banner", "--no-color",
            "--report-format=json", "--report-path", str(report),
        ],
        cwd=scan_root, capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == (1 if expected_findings else 0), result.stderr
    findings = json.loads(report.read_text(encoding="utf-8"))
    assert len(findings) == expected_findings
    assert all(item["RuleID"] == "generic-api-key" for item in findings)
    assert value not in result.stdout + result.stderr + report.read_text(encoding="utf-8")
