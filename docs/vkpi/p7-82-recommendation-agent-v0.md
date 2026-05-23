# V-KPI P7.82 Recommendation Agent v0

P7.82 adds a read-only Recommendation Agent. It consumes existing P7.81
Evidence Agent chains and produces ranked recommendation candidates for human
review.

It does not persist `vkpi_kol_recommendations`, create projects, create tasks,
trigger outreach, trigger sync, call providers, or call LLMs.

## Inputs

Target KOLs come from one of three read-only paths:

- explicit `kol_pool_ids`
- latest `runtime/ops/*p7-81-evidence-agent-v0.json`
- a fresh read-only Evidence Agent build from the latest P6.77 weekly action
  plan, only when no P7.81 artifact exists

The agent also reads existing operator feedback and competitor relation tables
when available. Missing tables are reported as unavailable, not inferred.

## Output

Each candidate includes:

- KOL identity
- suggested decision bucket: `contact_candidate`, `watch_candidate`,
  `caution_candidate`, `avoid_candidate`, or `review_candidate`
- score inputs from weekly action priority, evidence quality, competitor risk,
  and operator feedback
- claims copied from P7.81 with `new_fact_generated=false`
- traceable evidence refs
- `human_confirmation_required=true`

Chains with too little traceable evidence are returned as `blocked_candidates`
instead of being promoted as recommendation candidates.

## Acceptance

```bash
PYTHONPATH=backend .venv/bin/python scripts/vkpi_recommendation_agent_v0.py \
  --ops-dir runtime/ops \
  --limit 12 \
  --min-evidence-refs 3 \
  --ref-limit 12 \
  --claim-limit 12 \
  --json-out runtime/ops/p7-82-recommendation-agent-v0.json \
  --md-out runtime/ops/p7-82-recommendation-agent-v0.md
```

The report passes when:

- an evidence source is available
- candidates are traceable or explicitly blocked
- generated facts are blocked
- human confirmation is required
- no persistence, outreach, task, sync, provider, or LLM side-effect flag is set

## API

```http
GET /api/admin/vkpi/industry-data/recommendation-agent/v0?limit=12
GET /api/admin/vkpi/industry-data/recommendation-agent/v0?kol_pool_ids=123,456
```

The API is read-only and requires VKPI read permission.
