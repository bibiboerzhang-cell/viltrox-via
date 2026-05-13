# P3 Clean Package Progress Strategy

Date: 2026-05-13
Repo: `/Users/bibiboer/Documents/V-KPI——marketing`
Purpose: clean source package scan, current progress reset, and next execution strategy.

## 1. Package Scan Result

Current source tree before this report was clean.

- Strict documentation inventory: 45 files.
- Documentation coverage includes root README/deployment docs, P2 release notes/status, P3.9 QA records, and P3.10 communication QA records.
- Secret pattern scan result: 0 source/doc hits for Google API keys, Apify tokens, OpenAI keys, or Anthropic keys.
- Large local-only files found outside package scope:
  - `runtime/data/postgres/pg_wal/*` local Postgres WAL files.
  - `submissions.db` local SQLite data file.
- Clean package must exclude `.env*`, `.git`, `.venv`, `node_modules`, `frontend/dist`, `runtime`, `backend/runtime`, `uploads`, `frames`, local DB files, caches, logs, and archives.

## 2. Current Progress Verdict

### P2

P2 is closed as a shareable baseline. `docs/VKPI_P2_30_RELEASE_NOTES.md` records the P2 clean-package policy and `77/77` full-smoke baseline from that phase.

### P3.5-P3.8

The earlier P3.5-P3.8 surface was patched through P3.9 deep audit, but it is not equivalent to a complete Socialinsider-grade analytics product.

Verified or protected:

- KOL Pool field mapping and promote-to-main flow were protected by smoke and UI wording updates.
- Daily Top100 ambiguity was separated into scope/counting issues instead of one misleading `0/11` display.
- Button inventory and risky action QA started to remove fake actions from the main project workflow.

Still open:

- Global owner/team permission filtering is not complete across every KOL/project/media API.
- Analytics pages still lack full historical trends, metric picker, compare, sentiment, topic tracking, and content pillar maturity.
- Media experience still needs full-content mode, original-post open, playback fallback, and single-post analysis.

### P3.9

P3.9 is complete for its bounded project-flow scope.

Verified real paths:

- Project creation with KOL and product selection.
- Project detail drawer entry.
- Attachment upload and readback.
- Stage advance and cost entry API paths.
- PDF/CSV export and weekly report endpoint paths.
- Explicit row detail action instead of hidden row-click dependency.

P3.9 is not a claim that all analytics, collaboration, or media functionality is complete.

### P3.10

P3.10 communication workflow has a real browser-verified start.

Verified:

- UI message record creation.
- UI evidence file upload.
- Project detail API readback of message and evidence URL.

Still open:

- Communication history needs role visibility rules.
- Communication categories, reminders, and status linkage remain future work.
- Email/Gmail/Feishu integration is not yet implemented.

## 3. Main Product Gaps Now

These are the real gaps that should drive the next rounds:

1. Permission and collaboration model: every new API must be written with current staff and owner/team scope in mind, even if enforcement is staged.
2. Daily Top100: confirm real candidate source, employee assignment rules, duplicate assignment prevention, and visible empty-state reasoning.
3. System Settings platform crawl UI: replace dense 13-card configuration grid with compact list plus single-platform detail panel.
4. Data Quality page: reduce button overload through grouped actions and explicit enable/disabled reasons.
5. Media and post analysis: images/video must have reliable proxy/fallback, open-original action, full-list view, and single-post analysis.
6. Socialinsider-style analytics: date range, compare, metric picker, trend charts, and sections should be real data paths, not UI-only controls.
7. Export/report/browser QA: endpoints are real, but UI download/open behavior must be regression-tested after each UI change.
8. Deployment/runtime consistency: keep build hash, backend commit, cache behavior, and clean package checks visible before handoff.

## 4. Recommended Next Strategy

Do not reopen broad file splitting as the main path. The main risk is no longer file size; it is fake controls, partial data paths, and team workflow gaps.

Recommended order:

1. P3.10C - Collaboration contract prep
   - Add or confirm owner/team fields in new API responses.
   - Define `my / team / all` visibility semantics.
   - Acceptance: no newly touched KOL/project/media endpoint is written without current staff context.

2. P3.11 - Daily Top100 real chain
   - Trace candidate generation from source table to staff assignment to UI.
   - Remove misleading employee coverage wording.
   - Prevent duplicate assignment across staff.
   - Acceptance: one seeded real candidate path and one empty-state path both render correctly.

3. P3.12 - System Settings platform crawl UI收口
   - 13 platforms become a compact list.
   - Each row has a direct enable/disable switch.
   - Advanced budget/API/limit settings move into one focused detail panel.
   - Acceptance: Instagram can be enabled/disabled and budget/limits saved without page overflow.

4. P3.13 - Data Quality and risky buttons consolidation
   - Group actions by risk and data object.
   - Dead buttons must become disabled with reason or real endpoint-backed actions.
   - Acceptance: no primary visible button in this page is UI-only.

5. P3.14 - Media/post UX and single-post analysis
   - Full content list, open original, media proxy fallback, and post-analysis action.
   - Acceptance: at least Instagram and YouTube each have one real media item path tested.

6. P3.15 - Handoff hardening
   - Clean package, release notes, smoke matrix, feedback channel, and runtime health checks.
   - Acceptance: teammate can unzip, configure `.env`, start, login, and run the documented smoke set.

## 5. P4/P5 Boundary

P4 should not start until the P3 workflow is usable by real staff.

P4 should focus on intelligence layers:

- Multi-model KOL profile analysis.
- Batch video/content summarization.
- Sentiment, topic, pillar, and audience fit scoring.
- Cost dashboard and LLM budget gates before agent automation.
- Agent suggestions only after communication history and project state are trustworthy.

P5 should focus on operationalization:

- Feishu/Gmail/Slack style integrations.
- Backup/restore and production monitoring.
- Scheduled reporting and user feedback loop.
- Externalizable team handoff, not more local-only feature piling.

## 6. Package Acceptance Checklist

For this clean package:

- Archive integrity test must pass.
- Forbidden-path scan must return no `.env`, runtime data, node_modules, dist, upload files, local DB, or git directory.
- Secret pattern scan must return 0 high-confidence source/doc hits.
- Package size should be practical for teammate handoff; if it is unexpectedly large, inspect artifact paths before delivery.
