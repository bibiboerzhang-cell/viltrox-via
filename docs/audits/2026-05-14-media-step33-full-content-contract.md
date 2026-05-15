# P4 Step33 - Media Full Content / Top-only Contract

## Scope

This round only covers the data-analysis media list loading contract. It does not change crawler behavior, media proxy behavior, or post-analysis logic.

## Problem Checked

User-visible issue: media/content areas looked like they only exposed top items, even when the UI had buttons that implied more content.

Root cause found in current code:

- Project post list API supports `limit <= 500`, but the frontend loaded only `100` posts through `listIndustryPosts(apiToken, projectId, 100)`.
- Account detail service hard-coded `LIMIT 50`, so account profile pages could never show more than 50 posts, regardless of UI toggles.
- Home, Posts, and account Content tabs already had Top/All toggles, but those toggles only operated on the already-loaded subset.

## Changes

- `backend/app/services/vkpi/industry_data.py`
  - `get_account(account_id, post_limit=500)` now supports a bounded post limit.
  - Account detail posts are capped at 500 instead of a hard-coded 50.

- `backend/app/api/routers/vkpi_industry_automation.py`
  - `GET /api/admin/vkpi/industry-data/accounts/{account_id}` now accepts `limit`, default 500, max 500.

- `frontend/src/services/vkpi.ui-api.ts`
  - `getIndustryAccount(..., limit=500)` sends the limit explicitly.
  - `listIndustryPosts(..., limit=500)` defaults to max supported window.

- `frontend/src/components/vkpi/pages/data-analysis/CrossPlatformPanel.tsx`
  - Initial project post load now asks for 500 posts.

- `scripts/smoke_vkpi_p4_33_media_full_content_contract.py`
  - Added a static contract smoke to prevent regression to top-only windows.

## Verification

Commands run:

```bash
./scripts/run_smoke.sh smoke_vkpi_p4_33_media_full_content_contract.py smoke_vkpi_p4_4_media_ux_contract.py smoke_vkpi_p3_13c_post_detail_contract.py
```

Result:

```text
PASS=3 / FAIL=0 / TOTAL=3
```

Additional checks:

```bash
.venv/bin/python -m py_compile scripts/smoke_vkpi_p4_33_media_full_content_contract.py backend/app/services/vkpi/industry_data.py backend/app/api/routers/vkpi_industry_automation.py
cd frontend && npm run build
git diff --check
```

Results:

- `py_compile`: PASS
- `npm run build`: PASS
- `git diff --check`: PASS

## Current Truth

- The UI now loads up to 500 posts and can show all loaded posts.
- This is not infinite pagination. If an account/project has more than 500 posts, a future round must add cursor/pagination.
- Existing Top/All buttons are real UI-state buttons, not fake buttons.
- Full Socialinsider-style long-history exploration still needs pagination, date slicing, and backend aggregation. That is P5-level, not this Step33 fix.

## Remaining Media Gaps

1. Add real pagination/cursor if accounts exceed 500 stored posts.
2. Improve visual media cards for the profile page so it feels less like raw data tables.
3. Add explicit "loaded N / total available unknown" copy until backend has total counts.
4. Continue browser QA with real login to verify video playback and original-post links after this limit change.
