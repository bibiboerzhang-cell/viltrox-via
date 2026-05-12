# P3.9N KOL Pool Manual-ID Removal + Settings Crawl UI Check

Date: 2026-05-13

## Scope

Continue the P3.5-P3.9 cleanup after the P3.9 deep audit. This pass removes the remaining manual main-KOL ID prompt from KOL Pool and verifies the Settings platform crawl UI is already in the compact list/detail shape.

## Changes

- `KolPoolPanel.tsx`
  - Removed the old `window.prompt` path for manually entering a main KOL ID.
  - Removed `onLinkToMain` from the panel API.
  - The only visible KOL Pool main-table action is now the automatic create/match/link path through `onPromoteToMain`.

- `DiscoverPage.tsx`
  - Removed the old `linkKolPoolToMain` wiring from the KOL Pool tab.

- `smoke_vkpi_kol_pool_decision_view_frontend.py`
  - Added a static regression check that blocks `window.prompt` / `主 KOL ID` from returning to `KolPoolPanel.tsx`.

## Settings Crawl UI Check

`SettingsControlPanels.tsx` already contains the compact platform crawl implementation:

- `vkpi-platform-crawl-console`
- platform list on the left
- selected platform detail on the right
- primary open/close action in the header/detail
- advanced range collapsed behind `展开高级范围`

This is protected by `smoke_vkpi_p3_1d_settings_crawl_ui.py`.

If the browser still shows the old dense 13-card layout, treat it as a stale bundle or not-current-build problem first, not a missing source-code patch.

## Verification

```bash
./scripts/run_smoke.sh \
  smoke_vkpi_kol_pool_promote_to_main.py \
  smoke_vkpi_kol_pool_decision_view_frontend.py \
  smoke_vkpi_project_create_selection_flow.py \
  smoke_vkpi_p2_28_project_flow_frontend.py \
  smoke_vkpi_p3_1d_settings_crawl_ui.py
```

Result: PASS=5 / FAIL=0 / TOTAL=5.

```bash
cd frontend && npm run build
```

Result: PASS.
