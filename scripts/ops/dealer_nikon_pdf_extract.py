#!/usr/bin/env python3
"""Extract the Nikon USA authorized-dealer PDF into a reviewable JSON snapshot.

Scope / truth boundaries:

* This is a local extraction tool only.  It reads one operator-supplied PDF
  snapshot and writes one JSON file next to it; it never touches the database,
  never crawls, and never publishes anything.
* A manufacturer directory row only proves that Nikon listed an organization
  name, a city and a state on the snapshot date.  It proves no street address,
  no physical storefront, no inventory and no Viltrox relationship.
* The output keeps both statistics: raw directory rows (one PDF line each)
  and unique organizations (casefolded organization-name identity), so later
  stages can reason about the dedupe explicitly.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PDF = ROOT / "runtime" / "dealer-source-audit" / "20260716" / "nikon.pdf"
DEFAULT_OUT = ROOT / "runtime" / "dealer-source-audit" / "20260716" / "nikon_rows.json"
DOCUMENT_URL = "https://www.nikonusa.com/where-to-buy/nikon_img_auth_dealers.pdf"
DEALER_TYPES = {"NPD", "NID"}
US_STATE_RE = re.compile(r"^[A-Z]{2}$")
HEADER_TOKENS = {"authorized dealer", "city", "region", "dealer type"}


def _clean(cell: object) -> str:
    """Collapse a possibly multi-line PDF table cell to single-space text."""
    return re.sub(r"\s+", " ", str(cell or "")).strip()


def _is_header_or_legend(cells: list[str]) -> bool:
    joined = " ".join(cells).casefold()
    if not joined:
        return True
    if "authorized dealer" in joined and "dealer type" in joined:
        return True
    if joined.startswith(("npd =", "nid =", "authorized nikon dealers as of")):
        return True
    if all((cell.casefold() in HEADER_TOKENS or not cell) for cell in cells):
        return True
    return False


def _as_of_date(text: str) -> str:
    match = re.search(r"as of\s+(\d{2}/\d{2}/\d{4})", text, flags=re.IGNORECASE)
    return match.group(1) if match else ""


def extract_rows(pdf_path: Path) -> dict:
    import pdfplumber

    raw_rows: list[dict] = []
    skipped: list[dict] = []
    as_of = ""
    with pdfplumber.open(str(pdf_path)) as pdf:
        page_count = len(pdf.pages)
        for page_number, page in enumerate(pdf.pages, start=1):
            if not as_of:
                as_of = _as_of_date(page.extract_text() or "")
            for table in page.extract_tables() or []:
                for cells in table:
                    values = [_clean(cell) for cell in (cells or [])]
                    if _is_header_or_legend(values):
                        continue
                    values = [value for value in values if value]
                    if len(values) != 4:
                        skipped.append({"page": page_number, "cells": values})
                        continue
                    organization_name, city, state, dealer_type = values
                    state = state.upper()
                    dealer_type = dealer_type.upper()
                    if dealer_type not in DEALER_TYPES or not US_STATE_RE.match(state):
                        skipped.append({"page": page_number, "cells": values})
                        continue
                    raw_rows.append(
                        {
                            "row_index": len(raw_rows) + 1,
                            "page": page_number,
                            "organization_name": organization_name,
                            "city": city,
                            "state": state,
                            "dealer_type": dealer_type,
                        }
                    )

    unique_orgs: dict[str, int] = {}
    identity_counts: dict[tuple[str, str, str, str], int] = {}
    by_type: dict[str, int] = {}
    for row in raw_rows:
        unique_orgs.setdefault(row["organization_name"].casefold(), 0)
        unique_orgs[row["organization_name"].casefold()] += 1
        identity = (
            row["organization_name"].casefold(),
            row["city"].casefold(),
            row["state"],
            row["dealer_type"],
        )
        identity_counts[identity] = identity_counts.get(identity, 0) + 1
        by_type[row["dealer_type"]] = by_type.get(row["dealer_type"], 0) + 1

    duplicate_identity_rows = sum(count - 1 for count in identity_counts.values() if count > 1)
    return {
        "page_count": page_count,
        "as_of": as_of,
        "rows": raw_rows,
        "skipped": skipped,
        "stats": {
            "raw_rows": len(raw_rows),
            "unique_organizations": len(unique_orgs),
            "duplicate_full_identity_rows": duplicate_identity_rows,
            "organizations_with_multiple_rows": sum(
                1 for count in unique_orgs.values() if count > 1
            ),
            "by_dealer_type": dict(sorted(by_type.items())),
            "skipped_fragments": len(skipped),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    if not args.pdf.is_file():
        print(f"error: PDF not found: {args.pdf}", file=sys.stderr)
        return 2
    pdf_bytes = args.pdf.read_bytes()
    document_sha256 = hashlib.sha256(pdf_bytes).hexdigest()

    extraction = extract_rows(args.pdf)
    payload = {
        "schema": "vkpi.dealer-source-extract/nikon-usa-pdf/v1",
        "claim_status": "descriptive_only",
        "document": {
            "path": str(args.pdf.relative_to(ROOT)) if args.pdf.is_relative_to(ROOT) else str(args.pdf),
            "document_sha256": document_sha256,
            "document_url": DOCUMENT_URL,
            "document_bytes": len(pdf_bytes),
            "page_count": extraction["page_count"],
            "directory_as_of": extraction["as_of"],
            "extracted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "extractor": "scripts/ops/dealer_nikon_pdf_extract.py+pdfplumber",
        },
        "truth_boundaries": {
            "row_proves_street_address": False,
            "row_proves_physical_store": False,
            "row_proves_viltrox_relationship": False,
            "row_scope": "organization_and_city_only",
        },
        "stats": extraction["stats"],
        "rows": extraction["rows"],
        "skipped": extraction["skipped"],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    stats = extraction["stats"]
    print(f"document_sha256={document_sha256}")
    print(f"directory_as_of={extraction['as_of']}")
    print(f"raw_rows={stats['raw_rows']}")
    print(f"unique_organizations={stats['unique_organizations']}")
    print(f"organizations_with_multiple_rows={stats['organizations_with_multiple_rows']}")
    print(f"duplicate_full_identity_rows={stats['duplicate_full_identity_rows']}")
    print(f"by_dealer_type={json.dumps(stats['by_dealer_type'], sort_keys=True)}")
    print(f"skipped_fragments={stats['skipped_fragments']}")
    print(f"out={args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
