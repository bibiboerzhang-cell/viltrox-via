"""Tests for the maintainer qualification receipt generator."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from vkpi_engineering_health_evolution_people import (  # noqa: E402
    CORE_DOMAINS,
    _qualification_evidence,
)
from vkpi_engineering_health_maintainer_receipt import (  # noqa: E402
    MaintainerReceiptError,
    build_receipt,
    evidence_template,
    main,
)

HEAD = "343e0ea6552b4ffcff96caea51b65bcdfe5eb4b8"


def _parse_ts(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    return parsed.astimezone(timezone.utc)


def _full_person(email: str = "dev@example.com") -> dict:
    return {
        "identity": email,
        "merged_pr_refs": [
            "https://github.com/org/repo/pull/1",
            "https://github.com/org/repo/pull/2",
            "https://github.com/org/repo/pull/3",
        ],
        "independent_review_refs": [
            "https://github.com/org/repo/pull/4#pullrequestreview-1",
            "https://github.com/org/repo/pull/5#pullrequestreview-2",
        ],
        "operational_evidence_refs": [
            {"description": "restarted stuck worker lane", "date": "2026-08-20"},
        ],
    }


def _full_evidence() -> dict:
    return {
        "source": "docs/maintainer_evidence.json",
        "domains": {
            domain: {"people": [_full_person()]} for domain in sorted(CORE_DOMAINS)
        },
    }


def test_insufficient_when_counts_short() -> None:
    evidence = _full_evidence()
    person = evidence["domains"]["kol"]["people"][0]
    person["merged_pr_refs"] = person["merged_pr_refs"][:2]  # only 2 of 3
    result = build_receipt(evidence, head=HEAD)
    assert result["status"] == "insufficient"
    assert "receipt" not in result
    assert any("kol" in gap and "merged PR" in gap for gap in result["gaps"])


def test_insufficient_when_domain_missing() -> None:
    evidence = _full_evidence()
    del evidence["domains"]["workers"]
    result = build_receipt(evidence, head=HEAD)
    assert result["status"] == "insufficient"
    assert any(gap.startswith("workers:") for gap in result["gaps"])


def test_blank_template_placeholders_do_not_count() -> None:
    evidence = _full_evidence()
    person = evidence["domains"]["frontend_delivery"]["people"][0]
    person["merged_pr_refs"] = ["", "", ""]
    result = build_receipt(evidence, head=HEAD)
    assert result["status"] == "insufficient"


def test_ready_receipt_passes_people_validation() -> None:
    result = build_receipt(_full_evidence(), head=HEAD)
    assert result["status"] == "ready"
    receipt = result["receipt"]
    assert receipt["schema_version"] == "vkpi_maintainer_qualification_receipt_v1"
    assert receipt["candidate"]["head"] == HEAD
    qualified, evidence_summary = _qualification_evidence(
        receipt, head=HEAD, source="test", timestamp_parser=_parse_ts
    )
    assert evidence_summary["status"] == "observed"
    assert evidence_summary["candidate_head"] == HEAD
    assert set(qualified) == set(CORE_DOMAINS)
    for domain in CORE_DOMAINS:
        assert qualified[domain] == {"dev@example.com"}
        assert evidence_summary["qualified_people_by_domain"][domain] == 1


def test_missing_source_is_error() -> None:
    evidence = _full_evidence()
    evidence["source"] = ""
    with pytest.raises(MaintainerReceiptError):
        build_receipt(evidence, head=HEAD)


def test_unknown_domain_is_error() -> None:
    evidence = _full_evidence()
    evidence["domains"]["made_up"] = {"people": [_full_person()]}
    with pytest.raises(MaintainerReceiptError):
        build_receipt(evidence, head=HEAD)


def test_template_covers_all_domains() -> None:
    template = evidence_template()
    assert set(template["domains"]) == set(CORE_DOMAINS)
    # Template must be insufficient as-is (no fabricated evidence).
    template["source"] = "placeholder"
    result = build_receipt(template, head=HEAD)
    assert result["status"] == "insufficient"


def test_cli_binds_current_head(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    repo_root = SCRIPTS.parent
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(_full_evidence()), encoding="utf-8")
    output_path = tmp_path / "receipt.json"
    code = main(
        [
            "--input",
            str(evidence_path),
            "--output",
            str(output_path),
            "--repo-root",
            str(repo_root),
        ]
    )
    assert code == 0
    receipt = json.loads(output_path.read_text(encoding="utf-8"))
    assert receipt["candidate"]["head"] == head
    _parse_ts(receipt["generated_at"])
    qualified, summary = _qualification_evidence(
        receipt, head=head, source="cli", timestamp_parser=_parse_ts
    )
    assert summary["status"] == "observed"
    assert all(qualified[domain] for domain in CORE_DOMAINS)
    stdout = json.loads(capsys.readouterr().out)
    assert stdout["status"] == "ready"


def test_cli_insufficient_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    evidence = _full_evidence()
    evidence["domains"]["authentication"]["people"] = []
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    code = main(["--input", str(evidence_path), "--repo-root", str(SCRIPTS.parent)])
    assert code == 1
    stdout = json.loads(capsys.readouterr().out)
    assert stdout["status"] == "insufficient"
    assert stdout["gaps"]
