# ADR: Intelligence Card Evidence Drawer Contract

Date: 2026-05-23

## Context

P2 is moving from summary cards to explainable decisions:

- the single KOL Intelligence Card API exists;
- 11D confidence is visible in the KOL Pool drawer;
- Memory Card v0 surfaces legacy/Excel history, recent cached posts, and competitor memory;
- Product Fit, competitor relation, Brand Signal, and future comment/video intelligence all need a shared drilldown shape.

The existing dashboard already has a metric Evidence Drawer backed by lineage rows. KOL decision evidence should reuse the same principle: a visible score or claim must be traceable to a concrete row, cached post, legacy record, or deterministic rule output. Missing evidence must stay missing.

## Decision

Every Intelligence Card section that can drive a staff decision exposes two layers:

1. `evidence_index`: lightweight root index for the drawer tabs.
2. Section-local `evidence` or source rows for the selected tab.

The root index keeps navigation cheap:

```json
{
  "section": "product_fit",
  "label": "Product Fit",
  "status": "ready",
  "source": "vkpi_memory_entities/product families",
  "evidence_count": 4,
  "freshness_hours": 22,
  "confidence": 0.76
}
```

The drawer opens a section and renders normalized evidence leaves:

```json
{
  "evidence_id": "ev_rule_engine_kol_pool_1503_dimensions11_product_fit",
  "source": "rule_engine",
  "source_table": "vkpi_kol_pool",
  "source_id": 1503,
  "source_url": "https://youtube.com/@creator",
  "captured_at": "2026-05-23T08:00:00Z",
  "freshness_hours": 6,
  "confidence": 0.72,
  "confidence_method": "rule_v0",
  "reasoning": "近期内容和历史标签同时指向摄影镜头评测。",
  "raw_data_ref": "vkpi_kol_pool:1503:raw_platform_data",
  "rebuttal_supported": true
}
```

## Source Enum

Allowed `source` values:

- `cooperation_history`
- `competitor_signal`
- `brand_signal`
- `comment_sample`
- `excel_legacy`
- `platform_cache`
- `rule_engine`
- `official_catalog`
- `llm_inference`
- `video_analysis`

`llm_inference` and `video_analysis` remain inactive until the controlled LLM/Gemini phases. They are reserved names, not permission to run providers.

## Section Contracts

### 11D Confidence

Section key: `dimensions11`

Evidence leaves come from deterministic scoring inputs:

- `source=rule_engine`
- `source_table=vkpi_kol_pool`
- `source_id=kol_pool_id`
- `confidence_method=rule_v0`
- one leaf per block when evidence exists:
  - `block1_content`
  - `block2_performance`
  - `block3_business`
  - `block4_specialty`

If a block confidence is `0`, the drawer shows the block as `no evidence` and does not create a fake evidence leaf.

### Memory Card

Section key: `memory_card`

Evidence leaves come from existing historical sources:

- `source=excel_legacy` for `source_type/source_ref`, profile notes, recommended products, or concerns.
- `source=cooperation_history` for known cooperation rows or `brand_collaborations_json`.
- `source=platform_cache` for cached recent posts.
- `source=competitor_signal` for competitor memory reused from the competitor section.

The drawer must distinguish "history exists" from "recent Viltrox cooperation exists". A legacy row alone is not proof of recent collaboration.

### Competitors

Section key: `competitors`

Evidence leaves come from `vkpi_competitor_relation` when persisted, otherwise cached post detection:

- `source=competitor_signal`
- `source_table=vkpi_competitor_relation` when row-backed
- `source_table=vkpi_kol_pool` when derived from cached raw posts
- `confidence_method=rule_v0`

Risk tiers remain explicit: `avoid`, `caution`, `safe`, `opportunity`, or `unknown`.

### Brand Signal

Section key: `brand_signal`

Evidence leaves come from cached post fields and local signal detection:

- `source=brand_signal`
- `source_table=vkpi_kol_pool`
- `source_id=kol_pool_id`
- `source_url` is the post URL when available

The drawer must show signal type and brand role. It must not imply full comment or full history coverage.

### Product Fit

Section key: `product_fit`

Evidence leaves must identify why a KOL maps to a product or SKU:

- `source=official_catalog` for SKU/card/mount/spec/price facts.
- `source=rule_engine` for 11D product-fit match and adjacent category scoring.
- `source=cooperation_history` for prior cooperation evidence.
- `source=competitor_signal` for risk modifiers.

Product-family-only rows can be shown as low-confidence discovery evidence, but the UI cannot call SKU-level Product Fit complete unless official catalog fields are present:

- SKU or product key
- mount/card applicability where relevant
- price/spec source when available
- rule contribution and confidence

### Comment Intelligence

Section key: `comment_intelligence`

This is future-facing for P2/P1 comment contract integration:

- `source=comment_sample`
- must carry `declared`, `cached`, `cap`, and `status`
- no sentiment summary is shown unless it names the cached sample count used

## Drawer UI Rules

- Hidden if the section has `status=skipped`.
- Greyed if `status=empty` or confidence is `0`.
- Mark stale when `freshness_hours > 168`.
- Show `Open original` only when `source_url` exists.
- Show "cached sample" wording for platform-cache and comment evidence.
- Show "official catalog" wording only for SKU/spec rows backed by catalog data.
- Do not show provider, LLM, or Gemini badges unless the evidence leaf says the call already happened and names the stored row.

## API Rules

- `evidence_index` is a navigation summary, not the source of truth.
- Each section owns its detailed evidence rows.
- Section evidence must be read-only in P2.
- `provider_calls`, `llm_calls`, and `write_db` remain false for card assembly.
- Missing tables or optional modules return `status=unavailable` with an error reason; they do not silently become empty evidence.

## Not In This ADR

- No new Apify/Gemini/LLM run.
- No migration.
- No worker queue or websocket.
- No full legacy KOL refresh.
- No Product Fit scoring change.

## Acceptance

- Intelligence Card root keeps `evidence_index` for `freshness`, `dimensions11`, `competitors`, `brand_signal`, `memory_card`, and `product_fit`.
- Evidence Drawer v0 renders one section at a time from the section-local evidence rows.
- Product Fit drawer differentiates official SKU evidence from product-family discovery evidence.
- Competitor drawer renders risk tier and original/cached evidence.
- 11D drawer greys blocks with `confidence=0` instead of showing default explanations.
- The implementation can be tested with cached KOL Pool data and must not require provider credentials.
