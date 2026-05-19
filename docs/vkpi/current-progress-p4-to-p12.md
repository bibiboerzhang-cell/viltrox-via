# V-KPI Current Progress: P4 to Current

Generated on 2026-05-19.

This document summarizes where the V-KPI v5.3.1 execution track is now, what changed from P4 onward, what issues appeared, and what remains before calling the current loop fully closed.

## 中文摘要

当前主线已经从 P4 推荐层一路推进到 P12 的核心能力闭环。P4-P9 的规则推荐、预算守门、内容脑、告警、竞品脑、自然语言搜索已经完成可运行版本；P10 Learning Loop 已经具备 snapshot、backlog、显式反馈入口和 Operating Review 操作面，但真实反馈表仍然为空，所以不能宣称学习闭环完全 ready。

目前唯一真正阻塞项不是代码，而是需要一次真实人工反馈动作：在 Settings -> Operating Review 中对某条推荐 backlog 点击 `入选` / `需复核` / `拒绝`，或用 CLI 写入一条显式推荐反馈。完成后再跑告警，当前 `recommendation.review_gap` 才能关闭。

本轮遇到的主要问题包括：P4 早期不能复用 P3 helper 作为生产推荐输入、推荐 outcome 没有同步写入 feedback、P6 内容分析曾剩 7 条 pending、P7 smoke 曾误生成额外 preview-run alert、P8 竞品信号曾停留在 pending_review、P10 发现 outcome 已有但 feedback 为空。文档下面逐包记录了处理方式和剩余边界。

## Current State

```text
git_latest=c593e4b feat(vkpi): add operating review memory feedback action
working_tree_before_this_doc=clean
provider_calls=false
ai_cost_calls=0
open_alerts=1
remaining_open=recommendation.review_gap
recommendation_feedback=0
memory_feedback=0
pending_competitor_signals=0
recommendation_outcomes=171
```

Current top work items:

```text
1. Recommendation run needs feedback: recrun-af0053af53b32e1a
2. recommendation_feedback_gap: recrun-af0053af53b32e1a, 75 recommendations without feedback
3. recommendation_feedback_gap: p4nlm-d8091029270e3230, 3 recommendations without feedback
4. recommendation_feedback_gap: p4kpf-b6b5445fea4dca44, 3 recommendations without feedback
5. recommendation_feedback_gap: p4pna-77d0e0701d07c394, 3 recommendations without feedback
```

The only remaining blocker is not code. It is the absence of real operator feedback rows.

## Overall Progress

```text
P1   100%  AI cost ledger + Budget Guard foundation
P2   100%  legacy Excel staging, entity resolution, KOL pool commit, rollback chain
P3   100%  Memory v0, readiness, feedback surfaces
P4   100%  recommendation dry-run/persisted runs/action bridge
P5   100%  Budget Guard defaults, scopes, monitor UI
P6   100%  content brain deterministic analysis and review surface
P7    95%  alert rules and lifecycle verified; one real feedback alert remains open
P8   100%  competitor brain signals committed and reviewed
P9   100%  natural search CLI/API/frontend
P10   85%  learning snapshot/backlogs/action feedback entry points; real feedback still empty
P11  done/optional  SSE task stream readiness and fallback adapter
P12  done/core      RBAC status and settings visibility
```

Practical overall completion: about 88-90%.

## P4: Recommendation Layer

P4 started from a strict rule: do not call LLMs, do not write business-side effects by default, and make every recommendation explainable.

### P4-0 Design

Document:

```text
docs/vkpi/p4-new-launch-match-dry-run.md
```

Important design correction:

```text
P4 must read Memory tables directly.
P4 must not treat P3 product-kol-candidates helper as the production recommendation input.
```

Reason:

```text
P3 helper was a development/review surface.
P4 needs direct control over scoring, evidence, penalties, and explainability.
```

### P4-1 to P4-4: New Launch Match

Documents:

```text
docs/vkpi/p4-1-dry-run-acceptance.md
docs/vkpi/p4-2-recommendation-reasons.md
docs/vkpi/p4-3-persisted-preview-runs.md
docs/vkpi/p4-4-preview-run-api.md
```

