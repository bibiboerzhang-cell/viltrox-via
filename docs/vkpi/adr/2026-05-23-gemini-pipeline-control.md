# ADR: Gemini Pipeline Control Before Any Batch

Decision date: 2026-05-23

## Context

P4.55 added a read-only Gemini single-KOL preflight and a controlled live-run
harness. P4.56 added a go/no-go report. The current production state is still
`hold` unless a staff operator explicitly enables the required budget/provider
gates and approves one paid KOL test.

The next question is how a future Gemini pipeline should work without drifting
into uncontrolled batch spend.

## Decision

Do not create a Gemini batch executor yet.

P4.57 defines the control policy only:

- Batch execution remains disabled by default.
- A paid run must start with exactly one KOL.
- A 30-KOL small batch can be designed only after the one-KOL paid result is
  reviewed for cost, latency, returned fields, and evidence quality.
- No Gemini batch is connected to a timer, search flow, sync flow, or task queue.
- All future execution commands must require explicit operator flags.

## Pipeline Limits

Initial future small-batch policy:

| Control | Value |
|---|---:|
| first paid live test | 1 KOL |
| future small-batch max | 30 KOL |
| initial concurrency | 1 |
| hard max concurrency | 2 |
| minimum delay between starts | 60 seconds |
| per-KOL max retries | 2 |
| retry backoff | 15 minutes, 60 minutes, then stop |
| stop if errors in one batch | 3 |
| stop if error rate | 20% |
| stop on budget warning/hard stop | yes |

## Retry Policy

Retryable:

- transient network/provider timeout
- provider 5xx
- File API temporary processing failure

Not retryable:

- no cached video candidate
- invalid or unsupported URL
- provider not configured
- budget hard stop
- quota/rate-limit hard stop until the next operator review
- malformed model configuration

Every retry must keep the same `kol_pool_id`, video URL, attempt number, error,
latency, and estimated cost metadata. Retry does not write business analysis
fields until an operator explicitly approves the persistence contract.

## Budget Scopes

The existing first-test scope is:

- `cron:p4_gemini_single_kol`

Future batch work must add a separate scope before any executor exists:

- `cron:p4_gemini_batch_30`

Batch work must still pass:

- `monthly_total`
- `single_call`
- `provider:gemini`
- task scope

## Acceptance

P4.57 is complete when:

- This ADR exists.
- A read-only pipeline readiness report exists.
- The report shows provider/LLM calls are false.
- Batch execution is false.
- Max batch size is capped at 30.
- Concurrency is capped at 2.
- Retry and stop rules are visible.
- The report can evaluate sample KOL go/no-go state without calling providers.

## Non-Goals

P4.57 must not:

- call Gemini
- add a batch executor
- enqueue async tasks
- write Gemini analysis into business tables
- change timers
- enable provider budget gates
- run a 30-KOL batch
