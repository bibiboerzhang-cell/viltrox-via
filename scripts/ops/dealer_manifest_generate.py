#!/usr/bin/env python3
"""Generate reviewed-physical-store manifest files from judged Nikon candidates.

Input: ``vkpi_dealer_event_candidates`` rows whose advisory judge verdict is
``physical`` with a complete store record (street address, phone, retailer-own
website and retailer-own store-page evidence URL, none on a manufacturer
domain).  Each selected store gets address-level coordinates from the US
Census geocoder, then lands in one or more manifest files that satisfy
``reviewed_physical_store_manifest.load_manifest`` (schema
``vkpi.reviewed-physical-store-manifest/v1``, max 20 stores per file).

Hard boundaries:

* Zero database writes — this script only reads candidates and writes JSON.
* It never runs ``--publish``; plan/publish stay explicit human actions via
  ``scripts/ops/apply_reviewed_physical_store_manifest.py``.
* A store that the Census geocoder cannot match is dropped and counted, never
  given invented coordinates.
* ``brand_listing_url`` currently mirrors the retailer store page (the judge
  collects no product-listing page); a later enrichment pass may upgrade it.
"""
from __future__ import annotations

import argparse
import json
import string
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from stdout_utils import out as stdout_out  # noqa: E402

SOURCE_REGISTRY_ID = "nikon_usa_pdf_20260716"
MANIFEST_SCHEMA = "vkpi.reviewed-physical-store-manifest/v1"
MAX_STORES_PER_FILE = 20
MANUFACTURER_HOSTS = ("nikonusa.com", "nikon.com", "canon.com", "sony.com", "godox.com")
DEFAULT_OUT_DIR = BACKEND / "app" / "domains" / "commerce"

TRUTH_BOUNDARIES = {
    "canonical_address_source": "retailer_owned_store_page",
    "coordinate_source": "us_census_geocoder_address_match",
    "google_places_is_canonical": False,
    "manufacturer_directory_proves_branch_inventory": False,
    "manufacturer_directory_proves_viltrox_authorization": False,
    "product_page_proves_current_inventory": False,
    "store_facts_source": "gemini_google_search_advisory_judge_pending_human_review",
}


def _host(url: str) -> str:
    return str(urlsplit(str(url or "").strip()).hostname or "").casefold()


def _retailer_host(website_url: str) -> str:
    host = _host(website_url)
    return host[4:] if host.startswith("www.") else host


def _is_manufacturer(host: str) -> bool:
    return any(host == item or host.endswith("." + item) for item in MANUFACTURER_HOSTS)


def _on_host(url: str, expected_host: str) -> bool:
    host = _host(url)
    return bool(expected_host) and (host == expected_host or host.endswith("." + expected_host))


def _store_complete(store: dict[str, Any], expected_host: str) -> bool:
    return bool(
        str(store.get("name") or "").strip()
        and str(store.get("address") or "").strip()
        and str(store.get("city") or "").strip()
        and len(str(store.get("state") or "").strip()) == 2
        and len(str(store.get("phone") or "").strip()) >= 7
        and str(store.get("store_page_url") or "").startswith("https://")
        and _on_host(str(store.get("store_page_url") or ""), expected_host)
        and not _is_manufacturer(_host(str(store.get("store_page_url") or "")))
    )


def _source_id(expected_host: str) -> str:
    stem = expected_host.split(".")[0] if expected_host else "retailer"
    cleaned = "".join(ch for ch in stem if ch in string.ascii_lowercase + string.digits)
    return f"{cleaned or 'retailer'}_store_page"


_FILE_TAG = "nikon"


def _chunk_paths(out_dir: Path, chunk_count: int) -> list[Path]:
    if chunk_count <= 1:
        return [out_dir / f"reviewed_physical_stores_us_v2_{_FILE_TAG}.json"]
    return [
        out_dir / f"reviewed_physical_stores_us_v2{string.ascii_lowercase[index]}_{_FILE_TAG}.json"
        for index in range(chunk_count)
    ]


