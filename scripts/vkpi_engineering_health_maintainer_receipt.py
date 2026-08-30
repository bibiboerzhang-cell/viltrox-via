"""Format real maintainer evidence into a vkpi_maintainer_qualification_receipt_v1.

The script NEVER invents evidence. It reads a user-filled evidence JSON file
(see --template), validates the reference counts against the qualification
policy in vkpi_engineering_health_evolution_people.py, and either:

* writes a receipt payload that passes `_qualification_evidence` validation
  (status=ready), bound to the current repository HEAD; or
* reports status=insufficient with the exact per-domain/per-person gaps,
  and refuses to write a receipt.
"""
from __future__ import annotations

import argparse
import json

try:
    from scripts.stdout_utils import out as stdout_out
except ModuleNotFoundError:  # direct execution: scripts/ is sys.path[0]
    from stdout_utils import out as stdout_out
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from vkpi_engineering_health_evolution_people import (  # noqa: E402
    CORE_DOMAINS,
    MIN_QUALIFIED_INDEPENDENT_REVIEWS,
    MIN_QUALIFIED_MERGED_PRS,
    MIN_QUALIFIED_OPERATIONAL_EVIDENCE,
    QUALIFICATION_SCHEMA_VERSION,
)

REFERENCE_REQUIREMENTS: tuple[tuple[str, int, str], ...] = (
    ("merged_pr_refs", MIN_QUALIFIED_MERGED_PRS, "merged PR references"),
    (
        "independent_review_refs",
        MIN_QUALIFIED_INDEPENDENT_REVIEWS,
        "independent review references",
    ),
    (
        "operational_evidence_refs",
        MIN_QUALIFIED_OPERATIONAL_EVIDENCE,
        "operational evidence references",
    ),
)


class MaintainerReceiptError(ValueError):
    """Raised when the evidence input cannot be interpreted safely."""


