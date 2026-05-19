# V-KPI P1-P12 体检报告 2026-05-19

## 0. 元信息 (commit / git status / tag / 时间)

```bash
git rev-parse HEAD
```

```text
5c37457d67f0350fe0ec83dbd18d6edf46f8d7d5
```

```bash
git log --oneline -50
```

```text
5c37457 docs(vkpi): report official account data cutoff
c7c960c docs(vkpi): summarize P4 to current progress
c593e4b feat(vkpi): add operating review memory feedback action
8c1c883 test(vkpi): verify recommendation review gap alert lifecycle
dab2350 feat(vkpi): add explicit recommendation feedback review action
b7b6d8b feat(vkpi): add operating review recommendation feedback actions
ca86b47 feat(vkpi): record feedback from recommendation actions
7fcf076 feat(vkpi): surface feedback backlogs in operating review
eb44381 docs(vkpi): record feedback backlog and content alert verification
5e63508 feat(vkpi): add memory feedback backlog snapshot
adf3c7e feat(vkpi): add recommendation feedback backlog snapshot
91b5170 docs(vkpi): record alert triage apply verification
21ffe2e feat(vkpi): add alert triage suggestions
bf39e4f docs(vkpi): record competitor review apply verification
0232784 feat(vkpi): add competitor review suggestion apply dry-run
4cea319 feat(vkpi): add competitor review suggestions
8867ca2 feat(vkpi): add competitor signal review controls
bd54b03 feat(vkpi): add competitor signal review actions
a6b51fc feat(vkpi): surface operating review in settings
a3c2497 feat(vkpi): add post-completion operating review snapshot
d1a2a81 docs(vkpi): report v5.3.1 execution completion
92ebec8 docs(vkpi): report P11 SSE completion
e24c786 feat(vkpi): add TaskCenter SSE fallback adapter
030e616 feat(vkpi): expose realtime task readiness
bf8e4b1 docs(vkpi): design P11 SSE task stream
f2a03ae docs(vkpi): report P12 RBAC completion
4be9cca feat(vkpi): show RBAC status in settings
bbbf4fe feat(vkpi): expose RBAC status API
86c622d feat(vkpi): add RBAC status snapshot CLI
7cf699b docs(vkpi): design P12 RBAC and magic link boundary
3459bd3 docs(vkpi): report P10 learning snapshot completion
22af297 feat(vkpi): expose learning snapshot api
028713c feat(vkpi): add learning loop snapshot cli
a5b9042 docs(vkpi): design P10 learning snapshot
f6fb169 docs(vkpi): report P9 natural search completion
cb78327 feat(vkpi): add natural search review panel
f05cbc0 feat(vkpi): expose deterministic natural search api
245faed feat(vkpi): add deterministic natural search cli
527f99d docs(vkpi): design P9 deterministic natural search
49553e6 docs(vkpi): report P8 competitor brain completion
b5158c7 feat(vkpi): expose competitor brain review surface
487bb5f feat(vkpi): commit competitor brain signals for review
b149715 feat(vkpi): add competitor signal review schema
fcbe3f9 feat(vkpi): add competitor brain preview
7cd2466 docs(vkpi): design P8 competitor brain preview
74a43e8 docs(vkpi): close P7 alert anomaly layer
e922117 feat(vkpi): add recommendation review gap alert
5953056 feat(vkpi): add content brain backlog alert rule
75c5891 feat(vkpi): add budget guard alert rule
46ea765 docs(vkpi): report P6 content brain completion
```

```bash
git status --short
```

```text
```

```bash
git tag --list 'v0.*'
```

```text
v0.2-stage-0-complete
v0.3-memory-ready
v0.4-v531-core-complete
```

```bash
date -u
```

```text
Tue May 19 08:31:02 UTC 2026
```

```bash
wc -l docs/vkpi/*.md | tail -5
```

```text
      99 docs/vkpi/p9-natural-search-completion-report.md
     119 docs/vkpi/p9-natural-search-v1.md
      92 docs/vkpi/post-v531-operating-review.md
     198 docs/vkpi/v5.3.1-execution-completion-report.md
   11012 total
```

## 1. P1 基础设施

```bash
.venv/bin/python -m py_compile backend/app/services/vkpi/llm_gateway.py backend/app/services/vkpi/budget_guard.py backend/app/api/routers/vkpi_budgets.py
```

```text
```

```bash
psql -d vkpi -c "
SELECT scope, cap_usd, current_spend, warning_at, hard_stop_at, fallback_action
FROM vkpi_provider_budget_caps
ORDER BY scope;"
```

```text
FAILED returncode=2
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```

```bash
psql -d vkpi -c "
SELECT COUNT(*) AS ai_cost_calls, COALESCE(SUM(cost_usd), 0) AS total_spend
FROM vkpi_ai_cost_ledger;"
```

```text
FAILED returncode=2
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```

```bash
psql -d vkpi -c "
SELECT ai_provider, COUNT(*), SUM(cost_usd)
FROM vkpi_ai_cost_ledger
WHERE occurred_at > NOW() - INTERVAL '30 days'
GROUP BY ai_provider;"
```

```text
FAILED returncode=2
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```

## 2. P2 历史导入

```bash
psql -d vkpi -c "
SELECT batch_uid, status, committed_at, rollback_until, rolled_back_at, 
       committed_rows, rolled_back_rows, source_file_sha256
FROM vkpi_legacy_import_batches
ORDER BY id;"
```

```text
FAILED returncode=2
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```

