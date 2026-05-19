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
approve/reject signals
write canonical competitor products
call providers
run crawlers
write AI cost ledger
```

## Backend API

```text
GET /api/admin/vkpi/industry-data/competitor-brain/status
GET /api/admin/vkpi/industry-data/competitor-brain/signals
```

Signal filters:

```text
review_status
brand
signal_type
limit
```

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
```

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
npm run build passed
git diff --check passed
```

## Next

P8 completion can be documented after deciding whether to stop at review surface or add approve/reject actions.