Implemented:

```text
new_launch_match dry-run
rule scoring from Memory facts and links
pro/con evidence chain
Markdown/JSON output
persisted preview runs
recommendation explanations
preview run API
```

Guardrails:

```text
provider_calls=false
Budget Guard check still runs with estimated_cost=0
no project creation
no staff assignment
no outreach
no feedback auto-generation
```

Issue encountered:

```text
Initial plan risk: using P3 helper would duplicate hidden scoring.
Fix: P4 reads vkpi_memory_entities, vkpi_memory_facts, vkpi_memory_links directly.
```

### P4-5 to P4-9: KOL Product Fit and Run Index

Documents:

```text
docs/vkpi/p4-5-kol-product-fit-dry-run.md
docs/vkpi/p4-6-kol-product-fit-acceptance.md
docs/vkpi/p4-7-kol-product-fit-reasons.md
docs/vkpi/p4-8-kol-product-fit-persisted-runs.md
docs/vkpi/p4-9-two-scenario-run-index.md
```

Implemented:

```text
second recommendation scenario: kol_product_fit
explainable reasons
persisted preview runs
two-scenario run index
frontend visibility for recommendation runs
```

Issue encountered:

```text
Preview runs created recommendation rows but did not yet represent human feedback.
This later became part of the P10 recommendation_feedback_empty gap.
```

### P4-10 to P4-14: Project Next Action and Review UI

Documents:

```text
docs/vkpi/p4-10-project-next-action-design.md
docs/vkpi/p4-11-project-next-action-acceptance.md
docs/vkpi/p4-12-project-next-action-reasons.md
docs/vkpi/p4-13-project-next-action-persisted-runs.md
docs/vkpi/p4-14-recommendation-run-review-ui.md
```

Implemented:

```text
project_next_action dry-run
reasons and persisted runs
recommendation run review UI
action bridge for recommendation candidates
```

Issue encountered:

```text
shortlist/reject actions originally wrote outcome rows but not vkpi_recommendation_feedback rows.
P10 therefore could not treat those actions as learning feedback.
```

Fix added later:

```text
backend/app/services/vkpi/product_analysis_actions.py

shortlist -> feedback_type='shortlist'
reject    -> feedback_type='reject'
claim     -> feedback_type='claim'

Same recommendation_id + feedback_type is deduped.
```

## P5: Budget Guard

Documents:

```text
docs/vkpi/p5-budget-guard-integration.md
docs/vkpi/p5-budget-monitor-ui.md
```

Implemented:

```text
default budget caps
cron/provider scopes
Budget Monitor UI
Budget Guard checks before later AI/provider-capable flows
```

Current status:

```text
budget_warning_scopes=0
budget_hard_stop_scopes=0
ai_cost_calls=0
```

Issue encountered:

```text
P4/P6/P8/P10 mostly run deterministic flows.
Budget Guard is still wired, but no provider spend should be recorded.
```

## P6: Content Brain

Documents:

```text
docs/vkpi/p6-content-brain-v0.md
docs/vkpi/p6-2-content-brain-dry-run.md
docs/vkpi/p6-3-content-brain-budget-scope.md
docs/vkpi/p6-4-content-brain-commit-analysis.md
docs/vkpi/p6-5-content-brain-review-surface.md
docs/vkpi/p6-content-brain-completion-report.md
```

Implemented:

```text
deterministic post-level content analysis
content tags
product intents
risk flags
brand mentions
review surface
commit-analysis mode with --confirm
```

Issue encountered:

```text
content_brain.analysis_backlog opened because 7 of 10 posts were still pending.
```

Fix:

```text
scripts/p6_content_brain.py --limit 20 --commit-analysis --confirm

posts_updated=7
skipped_done=3
post_count=10
analyzed_count=10
pending_count=0
coverage_ratio=1.0
content_brain.analysis_backlog=resolved
```

## P7: Alerts

Documents:

```text
docs/vkpi/p7-1-budget-guard-alert-rule.md
docs/vkpi/p7-2-content-brain-backlog-alert.md
docs/vkpi/p7-3-recommendation-review-gap-alert.md
docs/vkpi/p7-alerts-completion-report.md
```