```bash
psql -d vkpi -c "
SELECT pipeline, COUNT(*)
FROM (
  SELECT 'kol_profiles' AS pipeline FROM vkpi_legacy_kol_profiles_staging
  UNION ALL SELECT 'cooperations' FROM vkpi_legacy_cooperations_staging
  UNION ALL SELECT 'launch_plans' FROM vkpi_legacy_launch_plans_staging
  UNION ALL SELECT 'official_content' FROM vkpi_legacy_official_content_staging
  UNION ALL SELECT 'official_materials' FROM vkpi_legacy_official_materials_staging
  UNION ALL SELECT 'product_costs' FROM vkpi_legacy_product_costs_staging
  UNION ALL SELECT 'risk_watchlist' FROM vkpi_legacy_risk_watchlist_staging
  UNION ALL SELECT 'voc_alerts' FROM vkpi_legacy_voc_alerts_staging
) x GROUP BY pipeline ORDER BY pipeline;"
```

```text
FAILED returncode=2
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```

```bash
psql -d vkpi -c "
SELECT weak_label, resolution_decision, COUNT(*)
FROM vkpi_legacy_kol_entities
GROUP BY weak_label, resolution_decision
ORDER BY weak_label, resolution_decision;"
```

```text
FAILED returncode=2
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```

```bash
psql -d vkpi -c "
SELECT commit_attempt, 
       COALESCE(rollback_status, 'active') AS status,
       COUNT(*)
FROM vkpi_legacy_import_committed_refs
GROUP BY commit_attempt, rollback_status
ORDER BY commit_attempt;"
```

```text
FAILED returncode=2
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```

```bash
psql -d vkpi -c "
SELECT 
  COUNT(*) AS total,
  COUNT(*) FILTER (WHERE source_type='legacy_excel_p2d') AS legacy_imported,
  COUNT(*) FILTER (WHERE sync_status='imported') AS imported,
  COUNT(*) FILTER (WHERE sync_status='needs_human_review') AS needs_review
FROM vkpi_kol_pool;"
```

```text
FAILED returncode=2
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```

## 3. P3 Memory

```bash
.venv/bin/python scripts/build_vkpi_memory.py --readiness
```

```text
status=ready_for_p4_dry_run
provider_calls_allowed=false
gate.kol_memory=pass severity=critical actual=1012 expected_min=1000
gate.product_family_memory=pass severity=critical actual=659 expected_min=1
gate.historical_product_links=pass severity=critical actual=2358 expected_min=1
gate.market_signals=pass severity=critical actual=2486 expected_min=1
gate.launch_signals=pass severity=critical actual=52 expected_min=1
gate.official_content_signals=pass severity=warning actual=2168 expected_min=1
gate.voc_signals=pass severity=warning actual=37 expected_min=1
gate.budget_guard_tables=pass severity=warning actual=1 expected_min=1
gate.budget_guard_caps=pass severity=warning actual=11 expected_min=5
entities.kol=1012
entities.market_topic=347
entities.official_account=63
entities.product=885
entities.product_family=659
facts.contact_status=1012
facts.cooperation=2358
facts.country=953
facts.evidence_count=4048
facts.launch_plan=52
facts.market_signal=2486
facts.product_cost=823
facts.product_normalization=885
facts.review_state=1012
facts.risk_flag=7
facts.sync_status=1012
facts.weak_label=1012
links.normalized_to_product_family=782
links.official_account_published_product=1557
links.worked_on_product=2358
product_normalization_status.ambiguous_mount_only=5
product_normalization_status.empty=1
product_normalization_status.unclassified=97
market_signals.launch_plan=52
market_signals.official_content=2168
market_signals.official_material=229
market_signals.voc_alert=37

2026-05-19 16:31:02,643 [INFO] viltrox.core.config: JWT_SECRET loaded from environment
2026-05-19 16:31:02,644 [INFO] viltrox.core.config: yt-dlp running without proxy (direct IP)
```

```bash
psql -d vkpi -c "
SELECT entity_type, COUNT(*) FROM vkpi_memory_entities GROUP BY entity_type ORDER BY 2 DESC;"
```

```text
FAILED returncode=2
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```

```bash
psql -d vkpi -c "
SELECT fact_type, COUNT(*) FROM vkpi_memory_facts GROUP BY fact_type ORDER BY 2 DESC LIMIT 15;"
```

```text
FAILED returncode=2
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```

```bash
psql -d vkpi -c "
SELECT link_type, COUNT(*) FROM vkpi_memory_links GROUP BY link_type ORDER BY 2 DESC;"
```

```text
FAILED returncode=2
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```

```bash
psql -d vkpi -c "
SELECT COUNT(*) AS memory_feedback FROM vkpi_memory_feedback;"
```

```text
FAILED returncode=2
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```

## 4. P4 推荐层

```bash
psql -d vkpi -c "
SELECT scenario, COUNT(*) AS runs, MAX(created_at) AS last_run
FROM vkpi_kol_recommendation_runs
GROUP BY scenario
ORDER BY scenario;" 2>/dev/null || echo "table vkpi_kol_recommendation_runs not found - check actual table name"
```

```text
table vkpi_kol_recommendation_runs not found - check actual table name
```

```bash
psql -d vkpi -c "
SELECT COUNT(*) AS total_recommendations FROM vkpi_kol_recommendations;"
```

```text
FAILED returncode=2
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```

```bash
psql -d vkpi -c "
SELECT scenario, COUNT(*) AS items, AVG(score)::numeric(6,2) AS avg_score
FROM vkpi_kol_recommendations
GROUP BY scenario
ORDER BY items DESC;"
```

```text
FAILED returncode=2
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```

