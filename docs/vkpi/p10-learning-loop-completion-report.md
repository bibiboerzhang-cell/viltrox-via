# V-KPI P10 Learning Loop Completion Report

## Status

P10 Learning Loop v0 is complete as a read-only feedback and readiness snapshot.

Scoring changes are intentionally blocked because current feedback data is not ready.

## Completed Packages

```text
P10-0  read-only learning boundary design
P10-1  service + CLI snapshot
P10-2  read-only API
P10-3  recommendation feedback backlog snapshot
P10-4  Memory feedback backlog snapshot
P10-5  recommendation action feedback bridge
```

## Current Snapshot

```text
recommendation_feedback=0
memory_feedback=0
competitor_signals=25
recommendation_outcomes=171
alerts=18
```

Readiness:

```text
recommendation_feedback_ready=false
memory_feedback_ready=false
competitor_review_ready=false
outcome_data_ready=true
```

Current gaps:

```text
recommendation_feedback_empty
memory_feedback_empty
competitor_signals_pending_review
open_alerts_need_resolution
recommendation_outcomes_have_no_shortlist_actions
```

## Files

```text
backend/app/services/vkpi/learning_loop.py
backend/app/api/routers/vkpi_learning.py
scripts/p10_learning_snapshot.py
```

## API

```text
GET /api/admin/vkpi/learning/snapshot
GET /api/admin/vkpi/learning/recommendation-feedback-backlog
GET /api/admin/vkpi/learning/memory-feedback-backlog
```

## Recommendation Feedback Backlog

P10-3 adds a read-only backlog for recommendation rows that have no explicit human feedback yet.

```bash
scripts/p10_recommendation_feedback_backlog.py --limit 100
scripts/p10_recommendation_feedback_backlog.py --run-uid recrun-af0053af53b32e1a --json
```

Output guarantees:

```text
provider_calls=false
write_db=false
recommendation outcomes are shown only as context
no recommendation feedback rows are auto-created
```

The backlog separates four states:

```text
capture_rejection_feedback       outcome/status already shows rejection; still requires human write action
capture_shortlist_feedback       outcome/status already shows shortlist; still requires human write action
review_positive_business_signal  downstream business outcome exists; requires human interpretation
needs_human_review               no explicit action or outcome exists
```

## Memory Feedback Backlog

P10-4 adds a read-only backlog for Memory entities that are most likely to need human verification.

```bash
scripts/p10_memory_feedback_backlog.py --entity-type kol --limit 100
```

Current signals used:

```text
risk_flag
sync_status=needs_human_review
review_state=needs_human_review
weak_label=profile_missing_review / risk_review
contact_status=missing / unknown
low evidence_count
```

Output guarantees:

```text
provider_calls=false
write_db=false
no vkpi_memory_feedback row is auto-created
existing Memory facts are used only to rank review priority
```

Verified backlog shape on 2026-05-19:

```text
recommendation_feedback_backlog:
  recommendation_rows=84
  missing_feedback_rows=84
  with_feedback_rows=0
  run_count=4

memory_feedback_backlog:
  entity_type=kol
  entity_rows=1012
  backlog_candidates=567
  high_priority=43
  review_risk_memory=7
  verify_memory_entity=36
  memory_feedback_rows=0

vkpi_ai_cost_ledger.count=0
```

## Recommendation Action Feedback Bridge

P10-5 fixes the write-side bridge so real operator actions create learning feedback rows.

```text
shortlist action      -> vkpi_recommendation_feedback.feedback_type='shortlist'
reject action         -> vkpi_recommendation_feedback.feedback_type='reject'
claim action          -> vkpi_recommendation_feedback.feedback_type='claim'
create_project action -> already writes feedback_type='create_project'
```

Guardrails:

```text
same recommendation_id + feedback_type is written once
repeat clicks update outcome/status but do not duplicate feedback rows
no feedback row is created by backlog snapshots
no feedback row is created without an explicit product recommendation action
```

Smoke verification:

```text
scripts/smoke_vkpi_product_industry_phase0.py
  shortlist action created exactly 1 recommendation feedback row
  cleanup returned vkpi_recommendation_feedback to 0 in local verification

frontend npm run build passed
vkpi_ai_cost_ledger.count=0
```

## Guarantees

```text
No provider call
No scoring change
No learning table write
No feedback auto-generation
No alert auto-resolution
AI cost ledger remains 0
```

## Acceptance

```text
python3 -m py_compile passed
CLI snapshot generated Markdown
json_out and md_out generated
API router registered
git diff --check passed
```

## Next

Before P10 can change scoring, at least one of these must happen:

```text
recommendation_feedback > 0
competitor_signals pending_review reduced by human review
memory_feedback > 0
open alerts reviewed/resolved
```

Until then, continue to P12 RBAC/Magic Link or P11 SSE optional rather than pretending the learning loop can tune recommendations.
