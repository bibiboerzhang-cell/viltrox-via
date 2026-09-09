"""The reviewed SQL digest exception must not hide other keys or other files."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tomllib

import pytest


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / ".gitleaks.toml"
SOURCE = "scripts/ops/release_forward_policy.py"
MIGRATION = "307_users_token_version.sql"
DIGEST = hashlib.sha256((ROOT / "migrations" / MIGRATION).read_bytes()).hexdigest()
OTHER_DIGEST = hashlib.sha256(b"synthetic-non-credential-digest").hexdigest()


def test_migration_allowlist_requires_exact_rule_path_line_and_actual_sql_digest():
    entries = tomllib.loads(CONFIG.read_text())["allowlists"]
    matches = [entry for entry in entries if entry.get("description") ==
               "Exact reviewed migration 307 SHA256, not an API credential"]
    assert len(matches) == 1
    entry = matches[0]
    assert entry["targetRules"] == ["generic-api-key"]
    assert entry["condition"] == "AND"
    assert entry["regexTarget"] == "line"
    assert entry["paths"] == [r"^scripts/ops/release_forward_policy\.py$"]
    assert len(entry["regexes"]) == 1
    expression = entry["regexes"][0]
    assert re.fullmatch(expression, f'    "{MIGRATION}": "{DIGEST}",')
    assert not re.fullmatch(expression, f'    "{MIGRATION}": "{OTHER_DIGEST}",')
    assert not re.fullmatch(expression, f'    "api_token": "{DIGEST}",')


@pytest.mark.parametrize("path,key,digest,findings", [
    (SOURCE, MIGRATION, DIGEST, 0),
    ("other.py", MIGRATION, DIGEST, 1),
    ("prefix/" + SOURCE, MIGRATION, DIGEST, 1),
    (SOURCE, MIGRATION, OTHER_DIGEST, 1),
    (SOURCE, "api_token", DIGEST, 1),
])
def test_real_gitleaks_exception_does_not_hide_different_values_or_locations(
    tmp_path, path, key, digest, findings,
):
    executable = shutil.which("gitleaks")
    if executable is None:
        pytest.skip("local gitleaks unavailable; exact TOML contract still runs")
    root = tmp_path / "input"
    source = root / path
    source.parent.mkdir(parents=True)
    source.write_text(f'    "{key}": "{digest}",\n')
    report = tmp_path / "redacted-findings.json"
    result = subprocess.run([
        executable, "dir", ".", "--config", str(CONFIG), "--redact=100",
        "--no-banner", "--no-color", "--report-format=json", "--report-path", str(report),
    ], cwd=root, capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == (1 if findings else 0), result.stderr
    observed = json.loads(report.read_text())
    assert len(observed) == findings
    assert all(item["RuleID"] == "generic-api-key" for item in observed)
    assert digest not in result.stdout + result.stderr + report.read_text()