```bash
psql -d vkpi -c "
SELECT COUNT(*) FROM vkpi_recommendation_outcomes;"
```

```text
FAILED returncode=2
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```

```bash
psql -d vkpi -c "
SELECT immediate_action, COUNT(*) FROM vkpi_recommendation_outcomes
GROUP BY immediate_action;"
```

```text
FAILED returncode=2
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```

## 5. P5 Budget Guard

```bash
psql -d vkpi -c "
SELECT scope, cap_usd, current_spend,
       (current_spend / NULLIF(cap_usd, 0) * 100)::numeric(5,2) AS pct_used,
       fallback_action
FROM vkpi_provider_budget_caps
ORDER BY scope;"
```

```text
FAILED returncode=2
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```

## 6. P6 内容脑

```bash
psql -d vkpi -c "
SELECT COUNT(*) FROM vkpi_kol_content_analysis;" 2>/dev/null || echo "content analysis table check"
```

```text
content analysis table check
```

```bash
ls -la backend/app/services/vkpi/content_brain* 2>/dev/null
```

```text
-rw-r--r--@ 1 bibiboer  staff  26697 May 19 14:29 backend/app/services/vkpi/content_brain.py
```

```bash
ls -la docs/vkpi/p6* 2>/dev/null
```

```text
-rw-r--r--@ 1 bibiboer  staff  2244 May 19 14:21 docs/vkpi/p6-2-content-brain-dry-run.md
-rw-r--r--@ 1 bibiboer  staff   847 May 19 14:22 docs/vkpi/p6-3-content-brain-budget-scope.md
-rw-r--r--@ 1 bibiboer  staff  2191 May 19 14:25 docs/vkpi/p6-4-content-brain-commit-analysis.md
-rw-r--r--@ 1 bibiboer  staff  1371 May 19 14:28 docs/vkpi/p6-5-content-brain-review-surface.md
-rw-r--r--@ 1 bibiboer  staff  1676 May 19 14:30 docs/vkpi/p6-content-brain-completion-report.md
-rw-r--r--@ 1 bibiboer  staff  1820 May 19 14:17 docs/vkpi/p6-content-brain-v0.md
```

## 7. P7 告警

```bash
.venv/bin/python scripts/p7_alert_status.py --limit 50
```

```text
# P7 Alert Status

```text
open_total=1
p7_open_total=1
ai_cost_calls=0
ai_cost_spend=0.0000
budget_warning_scopes=0
budget_hard_stop_scopes=0
```

## By Rule

- content_brain.analysis_backlog: status=resolved severity=warning count=1
- project.stalled_review: status=resolved severity=warning count=16
- recommendation.review_gap: status=open severity=danger count=1

## Open Alerts

- [danger] recommendation.review_gap #200: Recommendation run needs feedback: recrun-af0053af53b32e1a


2026-05-19 16:31:02,941 [INFO] viltrox.core.config: JWT_SECRET loaded from environment
2026-05-19 16:31:02,942 [INFO] viltrox.core.config: yt-dlp running without proxy (direct IP)
```

```bash
psql -d vkpi -c "
SELECT alert_type, severity, COUNT(*),
       COUNT(*) FILTER (WHERE resolved_at IS NULL) AS open,
       COUNT(*) FILTER (WHERE resolved_at IS NOT NULL) AS resolved
FROM vkpi_alerts
GROUP BY alert_type, severity
ORDER BY alert_type;"
```

```text
FAILED returncode=2
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```

## 8. P8 竞品脑

```bash
psql -d vkpi -c "
SELECT COUNT(*) FROM vkpi_competitor_signals;" 2>/dev/null || echo "check competitor signals table"
```

```text
check competitor signals table
```

```bash
ls -la backend/app/services/vkpi/competitor* 2>/dev/null
```

```text
-rw-r--r--@ 1 bibiboer  staff  37095 May 19 15:26 backend/app/services/vkpi/competitor_brain.py
```

```bash
ls -la docs/vkpi/p8* 2>/dev/null
```

```text
-rw-r--r--@ 1 bibiboer  staff  2783 May 19 14:42 docs/vkpi/p8-1-competitor-brain-preview.md
-rw-r--r--@ 1 bibiboer  staff  1846 May 19 14:43 docs/vkpi/p8-2-competitor-signal-schema.md
-rw-r--r--@ 1 bibiboer  staff  1851 May 19 14:45 docs/vkpi/p8-3-competitor-signal-commit.md
-rw-r--r--@ 1 bibiboer  staff  3270 May 19 15:28 docs/vkpi/p8-4-competitor-brain-review-surface.md
-rw-r--r--@ 1 bibiboer  staff  2154 May 19 14:48 docs/vkpi/p8-competitor-brain-completion-report.md
-rw-r--r--@ 1 bibiboer  staff  4569 May 19 14:39 docs/vkpi/p8-competitor-brain-v0.md
```

## 9. P9 自然语言搜索

```bash
ls -la backend/app/services/vkpi/natural_search* 2>/dev/null
```

```text
-rw-r--r--@ 1 bibiboer  staff  12067 May 19 14:50 backend/app/services/vkpi/natural_search.py
```

```bash
ls -la docs/vkpi/p9* 2>/dev/null
```

```text
-rw-r--r--@ 1 bibiboer  staff  2232 May 19 14:50 docs/vkpi/p9-1-natural-search-cli.md
-rw-r--r--@ 1 bibiboer  staff  1101 May 19 14:51 docs/vkpi/p9-2-natural-search-api.md
-rw-r--r--@ 1 bibiboer  staff  1176 May 19 14:53 docs/vkpi/p9-3-natural-search-frontend.md
-rw-r--r--@ 1 bibiboer  staff  1731 May 19 14:53 docs/vkpi/p9-natural-search-completion-report.md
-rw-r--r--@ 1 bibiboer  staff  2395 May 19 14:48 docs/vkpi/p9-natural-search-v1.md
```

