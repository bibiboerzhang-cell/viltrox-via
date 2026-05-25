# V-KPI Line Guard Baseline

Status: baseline debt report

This report records the first Domain OS line-guard baseline. The target is zero non-exempt business source files above 800 lines. Existing violations are migration debt, not acceptable precedent for new work.

Run:

```bash
PYTHONPATH=backend .venv/bin/python scripts/check_line_guard.py
```

Current summary from the pre-migration scan:

| Area | Count |
|---|---:|
| All source violations | 62 |
| Frontend violations | 18 |
| Backend violations | 42 |
| Test violations | 2 |

Highest-risk files:

| Lines | File |
|---:|---|
| 7535 | `frontend/src/components/vkpi/pages/RepairCenterPage.tsx` |
| 4377 | `frontend/src/components/vkpi/VkpiDashboard.css` |
| 4060 | `frontend/src/components/vkpi/pages/projects/projectBoard.css` |
| 3854 | `frontend/src/components/vkpi/pages/data-analysis/styles/data-analysis.css` |
| 3406 | `backend/app/services/vkpi/repair_repository.py` |
| 3334 | `frontend/src/components/vkpi/pages/DashboardPremium.tsx` |
| 3022 | `frontend/src/components/vkpi/pages/DiscoverPage.tsx` |
| 2866 | `frontend/src/services/vkpi.ui-api.ts` |
| 2699 | `backend/app/services/via/session_service.py` |
| 2342 | `frontend/src/components/vkpi/pages/projects/ProjectDetailView.tsx` |
| 2338 | `backend/app/services/vkpi/memory.py` |
| 2234 | `backend/app/services/memory/via_learning.py` |
| 2121 | `backend/app/api/routers/admin.py` |

Priority order:

1. Freeze Repair Center and stop adding behavior there.
2. Split frontend API clients so domain pages do not depend on one giant API file.
3. Split Dashboard page and dashboard CSS before further UI work.
4. Move market/intelligence code into explicit domains before adding more providers.
5. Split KOL/project/product/attribution files as their domains migrate.

Until a file is below 800 lines, it should only receive changes that are part of its extraction.
