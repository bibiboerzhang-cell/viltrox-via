# V-OS Middleware — Phase 1: Party Engine

**Date:** 2026-04-22
**Status:** Day 1 delivery — migration + services + 3 trigger wire-ups + smoke test
**Risk:** Low (all new tables, no changes to existing tables; all wire-ups best-effort)

---

## What this is

A unified customer master key (`party_id`) + identity stitching + append-only event stream, living alongside the existing `users` / `via_sessions` / `orders` tables **without replacing them**.

**Phase 1 only does these things:**

1. Adds 4 new Postgres tables: `parties`, `identity_links`, `consent_records`, `events`.
2. Adds `app/services/party/` module with `email_normalize`, `party_service`, `event_writer`.
3. Wires **3 existing trigger points** to emit events into the new table:
   - Shopify order webhook ingest → `shop.purchase` event (`app/services/commerce/orders.py`)
   - Via session bootstrap → `via.session_started` event (`app/services/via/session_service.py`)
   - Creator video submission → `creator.submission_created` event (`app/api/routers/audit.py`)
4. All three wire-ups are **best-effort and silently skip** when PG is unavailable or the migration hasn't been applied yet. Nothing existing breaks.

**Phase 1 explicitly does NOT do:**

- ❌ ClickHouse fan-out (Phase 2)
- ❌ Stitch on sign-in / scan / verification (Phase 2)
- ❌ Trust / BuyerIntent / CreatorPotential scoring split (Phase 3)
- ❌ Admin UI that reads these tables (Phase 4)
- ❌ Customer 360 workspace (Phase 4)
- ❌ RBAC field-level redaction (Phase 5)
- ❌ Backfill of existing users + submissions into parties (separate task after smoke test passes)

---

## Schema summary

```
parties (party_id UUID PK, lifecycle_stage, is_creator, is_customer, last_activity_at, …)
    └─ 1:N identity_links (party_id, link_type, link_value_hash, confidence, …)
    └─ 1:N consent_records (party_id, consent_type, consent_given, policy_version, …)
    └─ 1:N events (event_id UUID, party_id, event_type, occurred_at, payload JSONB, …)
```

**Design decisions locked 2026-04-22:**

| Decision | Value | Why |
|---|---|---|
| party_id format | UUID v4 | No timestamp leakage |
| Email storage | SHA-256 hash only, dual (raw + normalized) | Gmail dots/plus stitching without losing raw audit trail |
| Gmail normalization | Whitelist: `@gmail.com` + `@googlemail.com` only | Other domains' rules differ; preserve dots for icloud/outlook/etc |
| Consent table | email_normalization_consent default false | GDPR defensible — audit record of what we collapsed |
| Event storage | PG JSONB (Phase 1) → +CH later | Ship now, scale Phase 2 |
| Time index | BRIN on `created_at` / `occurred_at` | 1000× smaller than btree, good for append-only |
| Cross-system unique | `(link_type, link_value_hash)` unique partial index WHERE active | Stitch lookups O(1), allows soft-retirement |

Full schema in `migrations/010_party_layer.sql`.

---

## How to deploy

### Server (prod)

```bash
# 1. Pull new code
cd /opt/viltrox-2.0
git pull   # or copy new files from this zip

# 2. Restart backend — migration 010 auto-applies on first PG connection
systemctl restart viltrox-2.0

# 3. Tail logs to confirm migration ran clean
journalctl -u viltrox-2.0 -f | grep -iE 'migration|party'

# 4. Verify tables exist
psql "$DATABASE_URL" -c "\dt parties; \dt identity_links; \dt events; \dt consent_records;"

# 5. Run smoke test
cd /opt/viltrox-2.0/backend
DATABASE_URL="$DATABASE_URL" DB_RUNTIME_BACKEND=postgres \
    python3 scripts/smoke_events.py
```

Expected smoke-test output: ends with `✓ Phase 1 smoke test PASSED`.

### Rollback (if anything goes sideways)