## 10. P10 Learning Loop

```bash
.venv/bin/python scripts/p10_learning_snapshot.py
```

```text
# P10 Learning Snapshot

```text
scenario=p10_learning_snapshot
provider_calls=false
write_db=false
competitor_signals=25
memory_feedback=0
recommendation_feedback=0
readiness.competitor_review_ready=true
readiness.memory_feedback_ready=false
readiness.outcome_data_ready=true
readiness.recommendation_feedback_ready=false
```

## Gaps

- recommendation_feedback_empty
- memory_feedback_empty
- open_alerts_need_resolution
- recommendation_outcomes_have_no_shortlist_actions


2026-05-19 16:31:03,105 [INFO] viltrox.core.config: JWT_SECRET loaded from environment
2026-05-19 16:31:03,105 [INFO] viltrox.core.config: yt-dlp running without proxy (direct IP)
```

```bash
psql -d vkpi -c "
SELECT COUNT(*) FROM vkpi_recommendation_feedback;"
```

```text
FAILED returncode=2
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```

```bash
psql -d vkpi -c "
SELECT feedback_type, COUNT(*) FROM vkpi_recommendation_feedback
GROUP BY feedback_type;"
```

```text
FAILED returncode=2
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```

```bash
psql -d vkpi -c "
SELECT COUNT(*) FROM vkpi_memory_feedback;"
```

```text
FAILED returncode=2
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```

```bash
.venv/bin/python scripts/p10_recommendation_feedback_backlog.py --limit 5
```

```text
# P10 Recommendation Feedback Backlog

```text
scenario=p10_recommendation_feedback_backlog
provider_calls=false
write_db=false
run_uid=all
recommendation_rows=84
missing_feedback_rows=84
with_feedback_rows=0
returned=5
suggested.needs_human_review=5
```

## Runs

- recrun-af0053af53b32e1a: status=completed strategy=rule_v0 missing_feedback=75 feedback_rows=0
- p4pna-77d0e0701d07c394: status=previewed strategy=project_next_action_v1 missing_feedback=3 feedback_rows=0
- p4kpf-b6b5445fea4dca44: status=previewed strategy=kol_product_fit_v1 missing_feedback=3 feedback_rows=0
- p4nlm-d8091029270e3230: status=previewed strategy=new_launch_match_v1 missing_feedback=3 feedback_rows=0

## Backlog

- rec_id=786 run=p4pna-77d0e0701d07c394 rank=1 score=42.00 kol=project:project:3620 action=needs_human_review confidence=0.50 outcome=none reasons=no_feedback_or_business_action,status_previewed,feedback_rows_zero
- rec_id=787 run=p4pna-77d0e0701d07c394 rank=2 score=37.00 kol=project:project:3622 action=needs_human_review confidence=0.50 outcome=none reasons=no_feedback_or_business_action,status_previewed,feedback_rows_zero
- rec_id=788 run=p4pna-77d0e0701d07c394 rank=3 score=37.00 kol=project:project:3621 action=needs_human_review confidence=0.50 outcome=none reasons=no_feedback_or_business_action,status_previewed,feedback_rows_zero
- rec_id=783 run=p4kpf-b6b5445fea4dca44 rank=1 score=87.00 kol=media:blog action=needs_human_review confidence=0.50 outcome=none reasons=no_feedback_or_business_action,status_previewed,feedback_rows_zero
- rec_id=784 run=p4kpf-b6b5445fea4dca44 rank=2 score=87.00 kol=media:blog action=needs_human_review confidence=0.50 outcome=none reasons=no_feedback_or_business_action,status_previewed,feedback_rows_zero


2026-05-19 16:31:03,256 [INFO] viltrox.core.config: JWT_SECRET loaded from environment
2026-05-19 16:31:03,256 [INFO] viltrox.core.config: yt-dlp running without proxy (direct IP)
```

```bash
.venv/bin/python scripts/p10_memory_feedback_backlog.py --entity-type kol --limit 5
```

```text
# P10 Memory Feedback Backlog

```text
scenario=p10_memory_feedback_backlog
provider_calls=false
write_db=false
entity_type=kol
entity_rows=1012
backlog_candidates=567
memory_feedback_rows=0
returned=5
severity.high=43
severity.low=524
suggested.add_contact_context=350
suggested.review_risk_memory=7
suggested.verify_low_evidence_memory=174
suggested.verify_memory_entity=36
```

## Backlog

- entity=mem_kol_15625e5f6799e946f23e name=Rah Sharma action=review_risk_memory severity=high priority=180 sync=needs_human_review weak=risk_review review=needs_human_review contact=available_restricted risk_flags=1 evidence=2 reasons=risk_flag_or_risk_review,needs_human_review
- entity=mem_kol_a70a50d0585a8fa6dfd8 name=Mike Salgado action=review_risk_memory severity=high priority=180 sync=needs_human_review weak=risk_review review=needs_human_review contact=available_restricted risk_flags=1 evidence=2 reasons=risk_flag_or_risk_review,needs_human_review
- entity=mem_kol_15348798eede8266cfef name=eustace_kanyanda action=review_risk_memory severity=high priority=180 sync=needs_human_review weak=risk_review review=needs_human_review contact=available_restricted risk_flags=1 evidence=2 reasons=risk_flag_or_risk_review,needs_human_review
- entity=mem_kol_94d5e585e61e3e17ff9c name=Ryan Holland action=review_risk_memory severity=high priority=180 sync=needs_human_review weak=risk_review review=needs_human_review contact=available_restricted risk_flags=1 evidence=2 reasons=risk_flag_or_risk_review,needs_human_review
- entity=mem_kol_2ba83c716bcfb49f5275 name=chien_vision action=review_risk_memory severity=high priority=180 sync=needs_human_review weak=risk_review review=needs_human_review contact=available_restricted risk_flags=1 evidence=3 reasons=risk_flag_or_risk_review,needs_human_review


2026-05-19 16:31:03,602 [INFO] viltrox.core.config: JWT_SECRET loaded from environment
2026-05-19 16:31:03,603 [INFO] viltrox.core.config: yt-dlp running without proxy (direct IP)
```

