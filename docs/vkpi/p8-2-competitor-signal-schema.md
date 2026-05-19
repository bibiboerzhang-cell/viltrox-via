# V-KPI P8-2 Competitor Signal Schema

## Scope

P8-2 adds a narrow reviewed signal store for P8.

It does not write any signal rows.

## Migration

```text
migrations/064_vkpi_competitor_signals.sql
migrations/064_vkpi_competitor_signals_down.sql
```

The migration is connected in:

```text
backend/app/db/connection.py::_POSTGRES_MIGRATION_SEQUENCE
```

## Tables

### vkpi_competitor_signal_runs

One row per committed/reviewed P8 signal run.

```text
id
run_uid
status
source_summary_json
signal_count
committed_by
created_at
committed_at
```

### vkpi_competitor_signals

One row per traceable competitor signal.

```text
id
signal_uid
run_id
brand
normalized_brand
signal_type
severity
score
product_hints_json
source_table
source_id
source_sheet
source_row
source_url
platform
detail
evidence_json
review_status
created_at
updated_at
```

## Why Not vkpi_competitor_products

`vkpi_competitor_products` is a canonical product definition table.

P8-1 output is observation data:

```text
brand mention
VOC issue
pricing-sensitive signal
competitor-focus signal
source evidence
```

Those observations should be reviewed before they become canonical competitor products.

## Current Local Verification

After applying the migration through runtime init:

```text
vkpi_competitor_signal_runs=0
vkpi_competitor_signals=0
```

This is expected. P8-2 only adds storage shape; P8-3 will commit reviewed preview signals.

## Acceptance

```text
python3 -m py_compile backend/app/db/connection.py passed
runtime migration init passed
064 is present in _POSTGRES_MIGRATION_SEQUENCE
down migration exists
both new tables exist and are empty
existing vkpi_competitor_products remains untouched
```

## Next

P8-3 should add an explicit commit path:

```text
scripts/p8_competitor_brain.py --commit-signals --confirm
```

Default must remain dry-run.