```bash
psql "$DATABASE_URL" -f migrations/010_party_layer_rollback.sql
```

This drops all 4 new tables + the view. Your existing `users` / `orders` / `submissions` data is untouched.

---

## How to verify it's actually receiving events

After deploy, **without manual intervention**, these 3 things should start writing to `events`:

```sql
-- Should start growing as Shopify webhooks come in
SELECT event_type, COUNT(*), MAX(occurred_at) FROM events
WHERE event_source = 'shopify_webhook' GROUP BY event_type;

-- Should grow whenever anyone opens a Via session
SELECT event_type, COUNT(*), MAX(occurred_at) FROM events
WHERE event_source = 'via_runtime' GROUP BY event_type;

-- Should grow whenever a creator submits a video
SELECT event_type, COUNT(*), MAX(occurred_at) FROM events
WHERE event_source = 'creator_api' GROUP BY event_type;
```

If a column stays at 0 for >24h **after known-good traffic on that surface**, check:

```bash
# Logs with "phase1 party-layer emit failed" — these are non-fatal but indicate wire-up bugs
journalctl -u viltrox-2.0 | grep -i phase1
```

---

## What to do next (Phase 2 preview)

Once Phase 1 is stable for ~1 week:

1. **Backfill existing users into parties** — one-shot script that walks `users` + `submissions` + `orders` and creates matching parties + identity_links. Estimated rows: your sample DB has 2 users, 5 submissions — trivial. On prod with real data this might be 10-100k rows, still sub-minute.
2. **Sign-in stitch** — when anonymous Via session → logged-in user, call `get_or_create_by_email(user.email)` and reassign recent events' `party_id`.
3. **Outbox + CH fan-out** — add `events_outbox` table, small cron worker, ClickHouse double-write.
4. **Trust score extraction** — move `users.trust_score` computation to read from `events` + `submissions`, publish to `party_scores` table.
5. **BuyerIntent + CreatorPotential** — two new score formulas, computed nightly from `events`.

---

## File manifest

### New files (all additive)

```
migrations/
    010_party_layer.sql
    010_party_layer_rollback.sql
backend/app/services/party/
    __init__.py
    email_normalize.py          # gmail whitelist + SHA-256 hashing
    party_service.py            # create_party, get_or_create_by_email, …
    event_writer.py             # write_event + 3 convenience emitters
backend/scripts/
    smoke_events.py             # this test
    README_party_engine_phase1.md   # this doc
```

### Modified files (each a small, contained block; easy to review)

```
backend/app/db/connection.py
    +1 line in _POSTGRES_MIGRATION_SEQUENCE

backend/app/services/commerce/orders.py
    +1 try/except block + 1 helper function after order insert
    (~45 lines added; no existing lines modified)

backend/app/services/via/session_service.py
    +1 try/except block + 1 helper function before return bundle
    (~55 lines added; no existing lines modified)

backend/app/api/routers/audit.py
    +1 try/except block in audit_async + 1 helper function
    (~50 lines added; no existing lines modified)
```

All modifications are **add-only**. No existing lines deleted or rewritten. Git diff will be clean and reviewable.

---

## Questions / issues

Expected issues you might hit in first week:

- **`migration 010_party_layer.sql failed`** — check PG version (need >= 12 for BRIN). Run `SELECT version();`.
- **`events.party_id` nulls accumulating** — expected for anonymous Via sessions before stitch is implemented (Phase 2).
- **`(link_type, link_value_hash) unique violation` on concurrent inserts** — benign race; the existing row wins, the new caller silently no-ops and returns `False`. This is by design.
- **Gmail normalization caught a customer who says "that's not me!"** — this is the risk we locked in. Fix path: add their address to `consent_records.email_normalization_consent = false`, then admin manually retires the normalized link.

If the smoke test fails after deploy, don't panic — existing system still works. File a ticket and roll back migration 010.

---

*End of Phase 1 readme. Next checkpoint: Phase 2 planning after 1 week of production data.*