## 11. P11 SSE

```bash
ls -la backend/app/api/sse* 2>/dev/null || ls -la backend/app/api/routers/*sse* 2>/dev/null
```

```text
-rw-r--r--@ 1 bibiboer  staff   3995 May  6 14:26 backend/app/api/routers/sse.py
-rw-r--r--@ 1 bibiboer  staff  11385 May  9 15:37 backend/app/api/routers/vkpi_evidence_assets.py
-rw-r--r--@ 1 bibiboer  staff    340 May  9 00:10 backend/app/api/routers/vkpi_workflow_assets.py
```

```bash
ls -la docs/vkpi/p11* 2>/dev/null
```

```text
-rw-r--r--@ 1 bibiboer  staff  1302 May 19 15:08 docs/vkpi/p11-1-realtime-status-api.md
-rw-r--r--@ 1 bibiboer  staff  1419 May 19 15:10 docs/vkpi/p11-2-task-center-sse-adapter.md
-rw-r--r--@ 1 bibiboer  staff  2155 May 19 15:11 docs/vkpi/p11-sse-task-stream-completion-report.md
-rw-r--r--@ 1 bibiboer  staff  3450 May 19 15:07 docs/vkpi/p11-sse-task-stream-v0.md
```

## 12. P12 RBAC

```bash
.venv/bin/python scripts/p12_rbac_status.py 2>/dev/null || echo "check P12 CLI"
```

```text
# P12 RBAC Status

```text
scenario=p12_rbac_status
provider_calls=false
write_db=false
staff.accepted=2
staff.active=2
staff.active_owners=2
staff.domain_verified=2
staff.missing_email=0
staff.owners=2
staff.pending_invite=0
staff.suspended=0
staff.total=2
access.active_can_admin_vkpi=2
access.active_can_manage_members=2
access.active_can_read_vkpi=2
access.active_can_write_vkpi=2
invite_tokens.active=0
invite_tokens.expired_unused=0
invite_tokens.total=0
invite_tokens.used=0
```

## Role Distribution

- admin: 2

## V-KPI Permissions

- active admin: 2
- active write: 0
- active read: 0
- active none: 0

## Gaps

- none
```

```bash
psql -d vkpi -c "
SELECT role, COUNT(*) FROM staff GROUP BY role;" 2>/dev/null
```

```text
FAILED returncode=2
```

```bash
ls -la docs/vkpi/p12* 2>/dev/null
```

```text
-rw-r--r--@ 1 bibiboer  staff  1752 May 19 15:01 docs/vkpi/p12-1-rbac-status-cli.md
-rw-r--r--@ 1 bibiboer  staff  1051 May 19 15:02 docs/vkpi/p12-3-rbac-status-api.md
-rw-r--r--@ 1 bibiboer  staff  1188 May 19 15:05 docs/vkpi/p12-4-rbac-status-frontend.md
-rw-r--r--@ 1 bibiboer  staff  2953 May 19 15:06 docs/vkpi/p12-rbac-magic-link-completion-report.md
-rw-r--r--@ 1 bibiboer  staff  4790 May 19 14:59 docs/vkpi/p12-rbac-magic-link-v0.md
```

## 13. 横向健康

```bash
.venv/bin/python scripts/vkpi_operating_review.py --limit 20
```

```text
# V-KPI Operating Review

```text
scenario=vkpi_operating_review
provider_calls=false
write_db=false
competitor_signals=25
memory_feedback=0
open_alerts=1
pending_competitor_signals=0
recommendation_feedback=0
recommendation_outcomes=171
```

## Top Work Items

1. [open_alert] Recommendation run needs feedback: recrun-af0053af53b32e1a source=vkpi_alerts:200 reason=recommendation.review_gap
2. [recommendation_feedback_gap] recrun-af0053af53b32e1a source=vkpi_kol_recommendation_runs:517 reason=75 recommendations without feedback
3. [recommendation_feedback_gap] p4nlm-d8091029270e3230 source=vkpi_kol_recommendation_runs:518 reason=3 recommendations without feedback
4. [recommendation_feedback_gap] p4kpf-b6b5445fea4dca44 source=vkpi_kol_recommendation_runs:519 reason=3 recommendations without feedback
5. [recommendation_feedback_gap] p4pna-77d0e0701d07c394 source=vkpi_kol_recommendation_runs:520 reason=3 recommendations without feedback

## Gaps

- open_alerts_need_resolution
- recommendation_feedback_empty
- memory_feedback_empty
- recommendation_runs_without_feedback


2026-05-19 16:31:04,103 [INFO] viltrox.core.config: JWT_SECRET loaded from environment
2026-05-19 16:31:04,103 [INFO] viltrox.core.config: yt-dlp running without proxy (direct IP)
```

