# V-KPI P7.83 Brief Agent v0

P7.83 adds a read-only Brief Agent. It consumes P7.82 Recommendation Agent
candidates and turns them into an operator checklist with evidence refs.

It does not write notifications, create tasks, trigger outreach, persist
recommendations, trigger sync, call providers, or call LLMs.

## Inputs

The agent reads one of these read-only sources:

- explicit `kol_pool_ids`, which rebuilds P7.82 read-only candidates for those IDs
- latest `runtime/ops/*p7-82-recommendation-agent-v0.json`
- a fresh read-only P7.82 build when no artifact exists

## Output

The report includes:

- `brief_items`: candidate summaries, evidence gaps, and risk notes
- `next_actions`: human review actions only
- `evidence_backlinks`: deduplicated refs used by the brief
- source and policy metadata

Every brief item and next action must include evidence refs. Untraceable content
is dropped or blocked instead of being turned into advice.

## Acceptance

```bash
PYTHONPATH=backend .venv/bin/python scripts/vkpi_brief_agent_v0.py \
  --ops-dir runtime/ops \
  --limit 8 \
  --min-evidence-refs 3 \
  --ref-limit 8 \
  --claim-limit 12 \
  --json-out runtime/ops/p7-83-brief-agent-v0.json \
  --md-out runtime/ops/p7-83-brief-agent-v0.md
```

The report passes when:

- a recommendation source is available
- brief items and next actions are traceable or the no-target state is explicit
- no generated fact, notification, task, outreach, sync, provider, LLM, or write
  side effect appears

## API

```http
GET /api/admin/vkpi/industry-data/brief-agent/v0?limit=8
GET /api/admin/vkpi/industry-data/brief-agent/v0?kol_pool_ids=123,456
```

The API is read-only and requires VKPI read permission.
