# P3.9O KOL Pool Auto-Link Only Guard

Date: 2026-05-13

## Scope

Continue P3.5-P3.9 cleanup by removing the remaining UI-level ambiguity around KOL Pool main-table linking.

## Changes

- `KolPoolPanel.tsx`
  - Removed the public `onLinkToMain` prop and all manual-link semantics from the panel.
  - Renamed the detail drawer action to `onPromoteToMain` so future code search does not confuse the action with the old manual ID path.
  - Main-table action now only means automatic create/match/link.

- `DiscoverPage.tsx`
  - No longer imports or wires `linkKolPoolToMain` into the KOL Pool panel.

- `smoke_vkpi_kol_pool_decision_view_frontend.py`
  - Keeps the guard that fails if `window.prompt` or `主 KOL ID` reappears in `KolPoolPanel.tsx`.

## Verification

```bash
rg -n "window\.prompt|主 KOL ID|onLinkToMain=|linkKolPoolToMain" frontend/src/components/vkpi scripts -S || true
```

Result: only the smoke guard itself contains the forbidden strings.

```bash
./scripts/run_smoke.sh \
  smoke_vkpi_kol_pool_promote_to_main.py \
  smoke_vkpi_kol_pool_decision_view_frontend.py \
  smoke_vkpi_p3_1d_settings_crawl_ui.py
```

Result: PASS=3 / FAIL=0 / TOTAL=3.

```bash
cd frontend && npm run build
```

Result: PASS.
