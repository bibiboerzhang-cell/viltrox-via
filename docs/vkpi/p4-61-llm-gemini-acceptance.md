# P4.61 LLM/Gemini Phase Acceptance

Date: 2026-05-23

## Scope

P4.61 is the second go/no-go gate for the controlled LLM/Gemini phase.

It aggregates existing read-only reports:

- LLM gateway budget acceptance;
- Gemini single-KOL go/no-go;
- Gemini batch-30 dry-run;
- AI Brief v0 acceptance.

It does not call Gemini, LLM providers, Apify, sync jobs, task queues, or write
database rows.

## Decision Semantics

`passed=true` means the acceptance report itself is safe and traceable.

It does not mean Gemini live or batch execution is approved.

The business decision is recorded separately:

- `evidence_only`: may continue when AI Brief items and next actions are
  traceable to evidence refs.
- `single_live_gemini`: may only move to one paid manual call when the candidate
  and provider budget gates are ready and an operator explicitly approves it.
- `batch_gemini`: remains on hold until one live result is reviewed.
- `final`: summarizes what can proceed and what stays blocked.

## Acceptance

Run:

```bash
.venv/bin/python scripts/vkpi_llm_gemini_acceptance.py \
  --kol-pool-id 4217 \
  --kol-pool-ids 4217 \
  --json
```

Expected:

- no provider calls;
- no LLM calls;
- no DB writes;
- no sync or task enqueue;
- AI Brief can continue only with evidence refs;
- Gemini batch stays blocked;
- final decision is recorded.

## Current Operating Rule

Until the final decision says otherwise:

- continue evidence-only AI surfaces;
- do not run Gemini batch;
- do not run a paid single-KOL call without explicit operator approval;
- do not create a batch executor.
