# P4.3 Daily Top100 Candidate Source Gate

## Scope

P4.3 does not rebuild Daily Top100 assignment. The assignment logic was already
covered by previous staff-scope, owner-assignment, responsible-import, unique
assignment, and source-trigger smokes.

This round makes the current upstream source state explicit and repeatable.

## Current Runtime Finding

`scripts/audit_vkpi_daily_top100_source.py --json` currently reports:

- status: `ok`
- blockers: `[]`
- enabled monitored products: `1`
- real suggestion SKU: `AF-35-55-F1.8-EVO-FE-Z`
- product-specific suggestions: `6`
- assigned Daily Top100 items for the real product: `5`

This means the current issue is not "Daily Top100 has no source". The product
source exists and has produced product-scoped suggestions. Future work should
focus on endpoint/browser QA and clearer UI wording for the active/eligible/
excluded staff counts.

## Added Gate

- `scripts/smoke_vkpi_p4_3_daily_top100_source_gate.py`

The smoke is read-only:

- does not call Apify;
- does not call YouTube;
- does not call LLMs;
- does not generate digest rows;
- asserts that the current environment still has at least one enabled monitored
  product, at least one real suggestion SKU, and assigned digest items for a real
  product.

## Verification

```bash
PYTHONPATH=backend .venv/bin/python -m py_compile \
  scripts/smoke_vkpi_p4_3_daily_top100_source_gate.py

PYTHONPATH=backend .venv/bin/python scripts/audit_vkpi_daily_top100_source.py --json

./scripts/run_smoke.sh smoke_vkpi_p4_3_daily_top100_source_gate.py

./scripts/run_smoke.sh --batch \
  smoke_vkpi_daily_top100_source_trigger.py \
  smoke_vkpi_daily_digest_unique_assignment.py \
  smoke_vkpi_daily_digest_staff_scope.py
```

## Acceptance

P4.3 is complete when:

- the audit status is `ok`;
- the source gate smoke passes;
- the existing Daily Top100 source-trigger and assignment regression smokes pass;
- the P4 transition plan points to this gate.

## Remaining Work After P4.3

- Browser QA for the Daily Top100 panel.
- UI wording for active staff vs eligible staff vs excluded staff.
- Real Instagram/TikTok monitor checks remain separate live-provider validation,
  not part of this read-only gate.
