# P4.60 AI Brief v0

Date: 2026-05-23

## Scope

AI Brief v0 is a read-only brief assembled from existing Intelligence Card and
Evidence Summary rows. It does not call an LLM and does not create new facts.

## Rules

- Every `brief_item` must include at least one `evidence_ref`.
- Every `next_action` must include at least one `evidence_ref`.
- No Gemini preflight, batch dry-run, or budget plan may appear as completed
  evidence.
- Empty sections may appear as coverage gaps only when backed by a section ref.
- Unsupported recommendations are omitted instead of filled with generic advice.
- `provider_calls=false`, `llm_calls=false`, and `write_db=false`.

## API

`GET /api/admin/vkpi/kol-pool/{kol_pool_id}/ai-brief`

The response includes:

- `headline`
- `brief_items`
- `next_actions`
- `evidence_backlinks`
- `source_summary`
- `checks`

## Acceptance

Run:

```bash
.venv/bin/python scripts/vkpi_ai_brief_acceptance.py --kol-pool-id 4217 --json
```

Expected:

- `passed=true`;
- `brief_item_count > 0`;
- every brief item and next action has evidence refs;
- no provider, LLM, sync, task, or DB write;
- no unsupported recommendations.

## Future

A future LLM-written brief may summarize the same evidence refs only after the
LLM gateway budget and provider ledger gates are reviewed. It must keep evidence
backlinks on every generated statement.