```bash
grep -E "057|058|059|060" backend/app/db/connection.py
```

```text
    "057_vkpi_ai_cost_budget.sql",
    "058_vkpi_legacy_import.sql",
    "058a_vkpi_legacy_import_launch_plan.sql",
    "058b_vkpi_legacy_import_dedupe.sql",
    "058c_vkpi_legacy_import_batch_column_compat.sql",
    "058d_vkpi_legacy_official_materials.sql",
    "058e_vkpi_legacy_entity_resolution.sql",
    "058f_vkpi_legacy_kol_entities_decisions.sql",
    "058g_vkpi_legacy_commit_attempts.sql",
    "059_vkpi_memory_tables.sql",
    "060_vkpi_budget_caps_defaults.sql",
```

```bash
ls migrations/ | grep -E "_down\.sql$" | wc -l
```

```text
      16
```

```bash
ls migrations/ | grep -v "_down\.sql$" | wc -l
```

```text
      70
```

```bash
cd frontend && npm run build 2>&1 | tail -10 && cd ..
```

```text
dist/assets/chunk-BFvfBhpT.js     0.95 kB
dist/assets/chunk-mNftlTX2.js     2.39 kB
dist/assets/app-DYpZ-Zkm.js     209.45 kB
dist/assets/chunk-Bu6CSJ_-.js   651.52 kB

(!) Some chunks are larger than 500 kB after minification. Consider:
- Using dynamic import() to code-split the application
- Use build.rollupOptions.output.manualChunks to improve chunking: https://rollupjs.org/configuration-options/#output-manualchunks
- Adjust chunk size limit for this warning via build.chunkSizeWarningLimit.
✓ built in 663ms
```

## 14. 文档完整性

```bash
ls docs/vkpi/ | wc -l
```

```text
      63
```

```bash
ls docs/vkpi/p*.md | sort
```

```text
docs/vkpi/p10-1-learning-snapshot-cli.md
docs/vkpi/p10-2-learning-snapshot-api.md
docs/vkpi/p10-learning-loop-completion-report.md
docs/vkpi/p10-learning-loop-v0.md
docs/vkpi/p11-1-realtime-status-api.md
docs/vkpi/p11-2-task-center-sse-adapter.md
docs/vkpi/p11-sse-task-stream-completion-report.md
docs/vkpi/p11-sse-task-stream-v0.md
docs/vkpi/p12-1-rbac-status-cli.md
docs/vkpi/p12-3-rbac-status-api.md
docs/vkpi/p12-4-rbac-status-frontend.md
docs/vkpi/p12-rbac-magic-link-completion-report.md
docs/vkpi/p12-rbac-magic-link-v0.md
docs/vkpi/p3-memory-completion-report.md
docs/vkpi/p4-1-dry-run-acceptance.md
docs/vkpi/p4-10-project-next-action-design.md
docs/vkpi/p4-11-project-next-action-acceptance.md
docs/vkpi/p4-12-project-next-action-reasons.md
docs/vkpi/p4-13-project-next-action-persisted-runs.md
docs/vkpi/p4-14-recommendation-run-review-ui.md
docs/vkpi/p4-2-recommendation-reasons.md
docs/vkpi/p4-3-persisted-preview-runs.md
docs/vkpi/p4-4-preview-run-api.md
docs/vkpi/p4-5-kol-product-fit-dry-run.md
docs/vkpi/p4-6-kol-product-fit-acceptance.md
docs/vkpi/p4-7-kol-product-fit-reasons.md
docs/vkpi/p4-8-kol-product-fit-persisted-runs.md
docs/vkpi/p4-9-two-scenario-run-index.md
docs/vkpi/p4-new-launch-match-dry-run.md
docs/vkpi/p5-budget-guard-integration.md
docs/vkpi/p5-budget-monitor-ui.md
docs/vkpi/p6-2-content-brain-dry-run.md
docs/vkpi/p6-3-content-brain-budget-scope.md
docs/vkpi/p6-4-content-brain-commit-analysis.md
docs/vkpi/p6-5-content-brain-review-surface.md
docs/vkpi/p6-content-brain-completion-report.md
docs/vkpi/p6-content-brain-v0.md
docs/vkpi/p7-1-budget-guard-alert-rule.md
docs/vkpi/p7-2-content-brain-backlog-alert.md
docs/vkpi/p7-3-recommendation-review-gap-alert.md
docs/vkpi/p7-alerts-completion-report.md
docs/vkpi/p8-1-competitor-brain-preview.md
docs/vkpi/p8-2-competitor-signal-schema.md
docs/vkpi/p8-3-competitor-signal-commit.md
docs/vkpi/p8-4-competitor-brain-review-surface.md
docs/vkpi/p8-competitor-brain-completion-report.md
docs/vkpi/p8-competitor-brain-v0.md
docs/vkpi/p9-1-natural-search-cli.md
docs/vkpi/p9-2-natural-search-api.md
docs/vkpi/p9-3-natural-search-frontend.md
docs/vkpi/p9-natural-search-completion-report.md
docs/vkpi/p9-natural-search-v1.md
docs/vkpi/post-v531-operating-review.md
```

```bash
git log --oneline --all --grep="P1\|P2\|P3\|P4\|P5\|P6\|P7\|P8\|P9\|P10\|P11\|P12" | head -100
```

