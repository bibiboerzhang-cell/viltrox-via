# V-KPI P6.77 Weekly Action Plan v0

P6.77 turns the launch estimate and today's signal digest into a weekly planning
checklist. It is not a recommendation engine and does not create outreach,
projects, tasks, or persisted decisions.

## Inputs

- P6.74 new launch acceptance candidates.
- P6.76 today new signals action items.

## Output

Each action includes:

- `action_type`
- priority and score
- human-readable title and reason
- entity keys such as KOL, platform, post, comment, brand, or SKU
- source evidence
- required human next step

## Acceptance

```bash
PYTHONPATH=backend .venv/bin/python scripts/vkpi_weekly_action_plan_v0.py \
  --top-n 12 \
  --lookback-hours 24 \
  --json-out runtime/ops/p6-77-weekly-action-plan-v0.json \
  --md-out runtime/ops/p6-77-weekly-action-plan-v0.md
```

The report passes when:

- launch acceptance report loads
- today signal digest loads
- actions are generated
- every action has evidence
- when no contact/review action qualifies, a low-priority planning input review
  action is returned instead of a blank checklist
- provider, LLM, write, task, outreach, project, and sync flags stay false

## API

```http
GET /api/admin/vkpi/industry-data/weekly-action-plan/v0?top_n=12&lookback_hours=24
```

The endpoint is read-only and uses the same service as the CLI.
