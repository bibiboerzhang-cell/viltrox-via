# V-KPI P8-3 Competitor Signal Commit

## Scope

P8-3 adds an explicit commit path for deterministic competitor brain signals.

Default remains dry-run.

## CLI

Dry-run preview:

```bash
python3 scripts/p8_competitor_brain.py --limit 10
```

Commit signals:

```bash
python3 scripts/p8_competitor_brain.py \
  --limit 200 \
  --commit-signals \
  --confirm \
  --committed-by codex:p8-3
```

Guard:

```text
--commit-signals without --confirm is rejected.
```

## Commit Target

```text
vkpi_competitor_signal_runs
vkpi_competitor_signals
```

All committed signals start as:

```text
review_status=pending_review
```

This is intentional. P8-3 commits traceable observations for review; it does not promote signals into canonical competitor products.

## Current Commit Result

```text
scenario=p8_competitor_signal_commit
run_uid=p8sig-370cc441ae4679de
run_id=1
inserted_signals=25
provider_calls=false
write_db=true
```

Database verification:

```text
runs=1
signals=25
pending_review=25
ai_cost=0
```

Signal distribution:

```text
canon      voc_issue           3
fujifilm   voc_issue           6
godox      competitor_focus    3
godox      competitor_mention  3
godox      pricing_sensitive   3
leica      voc_issue           1
nikon      voc_issue           3
sigma      voc_issue           1
sony       voc_issue           2
```

## Acceptance

```text
python3 -m py_compile backend/app/services/vkpi/competitor_brain.py passed
python3 -m py_compile scripts/p8_competitor_brain.py passed
--commit-signals without --confirm rejected
--commit-signals --confirm inserted 25 signals
all inserted signals are pending_review
vkpi_ai_cost_ledger remains 0
dry-run preview still reports write_db=false
git diff --check passed
```

## Next

P8-4 should expose read-only review APIs and a small frontend review surface for pending competitor signals.
