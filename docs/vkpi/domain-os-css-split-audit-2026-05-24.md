# V-KPI Domain OS CSS Split Audit

Date: 2026-05-24 PT

## Scope

This audit checks the CSS files created or affected by the current large-file split work. It focuses on whether the split is meaningful, not only whether the line count dropped.

The audit standards are:

1. File names must map to a component, page, or clear visual concern. Generic names like `part1.css` are not acceptable.
2. Each CSS file should be imported from no more than two places.
3. CSS selectors should map back to classes visible in TS/TSX source, unless the class is intentionally generated or legacy-preserved.

This scan is conservative. Selector warnings can include false positives for dynamic class names, status names, and legacy classes intentionally preserved for compatibility.

## Summary

| Metric | Count |
|---|---:|
| Audited CSS files | 46 |
| Files over 800 lines | 0 |
| Max CSS file length | 779 |
| Import-scope pass | 46 |
| Naming pass or acceptable wrapper | 46 |
| Selector pass | 28 |
| Selector warning | 17 |
| Selector fail | 1 |

## Result

The CSS split is mostly legitimate: the large CSS files were converted into semantic wrapper files and named part files such as `project-board-list.css`, `data-analysis-natural-search.css`, and `channel-kols-content.css`. It does not look like a pure line-count hard split.

However, the split is not clean enough to call closed. The selector scan found one high-risk file and several warning files that need either removal of unused rules or explicit compatibility notes.

## High-Risk File

| File | Lines | Issue | Required action |
|---|---:|---|---|
| `frontend/src/components/vkpi/pages/channels/styles/channel-kols-legacy-cards.css` | 418 | 16 of 19 class selectors were not found in TS/TSX source by static scan. | Confirm whether these legacy card classes are still rendered. Delete if unused, or document why they are compatibility-preserved. |

Suspect selectors:

```text
vkpi-my-kol-card
vkpi-my-kol-card__actions
vkpi-my-kol-card__avatar
vkpi-my-kol-card__contacts
vkpi-my-kol-card__content
vkpi-my-kol-card__content-head
vkpi-my-kol-card__identity
vkpi-my-kol-card__metrics
vkpi-my-kol-card__status
vkpi-my-kol-card__titleline
vkpi-my-kol-grid
vkpi-my-kol-link-button
vkpi-my-kol-post
vkpi-my-kol-post__body
vkpi-my-kol-post__media
vkpi-my-kol-posts
```

## Warning Files

| File | Lines | Suspect selectors | Notes |
|---|---:|---:|---|
| `frontend/src/components/vkpi/glass-future/components.css` | 413 | 3 | Broad shared glass file; should eventually split by component or design primitive. |
| `frontend/src/components/vkpi/pages/channels/styles/channel-kols-modal-responsive.css` | 214 | 9 | Shares legacy KOL card selectors with the high-risk file. |
| `frontend/src/components/vkpi/pages/data-analysis/styles/parts/data-analysis-charts-comments.css` | 573 | 5 | Check comment intelligence health/status classes. |
| `frontend/src/components/vkpi/pages/data-analysis/styles/parts/data-analysis-drawers.css` | 777 | 1 | Near the 800-line ceiling; next edit should split before adding more rules. |
| `frontend/src/components/vkpi/pages/data-analysis/styles/parts/data-analysis-overview-posts.css` | 750 | 8 | Near the 800-line ceiling; check tooltip/delta modifier usage. |
| `frontend/src/components/vkpi/pages/data-analysis/styles/parts/data-analysis-profile-import.css` | 460 | 12 | Platform classes may be generated from platform names; verify before deleting. |
| `frontend/src/components/vkpi/pages/discover/styles/discover-base.css` | 412 | 3 | Check removed hero metric classes. |
| `frontend/src/components/vkpi/pages/discover/styles/discover-context.css` | 456 | 1 | Check page modifier class. |
| `frontend/src/components/vkpi/pages/discover/styles/discover-responsive.css` | 81 | 3 | Responsive rules reference discover page/hero modifiers. |
| `frontend/src/components/vkpi/pages/discover/styles/discover-visual.css` | 388 | 1 | Check page modifier class. |
| `frontend/src/components/vkpi/pages/projects/styles/project-board-list.css` | 540 | 4 | Check project action/chip/KOL classes. |
| `frontend/src/components/vkpi/styles/vkpi-alerts-detail.css` | 762 | 1 | Near ceiling; shared detail panel selector needs owner. |
| `frontend/src/components/vkpi/styles/vkpi-dashboard-widgets.css` | 496 | 5 | Contains page modifier selectors; should move with dashboard domain later. |
| `frontend/src/components/vkpi/styles/vkpi-repair-foundation.css` | 702 | 7 | Status classes may be data-derived; keep only if rendered by Repair Center. |
| `frontend/src/components/vkpi/styles/vkpi-settings-traffic.css` | 514 | 31 | Highest warning count; likely includes removed platform crawl/settings UI. Needs focused cleanup. |
| `frontend/src/components/vkpi/styles/vkpi-shell.css` | 506 | 2 | Check user chip/version chip classes. |
| `frontend/src/components/vkpi/styles/vkpi-tables-loading.css` | 567 | 4 | Check table/message/pagination classes. |

