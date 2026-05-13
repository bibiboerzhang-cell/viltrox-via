# V-KPI P3.28 to P4 Transition Plan

Date: 2026-05-14

## Scope

P3.28 closes the local P3 execution loop and defines the P4 entry boundary.

It does not add business features. It exists to prevent P3 from expanding into
Socialinsider-level analytics work before the team has used the current system.

## Current P3 Release State

- Branch: `codex/vkpi-cleanup-d7`
- Current P3 package:
  `/Users/bibiboer/Downloads/vkpi-team-handoff-p3-20260514-053014-f3466726.zip`
- Current local release tag:
  `p3-to-p4-transition-20260514-f3466726`
- Git remote: not configured in this checkout.

## P3 Close Standard

P3 is closed against internal team usability, not full Socialinsider parity.

P3 is acceptable when:

- team members can run the source package with their own local `.env`;
- runtime identity is visible through `/health`;
- clean-package scans pass with no secrets or oversized files;
- monitoring, backup readiness, feedback entry, feedback admin, report export,
  and core fake-button QA smokes pass;
- team feedback can be collected during real use.

## P3 Non-Goals

The following are intentionally not P3 blockers:

- Socialinsider-level dashboard parity;
- full historical analytics across every metric;
- automated KOL deep-analysis agents;
- full video understanding at scale;
- Feishu/Gmail/OAuth integrations beyond current internal feedback paths;
- external customer packaging.

These belong to P4/P5 unless real team usage shows that one of them blocks
daily work.

## P4 Entry Modules

P4 should start from observed team use, not from a broad feature wish list.

### P4.1 Real Team Observation

Goal: let multiple employees use the current package and collect real feedback.

Acceptance:

- at least 3-5 real staff accounts use the system;
- feedback is submitted through the in-app feedback widget;
- feedback admin page can triage issues by status;
- issues are classified as blocker / workflow friction / enhancement.

Readiness gate:

- `docs/VKPI_P4_1_REAL_TEAM_OBSERVATION.md`
- `scripts/smoke_vkpi_p4_1_observation_readiness.py`

### P4.2 Role and Scope Audit Follow-Up

Goal: verify data visibility with more than two users.

Acceptance:

- one manager account and at least two employee accounts tested;
- employee account cannot see another employee's private KOL/project data;
- manager view can intentionally switch scope where allowed;
- any leaked endpoint is fixed with a targeted patch.

### P4.3 Daily Top100 Candidate Source Stabilization

Goal: make Daily Top100 useful only if the upstream candidate source is alive.

Acceptance:

- monitored product source exists or empty-state reason is explicit;
- candidate generation has a visible trigger and audit trail;
- duplicate assignment stays prevented;
- dashboard distinguishes no data, no staff, no eligible candidates, and
  generated candidates.

### P4.4 Media UX Completion

Goal: make account/post media usable enough for daily KOL decision work.

Acceptance:

- avatar/image/video cards show real media when available;
- original post opens in the source platform;
- broken media shows a reason rather than a fake preview;
- single-post analysis has a clear loading/error/result state;
- "view all" lists are real, not decorative.

### P4.5 Outreach and Communication History

Goal: turn the system from only "seeing data" into "doing KOL work".

Acceptance:

- KOL detail has a communication history panel;
- staff can log contact notes, links, screenshots, and attachment references;
- communication records link to KOL/project/staff;
- later Gmail/Feishu integrations can attach to the same record model.

### P4.6 Cost and Provider Observability

Goal: control API/LLM/crawler spend during deeper automation.

Acceptance:

- cost dashboard shows provider/model/task/user breakdown;
- daily and monthly limits are visible;
- exceeded limits downgrade or block non-critical tasks;
- Apify/YouTube/LLM live tests leave cost/audit records.

## P5 Direction

P5 is product-depth work after P4 proves daily team value.

Candidate P5 tracks:

- Socialinsider-style analytics parity;
- multi-model KOL content analysis;
- video/image understanding at scale;
- product-library synchronization and SKU intelligence;
- Feishu/Gmail integrations;
- agentic project follow-up and reminder automation;
- externalizable V-OS packaging.

## Next Operational Decision

Before any P4 code starts, decide one of:

1. Add a Git remote and push the P3 branch and tag.
2. Hand off the zip directly and run a 1-2 week internal observation period.

If option 2 is chosen, do not add new P3 features during the observation window.
Only fix blockers that prevent real staff usage.