```text
c7c960c docs(vkpi): summarize P4 to current progress
92ebec8 docs(vkpi): report P11 SSE completion
bf8e4b1 docs(vkpi): design P11 SSE task stream
f2a03ae docs(vkpi): report P12 RBAC completion
7cf699b docs(vkpi): design P12 RBAC and magic link boundary
3459bd3 docs(vkpi): report P10 learning snapshot completion
a5b9042 docs(vkpi): design P10 learning snapshot
f6fb169 docs(vkpi): report P9 natural search completion
527f99d docs(vkpi): design P9 deterministic natural search
49553e6 docs(vkpi): report P8 competitor brain completion
7cd2466 docs(vkpi): design P8 competitor brain preview
74a43e8 docs(vkpi): close P7 alert anomaly layer
46ea765 docs(vkpi): report P6 content brain completion
cf9820b feat(vkpi): add P4 project next-action dry-run
9bb11dd docs(vkpi): design P4 project next-action suggestions
d9fb7e9 docs(vkpi): verify P4 two-scenario run index
08f08d8 feat(vkpi): add P4 KOL product fit dry-run
c3d824a docs(vkpi): design P4 KOL product fit dry-run
a4f062b feat(vkpi): persist P4 new launch preview runs
c6f7cec feat(vkpi): add budget-gated P4 recommendation reasons
5a82f34 docs(vkpi): verify P4 new launch dry-run acceptance
fd2228d feat(vkpi): add P4 new launch match dry-run
da6cf78 docs(vkpi): design P4-1 new launch match dry-run
66fed26 feat(vkpi): seed budget caps for P4 readiness
060cb51 feat(vkpi): preserve P2D commit attempt history
6010700 docs(vkpi): document P3.5 readiness and feedback flow
b2b35cc docs(vkpi): add P3 memory completion report
036dc89 docs(vkpi): document P3 market memory signals
2df69e7 docs(vkpi): document P3 product family normalization
b99f459 docs(vkpi): document P3 memory query helpers
5c0ba13 feat(vkpi): add memory query helpers for P4 inputs
876d759 docs(vkpi): record P2D rollback drill verification
819ff75 chore(vkpi): add P2D rollback drill verifier
140f62c fix(vkpi): store P2D rollback windows in UTC
5f69dcb feat(vkpi): harden P2D rollback and recommit drill
dc186f5 docs(vkpi): record P2D KOL pool commit results
c86e1ba docs(vkpi): document P2D KOL pool dry-run
7a79aee feat(vkpi): add P2D KOL pool dry-run planner
942f234 docs(vkpi): document P2C-2 review decisions
0aa275b docs(vkpi): record P2B staging quality findings
0dcc4fe [P4.4B] fix discover search loading and overflow
61c446a [P4.4A] audit media UX truth
cf058a3 [P4.3D] document rollback playbook
0624cd7 [P4.3C] harden manual cron triggers
238793f [P4.3B] refresh business audit QA evidence
8c80b46 [P4.3B] audit business mutations
d0914b4 [P4.3A] confirm settings mutations
679d615 [P4.2D] decide P0 fix batches
851f916 [P4.2C] qa P0 mutation paths
5c2bd59 [P4.2B-1] audit first-tier mutation safety
ce83511 [P4.2A] add write endpoint inventory
f346672 docs(p3): define P4 transition boundary
1efd40b docs(p3): add team distribution guide
4a3f98c docs(p3): freeze team handoff release
aca2d4c fix(p3): align frontend build metadata in health
2bd6ecb chore(p3): checkpoint QA and analytics worktree
029027b feat(p1.6): add weekly report generation
3c9b03d feat(p1.5): add content pillar classification
d92a7ae feat(p1.3): add crawler comment interfaces
44a91cb feat(p1.3): add comments collection schema and API
dd828cd feat(p1): add V-KPI compatibility gate
```

```bash
echo "AUDIT_DONE"
```

```text
AUDIT_DONE
```

## 15. 已知问题(基于查询结果列出)