## Pass Files

These files passed naming, import scope, and selector scan:

```text
frontend/src/components/vkpi/VkpiDashboard.css
frontend/src/components/vkpi/glass-future/responsive.css
frontend/src/components/vkpi/pages/channels/channelContent.css
frontend/src/components/vkpi/pages/channels/channelKols.css
frontend/src/components/vkpi/pages/channels/styles/channel-content-cards.css
frontend/src/components/vkpi/pages/channels/styles/channel-content-overlays.css
frontend/src/components/vkpi/pages/channels/styles/channel-content-reddit-responsive.css
frontend/src/components/vkpi/pages/channels/styles/channel-kols-accounts.css
frontend/src/components/vkpi/pages/channels/styles/channel-kols-content.css
frontend/src/components/vkpi/pages/channels/styles/channel-kols-shell.css
frontend/src/components/vkpi/pages/data-analysis/styles/data-analysis.css
frontend/src/components/vkpi/pages/data-analysis/styles/parts/data-analysis-base.css
frontend/src/components/vkpi/pages/data-analysis/styles/parts/data-analysis-brains.css
frontend/src/components/vkpi/pages/data-analysis/styles/parts/data-analysis-natural-search.css
frontend/src/components/vkpi/pages/discover/discoverDecision.css
frontend/src/components/vkpi/pages/discover/styles/discover-profile.css
frontend/src/components/vkpi/pages/discover/styles/discover-search.css
frontend/src/components/vkpi/pages/projects/projectBoard.css
frontend/src/components/vkpi/pages/projects/styles/campaign-execution-finance.css
frontend/src/components/vkpi/pages/projects/styles/campaign-materials-contracts.css
frontend/src/components/vkpi/pages/projects/styles/campaign-overview.css
frontend/src/components/vkpi/pages/projects/styles/campaign-task-modals.css
frontend/src/components/vkpi/pages/projects/styles/project-board-modals.css
frontend/src/components/vkpi/pages/projects/styles/project-board-responsive.css
frontend/src/components/vkpi/pages/projects/styles/project-detail-drawer.css
frontend/src/components/vkpi/styles/vkpi-intelligence-kol-agents.css
frontend/src/components/vkpi/styles/vkpi-repair-execution.css
frontend/src/components/vkpi/styles/vkpi-responsive-overrides.css
```

## Follow-Up

1. Inspect `channel-kols-legacy-cards.css` and either delete unused legacy card rules or document why they remain.
2. Inspect `vkpi-settings-traffic.css`; the warning count suggests it contains stale settings/platform crawl UI rules.
3. Do not add new rules to near-ceiling files such as `data-analysis-drawers.css`, `data-analysis-overview-posts.css`, and `vkpi-alerts-detail.css`; split them first if they need changes.
4. When a page moves into `frontend/src/domains/*`, move its CSS with it instead of creating more legacy `components/vkpi/styles/*` files.
