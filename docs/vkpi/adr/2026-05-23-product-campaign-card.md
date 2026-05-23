# ADR: Product Campaign Card Is Read-Only Planning

- Date: 2026-05-23
- Status: Accepted
- Scope: V-KPI P5.70

## Context

The product/SKU/market layer now has alias facts, spec facts, KOL x SKU fit, and market intelligence v0. The next useful surface is a compact campaign planning card for choosing a product, KOL candidates, and risk notes.

## Decision

Build P5.70 as a read-only card. It reads existing database facts and returns evidence-backed candidates and risks, but does not write operational records.

## Consequences

Campaign planning becomes possible without jumping to automation. Human approval is still required before creating campaigns, outreach, or recommendation outputs.
