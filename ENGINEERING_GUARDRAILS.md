# Viltrox 2.0 Engineering Guardrails

These are repo-level red lines for `viltrox-2.0`. New work must follow them even when older code still carries debt.

## Contract and naming rules

- Public contract values must use **snake_case**.
- Canonical ID naming is `user_id` in payloads, service code, and repository code.
- `uid` is only allowed where an external interface already forces it.
- Shared contract values come from `shared/contracts.json`.
- Frontend must consume the mirrored vocabulary module generated from that file.
- Every external event ingress must have a schema-level unique key.
- For commerce/event pipelines, application-level dedupe is not enough under retries or concurrency.
- New event-backed tables must define idempotency keys in the migration itself before workers consume traffic.

## Event ingestion rules

- Historical replay must default closed.
- Any replay or backfill path must require an explicit cutoff or include-history switch.
- Shopify-style webhook ingestion must protect at least the event row, downstream order row, and payout row with separate unique constraints.

## Async and DB rules

- `async` route handlers must not call `get_conn()` directly.
- Allowed patterns inside async routes:
  - `await db_read(...)`
  - `await db_write(...)`
  - `await asyncio.to_thread(...)`
- Sync helpers may use `get_conn()` internally when they are wrapped by one of those allowed patterns.

## Error and logging rules

- No `print()` in backend runtime code.
- No `except Exception: pass`.
- No `except Exception: return {}` or `return None` as silent fallbacks.
- No raw `str(e)` returned to end users.
- User-facing errors must be generic; logs carry details.

## Cache rules

- Production cache semantics are Redis-first.
- In-process cache is fallback-only and must not define consistency guarantees.
- Any write that changes a cached view must invalidate the matching cache namespace.

## Enforcement

- `scripts/check_repo_hardening.py` is the automated guardrail checker.
- `scripts/verify_repo.sh` is the minimum local verification entrypoint.
- `.github/workflows/verify.yml` runs the same verification in CI.
- `scripts/hardening_allowlist.json` is a temporary debt register for legacy print/silent-exception hotspots and should shrink over time, not grow casually.