Implemented:

```text
budget guard alert rule
content brain backlog alert
recommendation review gap alert
alert triage suggestions
alert apply suggestions
smoke verification for recommendation review-gap lifecycle
```

Resolved:

```text
project.stalled_review resolved=16
content_brain.analysis_backlog resolved=1
```

Remaining:

```text
recommendation.review_gap open=1
alert_id=200
run_uid=recrun-af0053af53b32e1a
recommendations_without_feedback=75
```

Issue encountered:

```text
An early lifecycle smoke pass created extra preview-run review_gap alerts.
Fix: smoke now snapshots pre-existing review-gap alert keys and deletes only alerts created by the smoke.
Manual cleanup removed the accidental p4 preview-run alerts.
```

Verification:

```text
scripts/smoke_vkpi_recommendation_review_gap_alert.py

verified:
  no-feedback run creates recommendation.review_gap
  explicit feedback action writes vkpi_recommendation_feedback
  next alert pass resolves the alert
  smoke cleanup leaves no temporary alert or feedback row
```

## P8: Competitor Brain

Documents:

```text
docs/vkpi/p8-1-competitor-brain-preview.md
docs/vkpi/p8-2-competitor-signal-schema.md
docs/vkpi/p8-3-competitor-signal-commit.md
docs/vkpi/p8-4-competitor-brain-review-surface.md
docs/vkpi/p8-competitor-brain-completion-report.md
```

Implemented:

```text
competitor signal preview
competitor signal schema
signal commit
review controls
review suggestions
apply suggestions dry-run and confirm flow
frontend review surface
```

Current status:

```text
competitor_signals=25
pending_competitor_signals=0
competitor_review_ready=true
```

Issue encountered:

```text
P8 signals originally stayed pending_review.
Fix: deterministic review suggestions + explicit apply path moved the inserted signals to ready.
```

## P9: Natural Search

Documents:

```text
docs/vkpi/p9-natural-search-v1.md
docs/vkpi/p9-1-natural-search-cli.md
docs/vkpi/p9-2-natural-search-api.md
docs/vkpi/p9-3-natural-search-frontend.md
docs/vkpi/p9-natural-search-completion-report.md
```

Implemented:

```text
natural search CLI
natural search API
frontend search surface
search over existing V-KPI data surfaces
```

Boundary:

```text
No training.
No provider call required for the completed v1 path.
Search is a query/read layer over existing data.
```

## P10: Learning Loop

Documents:

```text
docs/vkpi/p10-learning-loop-v0.md
docs/vkpi/p10-1-learning-snapshot-cli.md
docs/vkpi/p10-2-learning-snapshot-api.md
docs/vkpi/p10-learning-loop-completion-report.md
```

Implemented:

```text
read-only learning snapshot
learning snapshot API
recommendation feedback backlog
Memory feedback backlog
Operating Review feedback panels
recommendation feedback actions: 入选 / 需复核 / 拒绝
Memory feedback action: 记录核查
explicit recommendation feedback CLI with --confirm
```

Current snapshot:

```text
recommendation_feedback=0
memory_feedback=0
competitor_signals=25
recommendation_outcomes=171

recommendation_feedback_ready=false
memory_feedback_ready=false
competitor_review_ready=true
outcome_data_ready=true
```

Current gaps:

```text
recommendation_feedback_empty
memory_feedback_empty
open_alerts_need_resolution
recommendation_outcomes_have_no_shortlist_actions
```

Issue encountered:

```text
There were outcomes but no feedback.
P10 intentionally refuses scoring mutation while feedback rows are empty.
```

Fix:

```text
Backlog visibility:
  scripts/p10_recommendation_feedback_backlog.py
  scripts/p10_memory_feedback_backlog.py
  GET /api/admin/vkpi/learning/recommendation-feedback-backlog
  GET /api/admin/vkpi/learning/memory-feedback-backlog

Write paths:
  recommendation actions now write feedback
  Operating Review can write recommendation feedback
  Operating Review can write Memory feedback
  CLI can write explicit recommendation feedback only with --confirm
```

Important boundary:

