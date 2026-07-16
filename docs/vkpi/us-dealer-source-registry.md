# US Dealer source registry

This registry is a source-identity control plane, not a dealer database and not
a crawler allow-list.  Its intended grain is one publisher-owned discovery or
official-ingest entry point.  It does not contain or authorize dealer rows.

## Truth boundary

- Manufacturer authorization proves only the named manufacturer's relationship.
- It never proves Viltrox authorization, Viltrox product presence, inventory,
  sales, local influence, or completeness of US coverage.
- Every dealer source defaults to `enabled=false`, `status=awaiting_review`,
  `terms_robots_status=pending_review`, and direct import disabled.
- A format fixture proves parser shape only.  It is not a reviewed live-source
  snapshot and is recorded as `format_fixture_only_not_source_verified`.
- No source may leave `awaiting_review` until terms/robots review, a dated source
  fixture, reviewer identity, timestamp and source passport exist.

## Registered manufacturer discovery surfaces

The registry currently identifies official public entry points for Nikon,
Canon, Sony, Fujifilm, Panasonic/LUMIX, OM SYSTEM, Leica, Blackmagic Design,
Tamron Americas, SIGMA, Hasselblad, Profoto and Phase One.  The exact publisher,
canonical URL, manufacturer scope, channel and fixture state live in
`backend/app/domains/events/us_coverage_source_registry.json`.

The source identity review fixture at
`tests/fixtures/us_dealer_source_registry/official_source_identity_review.json`
contains no dealer, contact, inventory or network-capture rows.  It only locks
the publisher/canonical identity reviewed on 2026-07-15.

## Viltrox official inputs

There is no inferred public Viltrox US dealer universe.  Two disabled slots are
registered instead:

1. `dealer_viltrox_us_company_feed` waits for a Viltrox-owned CSV/API/private
   master feed plus its authorization receipt and data contract.
2. `dealer_viltrox_us_manual_official_entry` waits for an authenticated Viltrox
   employee to submit exact dealer facts with field evidence and a review
   receipt.

The public Viltrox homepage or contact page proves publisher identity only.  It
does not prove that a company feed exists or that any retailer is authorized.

## Activation checklist

1. Confirm the exact publisher URL and intended US/product scope.
2. Record terms and robots review, reviewer ID and reviewed timestamp.
3. Capture a bounded dated fixture without credentials or unrelated personal
   data; store its SHA-256 and extractor version.
4. Run registry and offline adapter tests.
5. Review candidates individually and preserve source passports.
6. Keep automatic business-table promotion unavailable.

Until every step is complete, the correct state is `awaiting_review` and zero
dealer candidates are imported.

## 2026-07-15 technical preflight

Run the bounded, read-only transport check with:

```bash
PYTHONPATH=backend .venv/bin/python scripts/audit_vkpi_dealer_source_preflight.py \
  --output runtime/ops/dealer-source-technical-preflight-20260715.json
```

The current artifact covers all 34 registered discovery sources.  It records
28 reachable pages, four HTTP failures (two `403`, two `429`) and two sources
blocked by the robots gate.  Twenty-one reachable snapshots exposed same-host
terms or privacy link candidates.  The artifact SHA-256 is
`54a838a553210eea88fbae63c5a7b7e69cb987f411519067a12ee4ef7c7215a2`.

This check deliberately leaves legal approval, source activation, candidate
extraction and business-table writes at zero.  A reachable page and a content
hash are transport evidence only; they are not terms approval and do not prove
any Dealer entity, manufacturer relationship, Viltrox authorization, product
presence, inventory, sales, ROI or local impact.  The four HTTP failures require
an approved feed, a publisher-provided export, or a separately reviewed access
method.  The two robots-gated paths must not be fetched by this pipeline.

## Dealer to Event Radar projection

The Dealer map reads activities only through the durable exact
`vkpi_event_opportunity_dealers` relationship.  It never guesses a match from
names, cities or free text.  Linked activities remain hidden while their Event
Radar source is disabled; the API reports the suppressed exact-link count so
the UI can distinguish `no relation` from `relation pending source activation`.