- FAILED command in section 1. P1 基础设施: `psql -d vkpi -c "
SELECT scope, cap_usd, current_spend, warning_at, hard_stop_at, fallback_action
FROM vkpi_provider_budget_caps
ORDER BY scope;"` returned 2
```text
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```
- FAILED command in section 1. P1 基础设施: `psql -d vkpi -c "
SELECT COUNT(*) AS ai_cost_calls, COALESCE(SUM(cost_usd), 0) AS total_spend
FROM vkpi_ai_cost_ledger;"` returned 2
```text
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```
- FAILED command in section 1. P1 基础设施: `psql -d vkpi -c "
SELECT ai_provider, COUNT(*), SUM(cost_usd)
FROM vkpi_ai_cost_ledger
WHERE occurred_at > NOW() - INTERVAL '30 days'
GROUP BY ai_provider;"` returned 2
```text
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```
- FAILED command in section 2. P2 历史导入: `psql -d vkpi -c "
SELECT batch_uid, status, committed_at, rollback_until, rolled_back_at, 
       committed_rows, rolled_back_rows, source_file_sha256
FROM vkpi_legacy_import_batches
ORDER BY id;"` returned 2
```text
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```
- FAILED command in section 2. P2 历史导入: `psql -d vkpi -c "
SELECT pipeline, COUNT(*)
FROM (
  SELECT 'kol_profiles' AS pipeline FROM vkpi_legacy_kol_profiles_staging
  UNION ALL SELECT 'cooperations' FROM vkpi_legacy_cooperations_staging
  UNION ALL SELECT 'launch_plans' FROM vkpi_legacy_launch_plans_staging
  UNION ALL SELECT 'official_content' FROM vkpi_legacy_official_content_staging
  UNION ALL SELECT 'official_materials' FROM vkpi_legacy_official_materials_staging
  UNION ALL SELECT 'product_costs' FROM vkpi_legacy_product_costs_staging
  UNION ALL SELECT 'risk_watchlist' FROM vkpi_legacy_risk_watchlist_staging
  UNION ALL SELECT 'voc_alerts' FROM vkpi_legacy_voc_alerts_staging
) x GROUP BY pipeline ORDER BY pipeline;"` returned 2
```text
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```
- FAILED command in section 2. P2 历史导入: `psql -d vkpi -c "
SELECT weak_label, resolution_decision, COUNT(*)
FROM vkpi_legacy_kol_entities
GROUP BY weak_label, resolution_decision
ORDER BY weak_label, resolution_decision;"` returned 2
```text
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```
- FAILED command in section 2. P2 历史导入: `psql -d vkpi -c "
SELECT commit_attempt, 
       COALESCE(rollback_status, 'active') AS status,
       COUNT(*)
FROM vkpi_legacy_import_committed_refs
GROUP BY commit_attempt, rollback_status
ORDER BY commit_attempt;"` returned 2
```text
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```
- FAILED command in section 2. P2 历史导入: `psql -d vkpi -c "
SELECT 
  COUNT(*) AS total,
  COUNT(*) FILTER (WHERE source_type='legacy_excel_p2d') AS legacy_imported,
  COUNT(*) FILTER (WHERE sync_status='imported') AS imported,
  COUNT(*) FILTER (WHERE sync_status='needs_human_review') AS needs_review
FROM vkpi_kol_pool;"` returned 2
```text
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```
- FAILED command in section 3. P3 Memory: `psql -d vkpi -c "
SELECT entity_type, COUNT(*) FROM vkpi_memory_entities GROUP BY entity_type ORDER BY 2 DESC;"` returned 2
```text
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```
- FAILED command in section 3. P3 Memory: `psql -d vkpi -c "
SELECT fact_type, COUNT(*) FROM vkpi_memory_facts GROUP BY fact_type ORDER BY 2 DESC LIMIT 15;"` returned 2
```text
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```
- FAILED command in section 3. P3 Memory: `psql -d vkpi -c "
SELECT link_type, COUNT(*) FROM vkpi_memory_links GROUP BY link_type ORDER BY 2 DESC;"` returned 2
```text
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```
- FAILED command in section 3. P3 Memory: `psql -d vkpi -c "
SELECT COUNT(*) AS memory_feedback FROM vkpi_memory_feedback;"` returned 2
```text
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```
- FAILED command in section 4. P4 推荐层: `psql -d vkpi -c "
SELECT COUNT(*) AS total_recommendations FROM vkpi_kol_recommendations;"` returned 2
```text
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```
- FAILED command in section 4. P4 推荐层: `psql -d vkpi -c "
SELECT scenario, COUNT(*) AS items, AVG(score)::numeric(6,2) AS avg_score
FROM vkpi_kol_recommendations
GROUP BY scenario
ORDER BY items DESC;"` returned 2
```text
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```
- FAILED command in section 4. P4 推荐层: `psql -d vkpi -c "
SELECT COUNT(*) FROM vkpi_recommendation_outcomes;"` returned 2
```text
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```
- FAILED command in section 4. P4 推荐层: `psql -d vkpi -c "
SELECT immediate_action, COUNT(*) FROM vkpi_recommendation_outcomes
GROUP BY immediate_action;"` returned 2
```text
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```
- FAILED command in section 5. P5 Budget Guard: `psql -d vkpi -c "
SELECT scope, cap_usd, current_spend,
       (current_spend / NULLIF(cap_usd, 0) * 100)::numeric(5,2) AS pct_used,
       fallback_action
FROM vkpi_provider_budget_caps
ORDER BY scope;"` returned 2
```text
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```
- FAILED command in section 7. P7 告警: `psql -d vkpi -c "
SELECT alert_type, severity, COUNT(*),
       COUNT(*) FILTER (WHERE resolved_at IS NULL) AS open,
       COUNT(*) FILTER (WHERE resolved_at IS NOT NULL) AS resolved
FROM vkpi_alerts
GROUP BY alert_type, severity
ORDER BY alert_type;"` returned 2
```text
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```
- FAILED command in section 10. P10 Learning Loop: `psql -d vkpi -c "
SELECT COUNT(*) FROM vkpi_recommendation_feedback;"` returned 2
```text
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```
- FAILED command in section 10. P10 Learning Loop: `psql -d vkpi -c "
SELECT feedback_type, COUNT(*) FROM vkpi_recommendation_feedback
GROUP BY feedback_type;"` returned 2
```text
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```
- FAILED command in section 10. P10 Learning Loop: `psql -d vkpi -c "
SELECT COUNT(*) FROM vkpi_memory_feedback;"` returned 2
```text
psql: error: connection to server on socket "/tmp/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
```
- FAILED command in section 12. P12 RBAC: `psql -d vkpi -c "
SELECT role, COUNT(*) FROM staff GROUP BY role;" 2>/dev/null` returned 2
- recommendation_feedback_empty appears in Learning Loop output.
- memory_feedback_empty appears in Learning Loop output.
- open_alerts_need_resolution appears in Learning Loop output.
- recommendation_outcomes_have_no_shortlist_actions appears in Learning Loop output.
- recommendation.review_gap appears in alert/operating review output.

## 16. 红线指标(任何不对的)

- provider_calls_allowed=false in readiness output.
- recommendation_feedback_ready=false in learning snapshot output.
- memory_feedback_ready=false in learning snapshot output.
- One or more health-audit commands failed.
