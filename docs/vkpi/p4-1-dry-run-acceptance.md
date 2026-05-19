# V-KPI P4.1 New Launch Match Dry-Run Acceptance

## Scope

P4.1 acceptance verifies the deterministic `new_launch_match` dry-run only.

It does not verify:

```text
LLM reason generation
recommendation database writes
frontend review UI
additional recommendation scenarios
```

## Commands

```bash
python3 scripts/p4_new_launch_match.py \
  --product "AF 35mm F1.2 LAB FE" \
  --limit 100 \
  --primary-markets "Germany,United States,Japan" \
  --secondary-markets "United Kingdom,France,Canada" \
  --json-out /tmp/p4_1_af35.json \
  --md-out /tmp/p4_1_af35.md

python3 scripts/p4_new_launch_match.py \
  --product "AF 56mm F1.2 Pro" \
  --limit 100 \
  --primary-markets "United States,Japan,Germany" \
  --secondary-markets "United Kingdom,France,Canada" \
  --json-out /tmp/p4_1_af56.json \
  --md-out /tmp/p4_1_af56.md

python3 scripts/p4_new_launch_match.py \
  --product "AF 85mm F1.4 Pro" \
  --limit 100 \
  --primary-markets "United States,Germany,United Kingdom" \
  --secondary-markets "Japan,France,Canada" \
  --json-out /tmp/p4_1_af85.json \
  --md-out /tmp/p4_1_af85.md
```

## Results

```text
AF 35mm F1.2 LAB FE
  target_family=AF 35mm F1.2 LAB
  total_candidates_evaluated=1012
  returned=100
  markdown_display_count=55
  top_score=80.0
  median_score=63.0
  product_match: direct=20 adjacent=74 same_type=6
  evidence_min=7
  evidence_max=8

AF 56mm F1.2 Pro
  target_family=AF 56mm F1.2 Pro
  total_candidates_evaluated=1012
  returned=100
  markdown_display_count=52
  top_score=55.0
  median_score=43.0
  product_match: adjacent=13 same_type=87
  evidence_min=5
  evidence_max=6

AF 85mm F1.4 Pro
  target_family=AF 85mm F1.4 Pro
  total_candidates_evaluated=1012
  returned=100
  markdown_display_count=57
  top_score=50.0
  median_score=43.0
  product_match: same_type=100
  evidence_min=5
  evidence_max=6
```

## Acceptance Gates

```text
Produces 50-100 preview items: pass
Makes 0 provider calls: pass
Leaves vkpi_ai_cost_ledger unchanged: pass
Performs 0 recommendation-table writes: pass
Every returned item has at least 3 evidence items: pass
Every returned item has evidence_pro, evidence_con, and score_breakdown: pass
Scores are deterministic from Memory rows: pass
CLI rejects write/provider flags: pass
```

## Notes

`AF 35mm F1.2 LAB FE` has the strongest output because Memory contains direct
family cooperation evidence. `AF 56mm F1.2 Pro` and `AF 85mm F1.4 Pro` are
weaker because current Memory has less direct product-family evidence for those
queries, so ranking relies more on adjacent or same-type product history.

This is acceptable for P4.1. The next tuning pass should review the top 20 rows
per product with the business team before changing weights.
