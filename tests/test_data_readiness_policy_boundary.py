from __future__ import annotations

import ast
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.domains.market_brain import data_readiness
from app.shared import data_readiness_policy


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
POLICY_PATH = BACKEND / "app/shared/data_readiness_policy.py"
FORECAST_PATH = BACKEND / "app/domains/learning/forecast_feedback.py"
NOW = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)


def _absolute_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            imports.add(node.module or "")
    return imports


def test_market_brain_module_reexports_the_shared_policy_objects() -> None:
    assert data_readiness.DataRequirement is data_readiness_policy.DataRequirement
    assert data_readiness.DataReadiness is data_readiness_policy.DataReadiness
    assert data_readiness.evaluate_requirements is data_readiness_policy.evaluate_requirements
    assert data_readiness.build_source_readiness is data_readiness_policy.build_source_readiness
    assert data_readiness.READINESS_VERSION == data_readiness_policy.READINESS_VERSION
    assert data_readiness.DEFAULT_MAX_AGE_DAYS == data_readiness_policy.DEFAULT_MAX_AGE_DAYS


def test_forecast_source_readiness_serialization_is_exact() -> None:
    result = data_readiness_policy.build_source_readiness(
        "forecast_evaluations",
        observed=5,
        freshest_at=NOW - timedelta(days=3),
        minimum=5,
        max_age_days=45,
        now=NOW,
    )

    assert result == {
        "version": "market_brain_data_readiness_v1",
        "status": "ready",
        "ready": True,
        "claimable": True,
        "claim_level": "validated",
        "checks": {
            "forecast_evaluations": {
                "label": "forecast_evaluations observed source rows",
                "status": "ready",
                "observed": 5,
                "minimum": 5,
                "freshest_at": "2026-07-10T12:00:00+00:00",
                "age_days": 3.0,
                "max_age_days": 45,
                "sample_ready": True,
                "freshness_status": "fresh",
            }
        },
        "blockers": [],
        "note": (
            "Effectiveness claims require every sample and freshness check to pass; "
            "otherwise values are descriptive observations only."
        ),
    }


def test_readiness_status_precedence_and_empty_contract_are_preserved() -> None:
    mixed = data_readiness_policy.evaluate_requirements(
        [
            data_readiness_policy.DataRequirement(
                key="stale",
                observed=5,
                minimum=5,
                freshest_at=NOW - timedelta(days=46),
                max_age_days=45,
            ),
            data_readiness_policy.DataRequirement(
                key="missing",
                observed=0,
                minimum=5,
            ),
        ],
        now=NOW,
    ).to_dict()
    empty = data_readiness_policy.evaluate_requirements([], now=NOW).to_dict()

    assert mixed["status"] == "insufficient"
    assert mixed["blockers"] == ["stale:stale>45d", "missing:sample<5"]
    assert mixed["checks"]["stale"]["status"] == "stale"
    assert mixed["checks"]["missing"]["freshness_status"] == "not_required"
    assert empty == {
        "version": "market_brain_data_readiness_v1",
        "status": "insufficient",
        "ready": False,
        "claimable": False,
        "claim_level": "descriptive_only",
        "checks": {},
        "blockers": [],
        "note": (
            "Effectiveness claims require every sample and freshness check to pass; "
            "otherwise values are descriptive observations only."
        ),
    }


def test_shared_policy_import_is_dependency_free_and_forecast_owns_no_market_edge() -> None:
    assert _absolute_imports(POLICY_PATH) <= {
        "__future__",
        "dataclasses",
        "datetime",
        "typing",
    }
    forecast_imports = _absolute_imports(FORECAST_PATH)
    assert "app.shared.data_readiness_policy" in forecast_imports
    assert "app.domains.market_brain.data_readiness" not in forecast_imports

    script = "\n".join(
        [
            "import json, sys",
            f"sys.path.insert(0, {str(BACKEND)!r})",
            "import app.shared.data_readiness_policy",
            "names = [",
            "    'app.core.logging',",
            "    'app.db.connection',",
            "    'app.domains.learning.forecast_feedback',",
            "    'app.domains.market_brain.data_readiness',",
            "]",
            "print(json.dumps({name: name in sys.modules for name in names}, sort_keys=True))",
        ]
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stderr == ""
    assert json.loads(completed.stdout) == {
        "app.core.logging": False,
        "app.db.connection": False,
        "app.domains.learning.forecast_feedback": False,
        "app.domains.market_brain.data_readiness": False,
    }
