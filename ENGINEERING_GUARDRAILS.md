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

## Domain OS and file-size rules

- Every large-file split must first answer which domain owns the extracted code. If the owner cannot be named, do not split yet.
- New V-KPI feature code must land in an explicit domain, platform, or shared module.
- Do not add new business behavior to oversized legacy files except as part of an extraction.
- Do not create new legacy helper modules under `frontend/src/components/vkpi/*` or `backend/app/services/vkpi/*` when a domain, platform, or shared owner exists.
- Non-exempt business source files must stay at or below 800 lines.
- Target source file size is 300-600 lines; 800 lines is a hard ceiling, not a goal.
- Pages and routers are composition shells. Business behavior belongs in domain services/components.
- Provider calls must flow through platform/provider and domain ingest layers before reaching dashboard or intelligence UI.
- Domain migration progress is counted only when real business code lands under `backend/app/domains/*` or `frontend/src/domains/*`; README, `__init__`, and `index` skeletons do not count as business migration.
- Repair Center v0 is frozen and must not become the main product execution path.
- Repair Center files accept only bug fixes, deletions, and extraction work. Do not add new Repair stages, new persistence flows, or R102+ expansion work until an intelligence-layer business output has completed end to end.
- Domain PoC work must be isolated from unrelated legacy cleanup so the diff can be reviewed on its own.

## Enforcement

- `scripts/check_repo_hardening.py` is the automated guardrail checker.
- `scripts/check_line_guard.py` is the automated line-size checker for Domain OS migration.
- `scripts/verify_repo.sh` is the minimum local verification entrypoint.
- `.github/workflows/verify.yml` runs the same verification in CI.
- `scripts/hardening_allowlist.json` is a temporary debt register for legacy print/silent-exception hotspots and should shrink over time, not grow casually.