def _git_head(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise MaintainerReceiptError(
            f"cannot resolve repository HEAD: {result.stderr.strip()}"
        )
    head = result.stdout.strip()
    if not head:
        raise MaintainerReceiptError("git rev-parse HEAD returned empty output")
    return head


def _normalize_reference(item: Any) -> str:
    """Normalize one evidence reference to a non-empty string.

    Operational evidence may be given as {"description": ..., "date": ...};
    it is flattened to "<date>: <description>" without inventing either part.
    """
    if isinstance(item, dict):
        description = " ".join(str(item.get("description") or "").split())
        date = " ".join(str(item.get("date") or "").split())
        if description and date:
            return f"{date}: {description}"
        return description or date
    return " ".join(str(item or "").split())


def _person_references(
    person: dict[str, Any], name: str
) -> tuple[list[str], list[str]]:
    """Return (normalized unique refs, problems) for one reference list."""
    problems: list[str] = []
    raw = person.get(name)
    if raw is None:
        return [], problems
    if not isinstance(raw, list):
        problems.append(f"{name} must be a list")
        return [], problems
    normalized: list[str] = []
    for item in raw:
        ref = _normalize_reference(item)
        if not ref:
            continue  # blank template placeholders are simply missing evidence
        if ref in normalized:
            problems.append(f"{name} contains duplicate reference: {ref}")
            continue
        normalized.append(ref)
    return normalized, problems


def _evaluate_person(
    domain: str, index: int, person: Any
) -> tuple[dict[str, Any] | None, list[str]]:
    """Validate one person entry; return (receipt person or None, gaps)."""
    gaps: list[str] = []
    label = f"{domain}[{index}]"
    if not isinstance(person, dict):
        return None, [f"{label}: person entry must be an object"]
    identity = " ".join(str(person.get("identity") or "").casefold().split())
    if not identity or "@" not in identity:
        gaps.append(f"{label}: identity must be a canonical email")
    else:
        label = f"{domain}:{identity}"
    receipt_person: dict[str, Any] = {"identity": identity}
    for name, minimum, description in REFERENCE_REQUIREMENTS:
        refs, problems = _person_references(person, name)
        gaps.extend(f"{label}: {problem}" for problem in problems)
        if len(refs) < minimum:
            gaps.append(
                f"{label}: needs at least {minimum} {description}, has {len(refs)}"
            )
        receipt_person[name] = refs
    if gaps:
        return None, gaps
    return receipt_person, []


def _load_evidence(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise MaintainerReceiptError(f"cannot read evidence file: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise MaintainerReceiptError(f"evidence file is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise MaintainerReceiptError("evidence file must contain a JSON object")
    return payload


def build_receipt(
    evidence: dict[str, Any], *, head: str, generated_at: str | None = None
) -> dict[str, Any]:
    """Build the qualification result from user-supplied evidence.

    Returns {"status": "ready", "receipt": {...}} when every core domain has
    at least one fully evidenced person, else {"status": "insufficient",
    "gaps": [...]} — no receipt is produced in that case.
    """
    source = " ".join(str(evidence.get("source") or "").split())
    if not source:
        raise MaintainerReceiptError(
            "evidence 'source' is required (where this evidence was collected)"
        )
    raw_domains = evidence.get("domains")
    if not isinstance(raw_domains, dict):
        raise MaintainerReceiptError("evidence 'domains' must be an object")
    unknown = set(raw_domains) - set(CORE_DOMAINS)
    if unknown:
        names = ", ".join(sorted(unknown))
        raise MaintainerReceiptError(f"unknown maintainer domains: {names}")

    gaps: list[str] = []
    receipt_domains: dict[str, Any] = {}
    for domain in sorted(CORE_DOMAINS):
        domain_payload = raw_domains.get(domain)
        people = (
            domain_payload.get("people")
            if isinstance(domain_payload, dict)
            else None
        )
        if not isinstance(people, list) or not people:
            gaps.append(f"{domain}: no maintainer evidence provided")
            continue
        qualified_people: list[dict[str, Any]] = []
        seen_identities: set[str] = set()
        for index, person in enumerate(people):
            receipt_person, person_gaps = _evaluate_person(domain, index, person)
            gaps.extend(person_gaps)
            if receipt_person is None:
                continue
            identity = receipt_person["identity"]
            if identity in seen_identities:
                gaps.append(f"{domain}: duplicate identity {identity}")
                continue
            seen_identities.add(identity)
            qualified_people.append(receipt_person)
        if not qualified_people:
            gaps.append(f"{domain}: no person meets the qualification policy")
        receipt_domains[domain] = {"people": qualified_people}

    if gaps:
        return {
            "status": "insufficient",
            "candidate_head": head,
            "gaps": gaps,
            "policy": {
                "minimum_merged_pr_references": MIN_QUALIFIED_MERGED_PRS,
                "minimum_independent_review_references": (
                    MIN_QUALIFIED_INDEPENDENT_REVIEWS
                ),
                "minimum_operational_evidence_references": (
                    MIN_QUALIFIED_OPERATIONAL_EVIDENCE
                ),
            },
        }

    timestamp = generated_at or datetime.now(timezone.utc).isoformat()
    receipt = {
        "schema_version": QUALIFICATION_SCHEMA_VERSION,
        "candidate": {"head": head},
        "generated_at": timestamp,
        "source": source,
        "domains": receipt_domains,
    }
    return {"status": "ready", "receipt": receipt}


def evidence_template() -> dict[str, Any]:
    person = {
        "identity": "",
        "merged_pr_refs": ["", "", ""],
        "independent_review_refs": ["", ""],
        "operational_evidence_refs": [{"description": "", "date": ""}],
    }
    return {
        "source": "",
        "_instructions": (
            "Fill identity with the maintainer's canonical email. Every reference "
            "must be a real, checkable artifact (PR URL, review URL/quote, "
            "operational evidence with date). Blank entries are ignored; the "
            "script never fabricates evidence."
        ),
        "domains": {
            domain: {"people": [json.loads(json.dumps(person))]}
            for domain in sorted(CORE_DOMAINS)
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Format maintainer qualification evidence into a receipt."
    )
    parser.add_argument("--input", type=Path, help="evidence JSON file to format")
    parser.add_argument(
        "--output", type=Path, help="where to write the receipt payload (ready only)"
    )
    parser.add_argument(
        "--template", action="store_true", help="print a blank evidence template"
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=SCRIPT_DIR.parent,
        help="repository whose HEAD binds the receipt",
    )
    args = parser.parse_args(argv)

    if args.template:
        stdout_out(json.dumps(evidence_template(), ensure_ascii=False, indent=2) + "\n", end="")
        return 0
    if args.input is None:
        parser.error("--input is required unless --template is given")

    try:
        head = _git_head(args.repo_root)
        evidence = _load_evidence(args.input)
        result = build_receipt(evidence, head=head)
    except MaintainerReceiptError as exc:
        stdout_out(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False) + "\n", end="")
        return 2

    if result["status"] == "insufficient":
        stdout_out(json.dumps(result, ensure_ascii=False, indent=2) + "\n", end="")
        return 1

    receipt = result["receipt"]
    if args.output is not None:
        args.output.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    summary = {
        "status": "ready",
        "candidate_head": head,
        "output": str(args.output) if args.output is not None else None,
        "qualified_people_by_domain": {
            domain: len(receipt["domains"][domain]["people"])
            for domain in sorted(receipt["domains"])
        },
    }
    if args.output is None:
        summary["receipt"] = receipt
    stdout_out(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
