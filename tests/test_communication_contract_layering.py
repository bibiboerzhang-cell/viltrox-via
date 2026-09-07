"""Shared truth contracts keep their API without importing domain facades."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("module", [
    "app.shared.communication_truth", "app.shared.message_truth",
    "app.shared.vkpi_kpi_communication_truth",
    "app.shared.vkpi_kpi_evidence_enrichment",
])
def test_shared_truth_import_does_not_load_domain_or_service_packages(module):
    # A fresh interpreter checks the actual import boundary, not just source text.
    code = (
        "import importlib,json,sys; "
        f"sys.path.insert(0,{str(ROOT / 'backend')!r}); "
        f"importlib.import_module({module!r}); "
        "print(json.dumps(sorted(name for name in sys.modules "
        "if name == 'app.domains' or name.startswith('app.domains.') "
        "or name == 'app.services' or name.startswith('app.services.'))))"
    )
    completed = subprocess.run([sys.executable, "-I", "-c", code],
                               text=True, capture_output=True, check=True, timeout=10)
    assert json.loads(completed.stdout) == []


def test_recommendation_compatibility_export_is_the_same_shared_function():
    from app.domains.recommendations import communication_evidence as legacy
    from app.shared import communication_truth as shared
    from app.shared.communication_truth import communication_evidence
    from app.shared import vkpi_kpi_communication_truth as kpi

    assert legacy.communication_evidence is communication_evidence
    assert kpi.communication_evidence is communication_evidence
    for name in ("COMMUNICATION_NODES", "LABEL_SEMANTICS", "LABEL_SEMANTICS_VERSION",
                 "_ACTION_TIMES", "_first_noncommunication_action", "project_outcome_communications",
                 "blocked_communication_record"):
        assert getattr(legacy, name) is getattr(shared, name)
    first = communication_evidence()
    first["sent"] = True
    assert communication_evidence()["sent"] is None
    assert legacy.blocked_communication_record()["communication_evidence"]["sent"] is None


def test_message_compatibility_exports_are_identical_and_keep_unknown_transport():
    from app.domains.evidence import message_truth as legacy
    from app.shared import message_truth as shared

    assert legacy.__all__ == shared.__all__
    for name in [*shared.__all__, "_capture_id"]:
        assert getattr(legacy, name) is getattr(shared, name)
    body = shared.capture_message_fields({"body": "synthetic record", "kol_id": "8"}, project_id=3)
    assert body["kol_id"] == 8 and body["project_id"] == 3
    assert shared.resolve_capture_kol(body, 8) == 8
    projected = legacy.project_message_record(body)
    assert projected["communication_truth"]["sent"] is None
    assert projected["communication_truth"]["transport_outcome_eligible"] is False
