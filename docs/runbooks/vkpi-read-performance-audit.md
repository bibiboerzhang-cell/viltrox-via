# V-KPI GTM / Dashboard read performance audit

`scripts/ops/audit_vkpi_read_performance.py` is the bounded HTTP verifier for
the two reviewed summary reads:

- `GET /api/admin/vkpi/market-brain/summary`
- `GET /api/admin/vkpi/dashboard?window_days=30&scope=all`

It has no cache-clear/delete operation, never emits the bearer token or
response body, and cannot call an arbitrary route. The reviewed routes are DB
read aggregations; neither route owns provider, LLM, collection, queue, or
business-write work. This is a source contract, not runtime provider-call
instrumentation, and must be re-reviewed if either route starts delegating new
work.

## Evidence levels

### Non-destructive warm observation

`warm-observe` is the only mode allowed against a deployed service. The first
read is a warm-up/observation request; every measured request after it must be
an exact application-cache hit with an identical response digest. This mode
does not claim that the first request was cold and does not report a
cold-to-warm speedup. Any non-loopback target must use HTTPS; the auditor
refuses plain HTTP before loading or sending a bearer token.

```bash
VKPI_PERF_AUDIT_TOKEN_FILE=/absolute/private/token-file
.venv/bin/python scripts/ops/audit_vkpi_read_performance.py \
  --base-url https://viltroxtest.com \
  --mode warm-observe \
  --token-file "${VKPI_PERF_AUDIT_TOKEN_FILE}" \
  --warm-samples 5 \
  --warm-p95-max-ms 500 \
  --json-out runtime/ops/reviews/vkpi-read-performance-warm.json
```

This can populate an ordinary application read cache on a miss. It never
clears or deletes an existing cache entry and never writes business data.

### Strict local cold-to-warm proof

`strict-local-cold-warm` refuses non-loopback URLs. It also requires an
explicit operator attestation that all of these conditions are already true:

1. the candidate is disposable and bound only to loopback;
2. its database transaction/runtime is read-only;
3. all provider credentials and provider-capable background work are disabled;
4. its application cache is isolated and initially empty.

The verifier then requires the first request to expose `miss_builder` plus a
builder timing, and every following request to expose `hit` with no builder
timing. It does not manufacture a cold state and has no cache mutation endpoint.

```bash
.venv/bin/python scripts/ops/audit_vkpi_read_performance.py \
  --base-url http://127.0.0.1:18081 \
  --mode strict-local-cold-warm \
  --allow-unauthenticated-loopback \
  --confirm-isolated-readonly-runtime \
  --warm-samples 5 \
  --cold-max-ms 2000 \
  --warm-p95-max-ms 500 \
  --min-speedup 2 \
  --json-out runtime/ops/reviews/vkpi-read-performance-strict-local.json
```

The confirmation flag is an attestation, not independent runtime proof. Save
the disposable candidate launch evidence next to the JSON result.

## Why the normal isolated release candidate is not a strict-cold harness

`scripts/ops/run_isolated_candidate_web.sh` correctly enables the
release-validation fence and strips provider credentials. The same fence
deliberately disables application-cache mutation. On an empty cache,
`cache_get_or_build` therefore returns `fenced_builder` on every request: the
first read cannot populate the cache, so the second read cannot prove a hit.

That candidate remains valid for read-only acceptance and existing-cache
observation. It must not be described as a strict cold-to-warm benchmark. Do
not weaken its fence merely to produce a performance number. Use either a
disposable local integration fixture with an isolated in-memory cache, or a
separately reviewed candidate whose DB is independently read-only while only
its disposable cache can be written.

## Required observability contract

Each audited response must include:

- `X-VKPI-Cache`: `miss_builder` for the strict first read, then `hit`;
- `X-VKPI-Cache-Builder`: `1` for the builder, then `0`;
- `X-VKPI-Cache-Key-Version`: a bounded schema token;
- `Server-Timing`: `gtm-cache` / `gtm-builder` for GTM, or
  `dashboard-cache` / `dashboard-builder` for Dashboard.

The timing metric is surface-specific. A Dashboard header cannot satisfy a GTM
check, or vice versa. Missing headers are a failed observability gate, not a
performance pass.

## Existing verifier boundary

- `scripts/local_release_acceptance.py` records endpoint latency but treats the
  current threshold as a warning and does not prove application-cache state or
  inspect these cache headers.
- `scripts/ops/benchmark_vkpi_perf.sh` invokes Python builders directly. It is
  useful for comparison but is not an authenticated real-HTTP cold/warm proof,
  and it does not independently enforce DB read-only state.
- GTM already emits the required cache headers through
  `app.domains.market_brain.read_cache`. Dashboard must expose the equivalent
  contract before it can pass this audit.

These tools remain useful for their original purposes; their results must not
be relabeled as strict cold-to-warm acceptance.
