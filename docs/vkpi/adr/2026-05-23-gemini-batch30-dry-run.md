# ADR: Gemini Batch-30 Dry Run Before Any Batch Executor

Date: 2026-05-23

## Status

Accepted as a control gate.

## Context

P4.55 added a controlled single-KOL Gemini harness. P4.56 added the read-only
go/no-go report. P4.57 defined the future pipeline controls and kept batch
execution disabled.

P4.58 is named "30 KOL small-batch deep scan" in the roadmap, but a real batch
would be premature until one paid single-KOL result has been reviewed for cost,
latency, returned fields, and evidence quality.

## Decision

P4.58 is implemented as a dry-run readiness report only.

The report may:

- select up to 30 candidate `vkpi_kol_pool` rows from explicit IDs or natural
  search;
- call the existing read-only P4.56 go/no-go builder for each candidate;
- group selected IDs into planning windows;
- count decisions and blockers;
- verify that no provider, LLM, sync, task, Apify, or DB-write path was used.

The report must not:

- call Gemini;
- call Apify or refresh source data;
- enqueue tasks;
- run sync;
- write business data;
- write provider ledger rows;
- include executable batch commands;
- create or expose a batch executor.

## Controls

| Control | Value |
|---|---:|
| max target KOL | 30 |
| default planning window | 5 |
| max planning window | 5 |
| initial concurrency | 1 |
| hard max concurrency | 2 |
| minimum delay between future starts | 60 seconds |
| stop if errors >= | 3 |
| stop if error rate >= | 20% |
| retries per KOL | 2 |

The future batch executor remains blocked by:

- one reviewed paid single-KOL result;
- explicit operator approval;
- Gemini provider configuration;
- monthly and single-call budget gates;
- a separate `cron:p4_gemini_batch_30_future` budget scope.

## Rationale

The dry-run gives enough operational evidence to plan a batch without spending
provider budget. It also keeps the roadmap honest: P4.58 can produce a useful
artifact now, while the paid batch remains gated by the single-KOL review.

## Acceptance

Run:

```bash
.venv/bin/python scripts/vkpi_gemini_batch30_dry_run.py \
  --kol-pool-ids 4217 \
  --target-size 999 \
  --window-size 99 \
  --requested-concurrency 9 \
  --json
```

Expected:

- `passed=true`;
- `provider_calls=false`;
- `llm_calls=false`;
- `write_db=false`;
- `sync_triggered=false`;
- `task_enqueued=false`;
- `batch_execution_allowed=false`;
- `effective_target_size=30`;
- `effective_window_size=5`;
- `effective_concurrency=2`;
- readiness is blocked until provider/budget gates and one reviewed live result
  are available.

## Rollback

Remove the dry-run script and ADR. No runtime executor, timer, migration, or
business data is affected.
