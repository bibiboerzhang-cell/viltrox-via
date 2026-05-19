# V-KPI P8-4 Competitor Brain Review Surface

## Scope

P8-4 exposes committed competitor signals through read-only API and frontend review UI.

It reads:

```text
vkpi_competitor_signal_runs
vkpi_competitor_signals
```

It does not:

```text
write canonical competitor products
call providers
run crawlers
write AI cost ledger
```

## Backend API

```text
GET /api/admin/vkpi/industry-data/competitor-brain/status
GET /api/admin/vkpi/industry-data/competitor-brain/signals
GET /api/admin/vkpi/industry-data/competitor-brain/review-suggestions
POST /api/admin/vkpi/industry-data/competitor-brain/review-suggestions/apply
POST /api/admin/vkpi/industry-data/competitor-brain/signals/{signal_id}/review
```

Signal filters:

```text
review_status
brand
signal_type
limit
```

Review actions:

```text
ready / approve   -> review_status='ready'
reject            -> review_status='rejected'
ignore            -> review_status='ignored'
pending_review    -> review_status='pending_review'
```

The review endpoint updates only `vkpi_competitor_signals.review_status` and stores the decision trail in `evidence_json.review_history`. It does not write canonical competitor product tables.

Review suggestions are deterministic and read-only:

```bash
python3 scripts/p8_competitor_brain.py --review-suggestions
```

They do not apply decisions. Suggested `ready` rows still require CLI `--apply-suggestions --confirm`, CLI `--apply-review`, or the frontend per-row action.

Bulk apply also defaults to dry-run:

```bash
python3 scripts/p8_competitor_brain.py --apply-suggestions
python3 scripts/p8_competitor_brain.py --apply-suggestions --confirm
```

The API equivalent only writes when the JSON body contains `confirm=true`.

## Frontend

Data Analysis now includes a `竞品脑信号` panel below Content Brain.

The panel shows:

```text
run_count
signal_count
pending_review count
latest run_uid
brand distribution
signal type distribution
review status distribution
filtered signal table
per-row ready/rejected/ignored/pending_review actions
```

Frontend review actions call the review endpoint directly. The confirmation copy states that the action only updates `review_status` and does not write canonical competitor products.

## Current Data

Service smoke:

```text
schema_ready=True
run_count=1
signal_count=25
pending=25
signals_returned=5
top_signal_brand=godox
```

## Acceptance

```text
python3 -m py_compile backend/app/services/vkpi/competitor_brain.py passed
python3 -m py_compile backend/app/api/routers/vkpi_industry_automation.py passed
python3 scripts/p8_competitor_brain.py --review-signal <id> --action ready passed as dry-run
python3 scripts/p8_competitor_brain.py --review-suggestions passed as read-only
python3 scripts/p8_competitor_brain.py --apply-suggestions passed as dry-run
npm run build passed
git diff --check passed
```

## Next

Next package can batch-review low-risk signals after the team confirms the manual workflow on a few rows.
