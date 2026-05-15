# P4 Step34 - Media Loaded Count / Window Truth

## Scope

This round only improves media-list truthfulness in Data Analysis. It does not change crawler behavior, post ingestion, video proxy behavior, or analysis models.

## Why

After Step33, the page could load up to 500 posts and expose Top/All toggles. The remaining UX problem was that users could still read "显示全部" as "all historical platform content". In reality, the current backend contract is a 500-row loaded window.

## Changes

- `frontend/src/components/vkpi/pages/data-analysis/tabs/HomeTab.tsx`
  - Top Posts header now shows loaded-window copy.
  - If 500 posts are loaded, it explicitly says more history needs pagination.

- `frontend/src/components/vkpi/pages/data-analysis/tabs/PostsTab.tsx`
  - Posts tab side text now shows filtered count, loaded count, and loaded-window truth.
  - The collapsed-state hint says it shows all loaded content, not all infinite history.

- `frontend/src/components/vkpi/pages/data-analysis/drawers/tabs/index.tsx`
  - Account Content tab now shows visible/loaded count and 500-window cap text.

- `frontend/src/components/vkpi/pages/data-analysis/styles/data-analysis.css`
  - Added `da-load-window-note` and `da-load-window-hint` styles.

- `scripts/smoke_vkpi_p4_34_media_loaded_count_contract.py`
  - Added static contract smoke for loaded-window truth.

## Verification

Matched media smoke group:

```bash
./scripts/run_smoke.sh \
  smoke_vkpi_p4_34_media_loaded_count_contract.py \
  smoke_vkpi_p4_33_media_full_content_contract.py \
  smoke_vkpi_p4_4_media_ux_contract.py \
  smoke_vkpi_p3_13c_post_detail_contract.py
```

Result:

```text
PASS=4 / FAIL=0 / TOTAL=4
```

Additional checks:

```bash
.venv/bin/python -m py_compile \
  scripts/smoke_vkpi_p4_34_media_loaded_count_contract.py \
  scripts/smoke_vkpi_p4_33_media_full_content_contract.py \
  backend/app/services/vkpi/industry_data.py \
  backend/app/api/routers/vkpi_industry_automation.py

cd frontend && npm run build
git diff --check
```

Results:

- `py_compile`: PASS
- `npm run build`: PASS
- `git diff --check`: PASS

## Current Truth

- `显示全部` now means all loaded posts in the current 500-row window.
- It does not mean full account history from the platform.
- If an account/project needs more than 500 posts, the next engineering step is cursor/pagination.

## Remaining Media Work

1. Browser QA with logged-in session: confirm visible loaded-window text, full-list toggle, original post links, post drawer, and video fallback.
2. Backend pagination/cursor if product requires deeper history than 500 rows.
3. Improve card layout and media density for Socialinsider-style browsing.
