"""Ask P1 command-palette journey contract for the browser release gate.

Split out of test_browser_console_release_gate.py (thousand-line guard); shares
the reviewed capture fixture through the same module loader pattern as
test_browser_console_network_attribution.py.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "test_browser_console_release_gate.py"
SPEC = importlib.util.spec_from_file_location("vkpi_browser_gate_fixtures_ask_p1", FIXTURE_PATH)
assert SPEC and SPEC.loader
fixtures = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixtures)
gate = fixtures.gate
capture = fixtures.capture


@pytest.mark.parametrize(
    ("field", "value"),
    [("fact_count", 0), ("evidence_count", 0), ("optional_source_count", 1)],
)
def test_p1_clarification_or_empty_answer_and_optional_source_still_pass(
    field: str,
    value: int,
) -> None:
    """Ask P1: facts/evidence are diagnostics and the catalog is a legal optional source."""
    payload = capture()
    section = "global_search" if field == "optional_source_count" else "ask_find"
    payload["functional_proof"][section][field] = value
    result = gate.evaluate_capture(payload)
    assert result["overall"]["pass"] is True
    assert result["metrics"]["functional_proof"]["pass"] is True


def test_catalog_suggest_network_evidence_is_required_and_must_be_healthy() -> None:
    payload = capture()
    rows = payload["collection"]["network_responses"]
    catalog_rows = [row for row in rows if row["url"].endswith("/api/admin/vkpi/catalog/suggest")]
    assert len(catalog_rows) == 2
    # UI call + in-page probe both retained → passes with the reviewed counts.
    result = gate.evaluate_capture(payload)
    assert result["metrics"]["functional_proof"]["network_counts"] == {
        "intelligent_api_2xx": 1,
        "global_search_api_2xx": 2,
        "catalog_suggest_api_2xx": 2,
        "catalog_suggest_api_non_2xx": 0,
    }
    # Dropping the probe row breaks ui_count + 1 coverage.
    payload_missing = capture()
    payload_missing["collection"]["network_responses"] = [
        row for row in payload_missing["collection"]["network_responses"]
        if not row["url"].endswith("/api/admin/vkpi/catalog/suggest")
    ] + catalog_rows[:1]
    payload_missing["collection"]["network_summary"].update(
        {"response_count_total": 8, "request_count_total": 8, "retained_response_count": 8}
    )
    missing = gate.evaluate_capture(payload_missing)
    assert missing["overall"]["pass"] is False
    assert missing["metrics"]["functional_proof"]["network_evidence_pass"] is False
    # A 5xx catalog answer inside the journey family is never tolerated as optional.
    payload_error = capture()
    payload_error["collection"]["network_responses"].append(
        {**catalog_rows[0], "status": 503}
    )
    payload_error["collection"]["network_summary"].update(
        {
            "response_count_total": 10,
            "request_count_total": 10,
            "retained_response_count": 10,
            "response_error_count_total": 1,
        }
    )
    errored = gate.evaluate_capture(payload_error)
    assert errored["overall"]["pass"] is False
    assert errored["metrics"]["functional_proof"]["network_counts"]["catalog_suggest_api_non_2xx"] == 1