def main(argv: list[str] | None = None) -> int:
    global SOURCE_REGISTRY_ID, _FILE_TAG
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization-id", type=int, default=1)
    parser.add_argument("--source-registry-id", default=SOURCE_REGISTRY_ID)
    parser.add_argument("--brand-key", default="nikon")
    parser.add_argument("--file-tag", default="nikon")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--geocode-sleep", type=float, default=0.5)
    args = parser.parse_args(argv)
    SOURCE_REGISTRY_ID = args.source_registry_id
    _FILE_TAG = args.file_tag

    from app.db.connection import get_conn, is_postgres_runtime
    from app.domains.commerce import reviewed_physical_store_manifest as reviewed
    from app.domains.commerce.census_geocode import geocode_match

    if not is_postgres_runtime():
        stdout_out("error: manifest generation requires the PostgreSQL runtime", file=sys.stderr)
        return 2
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT id, candidate_payload_json
        FROM vkpi_dealer_event_candidates
        WHERE organization_id=? AND candidate_type='dealer_location'
          AND source_registry_id=?
          AND candidate_payload_json->'judge'->>'verdict' = 'physical'
        ORDER BY source_entity_key
        """,
        (args.organization_id, SOURCE_REGISTRY_ID),
    ).fetchall()

    counters = {
        "physical_candidates": len(rows),
        "candidates_missing_website": 0,
        "stores_seen": 0,
        "stores_incomplete": 0,
        "stores_duplicate_identity": 0,
        "geocode_attempted": 0,
        "geocode_hits": 0,
        "geocode_misses": 0,
    }
    manifest_stores: list[dict[str, Any]] = []
    identities: set[tuple[str, str]] = set()
    for row in rows:
        payload_raw = row["candidate_payload_json"] if hasattr(row, "keys") else row[1]
        payload = payload_raw if isinstance(payload_raw, dict) else json.loads(payload_raw or "{}")
        judge = payload.get("judge") or {}
        source_row = payload.get("row") or {}
        website_url = str(judge.get("retailer_website") or "").strip()
        expected_host = _retailer_host(website_url)
        if (
            not website_url.startswith("https://")
            or not expected_host
            or _is_manufacturer(expected_host)
        ):
            counters["candidates_missing_website"] += 1
            continue
        for store in judge.get("stores") or []:
            counters["stores_seen"] += 1
            if not isinstance(store, dict) or not _store_complete(store, expected_host):
                counters["stores_incomplete"] += 1
                continue
            organization_name = str(source_row.get("organization_name") or "").strip()
            store_name = str(store.get("name") or "").strip() or organization_name
            city = str(store.get("city") or "").strip()
            display_name = f"{store_name} · {city}"
            address = str(store.get("address") or "").strip()
            identity = (display_name.casefold(), address.casefold())
            if identity in identities:
                counters["stores_duplicate_identity"] += 1
                continue
            state = str(store.get("state") or "").strip().upper()
            postal_code = str(store.get("postal_code") or "").strip()
            oneline = ", ".join(part for part in (address, city, state) if part)
            if postal_code:
                oneline = f"{oneline} {postal_code}"
            counters["geocode_attempted"] += 1
            match = geocode_match(oneline)
            time.sleep(max(0.0, args.geocode_sleep))
            if match is None:
                counters["geocode_misses"] += 1
                continue
            counters["geocode_hits"] += 1
            identities.add(identity)
            store_page_url = str(store.get("store_page_url") or "").strip()
            manifest_stores.append(
                {
                    "source_id": _source_id(expected_host),
                    "organization_key_material": organization_name,
                    "name": display_name,
                    "address": address,
                    "city": city,
                    "state": state,
                    "postal_code": postal_code,
                    "phone": str(store.get("phone") or "").strip(),
                    "website_url": website_url,
                    "retailer_host": expected_host,
                    "location_source_url": store_page_url,
                    "brand_listing_url": store_page_url,
                    "observed_at": str(judge.get("judged_at") or "").strip(),
                    "coordinates": {
                        "provider": "us_census_geocoder",
                        "match_level": "exact_address",
                        "value_status": "observed",
                        "lat": match["lat"],
                        "lng": match["lng"],
                    },
                    "brand_relationships": [
                        {
                            "brand_key": args.brand_key,
                            "relationship_status": "official_directory_listed",
                            "authorization_status": "unverified",
                            "evidence_scope": "organization_and_city_only",
                            "evidence_url": str(payload.get("document_url") or "").strip(),
                        }
                    ],
                }
            )

    manifest_stores.sort(key=lambda item: (item["state"], item["city"], item["name"]))
    chunks = [
        manifest_stores[index : index + MAX_STORES_PER_FILE]
        for index in range(0, len(manifest_stores), MAX_STORES_PER_FILE)
    ]
    paths = _chunk_paths(args.out_dir, len(chunks))
    written: list[str] = []
    for path, chunk in zip(paths, chunks, strict=True):
        manifest = {
            "schema": MANIFEST_SCHEMA,
            "claim_status": "descriptive_only",
            "google_places_status": "pending",
            "source_registry_id": SOURCE_REGISTRY_ID,
            "truth_boundaries": dict(TRUTH_BOUNDARIES),
            "stores": chunk,
        }
        path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        reviewed.load_manifest(path)  # schema self-check; raises on violation
        written.append(str(path))

    stdout_out(json.dumps(counters, indent=2, sort_keys=True))
    stdout_out(f"manifest_stores={len(manifest_stores)} files={len(written)}")
    for path in written:
        stdout_out(f"manifest_file={path}")
    stdout_out("summary (state / city / name / phone):")
    for store in manifest_stores:
        stdout_out(
            f"  {store['state']} | {store['city']} | {store['name']} | {store['phone']}"
        )
    if not written:
        stdout_out("no stores qualified; nothing written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
