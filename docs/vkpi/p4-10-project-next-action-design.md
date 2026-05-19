# V-KPI P4-10 Project Next-Action Design

## Scope

P4-10 designs the third P4 recommendation scenario:

```text
project_next_action
```

It answers one question:

```text
For an existing KOL/project/staff workflow, what is the next auditable action to review?
```

P4-10 is design-only. It does not write code.

## Boundaries

P4-10 must not become Task Allocator.

Out of scope:

```text
automatic task assignment
automatic project stage transition
automatic outreach sending
calendar scheduling
staff workload optimization
magic link or external access
multi-agent routing
```

Those belong to later P12 or operations packages.

P4-10 only produces reviewable suggestions. A human must explicitly accept, dismiss, or convert a suggestion into a project/task action.

## Candidate Source

Allowed project workflow sources:

```text
vkpi_projects
vkpi_project_stage_events
vkpi_messages
vkpi_content_posts
vkpi_project_terms
vkpi_project_deliverables
vkpi_shipments
vkpi_sample_assets
vkpi_costs
vkpi_kol_pool
staff
```

Allowed recommendation/memory sources:

```text
vkpi_kol_recommendation_runs
vkpi_kol_recommendations
vkpi_recommendation_explanations
vkpi_memory_entities
vkpi_memory_facts
vkpi_memory_links
```

Allowed task visibility source:

```text
vkpi_async_tasks
vkpi_async_task_items
```

Forbidden sources:

```text
frontend-only UI state
unscoped staff lists
raw contact fields for unauthorized users
LLM-generated action without deterministic evidence
```

## Suggested Action Types

First version supports only these action types:

```text
follow_up_message
ship_sample
confirm_terms
request_content
review_content
record_cost
mark_published
close_or_archive
manual_review
```

Each suggestion must include:

```text
project_id
kol_pool_id or kol_id
assigned_staff_id
current_stage
suggested_action
priority
reason
evidence_pro
evidence_con
source_refs
```

## Scoring Formula

Base score is 100.

```text
stage_staleness          30
missing_required_artifact 20
recent_signal             15
business_value            15
risk_or_blocker           10
staff_scope_fit           10
```

### stage_staleness, 30

```text
stage unchanged > 14 days     30
stage unchanged 7-14 days     20
stage unchanged 3-6 days      10
stage changed <= 2 days        0
```

### missing_required_artifact, 20

Examples:

```text
stage=contacted but no reply/message after 7 days
stage=agreed but no terms
stage=sample_pending but no shipment
stage=content_pending but no content post
stage=published but no cost/outcome record
```

### recent_signal, 15

Signals that increase urgency:

```text
new inbound message
shipment delivered
content published
recommendation accepted/shortlisted
launch window nearing
```

### business_value, 15

Uses existing structured evidence only:

```text
recommendation score
project budget/cost status
KOL fit evidence
product launch relevance
```

### risk_or_blocker, 10

This can add urgency for manual review or reduce automation confidence.

```text
risk_flag
needs_human_review
missing contact
over budget
stale shipment
```

### staff_scope_fit, 10

Suggestion is valid only if the current staff scope can see the project. It is higher when the suggestion belongs to the assigned staff or project creator.

## Penalties And Hard Filters

Hard filters:

```text
deleted project
project outside viewer scope
blocked KOL risk with no admin override
missing project_id
missing evidence chain
```

Penalties:

```text
needs_human_review      x 0.85
missing contact         x 0.80
over budget             x 0.70
unassigned project      x 0.90
```

## Output JSON

```json
{
  "scenario": "project_next_action",
  "mode": "dry_run",
  "summary": {
    "projects_evaluated": 42,
    "suggestions_returned": 25
  },
  "items": [
    {
      "rank": 1,
      "score": 88.0,
      "priority": "high",
      "project_id": 123,
      "assigned_staff_id": 7,
      "current_stage": "sample_pending",
      "suggested_action": "ship_sample",
      "reason": "Project is waiting for sample shipment for 9 days.",
      "evidence_pro": [],
      "evidence_con": [],
      "allowed_next_operations": [
        "open_project",
        "dismiss_suggestion"
      ]
    }
  ]
}
```

P4-10 does not define a write endpoint. Persisted suggestion runs can reuse recommendation tables only after dry-run review in a later P4.11 package.

## CLI Design

```bash
python3 scripts/p4_project_next_action.py \
  --limit 50 \
  --staff-id 7 \
  --json-out /tmp/p4_project_next_action.json \
  --md-out /tmp/p4_project_next_action.md
```

Optional filters:

```text
--project-id <id>
--stage <stage>
--priority high
--include-unassigned
--include-low-evidence
```

Forbidden in first code package:

```text
--commit
--write-db
--assign-task
--transition-stage
--send-message
--provider-call
```

## RBAC

The implementation must call existing project scope helpers before returning a suggestion:

```text
scope.can_view_all
scope.effective_staff_id
scope.assert_project_access
```

Returned suggestions must not expose restricted contact fields. Contact status can be used as a score component, but raw email/phone/address must remain hidden unless the caller already has permission through existing APIs.

## Budget Guard

Deterministic dry-run:

```text
scope=cron:p4_recommendations_daily
estimated_cost_usd=0.0
```

Optional wording in a later package:

```text
scope=cron:p4_recommendation_reasons
```

## Acceptance Gates For Future P4.11 Code

```text
1. Dry-run only by default.
2. Reads existing project/workflow rows, not frontend state.
3. Applies RBAC before returning project suggestions.
4. Every suggestion has at least 3 evidence items.
5. Every suggestion includes supporting and opposing evidence arrays.
6. No project/task/stage/message writes occur.
7. Forbidden action flags are rejected.
8. AI cost ledger remains unchanged in deterministic mode.
9. Markdown and JSON outputs are produced.
10. Suggestions are reviewable but not executable without later explicit endpoints.
```

## Route After P4-10

```text
P4.11 implement project_next_action dry-run CLI
P4.12 optional reason wording for project_next_action
P4.13 persisted preview runs for project_next_action
P4.14 frontend review surface for all P4 preview runs
```
