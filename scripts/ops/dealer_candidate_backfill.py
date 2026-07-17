#!/usr/bin/env python3
"""Backfill Nikon PDF directory rows into vkpi_dealer_event_candidates.

Scope / truth boundaries:

* Input is the reviewed extraction snapshot written by
  ``scripts/ops/dealer_nikon_pdf_extract.py`` (organization/city/state only).
* Every row lands as a ``dealer_location`` candidate with the default
  ``review_status='pending'`` and ``promotion_gate_status='blocked'``.  The
  migration-257 gate keeps promotion a separate explicit human workflow; this
  script grants nothing and never touches ``vkpi_dealers``.
* Idempotent: inserts use ``ON CONFLICT DO NOTHING`` against the migration-257
  unique indexes, so reruns only report skips.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from stdout_utils import out as stdout_out  # noqa: E402

DEFAULT_ROWS_JSON = ROOT / "runtime" / "dealer-source-audit" / "20260716" / "nikon_rows.json"
SOURCE_REGISTRY_ID = "nikon_usa_pdf_20260716"
CANDIDATE_TYPE = "dealer_location"


def _stable_key(prefix: str, material: str) -> str:
    """Match reviewed_physical_store_manifest._stable_key exactly (sha256[:24])."""
    digest = hashlib.sha256(material.casefold().encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _slug(value: str, max_length: int = 48) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug[:max_length].strip("-") or "x"


def _source_entity_key(row: dict) -> str:
    """Stable per-row identity key within the Nikon registry snapshot."""
    identity = "|".join(
        (row["organization_name"], row.get("city", ""), row["state"], row.get("dealer_type", ""))
    )
    suffix = hashlib.sha256(identity.casefold().encode("utf-8")).hexdigest()[:8]
    key = ".".join(
        (
            _slug(row["organization_name"]),
            _slug(row.get("city", ""), 24),
            str(row.get("state", "")).lower(),
            suffix,
        )
    )
    return key[:160]


def _candidate_id(source_entity_key: str) -> str:
    material = "|".join((CANDIDATE_TYPE, SOURCE_REGISTRY_ID, source_entity_key))
    return "cand_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def main(argv: list[str] | None = None) -> int:
    global SOURCE_REGISTRY_ID
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows-json", type=Path, default=DEFAULT_ROWS_JSON)
    # R0 参数化(2026-07-17):复用管线到 Canon/Sony 等品牌目录,缺省保持 Nikon 兼容。
    parser.add_argument("--source-registry-id", default=SOURCE_REGISTRY_ID)
    parser.add_argument("--organization-id", type=int, default=1)
    args = parser.parse_args(argv)
    SOURCE_REGISTRY_ID = args.source_registry_id

    if args.organization_id <= 0:
        stdout_out("error: --organization-id must be positive", file=sys.stderr)
        return 2
    try:
        payload = json.loads(args.rows_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        stdout_out(f"error: unreadable rows json: {exc}", file=sys.stderr)
        return 2
    document = payload.get("document") or {}
    rows = payload.get("rows") or []
    document_sha256 = str(document.get("document_sha256") or "")
    document_url = str(document.get("document_url") or "")
    if not re.match(r"^[0-9a-f]{64}$", document_sha256) or not document_url.startswith("https://"):
        stdout_out("error: rows json lacks document_sha256/document_url", file=sys.stderr)
        return 2
    if not rows:
        stdout_out("error: rows json contains no rows", file=sys.stderr)
        return 2

    from app.db.connection import get_conn, is_postgres_runtime

    if not is_postgres_runtime():
        stdout_out("error: candidate backfill requires the PostgreSQL runtime", file=sys.stderr)
        return 2
    conn = get_conn()

    inserted = 0
    skipped = 0
    seen_entity_keys: set[str] = set()
    try:
        for row in rows:
            entity_key = _source_entity_key(row)
            if entity_key in seen_entity_keys:
                skipped += 1
                continue
            seen_entity_keys.add(entity_key)
            candidate_payload = {
                "claim_status": "descriptive_only",
                "document_sha256": document_sha256,
                "document_url": document_url,
                "directory_as_of": str(document.get("directory_as_of") or ""),
                "extractor": str(document.get("extractor") or ""),
                "row": {
                    "row_index": row["row_index"],
                    "page": row.get("page"),
                    "organization_name": row["organization_name"],
                    "city": row.get("city", ""),
                    "state": row["state"],
                    "dealer_type": row.get("dealer_type", ""),
                },
                "row_scope": "organization_and_city_only",
            }
            content_sha256 = hashlib.sha256(
                json.dumps(
                    {"document_sha256": document_sha256, "row": candidate_payload["row"]},
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            location_material = "|".join((row["organization_name"], row.get("city", ""), row["state"]))
            persisted = conn.execute(
                """
                INSERT INTO vkpi_dealer_event_candidates(
                    organization_id, id, candidate_type, source_registry_id,
                    source_entity_key, source_url, stable_org_key,
                    stable_location_key, content_sha256, candidate_payload_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?::jsonb)
                ON CONFLICT DO NOTHING
                RETURNING id
                """,
                (
                    args.organization_id,
                    _candidate_id(entity_key),
                    CANDIDATE_TYPE,
                    SOURCE_REGISTRY_ID,
                    entity_key,
                    document_url,
                    _stable_key("dealer_org", row["organization_name"]),
                    _stable_key("dealer_loc", location_material),
                    content_sha256,
                    json.dumps(
                        candidate_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    ),
                ),
            ).fetchone()
            if persisted:
                inserted += 1
            else:
                skipped += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    total = conn.execute(
        """
        SELECT COUNT(*) AS n FROM vkpi_dealer_event_candidates
        WHERE organization_id=? AND candidate_type=? AND source_registry_id=?
        """,
        (args.organization_id, CANDIDATE_TYPE, SOURCE_REGISTRY_ID),
    ).fetchone()
    stdout_out(f"source_registry_id={SOURCE_REGISTRY_ID}")
    stdout_out(f"rows_in_snapshot={len(rows)}")
    stdout_out(f"inserted={inserted}")
    stdout_out(f"skipped={skipped}")
    stdout_out(f"registry_candidates_total={int(total['n'] if hasattr(total, 'keys') else total[0])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