```text
No feedback row is auto-created by a snapshot or page refresh.
Only explicit user action writes feedback.
```

## P11: SSE Task Stream

Documents:

```text
docs/vkpi/p11-sse-task-stream-v0.md
docs/vkpi/p11-1-realtime-status-api.md
docs/vkpi/p11-2-task-center-sse-adapter.md
docs/vkpi/p11-sse-task-stream-completion-report.md
```

Implemented:

```text
realtime task readiness API
TaskCenter SSE fallback adapter
P11 completion report
```

Status:

```text
P11 is optional and not blocking the current P10 feedback loop.
```

## P12: RBAC / Magic Link

Documents:

```text
docs/vkpi/p12-rbac-magic-link-v0.md
docs/vkpi/p12-1-rbac-status-cli.md
docs/vkpi/p12-3-rbac-status-api.md
docs/vkpi/p12-4-rbac-status-frontend.md
docs/vkpi/p12-rbac-magic-link-completion-report.md
```

Implemented:

```text
RBAC status CLI
RBAC status API
Settings frontend visibility
explicitly continues using staff, not vkpi_staff
```

Status:

```text
P12 core is in place.
Magic Link remains governed by the current staff/RBAC boundary.
```

## Latest Commits in This Round

```text
c593e4b feat(vkpi): add operating review memory feedback action
8c1c883 test(vkpi): verify recommendation review gap alert lifecycle
dab2350 feat(vkpi): add explicit recommendation feedback review action
b7b6d8b feat(vkpi): add operating review recommendation feedback actions
ca86b47 feat(vkpi): record feedback from recommendation actions
7fcf076 feat(vkpi): surface feedback backlogs in operating review
eb44381 docs(vkpi): record feedback backlog and content alert verification
5e63508 feat(vkpi): add memory feedback backlog snapshot
adf3c7e feat(vkpi): add recommendation feedback backlog snapshot
```

## What Changed in the UI

Settings -> Operating Review now shows:

```text
overall open alerts
competitor review status
recommendation feedback backlog
Memory feedback backlog
top work items
```

Recommendation backlog row actions:

```text
入选   -> recommendation action shortlist -> vkpi_recommendation_feedback
需复核 -> recommendation action feedback  -> vkpi_recommendation_feedback
拒绝   -> recommendation action reject    -> vkpi_recommendation_feedback
```

Memory backlog row action:

```text
记录核查 -> POST /api/admin/vkpi/memory/feedback -> vkpi_memory_feedback
```

## What Remains

The remaining work is a real operator action:

```text
In Settings -> Operating Review, select at least one recommendation backlog row.
Click one of: 入选 / 需复核 / 拒绝.
Then rerun alerts.
```

Equivalent CLI:

```bash
.venv/bin/python scripts/p10_recommendation_feedback_backlog.py \
  --recommendation-id 705 \
  --action feedback \
  --note "needs product owner review" \
  --confirm
```

Expected result after one real recommendation feedback:

```text
recommendation_feedback > 0
recommendation_feedback_ready=true
recommendation.review_gap for that run resolves on next alert pass if the run now has feedback
```

Note:

```text
P10 readiness may still show memory_feedback_ready=false until a Memory backlog row receives 记录核查.
That is expected and should not be faked.
```

## Verification Commands

```bash
git status --short

.venv/bin/python scripts/p7_alert_status.py --limit 20
.venv/bin/python scripts/p10_learning_snapshot.py
.venv/bin/python scripts/vkpi_operating_review.py --limit 20
.venv/bin/python scripts/p10_recommendation_feedback_backlog.py --limit 10
.venv/bin/python scripts/p10_memory_feedback_backlog.py --entity-type kol --limit 10

.venv/bin/python scripts/smoke_vkpi_recommendation_review_gap_alert.py
cd frontend && npm run build
```

## Current Decision Boundary

Codex should not choose which real recommendation is good or bad.

Allowed:

```text
show backlog
provide explicit action buttons
verify feedback and alert lifecycle
write feedback only after explicit user/operator action
```

Not allowed:

```text
auto-accept recommendations
auto-reject recommendations
auto-create feedback rows from snapshots
mark P10 learning ready while feedback tables are empty
```
