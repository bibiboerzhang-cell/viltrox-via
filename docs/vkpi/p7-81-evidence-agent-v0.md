# V-KPI P7.81 Evidence Agent v0

P7.81 adds a read-only Evidence Agent. It organizes existing KOL evidence into
traceable chains for later recommendation and brief agents.

It does not generate facts, write rows, create alerts, enqueue tasks, trigger
sync, call providers, or call LLMs.

## Inputs

Target KOLs come from one of two sources:

- explicit `kol_pool_ids`
- latest `runtime/ops/*p6-77-weekly-action-plan-v0.json`

Evidence comes from the existing Intelligence Card and Evidence Summary
contracts:

- freshness
- 11D dimensions
- competitor relation
- brand signal
- comment intelligence
- video analysis
- memory card
- product fit

## Output

Each chain includes:

- KOL identity
- target source and weekly-action context when available
- section statuses
- missing sections
- extractive claims copied from existing evidence summaries
- evidence refs with source table, source id, URL, title, and confidence

Every claim keeps `new_fact_generated=false` and at least one evidence ref.

## Acceptance

```bash
PYTHONPATH=backend .venv/bin/python scripts/vkpi_evidence_agent_v0.py \
  --ops-dir runtime/ops \
  --limit 12 \
  --ref-limit 24 \
  --claim-limit 12 \
  --json-out runtime/ops/p7-81-evidence-agent-v0.json \
  --md-out runtime/ops/p7-81-evidence-agent-v0.md
```

The report passes when:

- target source is available
- no side-effect flag is set
- extractive claims do not generate facts
- evidence chains are returned or the no-target state is explicit

If neither explicit IDs nor a P6.77 artifact exists, the report returns
`agent_status=source_missing` and fails acceptance.

## API

```http
GET /api/admin/vkpi/industry-data/evidence-agent/v0?kol_pool_ids=123,456&limit=12
```

Omit `kol_pool_ids` to read the latest P6.77 weekly action plan artifact.
